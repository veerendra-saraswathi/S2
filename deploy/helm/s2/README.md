# S2 Helm chart

Deploys the ontology engine and its migration job. It does not install PostgreSQL, Keycloak or OPA: point it at existing ones in `values.yaml`.

**This chart has not been linted, templated or installed.** Run `helm lint deploy/helm/s2` and `helm template` before relying on it.

```bash
kubectl create secret generic s2-database \
  --from-literal=app-database-url='postgresql+psycopg://s2_app:...@postgres:5432/s2' \
  --from-literal=owner-database-url='postgresql+psycopg://s2_owner:...@postgres:5432/s2'

helm upgrade --install s2 deploy/helm/s2 \
  --set image.repository=<registry>/s2-ontology-engine --set image.tag=<tag> \
  --set oidc.issuer=https://id.example.com/realms/s2 \
  --set oidc.jwksUrl=https://id.example.com/realms/s2/protocol/openid-connect/certs \
  --set opa.url=http://opa.policy.svc.cluster.local:8181
```

The database must be PostgreSQL 18 with PostGIS available. The migration job runs as a pre-install and pre-upgrade hook with the owner URL; the API only ever receives the `s2_app` URL.
