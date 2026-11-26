# Security

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting for this repository (Security tab, "Report a vulnerability"). Include what you found, how to reproduce it, and the impact you expect.

## Scope and current state

S2 is in early development (Phase 1). The local Docker Compose stack is for development on a single machine and is deliberately insecure: default passwords, plaintext Kafka and databases, a password-grant Keycloak client. MQTT is the exception: every client logs in and is held to a topic ACL, gateways connect over TLS on 8883, and plain 1883 is bound to localhost (see `deploy/docker-compose/mosquitto`). Its certificate authority and passwords are generated per machine and are for development only. Do not expose it to a network.

The platform's security model and its known gaps are documented in [docs/architecture/overview.md](docs/architecture/overview.md#security-model-and-known-gaps).
