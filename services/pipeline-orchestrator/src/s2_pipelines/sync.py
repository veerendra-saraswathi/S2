"""Materialise a dataset into ontology objects.

An object type names the dataset that backs it (`backing_dataset`) and each property it
takes from that dataset names its column (`source_column`). The sync reads one snapshot
of the dataset and makes the objects agree with it, for those properties only:

* a row whose key has no object creates one, if the dataset supplies every required
  property; otherwise the row is rejected with the reason;
* a row whose mapped values differ from the object's patches just those values;
* a row that matches is left alone, so a repeated sync writes nothing;
* an object with no row is reported as `absent` and left alone. Deleting objects
  because a row disappeared is a decision for a later slice (ADR 0011).

Properties the dataset does not map (status, position, anything an action owns) are
never touched. Every write goes through the engine's API with provenance naming the
dataset, branch and snapshot, so each audit entry says where its values came from.

`plan` works out what would change without writing anything. That is a dry run, and,
pointed at a branch, a preview of what promoting the branch would do to the ontology.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from s2_pipelines.engine import EngineClient, EngineError
from s2_pipelines.lakehouse import MAIN, DatasetVersion, Lakehouse


class SyncError(Exception):
    """The sync cannot run at all (as opposed to a row it rejects)."""


@dataclass(frozen=True)
class Mapping:
    object_type: str
    dataset: str
    key_property: str
    key_column: str
    columns: dict[str, str]  # property -> column, the key excluded
    required: frozenset[str]  # required properties, the key excluded

    @classmethod
    def from_definition(cls, definition: dict[str, Any]) -> Mapping:
        name = definition["api_name"]
        dataset = definition.get("backing_dataset")
        if not dataset:
            raise SyncError(f"{name} has no backing_dataset")
        active = [p for p in definition["properties"] if p["status"] != "deprecated"]
        key = next(p for p in active if p["is_primary_key"])
        if not key.get("source_column"):
            raise SyncError(f"{name}.{key['api_name']}: the primary key has no source_column")
        columns = {
            p["api_name"]: p["source_column"]
            for p in active
            if p.get("source_column") and not p["is_primary_key"] and p["data_type"] != "timeseries"
        }
        if not columns:
            raise SyncError(f"{name}: no property has a source_column")
        required = frozenset(p["api_name"] for p in active if p["is_required"] and not p["is_primary_key"])
        return cls(name, dataset, key["api_name"], key["source_column"], columns, required)

    def lineage(self) -> dict[str, str]:
        """Column-level lineage: dataset column -> object property."""
        return {self.key_column: self.key_property, **{c: p for p, c in self.columns.items()}}


@dataclass
class Plan:
    mapping: Mapping
    version: DatasetVersion
    creates: dict[str, dict[str, Any]] = field(default_factory=dict)
    updates: dict[str, dict[str, Any]] = field(default_factory=dict)  # key -> only the changed values
    unchanged: int = 0
    rejected: dict[str, str] = field(default_factory=dict)  # key (or row label) -> reason
    absent: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "object_type": self.mapping.object_type,
            "dataset": self.version.dataset,
            "branch": self.version.branch,
            "snapshot_id": str(self.version.snapshot_id),
            "rows": len(self.version.rows),
            "create": len(self.creates),
            "update": len(self.updates),
            "unchanged": self.unchanged,
            "rejected": len(self.rejected),
            "absent": len(self.absent),
        }


@dataclass
class Result:
    plan: Plan
    run_id: str
    created: int = 0
    updated: int = 0
    rejected: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            **self.plan.summary(),
            "run_id": self.run_id,
            "created": self.created,
            "updated": self.updated,
            "rejected": len(self.rejected),
        }


def plan(mapping: Mapping, version: DatasetVersion, existing: dict[str, dict[str, Any]]) -> Plan:
    """What it would take to make `existing` (key -> properties) agree with `version`."""
    missing = sorted({mapping.key_column, *mapping.columns.values()} - set(version.columns))
    if missing:
        raise SyncError(f"{version.dataset} has no column(s) {', '.join(missing)}")

    result = Plan(mapping, version)
    seen: set[str] = set()
    for index, row in enumerate(version.rows):
        raw_key = row[mapping.key_column]
        if raw_key is None or str(raw_key) == "":
            result.rejected[f"row {index}"] = f"{mapping.key_column} is empty"
            continue
        key = str(raw_key)
        if key in seen:
            result.rejected[key] = f"{mapping.key_column} {key!r} appears more than once"
            result.creates.pop(key, None)
            result.updates.pop(key, None)
            continue
        seen.add(key)
        wanted = {prop: row[column] for prop, column in mapping.columns.items()}

        current = existing.get(key)
        if current is None:
            supplied = {prop for prop, value in wanted.items() if value is not None}
            lacking = sorted(mapping.required - supplied)
            if lacking:
                result.rejected[key] = (
                    f"cannot create {mapping.object_type} {key!r}: the dataset does not supply "
                    f"required {', '.join(lacking)}"
                )
                continue
            result.creates[key] = {prop: value for prop, value in wanted.items() if value is not None}
            continue

        changes = {prop: value for prop, value in wanted.items() if current.get(prop) != value}
        if changes:
            result.updates[key] = changes
        else:
            result.unchanged += 1

    result.absent = sorted(set(existing) - seen)
    return result


class Syncer:
    def __init__(self, lakehouse: Lakehouse, engine: EngineClient) -> None:
        self._lakehouse = lakehouse
        self._engine = engine

    def mapping(self, object_type: str, *, expect_dataset: str | None = None) -> Mapping:
        mapping = Mapping.from_definition(self._engine.object_type(object_type))
        if expect_dataset and mapping.dataset != expect_dataset:
            raise SyncError(
                f"{object_type} is backed by {mapping.dataset}, but the pipeline is configured "
                f"with {expect_dataset}; fix S2_SYNC_TARGETS or the pack"
            )
        return mapping

    def plan(self, object_type: str, *, branch: str = MAIN, expect_dataset: str | None = None) -> Plan:
        mapping = self.mapping(object_type, expect_dataset=expect_dataset)
        version = self._lakehouse.read(mapping.dataset, branch)
        existing = {o["primary_key"]: o["properties"] for o in self._engine.objects(object_type)}
        return plan(mapping, version, existing)

    def apply(self, the_plan: Plan, *, run_id: str | None = None) -> Result:
        mapping, version = the_plan.mapping, the_plan.version
        result = Result(the_plan, run_id or str(uuid.uuid4()))
        result.rejected.update(the_plan.rejected)
        provenance = {
            "dataset": version.dataset,
            "snapshot_id": str(version.snapshot_id),
            "branch": version.branch,
            "run_id": result.run_id,
        }
        for key, properties in the_plan.creates.items():
            try:
                self._engine.create(mapping.object_type, key, properties, provenance)
                result.created += 1
            except EngineError as exc:
                result.rejected[key] = exc.message
        for key, changes in the_plan.updates.items():
            try:
                self._engine.patch(mapping.object_type, key, changes, provenance)
                result.updated += 1
            except EngineError as exc:
                result.rejected[key] = exc.message
        return result

    def sync(
        self,
        object_type: str,
        *,
        branch: str = MAIN,
        expect_dataset: str | None = None,
        run_id: str | None = None,
    ) -> Result:
        return self.apply(self.plan(object_type, branch=branch, expect_dataset=expect_dataset), run_id=run_id)
