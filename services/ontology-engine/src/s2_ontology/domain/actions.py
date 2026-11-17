"""Governed actions: the only sanctioned way for operators and agents to change the twin.

One request runs this sequence inside a single database transaction:

    validate parameters (JSON Schema)
      -> ask OPA (authorisation and business rules, one decision)
      -> apply the ontology edits
      -> queue write-backs in the outbox
      -> append the audit entry

Either all of it commits or none of it does. Delivery of the queued write-backs to
ERP / CMMS / fleet systems happens afterwards, at-least-once, in services/action-executor.

Denials and policy-engine failures are themselves audited, in their own transaction,
before the error is raised.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from s2_common.auth import Principal
from s2_common.opa import Decision, OpaClient, PolicyError
from s2_ontology.api.schemas import ActionExecutionOut, WritebackOut
from s2_ontology.db.models import (
    ActionExecution,
    ActionType,
    ActorType,
    AuditOutcome,
    ExecutionStatus,
    LifecycleStatus,
    WritebackOutbox,
)
from s2_ontology.domain import audit, objects, registry
from s2_ontology.domain.errors import Conflict, Denied, DomainError, Invalid, NotFound, PolicyUnavailable
from s2_ontology.domain.templates import TemplateError, render


def execution_out(execution: ActionExecution) -> ActionExecutionOut:
    return ActionExecutionOut(
        id=execution.id,
        action=execution.action_type.api_name,
        action_type_version=execution.action_type_version,
        status=execution.status,
        parameters=execution.parameters,
        actor_type=execution.actor_type,
        actor_id=execution.actor_id,
        on_behalf_of=execution.on_behalf_of,
        approved_by=execution.approved_by,
        policy_decision_id=execution.policy_decision_id,
        error=execution.error,
        created_at=execution.created_at,
        completed_at=execution.completed_at,
        writebacks=[WritebackOut.model_validate(w) for w in execution.writebacks],
    )


def list_executions(
    session: Session,
    *,
    status: ExecutionStatus | None = None,
    action: str | None = None,
    limit: int = 100,
) -> list[ActionExecution]:
    """Newest first. Visible to anyone in the tenant, like a single execution."""
    query = (
        select(ActionExecution)
        .options(selectinload(ActionExecution.writebacks), selectinload(ActionExecution.action_type))
        .order_by(ActionExecution.created_at.desc(), ActionExecution.id.desc())
        .limit(limit)
    )
    if status is not None:
        query = query.where(ActionExecution.status == status)
    if action is not None:
        query = query.join(ActionExecution.action_type).where(ActionType.api_name == action)
    return list(session.scalars(query))


def get_execution(session: Session, execution_id: uuid.UUID) -> ActionExecution:
    execution = session.scalar(
        select(ActionExecution)
        .options(selectinload(ActionExecution.writebacks))
        .where(ActionExecution.id == execution_id)
    )
    if execution is None:
        raise NotFound(f"action execution '{execution_id}' does not exist")
    return execution


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #


def request(
    session: Session,
    opa: OpaClient,
    principal: Principal,
    api_name: str,
    parameters: dict[str, Any],
    idempotency_key: str,
) -> tuple[ActionExecution, bool]:
    """Run an action, or queue it for approval. Returns (execution, replayed)."""
    action = registry.get_action_type(session, api_name)
    if action.status != LifecycleStatus.ACTIVE:
        raise Conflict(f"action '{api_name}' is {action.status} and cannot be executed")

    previous = session.scalar(
        select(ActionExecution)
        .options(selectinload(ActionExecution.writebacks))
        .where(ActionExecution.idempotency_key == idempotency_key)
    )
    if previous is not None:
        if previous.action_type_id != action.id:
            raise Conflict("this Idempotency-Key was already used for a different action")
        return previous, True

    _validate_parameters(action, parameters)
    decision = _decide(session, opa, principal, action, parameters, stage="request")

    execution = ActionExecution(
        tenant_id=principal.tenant_id,
        action_type_id=action.id,
        action_type_version=action.version,
        status=ExecutionStatus.PENDING_APPROVAL,
        parameters=parameters,
        actor_type=ActorType(principal.actor_type),
        actor_id=principal.subject,
        on_behalf_of=principal.on_behalf_of,
        policy_decision_id=decision.decision_id,
        idempotency_key=idempotency_key,
    )
    execution.action_type = action
    session.add(execution)
    session.flush()

    if action.requires_approval:
        audit.record(
            session,
            principal,
            "action.requested",
            resource_type="action_execution",
            resource_id=str(execution.id),
            details={"action": api_name, "parameters": parameters},
            policy_decision_id=decision.decision_id,
        )
    else:
        _execute(session, principal, action, execution, decision.decision_id)
    return execution, False


def _validate_parameters(action: ActionType, parameters: dict[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(action.parameters).iter_errors(parameters), key=lambda e: list(e.path)
    )
    if errors:
        detail = [{"path": "/".join(str(p) for p in e.path), "message": e.message} for e in errors]
        raise Invalid(f"invalid parameters for action '{action.api_name}'", errors=detail)


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


def _target_snapshot(
    session: Session, action: ActionType, parameters: dict[str, Any]
) -> dict[str, Any] | None:
    """The object the action is about, as the policy will see it.

    It is the object addressed by the first edit on the action's target object type.
    None when the action has no target type or the object does not exist (yet).
    """
    target_type = action.target_object_type
    if target_type is None:
        return None
    edit = next((e for e in action.edits if e["object_type"] == target_type.api_name), None)
    if edit is None:
        return None
    try:
        key = render(edit["target"], parameters)
    except TemplateError:
        return None
    if key is None:
        return None
    object_type = registry.get_object_type(session, target_type.api_name)
    found = objects.find(session, object_type, str(key))
    if found is None:
        return None
    out = objects.to_out(object_type, *found)
    return {"object_type": out.object_type, "primary_key": out.primary_key, "properties": out.properties}


def _decide(
    session: Session,
    opa: OpaClient,
    principal: Principal,
    action: ActionType,
    parameters: dict[str, Any],
    *,
    stage: str,
    requested_by: str | None = None,
) -> Decision:
    policy_input = {
        "stage": stage,  # "request" | "approval"
        "actor": {
            "id": principal.subject,
            "type": principal.actor_type,
            "roles": sorted(principal.roles),
            "tenant_id": str(principal.tenant_id),
        },
        "requested_by": requested_by or principal.subject,
        "action": {
            "api_name": action.api_name,
            "version": action.version,
            "requires_approval": action.requires_approval,
            "agent_access": action.agent_access,
        },
        "parameters": parameters,
        "target": _target_snapshot(session, action, parameters),
    }
    try:
        decision = opa.decide(action.policy_path, policy_input)
    except PolicyError as exc:
        audit.record(
            session,
            principal,
            "action.policy_error",
            outcome=AuditOutcome.ERROR,
            resource_type="action_type",
            resource_id=action.api_name,
            details={"stage": stage, "error": str(exc)},
        )
        session.commit()
        raise PolicyUnavailable("the policy engine is unavailable; the action was not executed") from exc

    if not decision.allow:
        audit.record(
            session,
            principal,
            "action.denied",
            outcome=AuditOutcome.DENIED,
            resource_type="action_type",
            resource_id=action.api_name,
            details={"stage": stage, "reasons": list(decision.reasons), "parameters": parameters},
            policy_decision_id=decision.decision_id,
        )
        session.commit()
        raise Denied(
            "action denied by policy", reasons=list(decision.reasons), decision_id=decision.decision_id
        )
    return decision


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def _apply_edit(session: Session, principal: Principal, edit: dict[str, Any], params: dict[str, Any]) -> dict:
    object_type = registry.get_object_type(session, edit["object_type"])
    key = render(edit["target"], params)
    op = edit["op"]
    if op == "create_object":
        return objects.write(
            session, principal, object_type, key, render(edit["assign"], params), mode="create"
        )
    if op == "modify_object":
        return objects.write(
            session, principal, object_type, key, render(edit["assign"], params), mode="merge"
        )
    if op == "delete_object":
        return objects.delete(session, object_type, key)
    far_key = render(edit["link_target"], params)
    if op == "create_link":
        return objects.create_link(session, principal, object_type, key, edit["link_type"], far_key)
    if op == "delete_link":
        return objects.delete_link(session, object_type, key, edit["link_type"], far_key)
    raise Invalid(f"unknown edit operation '{op}'")


def _execute(
    session: Session,
    principal: Principal,
    action: ActionType,
    execution: ActionExecution,
    decision_id: str | None,
) -> None:
    """Apply the edits and queue the write-backs, atomically.

    A failure inside (a missing target, an invalid value) rolls the edits back to a
    savepoint and is recorded on the execution as `failed`; it never half-applies.
    """
    params = execution.parameters
    details: dict[str, Any] = {"action": action.api_name, "parameters": params}
    try:
        with session.begin_nested():
            changes = [_apply_edit(session, principal, edit, params) for edit in action.edits]
            for spec in action.writeback:
                execution.writebacks.append(
                    WritebackOutbox(
                        tenant_id=principal.tenant_id,
                        connector=spec["connector"],
                        operation=spec["operation"],
                        payload=render(spec["payload"], params),
                    )
                )
            session.flush()
    except (DomainError, TemplateError) as exc:
        execution.status = ExecutionStatus.FAILED
        execution.error = {"message": str(exc)}
        execution.completed_at = datetime.now(UTC)
        audit.record(
            session,
            principal,
            "action.failed",
            outcome=AuditOutcome.ERROR,
            resource_type="action_execution",
            resource_id=str(execution.id),
            details={**details, "error": str(exc)},
            policy_decision_id=decision_id,
        )
        return

    execution.status = ExecutionStatus.SUCCEEDED
    execution.completed_at = datetime.now(UTC)
    audit.record(
        session,
        principal,
        "action.executed",
        resource_type="action_execution",
        resource_id=str(execution.id),
        details={**details, "changes": changes, "writebacks": len(action.writeback)},
        policy_decision_id=decision_id,
    )


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #


def _pending(session: Session, execution_id: uuid.UUID) -> ActionExecution:
    # Lock the row: two people deciding at once must not both find it pending.
    if (
        session.scalar(select(ActionExecution.id).where(ActionExecution.id == execution_id).with_for_update())
        is None
    ):
        raise NotFound(f"action execution '{execution_id}' does not exist")
    execution = get_execution(session, execution_id)
    if execution.status != ExecutionStatus.PENDING_APPROVAL:
        raise Conflict(f"execution is '{execution.status}', not pending approval")
    return execution


def approve(
    session: Session, opa: OpaClient, principal: Principal, execution_id: uuid.UUID
) -> ActionExecution:
    """Approve and run a pending execution.

    The policy is evaluated again at approval time against the current state of the
    target, with the approver as actor: what was allowed when requested may no longer be.
    """
    execution = _pending(session, execution_id)
    action = execution.action_type
    if action.version != execution.action_type_version:
        raise Conflict("the action definition changed after this request was made; submit it again")
    decision = _decide(
        session,
        opa,
        principal,
        action,
        execution.parameters,
        stage="approval",
        requested_by=execution.actor_id,
    )
    execution.approved_by = principal.subject
    _execute(session, principal, action, execution, decision.decision_id)
    return execution


def reject(session: Session, principal: Principal, execution_id: uuid.UUID, reason: str) -> ActionExecution:
    execution = _pending(session, execution_id)
    execution.status = ExecutionStatus.REJECTED
    execution.error = {"rejected_by": principal.subject, "reason": reason}
    execution.completed_at = datetime.now(UTC)
    audit.record(
        session,
        principal,
        "action.rejected",
        resource_type="action_execution",
        resource_id=str(execution.id),
        details={"action": execution.action_type.api_name, "reason": reason},
    )
    return execution
