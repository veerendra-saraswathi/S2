"""Entry point.

python -m s2_agents once      one pass over the triage queues, printed as JSON
python -m s2_agents loop      a pass every S2_INTERVAL_S (the Compose service)
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading

from s2_agents.agent import Agent
from s2_agents.engine import Engine, EngineError
from s2_agents.llm import Ollama
from s2_agents.settings import get_agent_settings
from s2_common.logging import configure_logging

log = logging.getLogger("s2.agent")


def build() -> Agent:
    settings = get_agent_settings()
    return Agent(Engine.from_settings(settings), Ollama(settings), settings)


def once() -> int:
    run = build().run_once()
    print(json.dumps({"summary": run.summary(), "items": [o.as_dict() for o in run.outcomes]}, indent=2))
    return 0


def loop() -> int:
    settings = get_agent_settings()
    agent = build()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log.info("starting", extra={"model": settings.ollama_model, "interval_s": settings.interval_s})
    while not stop.is_set():
        try:
            run = agent.run_once()
            log.info("run finished", extra=run.summary())
        except EngineError as exc:
            log.error("engine unavailable", extra={"error": str(exc)})
        stop.wait(settings.interval_s)
    return 0


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_agent_settings().log_level)
    parser = argparse.ArgumentParser(prog="s2-agents")
    parser.add_argument("command", choices=["once", "loop"])
    args = parser.parse_args(argv)
    try:
        sys.exit(once() if args.command == "once" else loop())
    except EngineError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
