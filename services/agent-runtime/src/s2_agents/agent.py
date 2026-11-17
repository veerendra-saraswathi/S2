"""The agent: work through the triage queues and propose what a person should decide.

For each item in a queue that offers an action open to agents, a local model looks at
the evidence, may read a little more (the object, a telemetry trend, the object's recent
actions), and must end with exactly one of:

* propose(action, parameters, rationale): through the public API, as the agent, so the
  engine's rules apply: the action must be open to agents and gated by approval, the
  rationale is required, and the person the agent acts for cannot approve it;
* dismiss(reason): nothing is sent; the reason is logged.

The model cannot widen what it may do. It is offered only the queue's agent-proposable
actions, the object's key is filled in by the agent, not the model, and nothing it
writes is executed: the engine validates the parameters and a person decides.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from s2_agents.engine import Engine, EngineError
from s2_agents.llm import ChatModel, LlmError
from s2_agents.settings import AgentSettings

log = logging.getLogger("s2.agent")

SYSTEM = """You are the S2 fleet agent. You help a maintenance team by looking into objects that \
a triage rule has flagged, and deciding whether a person should be asked to act.

You can only propose. A person reviews every proposal and decides; nothing you propose \
happens unless they approve it. Propose only when the evidence supports it, and say plainly \
what the evidence is. If the evidence is weak, stale or contradictory, dismiss the item and \
say why.

