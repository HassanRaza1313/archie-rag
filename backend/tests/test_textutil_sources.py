from __future__ import annotations

from pathlib import Path

import pytest

from ragkit.config import SourceConfig
from ragkit.settings import Settings
from ragkit.sources import SourceError, discover, resolve_root
from ragkit.textutil import (
    bm25_document_vector,
    bm25_query_vector,
    jaccard,
    keyword_terms,
    normalize_question,
    shingles,
    split_sentences,
    term_id,
)


def test_split_sentences_keeps_abbreviations_and_numbers() -> None:
    assert split_sentences("It is 2.5 m tall. Next one! Is it? yes") == ["It is 2.5 m tall.", "Next one!", "Is it? yes"]


def test_keyword_terms_drop_stopwords() -> None:
    assert keyword_terms("What is the RATIO of browns to greens?") == ["ratio", "browns", "greens"]


def test_bm25_vectors() -> None:
    document = bm25_document_vector("compost compost pile")
    assert document[term_id("compost")] > document[term_id("pile")] > 0
    assert bm25_query_vector("the compost pile pile") == {term_id("compost"): 1.0, term_id("pile"): 1.0}


def test_shingles_and_jaccard() -> None:
    text = "one two three four five six seven"
    assert jaccard(shingles(text), shingles(text)) == 1.0
    assert jaccard(shingles(text), shingles("completely different words here now")) == 0.0
    assert jaccard(set(), {1}) == 0.0


def test_normalize_question() -> None:
    assert normalize_question("  What's  the RATIO?? ") == "what s the ratio"


def _settings(tmp_path: Path) -> Settings:
    return Settings(state_dir=tmp_path / "state", data_roots=[tmp_path / "data"])


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "docs"
    (root / "sub").mkdir(parents=True)
    for name in ("a.pdf", "b.md", "c.txt", "d.docx", "sub/e.md", "sub/skip.md"):
        (root / name).write_text("x", encoding="utf-8")
    return root


def test_discover_filters_types_and_patterns(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    settings = _settings(tmp_path)
    all_refs = discover(SourceConfig(path=str(root)), settings, "kb")
    assert [r.uri for r in all_refs] == ["a.pdf", "b.md", "c.txt", "sub/e.md", "sub/skip.md"]
    filtered = discover(SourceConfig(path=str(root), include=["sub/*"], exclude=["*skip*"]), settings, "kb")
    assert [r.uri for r in filtered] == ["sub/e.md"]
    flat = discover(SourceConfig(path=str(root), recursive=False, file_types=[".md"]), settings, "kb")
    assert [r.uri for r in flat] == ["b.md"]


def test_paths_outside_data_roots_are_rejected(tmp_path: Path) -> None:
    _tree(tmp_path)
    outside = tmp_path / "secret"
    outside.mkdir()
    settings = _settings(tmp_path)
    with pytest.raises(SourceError, match="outside the allowed data roots"):
        resolve_root(SourceConfig(path=str(outside)), settings, "kb")
    with pytest.raises(SourceError, match="outside"):
        resolve_root(SourceConfig(path=str(tmp_path / "data" / ".." / "secret")), settings, "kb")


def test_missing_folder_is_reported(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    with pytest.raises(SourceError, match="does not exist"):
        resolve_root(SourceConfig(path=str(tmp_path / "data" / "missing")), _settings(tmp_path), "kb")


def test_uploads_root_is_per_kb(tmp_path: Path) -> None:
    root = resolve_root(SourceConfig(type="uploads"), _settings(tmp_path), "kb_1")
    assert root == (tmp_path / "state" / "uploads" / "kb_1").resolve() and root.is_dir()


def test_content_hash_is_stable(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    ref = discover(SourceConfig(path=str(root)), _settings(tmp_path), "kb")[0]
    before = ref.content_hash()
    assert ref.content_hash() == before
    (root / "a.pdf").write_text("changed", encoding="utf-8")
    assert ref.content_hash() != before
