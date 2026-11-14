# 0003. Mosquitto for development; production broker undecided

Status: accepted for development (November 2026). The production choice is open.

## Context

The specification names EMQX as the MQTT broker. From version 5.9.0, EMQX is published under the Business Source License 1.1 rather than Apache-2.0. BSL is source-available, not open source; the vendor's licensing FAQ describes restrictions on offering EMQX as a service and on shipping it inside products sold to others, and clustering requires a licence key. S2 is meant to be open source and deployable by customers without vendor lock-in, so whether EMQX can be part of what S2 distributes is a legal question that has not been answered.

In the target architecture the MQTT broker belongs to the fleet side (Samanvaya 0.5 / 1). S2 is a client of it. Locally, S2 only needs *a* broker to develop against.

## Decision

- The local stack runs Eclipse Mosquitto.
- S2 speaks standard MQTT 3.1.1 / 5 and uses no broker-specific feature, so the broker is replaceable.
- The production broker is not chosen here. Candidates: a licensed EMQX, VerneMQ (Apache-2.0, clustered), or whatever the fleet platform already operates.

## Consequences

- Before S2 ships a broker to a customer, counsel reads the EMQX licence against S2's distribution model, or a permissively licensed broker is selected and load-tested.
- The bridge currently uses one persistent session, which fits a single broker. Running several bridge replicas needs MQTT 5 shared subscriptions; that is supported by the candidates above but not yet used.

## References

- EMQX licensing FAQ: <https://www.emqx.com/en/content/license-faq>
- EMQX repository licence notice: <https://github.com/emqx/emqx>