Use the read tools if they help (at most a few calls), then call exactly one of `propose` \
or `dismiss`. In `rationale`, write two to four sentences for the approver: what you saw, \
with numbers and times, and why the action follows. Do not invent readings."""


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


@dataclass
class Outcome:
    queue: str
    object_type: str
    key: str
    decision: str  # "proposed" | "dismissed" | "skipped" | "failed"
    detail: str
    action: str | None = None
    execution_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Item:
    rule: dict[str, Any]
    object: dict[str, Any]
    evidence: list[dict[str, Any]]
    object_type: dict[str, Any]
    proposable: dict[str, dict[str, Any]]  # action name -> action type
    key_property: str

    @property
    def key(self) -> str:
        return str(self.object["primary_key"])


@dataclass
class Run:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def proposed(self) -> int:
        return sum(o.decision == "proposed" for o in self.outcomes)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.decision] = counts.get(outcome.decision, 0) + 1
        return counts


class Agent:
    def __init__(self, engine: Engine, model: ChatModel, settings: AgentSettings) -> None:
        self._engine = engine
        self._model = model
        self._settings = settings

    # ------------------------------------------------------------------ a run

    def run_once(self, *, now: dt.datetime | None = None) -> Run:
        now = now or dt.datetime.now(dt.UTC)
        run = Run()
        action_types = {a["api_name"]: a for a in self._engine.action_types()}
        busy = self._busy_objects(now)
        type_cache: dict[str, dict[str, Any]] = {}

        for summary in self._engine.triage_queues():
            rule = summary["rule"]
            proposable = {
                name: action_types[name]
                for name in rule["actions"]
                if name in action_types
                and action_types[name]["agent_access"] == "propose"
                and action_types[name]["requires_approval"]
            }
            if summary["count"] == 0 or not proposable:
                continue
            object_type = type_cache.setdefault(
                rule["object_type"], self._engine.object_type(rule["object_type"])
            )
            key_property = next(p["api_name"] for p in object_type["properties"] if p["is_primary_key"])
            queue = self._engine.triage_queue(rule["api_name"])
            for entry in queue.get("items") or []:
                item = Item(rule, entry["object"], entry["evidence"], object_type, proposable, key_property)
                reason = busy.get((rule["object_type"], item.key))
                if reason:
                    run.outcomes.append(
                        Outcome(rule["api_name"], rule["object_type"], item.key, "skipped", reason)
                    )
                    continue
                if run.proposed >= self._settings.max_proposals_per_run:
                    run.outcomes.append(
                        Outcome(
                            rule["api_name"],
                            rule["object_type"],
                            item.key,
                            "skipped",
                            "proposal limit for this run",
                        )
                    )
                    continue
                outcome = self.investigate(item, now=now)
                run.outcomes.append(outcome)
                if outcome.decision == "proposed":
                    busy[(rule["object_type"], item.key)] = "proposed in this run"
                log.info("triage item", extra=outcome.as_dict())
        return run

    def _busy_objects(self, now: dt.datetime) -> dict[tuple[str, str], str]:
        """Objects with a request awaiting a decision, or whose agent proposal was rejected
        recently. Keyed by (object type, key); the value says why the item is skipped."""
        action_types = {a["api_name"]: a for a in self._engine.action_types()}
        busy: dict[tuple[str, str], str] = {}
        cooldown = dt.timedelta(seconds=self._settings.cooldown_after_rejection_s)
        for execution in self._engine.executions():
            action = action_types.get(execution["action"])
            target = action and action.get("target_object_type")
            if not target:
                continue
            keys = {str(v) for v in execution["parameters"].values() if isinstance(v, str | int)}
            if execution["status"] == "pending_approval":
                for key in keys:
                    busy.setdefault((target, key), f"{execution['action']} is awaiting a decision")
            elif execution["status"] == "rejected" and execution["actor_type"] == "agent":
                created = dt.datetime.fromisoformat(execution["created_at"])
                if now - created < cooldown:
                    for key in keys:
                        busy.setdefault((target, key), f"a person rejected {execution['action']} recently")
        return busy

    # ------------------------------------------------------------------ one item

    def investigate(self, item: Item, *, now: dt.datetime) -> Outcome:
        rule = item.rule
        base = {"queue": rule["api_name"], "object_type": rule["object_type"], "key": item.key}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": self._brief(item, now)},
        ]
        tools = self._tools(item)
        nudged = False
        for _ in range(self._settings.max_steps):
            try:
                reply = self._model.chat(messages, tools)
            except LlmError as exc:
                return Outcome(**base, decision="failed", detail=str(exc))
            messages.append(reply)
            calls = reply.get("tool_calls") or []
            if not calls:
                if nudged:
                    return Outcome(**base, decision="dismissed", detail="the model gave no decision")
                nudged = True
                messages.append({"role": "user", "content": "Decide now: call `propose` or `dismiss`."})
                continue
            for call in calls:
                name = call.get("function", {}).get("name", "")
                arguments = call.get("function", {}).get("arguments") or {}
                if name == "propose":
                    return self._propose(item, arguments, base)
                if name == "dismiss":
                    return Outcome(
                        **base, decision="dismissed", detail=str(arguments.get("reason") or "no reason given")
                    )
                result = self._read(item, name, arguments, now)
                messages.append(
                    {"role": "tool", "tool_name": name, "content": json.dumps(result, default=str)}
                )
        return Outcome(
            **base, decision="dismissed", detail=f"no decision within {self._settings.max_steps} steps"
        )

    def _brief(self, item: Item, now: dt.datetime) -> str:
        names = {p["api_name"]: p["display_name"] for p in item.object_type["properties"]}
        evidence = "\n".join(
            f"- {names.get(e['property'], e['property'])}: {e['actual']} ({e['op']} {e['expected']})"
            + (f", read at {e['observed_at']}" if e.get("observed_at") else "")
            for e in item.evidence
        )

        def params(action: dict[str, Any]) -> str:
            properties = action["parameters"].get("properties", {})
            return json.dumps({k: v for k, v in properties.items() if k != item.key_property})

        actions = "\n".join(
            f"- {name}: {a['display_name']}. {a.get('description') or ''} Parameters (other than "
            f"{item.key_property}, which is filled in for you): {params(a)}"
            for name, a in item.proposable.items()
        )
        series = [p["api_name"] for p in item.object_type["properties"] if p["data_type"] == "timeseries"]
        return (
            f"It is {now.isoformat(timespec='seconds')}.\n"
            f"Queue: {item.rule['display_name']} (severity {item.rule['severity']}). "
            f"{item.rule.get('description') or ''}\n"
            f"Object: {item.rule['object_type']} {item.key}, "
            f"properties {json.dumps(item.object['properties'])}.\n"
            f"Why it is in the queue:\n{evidence}\n"
            f"Time series you can read: {', '.join(series) or 'none'}.\n"
            f"Actions you may propose:\n{actions}"
        )

    def _tools(self, item: Item) -> list[dict[str, Any]]:
        return [
            _tool("get_object", "The object's current properties.", {}, []),
            _tool(
                "get_series_trend",
                "Summary of one of the object's time series over the last minutes: first, last, min, max, "
                "change per 10 minutes, number of readings, time of the latest.",
                {
                    "property": {"type": "string"},
                    "minutes": {"type": "integer", "minimum": 5, "maximum": 1440},
                },
                ["property"],
            ),
            _tool(
                "recent_actions",
                "The last actions requested on this object, with their outcome and reasons.",
                {},
                [],
            ),
            _tool(
                "propose",
                "Ask a person to approve an action on this object. Ends your work on it.",
                {
                    "action": {"type": "string", "enum": sorted(item.proposable)},
                    "parameters": {
                        "type": "object",
                        "description": "the action's parameters, except the object key",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "two to four sentences for the approver, with the evidence",
                    },
                },
                ["action", "parameters", "rationale"],
            ),
            _tool(
                "dismiss",
                "Decide that no action should be proposed now. Ends your work on it.",
                {"reason": {"type": "string"}},
                ["reason"],
            ),
        ]

    # ------------------------------------------------------------------ tools

    def _read(self, item: Item, name: str, arguments: dict[str, Any], now: dt.datetime) -> Any:
        try:
            match name:
                case "get_object":
                    return self._engine.object(item.rule["object_type"], item.key)["properties"]
                case "get_series_trend":
                    return self._trend(
                        item, str(arguments.get("property", "")), arguments.get("minutes", 60), now
                    )
                case "recent_actions":
                    return self._recent_actions(item)
                case _:
                    return {"error": f"there is no tool {name!r}"}
        except EngineError as exc:
            return {"error": exc.message}

    def _trend(self, item: Item, prop: str, minutes: Any, now: dt.datetime) -> dict[str, Any]:
        try:
            minutes = max(5, min(int(minutes), 1440))
        except (TypeError, ValueError):
            minutes = 60
        start = (now - dt.timedelta(minutes=minutes)).isoformat()
        points = self._engine.timeseries(item.rule["object_type"], item.key, prop, start)["points"]
        if not points:
            return {"property": prop, "minutes": minutes, "readings": 0}
        values = [p["value"] for p in points]
        first_t = dt.datetime.fromisoformat(points[0]["time"])
        last_t = dt.datetime.fromisoformat(points[-1]["time"])
        span_min = max((last_t - first_t).total_seconds() / 60, 1e-9)
        return {
            "property": prop,
            "minutes": minutes,
            "readings": len(values),
            "first": round(values[0], 4),
            "last": round(values[-1], 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "change_per_10_min": round((values[-1] - values[0]) / span_min * 10, 4)
            if len(values) > 1
            else 0.0,
            "latest_at": points[-1]["time"],
        }

    def _recent_actions(self, item: Item) -> list[dict[str, Any]]:
        mine = [
            e
            for e in self._engine.executions(limit=100)
            if str(e["parameters"].get(item.key_property, "")) == item.key
        ][:10]
        return [
            {
                "action": e["action"],
                "status": e["status"],
                "at": e["created_at"],
                "by": e["actor_type"],
                **({"reason": e["parameters"]["reason"]} if "reason" in e["parameters"] else {}),
                **({"rationale": e["rationale"]} if e.get("rationale") else {}),
            }
            for e in mine
        ]

    def _propose(self, item: Item, arguments: dict[str, Any], base: dict[str, str]) -> Outcome:
        action = str(arguments.get("action", ""))
        if action not in item.proposable:
            return Outcome(
                **base, decision="failed", detail=f"the model chose {action!r}, which it may not propose"
            )
        parameters = arguments.get("parameters")
        parameters = dict(parameters) if isinstance(parameters, dict) else {}
        parameters[item.key_property] = item.key  # never the model's choice
        rationale = str(arguments.get("rationale") or "").strip()
        if not rationale:
            return Outcome(**base, decision="failed", detail="the model gave no rationale", action=action)
        try:
            execution = self._engine.propose(action, parameters, rationale)
        except EngineError as exc:
            detail = exc.message + (f": {'; '.join(exc.reasons)}" if exc.reasons else "")
            return Outcome(**base, decision="failed", detail=f"the engine refused: {detail}", action=action)
        return Outcome(
            **base, decision="proposed", detail=rationale, action=action, execution_id=str(execution["id"])
        )
