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
