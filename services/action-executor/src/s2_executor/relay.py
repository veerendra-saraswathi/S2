"""The outbox relay: claim due write-backs, deliver them, record the outcome.

A row moves  pending -> delivered  or  pending -> dead.  The work is split into three
short database transactions so that nothing holds a lock across a network call:

1. **Claim.** One statement picks due rows with FOR UPDATE SKIP LOCKED, counts the
   attempt, and pushes `next_attempt_at` out by a lease. Workers never see each other's
   claims; a worker that crashes simply lets its lease run out and the row is retried.
2. **Deliver**, with no transaction open.
3. **Finalize.** Delivered, retry later, or dead, together with the audit entry for the
   first and last of those, in one transaction.

Finalizing only touches a row that is still `pending`. If a slow worker's lease expired
and another worker already finished the row, the slow worker's outcome is dropped, and
the target sees the idempotency key twice, which is what at-least-once means.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from s2_executor.connectors import Connector, DeliveryError, PermanentError
from s2_executor.settings import ExecutorSettings

log = logging.getLogger("s2.executor.relay")

ACTOR_ID = "s2-action-executor"

# SKIP LOCKED stops workers queueing behind each other. Correctness does not rest on it:
# the lease pushes next_attempt_at out in the same statement, so a worker that waited for a
# lock re-checks the predicate, finds the row no longer due, and moves on.
CLAIM_SQL = """
WITH due AS (
    SELECT id
      FROM ontology.writeback_outbox
     WHERE status = 'pending' AND next_attempt_at <= now()
     ORDER BY next_attempt_at
     LIMIT %(limit)s
       FOR UPDATE SKIP LOCKED
)
UPDATE ontology.writeback_outbox AS o
   SET attempts = o.attempts + 1,
       next_attempt_at = now() + make_interval(secs => %(lease_s)s)
  FROM due
 WHERE o.id = due.id
RETURNING o.id, o.tenant_id, o.action_execution_id, o.connector, o.operation, o.payload, o.attempts
"""

MARK_DELIVERED_SQL = """
UPDATE ontology.writeback_outbox
   SET status = 'delivered', delivered_at = now(), last_error = NULL
 WHERE id = %(id)s AND status = 'pending'
"""

MARK_DEAD_SQL = """
UPDATE ontology.writeback_outbox
   SET status = 'dead', last_error = %(error)s
 WHERE id = %(id)s AND status = 'pending'
"""

SCHEDULE_RETRY_SQL = """
UPDATE ontology.writeback_outbox
   SET next_attempt_at = now() + make_interval(secs => %(delay_s)s), last_error = %(error)s
 WHERE id = %(id)s AND status = 'pending'
"""

# seq, prev_hash, entry_hash and occurred_at are filled in by the chain trigger.
AUDIT_SQL = """
INSERT INTO ontology.audit_log
    (tenant_id, actor_type, actor_id, event_type, resource_type, resource_id, outcome, details)
VALUES
    (%(tenant_id)s, 'service', %(actor)s, %(event)s, 'writeback_outbox', %(id)s, %(outcome)s, %(details)s)
