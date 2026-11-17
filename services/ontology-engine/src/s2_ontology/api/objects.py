"""Objects, links and time-series reads.

Direct writes here are the bootstrap and sync path and need the ontology-admin role.
Operators and agents change objects through actions.

Every route applies the caller's markings (domain/markings.py): a type they are not
cleared for answers 404, and properties they are not cleared for are left out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_ontology.api.deps import ONTOLOGY_ADMIN, get_principal, get_session, require_roles
from s2_ontology.api.schemas import LinkWrite, ObjectOut, ObjectWrite, TimeseriesOut, TimeseriesPoint
from s2_ontology.db.models import ObjectType, PropertyDataType
from s2_ontology.domain import audit, markings, objects, registry
from s2_ontology.domain.errors import Denied, NotFound, TelemetryUnavailable

router = APIRouter(prefix="/v1/objects", tags=["objects"])


def _visible_type(session: Session, principal: Principal, api_name: str) -> ObjectType:
    return markings.require_type(principal, registry.get_object_type(session, api_name))


@router.get(
    "/{object_type}", response_model=list[ObjectOut], summary="List objects of a type, by primary key"
)
def list_objects(
    object_type: str,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    after: Annotated[
        str | None, Query(description="Return objects whose primary key sorts after this one")
    ] = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[ObjectOut]:
    definition = _visible_type(session, principal, object_type)
    hidden = markings.hidden_properties(principal, definition)
    found = objects.list_objects(session, definition, limit=limit, after=after)
    return [objects.to_out(definition, *item, hidden) for item in found]


@router.get("/{object_type}/{primary_key}", response_model=ObjectOut)
def get_object(
    object_type: str,
    primary_key: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> ObjectOut:
    definition = _visible_type(session, principal, object_type)
    hidden = markings.hidden_properties(principal, definition)
    return objects.to_out(definition, *objects.get(session, definition, primary_key), hidden)


@router.put(
    "/{object_type}/{primary_key}",
    response_model=ObjectOut,
    summary="Create or replace an object",
    description=(
        "The body is the object's complete state: properties left out are cleared, except "
        "properties the caller is not cleared to see, which keep their values. Writing such "
        "a property is refused."
    ),
)
def put_object(
    object_type: str,
    primary_key: str,
    body: ObjectWrite,
    principal: Principal = Depends(require_roles(ONTOLOGY_ADMIN)),
    session: Session = Depends(get_session),
) -> ObjectOut:
    definition = _visible_type(session, principal, object_type)
    hidden = markings.hidden_properties(principal, definition)
    refused = sorted(hidden & body.properties.keys())
    if refused:
        raise Denied(f"you are not cleared to write {', '.join(refused)}", properties=refused)
    change = objects.write(
        session, principal, definition, primary_key, body.properties, mode="replace", preserve=hidden
    )
    out = objects.to_out(definition, *objects.get(session, definition, primary_key), hidden)
    audit.record(
        session,
        principal,
        "object.upserted",
        resource_type=object_type,
        resource_id=out.primary_key,
        details=change,
    )
    session.commit()
    return out


@router.delete("/{object_type}/{primary_key}", status_code=204)
def delete_object(
    object_type: str,
    primary_key: str,
    principal: Principal = Depends(require_roles(ONTOLOGY_ADMIN)),
    session: Session = Depends(get_session),
) -> Response:
    definition = _visible_type(session, principal, object_type)
    change = objects.delete(session, definition, primary_key)
    audit.record(
        session,
        principal,
        "object.deleted",
        resource_type=object_type,
        resource_id=primary_key,
        details=change,
    )
    session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


@router.get(
    "/{object_type}/{primary_key}/links/{link}",
    response_model=list[ObjectOut],
    summary="Follow a link by its forward or reverse name",
)
def get_linked(
    object_type: str,
    primary_key: str,
    link: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[ObjectOut]:
    definition = _visible_type(session, principal, object_type)
    link_type, reverse = objects.resolve_link(session, definition, link)
    far = link_type.source_object_type if reverse else link_type.target_object_type
    if not markings.cleared(principal, far.markings):
        raise NotFound(f"{object_type} has no link '{link}'")
    far_type, found = objects.linked(session, definition, primary_key, link)
    hidden = markings.hidden_properties(principal, far_type)
    return [objects.to_out(far_type, *item, hidden) for item in found]


@router.post(
    "/{object_type}/{primary_key}/links/{link}",
    status_code=204,
    summary="Link this object to another (forward name only; linking twice is a no-op)",
)
def post_link(
    object_type: str,
    primary_key: str,
    link: str,
    body: LinkWrite,
    principal: Principal = Depends(require_roles(ONTOLOGY_ADMIN)),
    session: Session = Depends(get_session),
) -> Response:
    definition = _visible_type(session, principal, object_type)
    link_type, _ = objects.resolve_link(session, definition, link)
    if not markings.cleared(principal, link_type.target_object_type.markings):
        raise NotFound(f"{object_type} has no link '{link}'")
    change = objects.create_link(session, principal, definition, primary_key, link, body.target_primary_key)
    if change["changed"]:
        audit.record(
            session,
            principal,
            "link.created",
            resource_type=object_type,
            resource_id=primary_key,
            details=change,
        )
    session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Time series
# --------------------------------------------------------------------------- #

_SERIES = text(
    """
    SELECT time, value
      FROM telemetry.reading
     WHERE tenant_id = :tenant AND object_type = :object_type AND object_pk = :object_pk
       AND metric = :metric AND time >= :start AND time < :end
       AND value IS NOT NULL
     ORDER BY time DESC
     LIMIT :limit
    """
)


@router.get(
    "/{object_type}/{primary_key}/timeseries/{property_name}",
    response_model=TimeseriesOut,
    summary="Read a time-series property",
    description="The newest `limit` points in [start, end), oldest first. Default window: the last hour.",
)
def get_timeseries(
    request: Request,
    object_type: str,
    primary_key: str,
    property_name: str,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> TimeseriesOut:
    definition = _visible_type(session, principal, object_type)
    prop = next((p for p in definition.properties if p.api_name == property_name), None)
    if prop is not None and not markings.visible_property(principal, definition, prop):
        prop = None  # answer exactly as for a property that does not exist
    if prop is None or prop.data_type != PropertyDataType.TIMESERIES:
        raise NotFound(f"{object_type} has no time-series property '{property_name}'")
    telemetry = request.app.state.telemetry_engine
    if telemetry is None:
        raise TelemetryUnavailable("no telemetry store is configured (S2_TELEMETRY_DATABASE_URL)")

    end = end or datetime.now(UTC)
    start = start or end - timedelta(hours=1)
    metric = (prop.binding or {})["metric"]
    # Telemetry is keyed by (object type, primary key), not by the object's row, so a
    # series can be read before the object has been registered.
    parameters = {
        "tenant": principal.tenant_id,
        "object_type": object_type,
        "object_pk": primary_key,
        "metric": metric,
        "start": start,
        "end": end,
        "limit": limit,
    }
    with telemetry.connect() as connection:
        rows = connection.execute(_SERIES, parameters).all()
    return TimeseriesOut(
        object_type=object_type,
        primary_key=primary_key,
        property=property_name,
        metric=metric,
        unit=prop.unit,
        points=[TimeseriesPoint(time=row.time, value=row.value) for row in reversed(rows)],
    )
