"""The CLI's lakehouse commands, against a catalog and warehouse in a temporary directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from s2_pipelines import cli
from s2_pipelines.settings import get_pipeline_settings


@pytest.fixture(autouse=True)
def local_lakehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S2_CATALOG_URI", f"sqlite:///{tmp_path / 'catalog.db'}")
    monkeypatch.setenv("S2_WAREHOUSE", f"file://{tmp_path / 'warehouse'}")
    get_pipeline_settings.cache_clear()
    yield
    get_pipeline_settings.cache_clear()


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    cli.main(list(argv))
    return capsys.readouterr().out


def test_the_branch_walkthrough(capsys: pytest.CaptureFixture[str]) -> None:
    assert "3 rows" in run(capsys, "demo-load")
    assert "created at snapshot" in run(capsys, "branch", "create", "assets.commercial_drone", "preview")
    assert "firmware 4.2.0" in run(capsys, "demo-rollout", "--branch", "preview")
    assert '"preview"' in run(capsys, "branch", "list", "assets.commercial_drone")
    assert "@main = snapshot" in run(capsys, "branch", "promote", "assets.commercial_drone", "preview")
    assert "dropped" in run(capsys, "branch", "drop", "assets.commercial_drone", "preview")


def test_errors_are_one_line_not_a_traceback() -> None:
    with pytest.raises(SystemExit, match=r"error: dataset 'assets\.commercial_drone' does not exist"):
        cli.main(["branch", "list", "assets.commercial_drone"])
    with pytest.raises(SystemExit):
        cli.main(["branch", "create", "assets.commercial_drone"])  # no name


def test_token_source_uses_client_credentials_and_explains_a_missing_client() -> None:
    import httpx

    from s2_pipelines.engine import EngineError, TokenSource
    from s2_pipelines.settings import PipelineSettings

    seen: list[dict[str, str]] = []

    def idp(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        seen.append(form)
        if form.get("client_secret") == "good":
            return httpx.Response(200, json={"access_token": "t1", "expires_in": 900})
        return httpx.Response(401, json={"error": "invalid_client"})

    http = httpx.Client(transport=httpx.MockTransport(idp))
    good = TokenSource(
        PipelineSettings(pipeline_client_id="s2-pipeline", pipeline_client_secret="good"), http
    )
    assert good() == "t1" and good() == "t1"
    assert len(seen) == 1, "the token is reused until it nears expiry"
    assert seen[0]["grant_type"] == "client_credentials" and seen[0]["client_id"] == "s2-pipeline"

    bad = TokenSource(PipelineSettings(pipeline_client_id="s2-pipeline", pipeline_client_secret="bad"), http)
    with pytest.raises(EngineError, match="make dev-pipeline-client"):
        bad()

    dev = TokenSource(PipelineSettings(pipeline_client_id=None, pipeline_client_secret=None), http)
    with pytest.raises(EngineError):
        dev()
    assert seen[-1]["grant_type"] == "password" and seen[-1]["username"] == "admin"
