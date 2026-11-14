"""`{{ params.<name> }}` substitution for action edits and write-back payloads.

Deliberately not an expression language: there are no operators, functions or
conditionals. Rules about *whether* an action may run belong in its OPA policy.
"""

from __future__ import annotations

import re
from typing import Any

_REF = re.compile(r"\{\{\s*params\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class TemplateError(ValueError):
    pass


def render(value: Any, params: dict[str, Any]) -> Any:
    """Substitute parameter references anywhere inside a JSON-like value.

    A string that is exactly one reference takes the parameter's own type (number,
    boolean, object) and becomes None when the parameter was not supplied. A reference
    embedded in a longer string is interpolated as text and must be supplied.
    """
    if isinstance(value, str):
        whole = _REF.fullmatch(value.strip())
        if whole:
            return params.get(whole.group(1))

        def _interpolate(match: re.Match[str]) -> str:
            name = match.group(1)
            if params.get(name) is None:
                raise TemplateError(f"parameter '{name}' is required by a template but was not supplied")
            return str(params[name])

        return _REF.sub(_interpolate, value)
    if isinstance(value, dict):
        return {key: render(item, params) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, params) for item in value]
    return value


def referenced_params(value: Any) -> set[str]:
    """Every parameter name a JSON-like value refers to."""
    if isinstance(value, str):
        return set(_REF.findall(value))
    if isinstance(value, dict):
        return set().union(*(referenced_params(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(referenced_params(item) for item in value)) if value else set()
    return set()
