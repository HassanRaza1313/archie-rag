from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ragkit.config import (
    INDEX_STAGES,
    PRESETS,
    KnowledgeBaseConfig,
    config_json_schema,
    deep_merge,
    preset_config,
)
from ragkit.settings import Settings, load_dotenv


def test_defaults_are_valid_and_round_trip() -> None:
    config = KnowledgeBaseConfig()
    assert KnowledgeBaseConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_is_valid(name: str) -> None:
    assert isinstance(preset_config(name), KnowledgeBaseConfig)


def test_unknown_preset_raises() -> None:
    with pytest.raises(KeyError):
        preset_config("nope")


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeBaseConfig.model_validate({"chunking": {"chunk_sise": 100}})


def test_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        KnowledgeBaseConfig.model_validate({"chunking": {"overlap_pct": 90}})


def test_prompt_template_requires_slots() -> None:
    with pytest.raises(ValidationError, match="sources"):
        KnowledgeBaseConfig.model_validate({"generation": {"prompt_template": "Answer {question}"}})


def test_index_fingerprint_changes_only_for_index_settings() -> None:
    base = KnowledgeBaseConfig()
    query_change = base.model_copy(update={"rerank": base.rerank.model_copy(update={"top_n": 3})})
    index_change = base.model_copy(update={"chunking": base.chunking.model_copy(update={"chunk_size": 300})})
    assert query_change.index_fingerprint() == base.index_fingerprint()
    assert query_change.query_fingerprint() != base.query_fingerprint()
    assert index_change.index_fingerprint() != base.index_fingerprint()


def test_redact_at_ingest_is_index_time() -> None:
    base = KnowledgeBaseConfig()
    changed = base.model_copy(update={"privacy": base.privacy.model_copy(update={"redact_at_ingest": True})})
    assert changed.index_fingerprint() != base.index_fingerprint()


def test_schema_carries_ui_metadata() -> None:
    schema = config_json_schema()
    chunking = schema["$defs"]["ChunkingConfig"]
    assert chunking["rebuild"] is True and chunking["summary"]
    assert chunking["properties"]["chunk_size"]["tier"] == "essential"
    assert chunking["properties"]["section_headers"]["tier"] == "advanced"
    assert schema["$defs"]["RerankConfig"]["rebuild"] is False
    assert list(schema["properties"])[: len(INDEX_STAGES)] == list(INDEX_STAGES)


def test_deep_merge_is_non_destructive() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    merged = deep_merge(base, {"a": {"b": 9}})
    assert merged == {"a": {"b": 9, "c": 2}, "d": 3}
    assert base["a"]["b"] == 1


def test_settings_from_env_parses_lists_and_types() -> None:
    settings = Settings.from_env(
        {"RAG_DATA_ROOTS": "a, b", "RAG_PORT": "9000", "RAG_QDRANT_MODE": "server", "RAG_API_KEY": ""}
    )
    assert settings.data_roots == [Path("a"), Path("b")]
    assert settings.port == 9000 and settings.qdrant_mode == "server" and settings.api_key is None


def test_settings_reject_invalid_values() -> None:
    with pytest.raises(ValidationError):
        Settings.from_env({"RAG_QDRANT_MODE": "cloud"})


def test_load_dotenv_does_not_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nRAG_X_ONE='1'\nRAG_X_TWO=2\n", encoding="utf-8")
    monkeypatch.setenv("RAG_X_TWO", "kept")
    monkeypatch.delenv("RAG_X_ONE", raising=False)
    load_dotenv(env_file)
    import os

    assert os.environ["RAG_X_ONE"] == "1" and os.environ["RAG_X_TWO"] == "kept"
    monkeypatch.delenv("RAG_X_ONE")
