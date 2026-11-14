"""Governed actions: request, approve, reject, inspect."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_common.opa import OpaClient
from s2_ontology.api.deps import APPROVER, get_opa, get_principal, get_session, require_roles
from s2_ontology.api.schemas import ActionExecutionOut, ActionRequest, RejectRequest
from s2_ontology.db.models import ActionExecution, ExecutionStatus
from s2_ontology.domain import actions
from s2_ontology.domain.errors import Conflict

router = APIRouter(prefix="/v1", tags=["actions"])


def _respond(session: Session, execution: ActionExecution) -> ActionExecutionOut:
    """Commit, then report. A failed execution is committed too (it is a fact worth
    keeping, and so is its audit entry) but answers 409, with the execution in the body."""
    out = actions.execution_out(execution)
    session.commit()
    if execution.status == ExecutionStatus.FAILED:
        message = (out.error or {}).get("message", "the action could not be applied")
        raise Conflict(
            f"the action failed and nothing was changed: {message}", execution=out.model_dump(mode="json")
        )
    return out


@router.post(
    "/actions/{api_name}/apply",
    response_model=ActionExecutionOut,
    summary="Request an action",
    description=(
        "200: the action ran. 202: it awaits approval. 403: refused by its policy. "
        "409: it failed and changed nothing. Send an Idempotency-Key header to make retries safe; "
        "a replayed response carries `Idempotent-Replay: true`."
    ),
    responses={202: {"model": ActionExecutionOut, "description": "Awaiting approval"}},
)
def apply_action(
    api_name: str,
    body: ActionRequest,
    response: Response,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=200)] = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    opa: OpaClient = Depends(get_opa),
) -> ActionExecutionOut:
    key = idempotency_key or str(uuid.uuid4())
    execution, replayed = actions.request(session, opa, principal, api_name, body.parameters, key)
    if replayed:
        response.headers["Idempotent-Replay"] = "true"
    if execution.status == ExecutionStatus.PENDING_APPROVAL:
        response.status_code = 202
    return _respond(session, execution)


@router.get("/action-executions/{execution_id}", response_model=ActionExecutionOut)
def get_execution(
    execution_id: uuid.UUID,
    _: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> ActionExecutionOut:
    return actions.execution_out(actions.get_execution(session, execution_id))


@router.post(
    "/action-executions/{execution_id}/approve",
    response_model=ActionExecutionOut,
    summary="Approve and run a pending execution",
    description="The action's policy is evaluated again, with the approver as actor and the current state.",
)
def approve_execution(
    execution_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
    opa: OpaClient = Depends(get_opa),
) -> ActionExecutionOut:
    return _respond(session, actions.approve(session, opa, principal, execution_id))


@router.post("/action-executions/{execution_id}/reject", response_model=ActionExecutionOut)
def reject_execution(
    execution_id: uuid.UUID,
    body: RejectRequest,
    principal: Principal = Depends(require_roles(APPROVER)),
    session: Session = Depends(get_session),
) -> ActionExecutionOut:
    return _respond(session, actions.reject(session, principal, execution_id, body.reason))
