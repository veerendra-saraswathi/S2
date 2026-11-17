"""Read-side access control from security markings.

A marking is a label on an object type or a property, such as `internal` or
`region:IN-TG`. A principal holds a marking through a realm role named `marking:<name>`.
To see something, a principal must hold every marking on it: markings narrow, never widen.

* An object type the caller is not cleared for does not exist as far as they can tell:
  its objects, links and series answer 404, with the same message as an unknown type.
* A property the caller is not cleared for is left out of every object they read, its
  series answers 404, and its values are taken out of the audit entries they read.
* A property's effective markings are its own plus its type's.

Ontology definitions (`/v1/ontology/...`) stay readable by everyone in the tenant: they
describe the schema, not the data. Actions are not filtered: an action's effects are
defined by an ontology admin and run with the platform's authority, as the policy allows.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from s2_common.auth import Principal
from s2_ontology.db.models import ObjectType, PropertyType
from s2_ontology.domain.errors import NotFound

ROLE_PREFIX = "marking:"

# Change records written by domain/objects.py: {"op", "object": "Type/key", "before", "after"}.
_OBJECT_OPS = frozenset({"create_object", "modify_object", "replace_object", "delete_object"})
# Link records name both ends: {"op", "object": "Type/key", "link", "target": "Type/key"}.
_LINK_OPS = frozenset({"create_link", "delete_link"})


def clearances(principal: Principal) -> frozenset[str]:
    return frozenset(role[len(ROLE_PREFIX) :] for role in principal.roles if role.startswith(ROLE_PREFIX))


def cleared(principal: Principal, markings: Iterable[str]) -> bool:
    return set(markings) <= clearances(principal)


def require_type(principal: Principal, object_type: ObjectType) -> ObjectType:
    """The type, or the same NotFound an unknown type gives."""
    if not cleared(principal, object_type.markings):
        raise NotFound(f"object type '{object_type.api_name}' does not exist")
    return object_type


def hidden_properties(principal: Principal, object_type: ObjectType) -> frozenset[str]:
    """Names of the properties of `object_type` the principal may not see.

    A type the principal may not see hides all of them; callers normally stop earlier
    with `require_type`.
    """
    return frozenset(
        p.api_name for p in object_type.properties if not visible_property(principal, object_type, p)
    )


def visible_property(principal: Principal, object_type: ObjectType, prop: PropertyType) -> bool:
    return cleared(principal, [*object_type.markings, *prop.markings])


def redact(properties: dict[str, Any], hidden: frozenset[str]) -> dict[str, Any]:
    if not hidden:
        return properties
    return {name: value for name, value in properties.items() if name not in hidden}


# --------------------------------------------------------------------------- #
# Audit details
# --------------------------------------------------------------------------- #

TypeLookup = Callable[[str], ObjectType | None]


class AuditRedactor:
    """Takes out of audit details what a principal may not see.

    Works on the change records the engine writes, wherever they sit in `details`
    (an object write stores one at the top; an action stores a list under `changes`).
    Values of hidden properties are dropped from `before` and `after`. A record about an
    object type the principal may not see keeps only its `op`. Anything else in
    `details` is left alone.
    """

    def __init__(self, principal: Principal, lookup: TypeLookup) -> None:
        self._principal = principal
        self._lookup = lookup
        self._types: dict[str, ObjectType | None] = {}

    def _type(self, label: Any) -> ObjectType | None:
        if not isinstance(label, str) or "/" not in label:
            return None
        name = label.split("/", 1)[0]
        if name not in self._types:
            self._types[name] = self._lookup(name)
        return self._types[name]

    def _type_hidden(self, label: Any) -> bool:
        object_type = self._type(label)
        return object_type is not None and not cleared(self._principal, object_type.markings)

    def resource_hidden(self, resource_type: str | None) -> bool:
        return resource_type is not None and self._type_hidden(f"{resource_type}/")

    def redact(self, details: Any) -> tuple[Any, bool]:
        """(details as the principal may see them, whether anything was taken out)."""
        if isinstance(details, list):
            changed = False
            out = []
            for item in details:
                value, item_changed = self.redact(item)
                out.append(value)
                changed |= item_changed
            return out, changed
        if not isinstance(details, dict):
            return details, False

        op = details.get("op")
        if op in _OBJECT_OPS:
            return self._object_record(details)
        if op in _LINK_OPS:
            if self._type_hidden(details.get("object")) or self._type_hidden(details.get("target")):
                return {"op": op, "redacted": True}, True
            return details, False

        changed = False
        out = {}
        for key, value in details.items():
            out[key], item_changed = self.redact(value)
            changed |= item_changed
        return out, changed

    def _object_record(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        object_type = self._type(record.get("object"))
        if object_type is None:
            return record, False
        if not cleared(self._principal, object_type.markings):
            return {"op": record["op"], "redacted": True}, True
        hidden = hidden_properties(self._principal, object_type)
        if not hidden:
            return record, False
        out = dict(record)
        changed = False
        for side in ("before", "after"):
            values = record.get(side)
            if isinstance(values, dict) and hidden & values.keys():
                out[side] = redact(values, hidden)
                changed = True
        if changed:
            out["redacted"] = True
        return out, changed
