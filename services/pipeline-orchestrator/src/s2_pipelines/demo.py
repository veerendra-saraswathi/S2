"""The asset register behind the demo drones: `assets.commercial_drone`.

Keys and call signs match `s2ctl seed-demo`, so the first sync patches the seeded
drones (model, serial number, firmware) instead of creating new ones.
"""

from __future__ import annotations

import pyarrow as pa

DATASET = "assets.commercial_drone"

SCHEMA = pa.schema(
    [
        pa.field("drone_id", pa.string(), nullable=False),
        pa.field("call_sign", pa.string()),
        pa.field("model", pa.string()),
        pa.field("serial_number", pa.string()),
        pa.field("firmware_version", pa.string()),
    ]
)

REGISTER = [
    ("DRN-0001", "Garuda-1", "X8-Inspector", "GX8-24-0001", "4.1.0"),
    ("DRN-0002", "Garuda-2", "X8-Inspector", "GX8-24-0002", "4.1.0"),
    ("DRN-0003", "Garuda-3", "X8-Inspector", "GX8-24-0003", "4.1.0"),
]


def register(rows: list[tuple[str, ...]] = REGISTER) -> pa.Table:
    return pa.Table.from_pylist([dict(zip(SCHEMA.names, row, strict=True)) for row in rows], schema=SCHEMA)


def firmware_rollout(version: str = "4.2.0", drones: tuple[str, ...] = ("DRN-0003",)) -> pa.Table:
    """The register with a firmware upgrade on some drones: the change to try on a branch."""
    return register([(*row[:4], version if row[0] in drones else row[4]) for row in REGISTER])
