# AI and simulation (Phase 4)

Predictive models bound to object types, what-if simulation, and governed AI agents, per section 2.5 of the [specification](../../docs/architecture/spec.md).

## Built

- **Governed agents**: see [services/agent-runtime](../agent-runtime/README.md) and [ADR 0015](../../docs/architecture/adr/0015-governed-agents.md). Agents are actors with their own identity. They act on behalf of a named person, may only propose actions that need approval, must give a rationale, and cannot be approved by the person they act for.

## Not yet built

- A model registry and feature store (MLflow, Feast), with models bound to object types and their time-series properties.
- A simulator that runs scenarios against a branch of the ontology. How the ontology is branched (a schema per branch, or a copy-on-write overlay) is an open design question.
- Agents acting for whoever asked (token exchange), rather than one configured person.
