"""Iceberg datasets: read at a branch, write, branch, promote.

A dataset is named `namespace.table` (`assets.commercial_drone`), as in an object
type's `backing_dataset`. Every write is a new snapshot, so a sync can always say
exactly which version of a dataset it read, and reading the same snapshot twice gives
the same rows.

Branches are Iceberg's own, per table: `preview` starts at `main`'s current snapshot,
takes writes of its own, and can be promoted, which moves `main` to it. Promotion is a
fast-forward only: if `main` moved on after the branch was cut, it is refused, because
moving `main` would silently drop those writes.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.table import Table
from pyiceberg.table.snapshots import Snapshot

from s2_pipelines.settings import PipelineSettings

MAIN = "main"


class LakehouseError(Exception):
    pass


@dataclass(frozen=True)
class DatasetVersion:
    dataset: str
    branch: str
    snapshot_id: int
    rows: list[dict[str, Any]]
    columns: list[str]


def _json_value(value: Any) -> Any:
    """Arrow values as the ontology API takes them."""
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


class Lakehouse:
    def __init__(self, catalog: Catalog, *, warehouse: str, s3: dict[str, str] | None = None) -> None:
        self.catalog = catalog
        self._warehouse = warehouse
        self._s3 = s3 or {}

    @classmethod
    def from_settings(cls, settings: PipelineSettings) -> Lakehouse:
        properties: dict[str, str] = {"uri": settings.catalog_uri, "warehouse": settings.warehouse}
        s3: dict[str, str] = {}
        if settings.warehouse.startswith("s3://"):
            s3 = {
                "s3.region": settings.s3_region,
                **({"s3.endpoint": settings.s3_endpoint} if settings.s3_endpoint else {}),
                **({"s3.access-key-id": settings.s3_access_key} if settings.s3_access_key else {}),
                **({"s3.secret-access-key": settings.s3_secret_key} if settings.s3_secret_key else {}),
            }
            properties |= s3
        return cls(SqlCatalog("s2", **properties), warehouse=settings.warehouse, s3=s3)

    # ------------------------------------------------------------------ setup

    def ensure_warehouse(self) -> None:
        """Create the bucket if the warehouse is on S3. Idempotent."""
        if not self._warehouse.startswith("s3://"):
            return
        from pyarrow import fs

        bucket = urlparse(self._warehouse).netloc
        s3 = fs.S3FileSystem(
            access_key=self._s3.get("s3.access-key-id"),
            secret_key=self._s3.get("s3.secret-access-key"),
            endpoint_override=self._s3.get("s3.endpoint"),
            region=self._s3.get("s3.region", "us-east-1"),
            allow_bucket_creation=True,
        )
        try:
            s3.create_dir(bucket, recursive=False)
        except OSError as exc:
            # Already there. SeaweedFS also creates a bucket on first write.
            if "exist" not in str(exc).lower() and "owned" not in str(exc).lower():
                raise

    # ------------------------------------------------------------------ reading

    def table(self, dataset: str) -> Table:
        try:
            return self.catalog.load_table(dataset)
        except NoSuchTableError as exc:
            raise LakehouseError(f"dataset {dataset!r} does not exist") from exc

    def head(self, table: Table, branch: str = MAIN) -> Snapshot:
        snapshot = table.snapshot_by_name(branch)
        if snapshot is None:
            what = "has no data yet" if branch == MAIN else f"has no branch {branch!r}"
            raise LakehouseError(f"dataset {'.'.join(table.name()[-2:])} {what}")
        return snapshot

    def read(self, dataset: str, branch: str = MAIN, *, snapshot_id: int | None = None) -> DatasetVersion:
        table = self.table(dataset)
        snapshot_id = snapshot_id if snapshot_id is not None else self.head(table, branch).snapshot_id
        arrow = table.scan(snapshot_id=snapshot_id).to_arrow()
        rows = [{key: _json_value(value) for key, value in row.items()} for row in arrow.to_pylist()]
        return DatasetVersion(dataset, branch, snapshot_id, rows, arrow.schema.names)

    def branches(self, dataset: str) -> dict[str, int]:
        table = self.table(dataset)
        return {
            name: ref.snapshot_id for name, ref in table.refs().items() if ref.snapshot_ref_type == "branch"
        }

    # ------------------------------------------------------------------ writing

    def write(
        self, dataset: str, rows: pa.Table, *, branch: str = MAIN, properties: dict[str, str] | None = None
    ) -> int:
        """Replace the dataset's contents on `branch` with `rows`. Returns the new snapshot id."""
        namespace = dataset.rsplit(".", 1)[0]
        self.catalog.create_namespace_if_not_exists(namespace)
        table = self.catalog.create_table_if_not_exists(dataset, schema=rows.schema)
        if branch != MAIN and branch not in table.refs():
            raise LakehouseError(f"dataset {dataset} has no branch {branch!r}; create it first")
        if table.snapshot_by_name(branch) is None:
            table.append(rows, branch=branch, snapshot_properties=properties or {})  # nothing to replace
        else:
            table.overwrite(rows, branch=branch, snapshot_properties=properties or {})
        return self.head(self.table(dataset), branch).snapshot_id

    def create_branch(self, dataset: str, name: str) -> int:
        """A branch starting at main's current snapshot."""
        if name == MAIN:
            raise LakehouseError("'main' is not a branch you can create")
        table = self.table(dataset)
        if name in table.refs():
            raise LakehouseError(f"dataset {dataset} already has a branch {name!r}")
        head = self.head(table).snapshot_id
        table.manage_snapshots().create_branch(head, name).commit()
        return head

    def drop_branch(self, dataset: str, name: str) -> None:
        if name == MAIN:
            raise LakehouseError("'main' cannot be dropped")
        table = self.table(dataset)
        if name not in table.refs():
            raise LakehouseError(f"dataset {dataset} has no branch {name!r}")
        table.manage_snapshots().remove_branch(name).commit()

    def promote(self, dataset: str, branch: str) -> int:
        """Fast-forward main to `branch`. Returns main's new snapshot id."""
        table = self.table(dataset)
        target = self.head(table, branch)
        main = self.head(table).snapshot_id
        if target.snapshot_id == main:
            return main
        if not self._descends_from(table, target, main):
            raise LakehouseError(
                f"main has moved since {branch!r} was cut; promoting would drop main's newer writes. "
                f"Recreate the branch from main and apply the change again"
            )
        table.manage_snapshots().set_current_snapshot(snapshot_id=target.snapshot_id).commit()
        return target.snapshot_id

    @staticmethod
    def _descends_from(table: Table, snapshot: Snapshot, ancestor_id: int) -> bool:
        current: Snapshot | None = snapshot
        while current is not None:
            if current.snapshot_id == ancestor_id:
                return True
            parent = current.parent_snapshot_id
            current = table.snapshot_by_id(parent) if parent is not None else None
        return False
