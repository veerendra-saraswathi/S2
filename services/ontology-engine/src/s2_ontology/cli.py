"""Developer CLI for a running stack. Everything goes through the public API.

    python -m s2_ontology.cli token --user operator
    python -m s2_ontology.cli apply-pack ontologies/core-fleet/pack.yaml
    python -m s2_ontology.cli seed-demo

Inside the Compose stack:  docker compose exec ontology-engine python -m s2_ontology.cli ...
The dev users and their passwords come from deploy/docker-compose/keycloak/s2-realm.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from s2_common.settings import get_settings

DEV_PASSWORD_SUFFIX = "-dev"  # realm import convention: user `operator`, password `operator-dev`

DEMO_DRONES = [
    ("DRN-0001", "Garuda-1", 17.4435, 78.3772),
    ("DRN-0002", "Garuda-2", 17.4486, 78.3908),
    ("DRN-0003", "Garuda-3", 17.4239, 78.3483),
]
DEMO_SUBSTATIONS = [
    ("SS-HYD-07", "Gachibowli 220 kV", 17.4401, 78.3489),
    ("SS-HYD-11", "Madhapur 132 kV", 17.4483, 78.3915),
]


def fetch_token(user: str, password: str | None = None) -> str:
    settings = get_settings()
    response = httpx.post(
        settings.oidc_token_url,
        data={
            "grant_type": "password",
            "client_id": settings.oidc_cli_client_id,
            "username": user,
            "password": password or f"{user}{DEV_PASSWORD_SUFFIX}",
        },
        timeout=10,
    )
    if response.status_code != 200:
        sys.exit(f"could not get a token for '{user}': {response.status_code} {response.text}")
    return response.json()["access_token"]


def _client(api: str, user: str) -> httpx.Client:
    return httpx.Client(base_url=api, headers={"Authorization": f"Bearer {fetch_token(user)}"}, timeout=30)


def _check(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        sys.exit(
            f"{response.request.method} {response.request.url} -> {response.status_code}\n{response.text}"
        )
    return response.json() if response.content else None


def _point(lon: float, lat: float) -> dict[str, Any]:
    return {"type": "Point", "coordinates": [lon, lat]}


def cmd_token(args: argparse.Namespace) -> None:
    print(fetch_token(args.user, args.password))


def cmd_apply_pack(args: argparse.Namespace) -> None:
    with _client(args.api, args.user) as client:
        for path in args.packs:
            pack = yaml.safe_load(Path(path).read_text())
            result = _check(client.post("/v1/ontology/packs:apply", json=pack))
            print(
                f"{result['pack']} {result['version']}: "
                f"{len(result['created'])} created, {len(result['updated'])} updated, "
                f"{len(result['unchanged'])} unchanged"
            )


def cmd_seed_demo(args: argparse.Namespace) -> None:
    """A few objects whose keys match what tools/telemetry-sim publishes."""
    with _client(args.api, args.user) as client:
        for key, call_sign, lat, lon in DEMO_DRONES:
            body = {
                "properties": {
                    "callSign": call_sign,
                    "model": "X8-Inspector",
                    "status": "READY",
                    "position": _point(lon, lat),
                }
            }
            _check(client.put(f"/v1/objects/CommercialDrone/{key}", json=body))
        for key, name, lat, lon in DEMO_SUBSTATIONS:
            body = {
                "properties": {
                    "name": name,
                    "voltageKv": 220 if "220" in name else 132,
                    "location": _point(lon, lat),
                }
            }
            _check(client.put(f"/v1/objects/Substation/{key}", json=body))
        _check(
            client.post(
                "/v1/objects/CommercialDrone/DRN-0001/links/inspects",
                json={"target_primary_key": "SS-HYD-07"},
            )
        )
        _check(
            client.post(
                "/v1/objects/CommercialDrone/DRN-0002/links/inspects",
                json={"target_primary_key": "SS-HYD-11"},
            )
        )
    print(f"seeded {len(DEMO_DRONES)} drones, {len(DEMO_SUBSTATIONS)} substations, 2 links")


def cmd_get(args: argparse.Namespace) -> None:
    with _client(args.api, args.user) as client:
        print(json.dumps(_check(client.get(args.path)), indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="s2ctl", description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000", help="ontology engine base URL")
    commands = parser.add_subparsers(dest="command", required=True)

    token = commands.add_parser("token", help="print an access token for a dev user")
    token.add_argument("--user", default="operator")
    token.add_argument("--password", default=None)
    token.set_defaults(run=cmd_token)

    apply_pack = commands.add_parser("apply-pack", help="apply one or more ontology packs")
    apply_pack.add_argument("packs", nargs="+")
    apply_pack.add_argument("--user", default="admin")
    apply_pack.set_defaults(run=cmd_apply_pack)

    seed = commands.add_parser("seed-demo", help="create demo drones and substations")
    seed.add_argument("--user", default="admin")
    seed.set_defaults(run=cmd_seed_demo)

    get = commands.add_parser("get", help="GET an API path and print the JSON")
    get.add_argument("path")
    get.add_argument("--user", default="operator")
    get.set_defaults(run=cmd_get)

    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
