# 0002. SeaweedFS instead of MinIO

Status: accepted (November 2026)

## Context

The specification names MinIO as the S3-compatible store under the lakehouse and inspection media. Since it was written, MinIO's community edition has stopped being a viable dependency for a new platform: official container images were withdrawn in October 2025, the project went into maintenance mode that December, and the upstream repository was archived in 2026. The vendor's current offering is a commercial product.

S2 needs an S3 API it can ship to customers on-premise without a licence obligation on them.

## Decision

Use [SeaweedFS](https://github.com/seaweedfs/seaweedfs) (Apache-2.0) as the object store in the reference stack.

S2 services depend only on the S3 API. No code may use a store-specific API, so that a deployment can substitute Ceph RGW, a cloud provider's S3, or another compatible store.

## Consequences

- The local stack runs SeaweedFS in single-process mode with a static S3 identity file.
- Nothing in Phase 1 reads or writes the object store. Its first consumer is the Phase 2 pipeline orchestrator, which is also when S3 feature coverage (multipart, conditional writes for Iceberg commits) needs to be tested against it.
- Alternatives considered: RustFS (Apache-2.0, closest to MinIO in operation, but young), Garage (AGPL-3.0, narrower S3 coverage), Ceph RGW (mature, heavy for a laptop).

## References

- MinIO repository and its status notice: <https://github.com/minio/minio>
- Chainguard's note on the withdrawal of the community images: <https://www.chainguard.dev/docs/chainguard/containers/getting-started/web-and-data-services/minio>
