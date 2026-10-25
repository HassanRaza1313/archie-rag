from __future__ import annotations

import json
from pathlib import Path

import pytest

from ragkit import cli
from ragkit.settings import Settings
from tests.conftest import GOLDEN_FILE, FakeRegistry


@pytest.fixture
def env(settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAG_STATE_DIR", str(settings.state_dir))
    monkeypatch.setenv("RAG_DATA_ROOTS", ",".join(str(p) for p in settings.data_roots))
    monkeypatch.setenv("RAG_QDRANT_MODE", "embedded")
    registry = FakeRegistry()
    monkeypatch.setattr("ragkit.providers.ProviderRegistry", lambda _settings: registry)
    return settings


def test_kb_build_list_export_and_eval(
    env: Settings, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_file = tmp_path / "kb.yaml"
    config_file.write_text(f"source:\n  path: {corpus.as_posix()}\nrerank:\n  top_n: 4\n", encoding="utf-8")
    assert cli.main(["kb", "build", "sample", "--config", str(config_file)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "completed" and built["summary"]["documents"] == 3

    assert cli.main(["kb", "list"]) == 0
    assert "sample" in capsys.readouterr().out
    assert cli.main(["kb", "export", "sample"]) == 0
    assert "top_n: 4" in capsys.readouterr().out
    assert cli.main(["kb", "export", "missing"]) == 1

    assert cli.main(["eval", "sample", str(GOLDEN_FILE)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["items"] == 12 and report["metrics"]["hit_at_5"] >= 0.9
    assert cli.main(["eval", "missing", str(GOLDEN_FILE)]) == 1


def test_rebuilding_existing_kb_adds_a_version(env: Settings, corpus: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_file = corpus.parent / "kb.yaml"
    config_file.write_text(f"source:\n  path: {corpus.as_posix()}\n", encoding="utf-8")
    assert cli.main(["kb", "build", "sample", "--config", str(config_file)]) == 0
    assert cli.main(["kb", "build", "sample", "--config", str(config_file), "--preset", "low_latency"]) == 0
    capsys.readouterr()
    cli.main(["kb", "list"])
    assert " v2 " in capsys.readouterr().out


def test_serve_refuses_public_bind_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("RAG_API_KEY", raising=False)
    assert cli.main(["serve"]) == 2


def test_settings_public_bind_flag() -> None:
    assert Settings.from_env({"RAG_PUBLIC_BIND_OK": "true"}).public_bind_ok is True


def test_worker_refuses_embedded_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_QDRANT_MODE", "embedded")
    assert cli.main(["worker"]) == 2


def test_doctor_reports(env: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor"])
    output = capsys.readouterr().out
    assert "qdrant (embedded)" in output and "ollama reachable" in output
    assert code in (0, 1)
