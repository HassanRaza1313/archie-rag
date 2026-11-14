"""Every setting explains what changing it does: each choice, both switch states, both number directions."""

from __future__ import annotations

from typing import get_args, get_origin

from ragkit.config import KnowledgeBaseConfig
from ragkit.notes import NOTES

# Lists of things (file types, filters, detectors, strategies) explain themselves by their items.
SELF_EXPLAINING = {"source.path", "source.file_types", "source.include", "source.exclude", "retrieval.filter_fields",
                   "agent.strategies", "privacy.detectors"}  # fmt: skip


def expected_keys(annotation: object) -> set[str]:
    if annotation is bool:
        return {"true", "false"}
    if annotation in (int, float):
        return {"up", "down"}
    args = get_args(annotation)
    if args and get_origin(annotation) is not list and all(isinstance(a, str) for a in args):
        return set(args)
    return {"*"}


def test_every_setting_has_its_notes() -> None:
    missing, unknown = [], set(NOTES)
    for stage, stage_field in KnowledgeBaseConfig.model_fields.items():
        for name, field in stage_field.annotation.model_fields.items():  # type: ignore[union-attr]
            key = f"{stage}.{name}"
            unknown.discard(key)
            if key in SELF_EXPLAINING:
                continue
            want = expected_keys(field.annotation)
            if set(NOTES.get(key, {})) != want:
                missing.append((key, sorted(want - set(NOTES.get(key, {})))))
    assert not missing, missing
    assert not unknown, unknown  # notes for settings that no longer exist


def test_notes_stay_short() -> None:
    too_long = [(key, value) for key, notes in NOTES.items() for value, text in notes.items() if len(text) > 200]
    assert not too_long, too_long
