# Security

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting for this repository (Security tab, "Report a vulnerability"). Include what you found, how to reproduce it, and the impact you expect.

## Scope and current state

S2 is in early development (Phase 1). The local Docker Compose stack is for development on a single machine and is deliberately insecure: default passwords, no TLS, anonymous MQTT, a password-grant Keycloak client. Do not expose it to a network.

The platform's security model and its known gaps are documented in [docs/architecture/overview.md](docs/architecture/overview.md#security-model-and-known-gaps).
