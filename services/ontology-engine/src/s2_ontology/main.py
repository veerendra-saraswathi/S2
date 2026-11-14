"""ASGI entry point: `uvicorn s2_ontology.main:app`. Configuration comes from S2_* variables."""

from s2_ontology.app import create_app

app = create_app()
