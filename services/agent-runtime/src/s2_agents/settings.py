"""Agent configuration. Shares the S2_ prefix and the common settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field

from s2_common.settings import Settings


class AgentSettings(Settings):
    engine_api_url: str = "http://localhost:8000"
    # The agent's own identity: a service account whose tokens say s2_actor_type=agent
    # and name the person it acts for (Keycloak client s2-agent).
    agent_client_id: str = "s2-agent"
    agent_client_secret: str = "s2-agent-dev-secret"

    # A local model through Ollama. llama3.1 calls tools well enough at 8B; gpt-oss:20b
    # reasons better and needs far more memory.
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ollama_timeout_s: Annotated[float, Field(gt=0)] = 180.0
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.1

    interval_s: Annotated[float, Field(gt=0)] = 300.0
    max_proposals_per_run: Annotated[int, Field(ge=0)] = 3
    max_steps: Annotated[int, Field(ge=2, le=20)] = 8  # model turns per item
    # After a person rejects one of its proposals for an object, leave that object alone
    # for this long, rather than proposing the same thing again.
    cooldown_after_rejection_s: Annotated[float, Field(ge=0)] = 6 * 3600.0


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