"""


@dataclass(frozen=True)
class Claim:
    id: uuid.UUID
    tenant_id: uuid.UUID
    action_execution_id: uuid.UUID
    connector: str
    operation: str
    payload: dict[str, Any]
    attempts: int  # including this one


def backoff_delay(
    attempts: int, *, base_s: float, cap_s: float, rng: Callable[[], float] = random.random
) -> float:
    """Exponential, capped, with jitter in [50%, 100%] so a target that comes back after
    an outage is not hit by every retry at once."""
    ceiling = min(cap_s, base_s * 2 ** max(attempts - 1, 0))
    return ceiling * (0.5 + 0.5 * rng())


def assert_executor_role(connection: psycopg.Connection[Any]) -> None:
    """Refuse to run as a role that is wrong in either direction.

    Without BYPASSRLS, row-level security hides every tenant's rows and the relay would
    idle forever looking healthy. A superuser could do far more than the executor needs.
    """
    row = connection.execute(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert row is not None
    name, is_super, bypasses = row
    if is_super:
        raise RuntimeError(f"S2_EXECUTOR_DATABASE_URL connects as superuser {name!r}; use s2_executor")
    if not bypasses:
        raise RuntimeError(
            f"role {name!r} lacks BYPASSRLS, so row-level security would hide every outbox row; "
            "use the s2_executor role created by migration 0002"
        )


class Relay:
    def __init__(
        self,
        connection: psycopg.Connection[Any],
        connectors: Mapping[str, Connector],
        settings: ExecutorSettings,
        *,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._db = connection
        self._connectors = connectors
        self._settings = settings
        self._rng = rng

    # ------------------------------------------------------------------ one pass

    def run_once(self) -> int:
        """Claim and process one batch. Returns how many rows were claimed."""
        claims = self._claim()
        for claim in claims:
            self._process(claim)
        return len(claims)

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            claimed = self.run_once()
            if claimed < self._settings.batch_size:
                stop.wait(self._settings.poll_interval_s)

    # ------------------------------------------------------------------ steps

    def _claim(self) -> list[Claim]:
        with self._db.transaction(), self._db.cursor(row_factory=dict_row) as cursor:
            cursor.execute(CLAIM_SQL, {"limit": self._settings.batch_size, "lease_s": self._settings.lease_s})
            return [Claim(**row) for row in cursor.fetchall()]

    def _process(self, claim: Claim) -> None:
        started = time.monotonic()
        try:
            connector = self._connectors.get(claim.connector)
            if connector is None:
                raise PermanentError(f"no connector named {claim.connector!r} is configured")
            connector.deliver(claim.operation, claim.payload, claim.id)
        except DeliveryError as exc:
            self._record_failure(claim, str(exc), permanent=isinstance(exc, PermanentError))
        except Exception as exc:
            # A bug in a connector must not stop the relay, and must not lose the row.
            log.exception("connector raised unexpectedly", extra={"outbox_id": str(claim.id)})
            self._record_failure(claim, f"unexpected {type(exc).__name__}: {exc}", permanent=False)
        else:
            self._record_delivered(claim, elapsed_ms=round((time.monotonic() - started) * 1000))

    def _record_delivered(self, claim: Claim, *, elapsed_ms: int) -> None:
        with self._db.transaction():
            updated = self._db.execute(MARK_DELIVERED_SQL, {"id": claim.id}).rowcount
            if updated:
                self._audit(claim, "writeback.delivered", "success", {"elapsed_ms": elapsed_ms})
        if updated:
            log.info("write-back delivered", extra=self._log_fields(claim))
        else:
            log.warning(
                "write-back was delivered but the row is no longer pending; another worker "
                "finished it first (lease expired)",
                extra=self._log_fields(claim),
            )

    def _record_failure(self, claim: Claim, error: str, *, permanent: bool) -> None:
        exhausted = claim.attempts >= self._settings.max_attempts
        dead = permanent or exhausted
        with self._db.transaction():
            if dead:
                updated = self._db.execute(MARK_DEAD_SQL, {"id": claim.id, "error": error}).rowcount
                if updated:
                    reason = "permanent error" if permanent else "retries exhausted"
                    self._audit(claim, "writeback.dead", "error", {"error": error, "reason": reason})
            else:
                delay = backoff_delay(
                    claim.attempts,
                    base_s=self._settings.backoff_base_s,
                    cap_s=self._settings.backoff_cap_s,
                    rng=self._rng,
                )
                updated = self._db.execute(
                    SCHEDULE_RETRY_SQL, {"id": claim.id, "error": error, "delay_s": delay}
                ).rowcount
        fields = {**self._log_fields(claim), "error": error}
        if not updated:
            log.warning("failed delivery ignored: row no longer pending", extra=fields)
        elif dead:
            # Needs a person. Alerting keys off `alert` on this line until a real hook exists.
            log.error("write-back is dead", extra={**fields, "alert": True})
        else:
            log.warning("write-back failed, will retry", extra={**fields, "retry_in_s": round(delay, 1)})

    def _audit(self, claim: Claim, event: str, outcome: str, extra: dict[str, Any]) -> None:
        details = {
            "action_execution_id": str(claim.action_execution_id),
            "connector": claim.connector,
            "operation": claim.operation,
            "attempts": claim.attempts,
            **extra,
        }
        self._db.execute(
            AUDIT_SQL,
            {
                "tenant_id": claim.tenant_id,
                "actor": ACTOR_ID,
                "event": event,
                "id": str(claim.id),
                "outcome": outcome,
                "details": Jsonb(details),
            },
        )

    @staticmethod
    def _log_fields(claim: Claim) -> dict[str, Any]:
        return {
            "outbox_id": str(claim.id),
            "tenant_id": str(claim.tenant_id),
            "connector": claim.connector,
            "operation": claim.operation,
            "attempts": claim.attempts,
        }
