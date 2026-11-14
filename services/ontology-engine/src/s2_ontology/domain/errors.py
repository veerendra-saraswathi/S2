"""Domain errors. The application maps each to an HTTP status and a uniform JSON body:

{"detail": {"message": "...", ...extra}}
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


class Unauthenticated(DomainError):
    status_code = 401


class Denied(DomainError):
    """Refused by a role check or by policy. `reasons` and `decision_id` ride in extra."""

    status_code = 403


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    status_code = 409


class Invalid(DomainError):
    status_code = 422


class PolicyUnavailable(DomainError):
    """The policy engine could not be consulted. Nothing is allowed without a decision."""

    status_code = 503


class TelemetryUnavailable(DomainError):
    status_code = 503
