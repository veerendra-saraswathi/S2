"""The agent end to end: what it may propose, how, and what stays a person's decision."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from s2_agents.agent import Agent
from s2_agents.engine import Engine
from s2_agents.llm import LlmError, Ollama
from s2_agents.settings import AgentSettings

from .conftest import Scripted, Token, call, headers_for

RATIONALE = (
    "Actuator health fell from 0.95 to 0.68 over the last 30 minutes, about 0.09 per 10 minutes, "
    "and the drone is still in service. Grounding it now prevents a flight on failing actuators."
)
PROPOSE = call(
    "propose",
    action="proposeGrounding",
    parameters={"reason": "actuator health 0.68 and falling"},
    rationale=RATIONALE,
)


def outcome(run: Any, key: str) -> Any:
    (found,) = [o for o in run.outcomes if o.key == key]
    return found


def settings(**over: Any) -> AgentSettings:
    return AgentSettings(**{"max_proposals_per_run": 3, "max_steps": 6, **over})


def pending(engine: TestClient, token: Token) -> list[dict[str, Any]]:
    return engine.get(
        "/v1/action-executions", params={"status": "pending_approval"}, headers=headers_for(token, "viewer")
    ).json()


def status(engine: TestClient, token: Token, key: str) -> str:
    return engine.get(f"/v1/objects/CommercialDrone/{key}", headers=headers_for(token, "viewer")).json()[
        "properties"
    ]["status"]


def test_it_reads_the_trend_then_proposes_for_a_person_to_decide(
    engine: TestClient, agent_engine: Engine, token: Token, fleet: None
) -> None:
    model = Scripted(call("get_series_trend", property="actuatorHealth", minutes=35), PROPOSE)
    run = Agent(agent_engine, model, settings()).run_once()

    assert [(o.key, o.queue, o.decision) for o in run.outcomes] == [
        ("D1", "degradingActuators", "proposed"),
        ("D3", "groundedDrones", "dismissed"),  # it also looks at grounded drones: it may propose a return
    ]
    (execution,) = pending(engine, token)
    assert execution["action"] == "proposeGrounding"
    assert (execution["actor_type"], execution["actor_id"], execution["on_behalf_of"]) == (
        "agent",
        "svc-s2-agent",
        "supervisor",
    )
    assert execution["rationale"] == RATIONALE
    assert execution["parameters"] == {"droneId": "D1", "reason": "actuator health 0.68 and falling"}
    assert status(engine, token, "D1") == "READY", "a proposal changes nothing until a person approves it"

    brief = model.conversations[0][1]["content"]
    assert "Degrading actuators" in brief and "D1" in brief and "proposeGrounding" in brief
    trend = json.loads(model.conversations[1][-1]["content"])
    assert trend["readings"] == 31 and trend["first"] == 0.95 and trend["last"] == 0.68
    assert trend["change_per_10_min"] == pytest.approx(-0.09, abs=0.001)
    offered = {t["function"]["name"] for t in model.tools[0]}
    assert offered == {"get_object", "get_series_trend", "recent_actions", "propose", "dismiss"}
    propose_spec = next(t for t in model.tools[0] if t["function"]["name"] == "propose")
    assert propose_spec["function"]["parameters"]["properties"]["action"]["enum"] == ["proposeGrounding"]


def test_people_decide_and_four_eyes_reaches_through_the_agent(
    engine: TestClient, agent_engine: Engine, token: Token, fleet: None
) -> None:
    Agent(agent_engine, Scripted(PROPOSE), settings()).run_once()
    (execution,) = pending(engine, token)
    approve = f"/v1/action-executions/{execution['id']}/approve"

    supervisor = headers_for(token, "approver", sub="user-supervisor", preferred_username="supervisor")
    refused = engine.post(approve, headers=supervisor)
    assert refused.status_code == 403
    assert "the person an agent acts for cannot approve its proposal" in refused.json()["detail"]["reasons"]

    approver = headers_for(token, "approver", sub="user-approver", preferred_username="approver")
    assert engine.post(approve, headers=approver).status_code == 200
    assert status(engine, token, "D1") == "GROUNDED"


def test_it_does_not_propose_twice_or_nag_after_a_rejection(
    engine: TestClient, agent_engine: Engine, token: Token, fleet: None
) -> None:
    Agent(agent_engine, Scripted(PROPOSE), settings()).run_once()
    second = Agent(agent_engine, Scripted(), settings()).run_once()  # D1 is skipped: the model is not asked
    assert (outcome(second, "D1").decision, outcome(second, "D1").detail) == (
        "skipped",
        "proposeGrounding is awaiting a decision",
    )

    (execution,) = pending(engine, token)
    reject = f"/v1/action-executions/{execution['id']}/reject"
    approver = headers_for(token, "approver", sub="user-approver")
    assert (
        engine.post(reject, json={"reason": "sensor fault, not the actuators"}, headers=approver).status_code
        == 200
    )
    third = Agent(agent_engine, Scripted(), settings()).run_once()
    assert outcome(third, "D1").decision == "skipped"
    assert "rejected proposeGrounding recently" in outcome(third, "D1").detail
    assert pending(engine, token) == []


def test_the_model_cannot_choose_another_action_or_another_object(
    engine: TestClient, agent_engine: Engine, token: Token, fleet: None
) -> None:
    sneaky = call("propose", action="groundDrone", parameters={"reason": "now"}, rationale="because")
    run = Agent(agent_engine, Scripted(sneaky), settings()).run_once()
    assert outcome(run, "D1").decision == "failed" and "may not propose" in outcome(run, "D1").detail
    assert pending(engine, token) == [] and status(engine, token, "D1") == "READY"

    elsewhere = call(
        "propose", action="proposeGrounding", parameters={"droneId": "D2", "reason": "move it"}, rationale="r"
    )
    Agent(agent_engine, Scripted(elsewhere), settings()).run_once()
    (execution,) = pending(engine, token)
    assert execution["parameters"]["droneId"] == "D1", "the key is the agent's, never the model's"


def test_dismissing_sends_nothing(
    engine: TestClient, agent_engine: Engine, token: Token, fleet: None
) -> None:
    run = Agent(
        agent_engine, Scripted(call("dismiss", reason="readings look like sensor noise")), settings()
    ).run_once()
    assert (run.outcomes[0].decision, run.outcomes[0].detail) == (
        "dismissed",
        "readings look like sensor noise",
    )
    assert pending(engine, token) == []


def test_a_model_that_will_not_decide_is_dismissed(
    engine: TestClient, agent_engine: Engine, token: Token, fleet: None
) -> None:
    model = Scripted({"content": "Hmm."}, {"content": "Still thinking."})
    run = Agent(agent_engine, model, settings()).run_once()
    assert (outcome(run, "D1").decision, outcome(run, "D1").detail) == (
        "dismissed",
        "the model gave no decision",
    )
    assert "Decide now" in model.conversations[1][-1]["content"]

    loops = Scripted(*[call("get_object")] * 10)
    run = Agent(agent_engine, loops, settings(max_steps=3)).run_once()
    assert outcome(run, "D1").detail == "no decision within 3 steps"


def test_failures_are_reported_not_raised(
    engine: TestClient, agent_engine: Engine, token: Token, fleet: None
) -> None:
    down = Agent(agent_engine, Scripted(LlmError("Ollama is not reachable")), settings()).run_once()
    assert (outcome(down, "D1").decision, outcome(down, "D1").detail) == ("failed", "Ollama is not reachable")

    short = call("propose", action="proposeGrounding", parameters={"reason": "bad"}, rationale="falling")
    refused = Agent(agent_engine, Scripted(short), settings()).run_once()
    assert outcome(refused, "D1").decision == "failed"
    assert "the engine refused: invalid parameters" in outcome(refused, "D1").detail

    silent = call("propose", action="proposeGrounding", parameters={"reason": "falling fast"}, rationale="  ")
    assert (
        Agent(agent_engine, Scripted(silent), settings()).run_once().outcomes[0].detail
        == "the model gave no rationale"
    )


def test_the_proposal_limit_and_an_unknown_tool(
    engine: TestClient, agent_engine: Engine, token: Token, fleet: None
) -> None:
    run = Agent(agent_engine, Scripted(PROPOSE), settings(max_proposals_per_run=0)).run_once()
    assert {(o.key, o.decision, o.detail) for o in run.outcomes} == {
        ("D1", "skipped", "proposal limit for this run"),
        ("D3", "skipped", "proposal limit for this run"),
    }

    model = Scripted(call("delete_everything"), call("dismiss", reason="ok"))
    Agent(agent_engine, model, settings()).run_once()
    assert json.loads(model.conversations[1][-1]["content"]) == {
        "error": "there is no tool 'delete_everything'"
    }


def test_the_agent_cannot_see_what_markings_hide(engine: TestClient, token: Token, fleet: None) -> None:
    """Without marking:internal the actuator queue does not exist for it; only grounded drones remain."""
    blind = Engine(
        engine,
        lambda: token("operator", "viewer", sub="svc", s2_actor_type="agent", s2_on_behalf_of="supervisor"),
    )
    model = Scripted()
    run = Agent(blind, model, settings()).run_once()
    assert [(o.key, o.queue) for o in run.outcomes] == [("D3", "groundedDrones")]
    assert all("Degrading actuators" not in c[1]["content"] for c in model.conversations)


# --------------------------------------------------------------------------- Ollama


def test_ollama_request_and_reply() -> None:
    seen: dict[str, Any] = {}

    def ollama(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        args = json.dumps({"reason": "noise"})  # some models send arguments as a JSON string
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "dismiss", "arguments": args}}],
                }
            },
        )

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(ollama))
    reply = Ollama(AgentSettings(ollama_model="llama3.1", temperature=0.1), client).chat(
        [{"role": "user", "content": "hi"}], [{"type": "function"}]
    )
    assert reply["tool_calls"][0]["function"]["arguments"] == {"reason": "noise"}
    assert (seen["model"], seen["stream"], seen["options"]) == ("llama3.1", False, {"temperature": 0.1})


def test_ollama_down_or_failing_is_an_llm_error() -> None:
    def refused(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    for handler, message in (
        (refused, "not reachable"),
        (lambda _: httpx.Response(404, text="model not found"), "HTTP 404"),
    ):
        client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
        with pytest.raises(LlmError, match=message):
            Ollama(AgentSettings(), client).chat([], [])
