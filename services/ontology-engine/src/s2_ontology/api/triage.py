"""Triage queues: the objects that meet each rule now, with the evidence."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_ontology.api.deps import get_principal, get_session
from s2_ontology.api.schemas import TriageQueueOut
from s2_ontology.domain import registry, triage

router = APIRouter(prefix="/v1/triage", tags=["triage"])


@router.get(
    "/queues",
    response_model=list[TriageQueueOut],
    summary="Every triage queue you may see, with how many objects are in it now",
)
def list_queues(
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[TriageQueueOut]:
    telemetry = request.app.state.telemetry_engine
    out = []
    for rule in registry.list_triage_rules(session):
        if not triage.visible(principal, rule):
            continue
        evaluation = triage.evaluate(session, telemetry, principal, rule)
        out.append(
            TriageQueueOut(
                rule=registry.triage_rule_out(rule), count=len(evaluation.items), warning=evaluation.warning
            )
        )
    return out


@router.get(
    "/queues/{rule_name}",
    response_model=TriageQueueOut,
    summary="One queue: the objects in it, each with the readings that put it there",
)
def get_queue(
    request: Request,
    rule_name: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> TriageQueueOut:
    rule = triage.require_visible(principal, registry.get_triage_rule(session, rule_name))
    evaluation = triage.evaluate(session, request.app.state.telemetry_engine, principal, rule)
    return TriageQueueOut(
        rule=registry.triage_rule_out(rule),
        count=len(evaluation.items),
        warning=evaluation.warning,
        items=evaluation.items,
    )
