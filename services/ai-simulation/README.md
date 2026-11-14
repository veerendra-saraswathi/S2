# AI and simulation (Phase 4, not implemented)

Predictive models bound to object types, what-if simulation, and governed AI agents, per section 2.5 of the [specification](../../docs/architecture/spec.md).

## What is already in place for it

- **Agents are modelled as actors.** `actor_type = 'agent'` exists in action executions and the audit log, and an agent's execution must name the human it acts for (`on_behalf_of`, enforced by a database constraint).
- **Agents can only propose.** An action is open to agents only if `agent_access: propose`, and such an action must require human approval. That is enforced when the action is defined, in the policy baseline (`policies/s2/actions/common.rego`), and in the database.
- **Every decision is audited**, including denials.

What is missing is the wiring: nothing issues tokens that identify an agent yet, so the engine currently sees only users and services.

## Intended shape

- Model registry and feature store (MLflow, Feast) with models bound to object types and their time-series properties.
- A simulator that runs scenarios against a branch of the ontology. How the ontology is branched (schema per branch, or copy-on-write overlay) is an open design question.
- An agent runtime whose tools are the ontology engine's read API and `POST /v1/actions/{name}/apply`, authenticated as an agent on behalf of a user, with no other way to change anything.
