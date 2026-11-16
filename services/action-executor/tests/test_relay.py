"""The relay against a real PostgreSQL: claiming, retry, dead letter, tenants, privileges."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from s2_executor.connectors import PermanentError, TransientError
from s2_executor.relay import ACTOR_ID, Relay, assert_executor_role, backoff_delay


class FakeConnector:
    """Records calls; `outcomes` is consumed one per call (an exception is raised)."""

    def __init__(self, *outcomes: Exception | None) -> None:
        self.calls: list[tuple[str, dict[str, Any], uuid.UUID]] = []
        self._outcomes = list(outcomes)
        self._lock = threading.Lock()

    def deliver(self, operation: str, payload: dict[str, Any], idempotency_key: uuid.UUID) -> None:
        with self._lock:
            self.calls.append((operation, payload, idempotency_key))
            outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome

    def close(self) -> None:
        return None


def row(owner: psycopg.Connection[Any], outbox_id: uuid.UUID) -> dict[str, Any]:
    cursor = owner.execute(
        "SELECT status, attempts, next_attempt_at, delivered_at, last_error"
        " FROM ontology.writeback_outbox WHERE id = %s",
        (outbox_id,),
    )
    columns = [d.name for d in cursor.description]
    return dict(zip(columns, cursor.fetchone(), strict=True))


def audit_events(owner: psycopg.Connection[Any], tenant: uuid.UUID) -> list[tuple[str, str, str, dict]]:
    return owner.execute(
        "SELECT event_type, outcome, resource_id, details FROM ontology.audit_log"
        " WHERE tenant_id = %s ORDER BY seq",
        (tenant,),
    ).fetchall()


def chain_is_intact(owner: psycopg.Connection[Any], tenant: uuid.UUID) -> bool:
    entries, first_bad = owner.execute(
        "SELECT entries, first_bad_seq FROM ontology.audit_verify(%s)", (tenant,)
    ).fetchone()
    return first_bad is None and entries > 0


def make_due(owner: psycopg.Connection[Any], outbox_id: uuid.UUID) -> None:
    owner.execute(
        "UPDATE ontology.writeback_outbox SET next_attempt_at = now() - interval '1 second' WHERE id = %s",
        (outbox_id,),
    )


# --------------------------------------------------------------------------- backoff


def test_backoff_doubles_and_is_capped():
    full = {"base_s": 10, "cap_s": 100, "rng": lambda: 1.0}
    assert [backoff_delay(n, **full) for n in (1, 2, 3, 4, 5, 6)] == [10, 20, 40, 80, 100, 100]


def test_backoff_jitter_stays_between_half_and_all():
    low = backoff_delay(3, base_s=10, cap_s=100, rng=lambda: 0.0)
    high = backoff_delay(3, base_s=10, cap_s=100, rng=lambda: 1.0)
    assert (low, high) == (20, 40)


# --------------------------------------------------------------------------- delivery


def test_delivers_and_audits(executor_db, owner, settings, seed, tenant):
    (outbox_id,) = seed(1)
    connector = FakeConnector()

    assert Relay(executor_db, {"cmms": connector}, settings).run_once() == 1

    operation, payload, key = connector.calls[0]
    assert operation == "create_work_order"
    assert payload["external_id"] == "WO-0"
    assert key == outbox_id, "the outbox row id is the idempotency key"
    state = row(owner, outbox_id)
    assert state["status"] == "delivered"
    assert state["attempts"] == 1
    assert state["delivered_at"] is not None
    assert state["last_error"] is None

    ((event, outcome, resource_id, details),) = audit_events(owner, tenant)
    assert (event, outcome, resource_id) == ("writeback.delivered", "success", str(outbox_id))
    assert details["connector"] == "cmms"
    assert details["attempts"] == 1
    assert "payload" not in details and "secret title" not in str(details)
    assert chain_is_intact(owner, tenant)

    actor = owner.execute(
        "SELECT actor_type, actor_id FROM ontology.audit_log WHERE tenant_id = %s", (tenant,)
    ).fetchone()
    assert actor == ("service", ACTOR_ID)


def test_a_delivered_row_is_not_sent_again(executor_db, settings, seed):
    seed(1)
    connector = FakeConnector()
    relay = Relay(executor_db, {"cmms": connector}, settings)
    assert relay.run_once() == 1
    assert relay.run_once() == 0
    assert len(connector.calls) == 1


def test_rows_not_yet_due_are_left_alone(executor_db, owner, settings, seed):
    (outbox_id,) = seed(1, due_in_s=3600)
    connector = FakeConnector()
    assert Relay(executor_db, {"cmms": connector}, settings).run_once() == 0
    assert connector.calls == []
    assert row(owner, outbox_id)["attempts"] == 0


def test_oldest_due_first_and_batch_limit(executor_db, owner, settings, seed):
    first, second, third = seed(3)
    owner.execute(
        "UPDATE ontology.writeback_outbox SET next_attempt_at = now() - interval '3 minutes' WHERE id = %s",
        (third,),
    )
    settings.batch_size = 1
    connector = FakeConnector()
    Relay(executor_db, {"cmms": connector}, settings).run_once()
    assert connector.calls[0][1]["external_id"] == "WO-2", "the longest-waiting row goes first"
    assert row(owner, third)["status"] == "delivered"
    assert row(owner, first)["attempts"] == 0 and row(owner, second)["attempts"] == 0


# --------------------------------------------------------------------------- failure


def test_a_transient_failure_is_retried_later_with_backoff(executor_db, owner, settings, seed, tenant):
    (outbox_id,) = seed(1)
    connector = FakeConnector(TransientError("cmms: HTTP 503"), None)
    relay = Relay(executor_db, {"cmms": connector}, settings, rng=lambda: 1.0)

    relay.run_once()
    state = row(owner, outbox_id)
    assert state["status"] == "pending"
    assert state["attempts"] == 1
    assert state["last_error"] == "cmms: HTTP 503"
    wait = state["next_attempt_at"] - datetime.now(UTC)
    assert timedelta(seconds=5) < wait <= timedelta(seconds=10), "first retry waits the base delay"
    assert audit_events(owner, tenant) == [], "retries are logged, not audited"

    assert relay.run_once() == 0, "not due yet"
    make_due(owner, outbox_id)
    assert relay.run_once() == 1
    state = row(owner, outbox_id)
    assert (state["status"], state["attempts"], state["last_error"]) == ("delivered", 2, None)
    assert [e[0] for e in audit_events(owner, tenant)] == ["writeback.delivered"]


def test_a_permanent_failure_goes_straight_to_dead_letter(executor_db, owner, settings, seed, tenant, caplog):
    (outbox_id,) = seed(1)
    connector = FakeConnector(PermanentError("cmms: HTTP 422 for create_work_order"))

    with caplog.at_level("ERROR", logger="s2.executor.relay"):
        Relay(executor_db, {"cmms": connector}, settings).run_once()

    state = row(owner, outbox_id)
    assert (state["status"], state["attempts"]) == ("dead", 1)
    assert "422" in state["last_error"]
    ((event, outcome, _, details),) = audit_events(owner, tenant)
    assert (event, outcome, details["reason"]) == ("writeback.dead", "error", "permanent error")
    alerts = [r for r in caplog.records if getattr(r, "alert", False)]
    assert len(alerts) == 1 and alerts[0].outbox_id == str(outbox_id)
    assert chain_is_intact(owner, tenant)


def test_retries_run_out(executor_db, owner, settings, seed, tenant):
    (outbox_id,) = seed(1)
    connector = FakeConnector(*(TransientError("down") for _ in range(settings.max_attempts)))
    relay = Relay(executor_db, {"cmms": connector}, settings)

    for attempt in range(1, settings.max_attempts + 1):
        make_due(owner, outbox_id)
        assert relay.run_once() == 1
        assert row(owner, outbox_id)["attempts"] == attempt

    state = row(owner, outbox_id)
    assert state["status"] == "dead"
    ((event, _, _, details),) = audit_events(owner, tenant)
    assert (event, details["reason"]) == ("writeback.dead", "retries exhausted")
    make_due(owner, outbox_id)
    assert relay.run_once() == 0, "dead rows are never claimed"


def test_an_unconfigured_connector_is_dead_not_retried(executor_db, owner, settings, seed):
    (outbox_id,) = seed(1, connector="sap-pm")
    Relay(executor_db, {"cmms": FakeConnector()}, settings).run_once()
    state = row(owner, outbox_id)
    assert state["status"] == "dead"
    assert "sap-pm" in state["last_error"]


def test_a_connector_bug_is_retried_and_does_not_stop_the_batch(executor_db, owner, settings, seed):
    broken, fine = seed(1, connector="cmms")[0], seed(1, connector="samanvaya-fleet")[0]

    class Broken(FakeConnector):
        def deliver(self, *args: Any) -> None:
            raise KeyError("boom")

    Relay(executor_db, {"cmms": Broken(), "samanvaya-fleet": FakeConnector()}, settings).run_once()
    assert row(owner, broken)["status"] == "pending"
    assert "KeyError" in row(owner, broken)["last_error"]
    assert row(owner, fine)["status"] == "delivered"


# --------------------------------------------------------------------------- tenants


def test_every_tenant_is_served_and_audited_in_its_own_chain(executor_db, owner, settings, seed):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    seed(2, tenant_id=tenant_a)
    seed(1, tenant_id=tenant_b)

    assert Relay(executor_db, {"cmms": FakeConnector()}, settings).run_once() == 3

    assert len(audit_events(owner, tenant_a)) == 2
    assert len(audit_events(owner, tenant_b)) == 1
    assert chain_is_intact(owner, tenant_a) and chain_is_intact(owner, tenant_b)


# --------------------------------------------------------------------------- concurrency


def test_a_claimed_row_is_invisible_to_other_workers(connect_executor, settings, seed):
    seed(1)
    worker_a = Relay(connect_executor(), {"cmms": FakeConnector()}, settings)
    connector_b = FakeConnector()
    worker_b = Relay(connect_executor(), {"cmms": connector_b}, settings)

    claims = worker_a._claim()  # worker A has the row and is "mid-delivery"
    assert len(claims) == 1
    assert worker_b.run_once() == 0
    assert connector_b.calls == []


def test_two_workers_deliver_every_row_exactly_once(connect_executor, owner, settings, seed):
    seed(40)
    shared = FakeConnector()
    settings.batch_size = 3
    relays = [Relay(connect_executor(), {"cmms": shared}, settings) for _ in range(4)]

    def drain(relay: Relay) -> None:
        while relay.run_once():
            pass

    threads = [threading.Thread(target=drain, args=(r,)) for r in relays]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(shared.calls) == 40
    assert len({key for _, _, key in shared.calls}) == 40, "no row delivered twice"
    assert (
        owner.execute("SELECT count(*) FROM ontology.writeback_outbox WHERE status = 'delivered'").fetchone()[
            0
        ]
        >= 40
    )


def test_a_crashed_worker_is_covered_by_its_lease(connect_executor, owner, settings, seed):
    (outbox_id,) = seed(1)
    crashed = Relay(connect_executor(), {"cmms": FakeConnector()}, settings)
    (claim,) = crashed._claim()  # claims, then the process dies before finalizing
    assert claim.attempts == 1
    assert row(owner, outbox_id)["status"] == "pending"

    # While the lease runs the row is untouched; after it, another worker picks it up.
    rescuer = Relay(connect_executor(), {"cmms": FakeConnector()}, settings)
    assert rescuer.run_once() == 0
    make_due(owner, outbox_id)
    assert rescuer.run_once() == 1
    state = row(owner, outbox_id)
    assert (state["status"], state["attempts"]) == ("delivered", 2)


def test_a_late_finish_after_lease_expiry_changes_nothing(connect_executor, owner, settings, seed, tenant):
    (outbox_id,) = seed(1)
    slow = Relay(connect_executor(), {"cmms": FakeConnector()}, settings)
    (claim,) = slow._claim()

    make_due(owner, outbox_id)  # the lease runs out while the slow worker is still waiting on the target
    fast = Relay(connect_executor(), {"cmms": FakeConnector()}, settings)
    assert fast.run_once() == 1
    assert row(owner, outbox_id)["status"] == "delivered"

    slow._record_delivered(claim, elapsed_ms=1)
    slow._record_failure(claim, "late failure", permanent=True)

    assert row(owner, outbox_id)["status"] == "delivered"
    assert [e[0] for e in audit_events(owner, tenant)] == ["writeback.delivered"], "audited once"


# --------------------------------------------------------------------------- the role


def test_the_executor_role_passes_its_own_startup_check(executor_db):
    assert_executor_role(executor_db)


def test_a_superuser_is_refused(owner):
    with pytest.raises(RuntimeError, match="superuser"):
        assert_executor_role(owner)


def test_a_role_without_bypassrls_is_refused(database):
    from s2_executor.main import libpq_url

    with (
        psycopg.connect(libpq_url(database["app"])) as app_connection,
        pytest.raises(RuntimeError, match="BYPASSRLS"),
    ):
        assert_executor_role(app_connection)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ontology.writeback_outbox SET payload = '{}'",
        "UPDATE ontology.writeback_outbox SET connector = 'elsewhere'",
        "DELETE FROM ontology.writeback_outbox",
        "INSERT INTO ontology.writeback_outbox (action_execution_id, connector, operation, tenant_id)"
        " VALUES (gen_random_uuid(), 'x', 'y', gen_random_uuid())",
        "SELECT * FROM ontology.object_instance",
        "SELECT parameters FROM ontology.action_execution",
        "UPDATE ontology.audit_log SET outcome = 'success'",
        "DELETE FROM ontology.audit_log",
        "TRUNCATE ontology.audit_log",
    ],
)
def test_the_executor_can_do_only_what_delivery_needs(executor_db, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        executor_db.execute(statement)


def test_the_audit_chain_still_refuses_rewrites_for_the_executor(executor_db, seed, settings, tenant):
    seed(1)
    Relay(executor_db, {"cmms": FakeConnector()}, settings).run_once()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        executor_db.execute("UPDATE ontology.audit_log SET actor_id = 'someone-else'")
