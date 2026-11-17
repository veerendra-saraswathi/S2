"""Triage: which objects meet a rule's conditions right now, and why.

A rule's conditions all have to hold. Scalar conditions test the object's stored value;
a condition on a time series tests the latest reading, which must be recent enough
(`max_age_s`, default 15 minutes): a drone that stopped reporting is not "healthy", but
neither is it proof of a fault, so a missing or stale reading fails the condition.

A missing value fails every comparison except `ne`.

Markings apply (ADR 0010): a rule over a type or a property the caller is not cleared
for does not exist for them, because its items would reveal the hidden values.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_ontology.api.schemas import Evidence, TriageItemOut
from s2_ontology.db.models import PropertyDataType, TriageRule
from s2_ontology.domain import markings, objects
from s2_ontology.domain.errors import NotFound

DEFAULT_MAX_AGE_S = 900
MAX_OBJECTS = 5000

# Latest reading per object for one metric, no older than the cutoff.
_LATEST = text(
    """
    SELECT DISTINCT ON (object_pk) object_pk, value, time
      FROM telemetry.reading
     WHERE tenant_id = :tenant AND object_type = :object_type AND metric = :metric
       AND time >= :since AND value IS NOT NULL AND object_pk IN :keys
     ORDER BY object_pk, time DESC
    """
).bindparams(bindparam("keys", expanding=True))


@dataclass
class Evaluation:
    items: list[TriageItemOut]
    warning: str | None = None


def visible(principal: Principal, rule: TriageRule) -> bool:
    object_type = rule.object_type
    if not markings.cleared(principal, object_type.markings):
        return False
    tested = {c["property"] for c in rule.conditions}
    by_name = {p.api_name: p for p in object_type.properties}
    return all(
        markings.visible_property(principal, object_type, by_name[name]) for name in tested if name in by_name
    )


def require_visible(principal: Principal, rule: TriageRule) -> TriageRule:
    if not visible(principal, rule):
        raise NotFound(f"triage rule '{rule.api_name}' does not exist")
    return rule


def _holds(op: str, actual: Any, expected: Any) -> bool:
    if actual is None:
        return op == "ne"
    try:
        match op:
            case "eq":
                return actual == expected
            case "ne":
                return actual != expected
            case "in":
                return actual in expected
            case "lt":
                return actual < expected
            case "le":
                return actual <= expected
            case "gt":
                return actual > expected
            case "ge":
                return actual >= expected
    except TypeError:
        return False  # a stored value of the wrong type never matches
    return False


def evaluate(
    session: Session,
    telemetry: Engine | None,
    principal: Principal,
    rule: TriageRule,
    *,
    now: dt.datetime | None = None,
) -> Evaluation:
    object_type = rule.object_type
    definition = {p.api_name: p for p in object_type.properties}
    hidden = markings.hidden_properties(principal, object_type)
    found = objects.list_objects(session, object_type, limit=MAX_OBJECTS)
    rows = {instance.primary_key: (instance, geometry) for instance, geometry in found}
    values = {key: objects.property_map(object_type, *row) for key, row in rows.items()}

    series = [
        c for c in rule.conditions if definition[c["property"]].data_type == PropertyDataType.TIMESERIES
    ]
    latest: dict[str, dict[str, tuple[float, dt.datetime]]] = {}
    warning = None
    if series:
        if telemetry is None:
            warning = "no telemetry store is configured, so conditions on time series cannot hold"
        elif rows:
            now = now or dt.datetime.now(dt.UTC)
            with telemetry.connect() as connection:
                for condition in series:
                    metric = (definition[condition["property"]].binding or {})["metric"]
                    since = now - dt.timedelta(seconds=condition.get("max_age_s", DEFAULT_MAX_AGE_S))
                    result = connection.execute(
                        _LATEST,
                        {
                            "tenant": principal.tenant_id,
                            "object_type": object_type.api_name,
                            "metric": metric,
                            "since": since,
                            "keys": list(rows),
                        },
                    )
                    latest[condition["property"]] = {r.object_pk: (r.value, r.time) for r in result}

    items: list[TriageItemOut] = []
    for key, properties in values.items():
        evidence: list[Evidence] = []
        for condition in rule.conditions:
            name, op, expected = condition["property"], condition["op"], condition["value"]
            observed_at = None
            if name in latest or definition[name].data_type == PropertyDataType.TIMESERIES:
                reading = latest.get(name, {}).get(key)
                actual, observed_at = reading if reading else (None, None)
            else:
                actual = properties.get(name)
            if not _holds(op, actual, expected):
                break
            evidence.append(
                Evidence(property=name, op=op, expected=expected, actual=actual, observed_at=observed_at)
            )
        else:
            items.append(
                TriageItemOut(object=objects.to_out(object_type, *rows[key], hidden), evidence=evidence)
            )
    return Evaluation(items, warning)
