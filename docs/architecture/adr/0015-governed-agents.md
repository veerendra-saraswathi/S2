# 0015. Governed agents: a small loop on a local model, with governance in the engine

Status: accepted (November 2026). Phase 4, slice 1.

## Context

The specification asks for AI agents that help operators without becoming a way around the controls people are held to. Phase 1 already modelled agents as actors (`actor_type = 'agent'`, a required `on_behalf_of`) and made agent-open actions propose-only. What was missing: an identity for an agent, a reason recorded with each proposal, a rule that the person an agent acts for cannot also approve its proposal, and an agent.

The model must run locally (Ollama: `llama3.1` by default; `gpt-oss:20b` and `mistral` also available). An existing CrewAI prototype may replace the loop later.

## Decision

**Governance lives in the engine, not the agent.** Everything that makes an agent safe is enforced where every other actor is checked, so the agent implementation can be replaced (by CrewAI or anything else) without weakening it:

- The agent authenticates as Keycloak client `s2-agent` (client credentials). Its token carries `s2_actor_type=agent` and `s2_on_behalf_of=<person>`, and ordinary roles and markings. It sees only what those allow.
- It can apply only actions marked `agent_access: propose`, which must require approval. Nothing it does changes state until a person approves.
- **An agent must give a rationale** (migration 0005, `action_execution.rationale`, at most 4,000 characters). It is stored with the execution and in the audit entry. People may give one too.
- **Four eyes reaches through the agent**: the approver may not be the agent, nor the person it acts for (`common.rego`, matched by subject or username, because a Keycloak hardcoded claim holds a constant).

**The agent is a small purpose-built loop** (`services/agent-runtime`), not a framework. Each run it reads the triage queues it can see. For each item with an action it may propose, it gives the model a brief: the rule, the object, the evidence and the actions. The model may then call five tools: `get_object`, `get_series_trend`, `recent_actions`, `propose` and `dismiss`. The loop, not the model, decides:

- which object a proposal is for: the key is filled in from the queue item, and the model cannot change it;
- which actions are allowed: they are listed as an enum, and others are refused;
- whether an object is busy: it is skipped if a proposal is awaiting a decision, or if one by an agent was rejected within the cooldown (6 hours by default);
- how many proposals a run may make (3) and how many steps an item may take (8).

Failures (Ollama down, an engine refusal, a model that will not decide) are reported per item, not raised.

**pack core-fleet 0.4.0** adds `proposeGrounding`, the agent-open counterpart of `groundDrone`, offered by the *Degrading actuators* rule.

## Consequences

- The model's quality bounds the usefulness of proposals, not their safety. A bad proposal costs a reviewer's time; a rejection keeps it quiet for the cooldown.
- One agent identity acts for one configured person. Agents acting for whoever asked (token exchange) are later work.
- Tests use a scripted model against the real engine, OPA and telemetry. Behaviour with real models is checked by running `make agent-once` against Ollama, not in CI.
- The rationale is model-written text shown to approvers. It is rendered as text, never as HTML. It is evidence to weigh, not a fact.
- No memory between runs beyond what the engine records: the execution history is the agent's memory, which keeps it auditable.
