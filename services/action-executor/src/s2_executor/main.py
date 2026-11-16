"""Entry point:  python -m s2_executor"""

from __future__ import annotations

import logging
import signal
import threading
import time

import psycopg

from s2_common.logging import configure_logging
from s2_executor.connectors import build_connectors
from s2_executor.relay import Relay, assert_executor_role
from s2_executor.settings import get_executor_settings

log = logging.getLogger("s2.executor")

RECONNECT_DELAY_S = 5.0


def libpq_url(sqlalchemy_url: str) -> str:
    """Settings carry SQLAlchemy-style URLs; psycopg wants a plain libpq one."""
    scheme, _, rest = sqlalchemy_url.partition("://")
    return f"{scheme.split('+', 1)[0]}://{rest}"


def main() -> None:
    settings = get_executor_settings()
    configure_logging(settings.log_level)
    connectors = build_connectors(settings.connectors)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info("starting", extra={"connectors": sorted(connectors), "batch_size": settings.batch_size})
    while not stop.is_set():
        try:
            with psycopg.connect(libpq_url(settings.executor_database_url), autocommit=True) as connection:
                assert_executor_role(connection)
                Relay(connection, connectors, settings).run_forever(stop)
        except psycopg.OperationalError as exc:
            # Database down or restarting. Claims are leased, so nothing is lost.
            log.error("database unavailable, reconnecting", extra={"error": str(exc)})
            time.sleep(RECONNECT_DELAY_S)
    for connector in connectors.values():
        connector.close()
    log.info("stopped")


if __name__ == "__main__":
    main()
