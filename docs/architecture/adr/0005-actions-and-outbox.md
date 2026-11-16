# 0005. Actions commit in one transaction; write-backs through an outbox

Status: accepted (November 2026)

## Context

The specification calls for governed, transactional actions that validate business rules, update ontology state and write back to enterprise systems (dispatch a mission, create a work order in SAP or Maximo, ground a vehicle). An ERP cannot take part in a database transaction, and a network call inside one would hold locks for as long as the remote system takes to answer.

## Decision

An action request does this, in `services/ontology-engine`, in one PostgreSQL transaction:

1. validate the parameters against the action's JSON Schema;
2. ask OPA once, with the actor, the action, the parameters and the current state of the target; this single decision covers authorisation and business rules;
3. apply the ontology edits;
4. insert one `writeback_outbox` row per write-back;
5. append the audit entry.

Delivery to the outside world is separate and asynchronous. `services/action-executor` claims due rows from the outbox, calls the connector, and marks the row delivered or schedules a retry (details in [0009](0009-action-executor.md)). Delivery is at-least-once; the outbox row id is sent as the idempotency key.

Other rules:

- An action whose definition says `requires_approval` stops after step 2 as `pending_approval`. Approval re-runs step 2 with the approver as actor and the state as it is then, and then runs steps 3 to 5. The requester cannot be the approver.
- A denial, or a policy engine that cannot be reached, executes nothing and is itself audited.
- If an edit cannot apply (the target is gone, a value is invalid), the edits roll back to a savepoint and the execution is recorded as `failed`.
- Callers may send an `Idempotency-Key`; a repeated key returns the original execution.
- Edit templates substitute parameters and do nothing else. There is no expression language: conditions belong in the policy.

## Consequences

- The twin and its audit log cannot disagree, and a write-back can never be sent for a change that did not commit.
- The twin can be ahead of the enterprise system for as long as delivery takes or fails. The outbox makes that lag visible and retryable; it does not remove it. Actions that must not take effect until the remote system confirms need a two-step design (request, then confirm on callback), which is not built.
- Compensating a delivered write-back after a later failure is not handled.
- Delivery is built; see [0009](0009-action-executor.md), which refines the claim, retry and audit details above.
