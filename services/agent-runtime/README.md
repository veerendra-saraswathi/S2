# Agent runtime (Phase 4)

A governed agent that works the triage queues and **proposes** actions for people to approve. It runs on a local model through [Ollama](https://ollama.com), and its only way to change anything is the engine's action API. Design: [ADR 0015](../../docs/architecture/adr/0015-governed-agents.md).

## What it does on each run

1. It signs in as Keycloak client `s2-agent`, which acts on behalf of `supervisor`.
2. It reads the triage queues it may see. It keeps the items whose rule offers an action with `agent_access: propose`.
3. It skips an object if a proposal for it awaits a decision, or if one by an agent was rejected within the cooldown.
4. For each remaining item it gives the model a brief: the rule, the object, the evidence and the actions. The model then reads more through tools (`get_object`, `get_series_trend`, `recent_actions`). It ends with `propose` (action, parameters, rationale) or `dismiss` (reason).
5. A proposal lands in the Workshop's **Approvals** page with an *agent* badge, "on behalf of supervisor", and the rationale. Someone other than the supervisor must approve it.

## Running it (local stack)

```bash
ollama pull llama3.1      # on the host; Ollama listens on :11434
make dev-agent-client     # once: creates the s2-agent client in a running Keycloak
make agent-once           # one pass; prints what it decided for each item
make agent-up             # keep running, every 5 minutes
```

Settings (environment, `S2_` prefix):

| Setting | Default |
|---|---|
| `S2_OLLAMA_URL` | `http://host.docker.internal:11434` in Compose |
| `S2_OLLAMA_MODEL` | `llama3.1` (also tried: `gpt-oss:20b`, `mistral`) |
| `S2_TEMPERATURE` | `0.1` |
| `S2_INTERVAL_S` | `300` |
| `S2_MAX_PROPOSALS_PER_RUN` | `3` |
| `S2_MAX_STEPS` | `8` |
| `S2_COOLDOWN_AFTER_REJECTION_S` | `21600` (6 h) |

## Limits that do not depend on the model

- The key of the object comes from the queue item. The model cannot propose for another object.
- Only the actions the rule offers, and that are open to agents, are allowed.
- The engine refuses a proposal without a rationale, refuses actions that are not propose-only, and refuses approval by the agent or the person it acts for.

## Tests

`uv run pytest services/agent-runtime` runs the agent against the real engine, OPA and a telemetry table, with a scripted model (set `S2_TEST_ADMIN_URL`, and have `opa` on PATH).
