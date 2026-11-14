# Pipeline orchestrator (Phase 2, not implemented)

The lakehouse side of S2: versioned datasets, transformations, lineage and dataset branching, per sections 2.1 and 2.2 of the [specification](../../docs/architecture/spec.md).

## Intended shape

- Apache Iceberg tables on the S3-compatible object store already in the local stack.
- Dagster for orchestration and asset lineage; dbt for SQL transformations; DuckDB locally and Spark at scale.
- Batch and CDC sources (ERP, CMMS, SCADA historians) land as datasets. Object types name the dataset that backs them (`backing_dataset`) and properties name their column (`source_column`); a sync job materialises datasets into ontology objects through the ontology engine's API.

## Decisions to make first

- **The Iceberg catalog.** It decides how "data branching" works: a catalog with native branches (Nessie) versus Iceberg's own table-level branches behind a REST catalog (Polaris, Lakekeeper). Record the choice as an ADR.
- **Column-level lineage**: what Dagster and dbt provide out of the box against what the specification requires, and whether OpenLineage is needed.
- **Object-store coverage**: confirm the store handles what Iceberg commits need (see [ADR 0002](../../docs/architecture/adr/0002-object-store.md)).
