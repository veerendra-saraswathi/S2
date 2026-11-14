"""Ontology registry endpoints: apply packs, read definitions."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from s2_common.auth import Principal
from s2_ontology.api.deps import ONTOLOGY_ADMIN, get_principal, get_session, require_roles
from s2_ontology.api.schemas import ActionTypeOut, LinkTypeOut, ObjectTypeOut, OntologyPack, PackApplied
from s2_ontology.domain import registry

router = APIRouter(prefix="/v1/ontology", tags=["ontology"])


@router.post(
    "/packs:apply",
    response_model=PackApplied,
    summary="Create or update the object, link and action types defined by a pack (idempotent)",
)
def apply_pack(
    pack: OntologyPack,
    principal: Principal = Depends(require_roles(ONTOLOGY_ADMIN)),
    session: Session = Depends(get_session),
) -> PackApplied:
    result = registry.apply_pack(session, principal, pack)
    session.commit()
    return result


@router.get("/object-types", response_model=list[ObjectTypeOut])
def list_object_types(
    _: Principal = Depends(get_principal), session: Session = Depends(get_session)
) -> list[ObjectTypeOut]:
    return [ObjectTypeOut.model_validate(t) for t in registry.list_object_types(session)]


@router.get("/object-types/{api_name}", response_model=ObjectTypeOut)
def get_object_type(
    api_name: str, _: Principal = Depends(get_principal), session: Session = Depends(get_session)
) -> ObjectTypeOut:
    return ObjectTypeOut.model_validate(registry.get_object_type(session, api_name))


@router.get("/link-types", response_model=list[LinkTypeOut])
def list_link_types(
    _: Principal = Depends(get_principal), session: Session = Depends(get_session)
) -> list[LinkTypeOut]:
    return [registry.link_type_out(link) for link in registry.list_link_types(session)]


@router.get("/action-types", response_model=list[ActionTypeOut])
def list_action_types(
    _: Principal = Depends(get_principal), session: Session = Depends(get_session)
) -> list[ActionTypeOut]:
    return [registry.action_type_out(action) for action in registry.list_action_types(session)]
