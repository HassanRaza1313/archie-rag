"""Claim check: each sentence of an answer is checked against the passages it cites by a small local
entailment (NLI) model, so sentences the sources do not back can be flagged. ONNX on CPU, no PyTorch."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ragkit.textutil import split_sentences

# Binary entailment (MNLI, FEVER, ANLI, LingNLI), MIT licence; the int8 file is 87 MB and as accurate as fp32 here.
MODEL = "MoritzLaurer/DeBERTa-v3-xsmall-mnli-fever-anli-ling-binary"
MODEL_FILE = "onnx/model_quantized.onnx"
SUPPORTED = 0.5  # entailment probability at or above which a sentence counts as backed by its source
WINDOW_WORDS, STRIDE_WORDS = 200, 150  # the model reads 512 tokens; long passages are checked in overlapping windows
BATCH = 16

_CITATION = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")
_LEADING_CITATIONS = re.compile(r"^(?:\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\])+")
_MARKUP = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+|[*_`#]+")
_DONT_KNOW = re.compile(r"\b(do not|don't|cannot|can't) (know|find|answer)\b", re.IGNORECASE)


class Entailment(Protocol):
    def entailment(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Probability that each premise entails its hypothesis."""
        ...


@dataclass
class Claim:
    text: str  # the sentence as it appears in the answer, citations included
    labels: list[int]  # the sources it is checked against (1-based)
    support: float = 0.0


def _labels(text: str) -> list[int]:
    return [int(item.strip()[1:]) for group in _CITATION.findall(text) for item in re.split(r"[,;]", group)]


def claims_of(answer: str, source_count: int) -> list[Claim]:
    """Sentences that state something, each with the sources it should be checked against.

    A sentence without its own citation borrows the next cited sentence's in the same paragraph (a citation often
    closes a run of sentences), else the previous one's, else every source the answer cites."""
    cited_anywhere = sorted({label for label in _labels(answer) if 1 <= label <= source_count})
    claims: list[Claim] = []
    for paragraph in answer.split("\n"):
        sentences: list[str] = []
        for sentence in split_sentences(paragraph):
            lead = _LEADING_CITATIONS.match(sentence)
            if lead and sentences:  # "...the end. [S2] Next" splits before the citation; give it back
                sentences[-1] += " " + lead.group().strip()
                sentence = sentence[lead.end() :].strip()
            if sentence:
                sentences.append(sentence)
        own = [[label for label in _labels(s) if 1 <= label <= source_count] for s in sentences]
        for index, sentence in enumerate(sentences):
            if len(hypothesis(sentence)) < 20 or sentence.rstrip().endswith("?") or _DONT_KNOW.search(sentence):
                continue
            after = next((labels for labels in own[index:] if labels), None)
            before = next((labels for labels in reversed(own[:index]) if labels), None)
            labels = own[index] or after or before or cited_anywhere
            if labels:
                claims.append(Claim(sentence, labels))
    return claims


def hypothesis(sentence: str) -> str:
    return " ".join(_MARKUP.sub("", _CITATION.sub("", sentence)).split())


def windows(passage: str) -> list[str]:
    tokens = passage.split()
    if len(tokens) <= WINDOW_WORDS:
        return [passage]
    starts = range(0, max(1, len(tokens) - WINDOW_WORDS + STRIDE_WORDS), STRIDE_WORDS)
    return [" ".join(tokens[start : start + WINDOW_WORDS]) for start in starts]


def check(model: Entailment, claims: list[Claim], passages: list[str]) -> list[Claim]:
    """Score each claim by its best-supporting window among the passages it cites."""
    pairs: list[tuple[str, str]] = []
    owners: list[int] = []
    for index, claim in enumerate(claims):
        for label in claim.labels:
            for window in windows(passages[label - 1]):
                pairs.append((window, hypothesis(claim.text)))
                owners.append(index)
    scores: list[float] = []
    for start in range(0, len(pairs), BATCH):
        scores.extend(model.entailment(pairs[start : start + BATCH]))
    for owner, score in zip(owners, scores, strict=True):
        claims[owner].support = max(claims[owner].support, score)
    return claims


def verdict(claims: list[Claim]) -> tuple[str, str]:
    if not claims:
        return "pass", "no sentences to check"
    backed = sum(claim.support >= SUPPORTED for claim in claims)
    reason = f"{backed} of {len(claims)} sentences backed by their sources"
    return ("pass" if backed * 2 >= len(claims) else "fail"), reason


class NliModel:
    """The entailment model, downloaded once into the model cache."""

    def __init__(self, cache_dir: Path | None) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise ImportError("the claim check needs the 'rerank' extra: pip install 'ragkit[rerank]'") from exc
        cache = str(cache_dir) if cache_dir else None
        self._np = np
        self._session = ort.InferenceSession(
            hf_hub_download(MODEL, MODEL_FILE, cache_dir=cache), providers=["CPUExecutionProvider"]
        )
        self._inputs = {item.name for item in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(hf_hub_download(MODEL, "tokenizer.json", cache_dir=cache))
        self._tokenizer.enable_padding()
        self._tokenizer.enable_truncation(512)

    def entailment(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        np = self._np
        encoded = self._tokenizer.encode_batch(pairs)
        feed: dict[str, Any] = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
        }
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.array([e.type_ids for e in encoded], dtype=np.int64)
        logits = self._session.run(None, feed)[0]
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        return [float(p) for p in (exp / exp.sum(axis=1, keepdims=True))[:, 0]]  # label 0 is "entailment"


_lock = threading.Lock()
_models: dict[Path | None, NliModel] = {}


def load(cache_dir: Path | None) -> NliModel:
    with _lock:  # one download even when the first answers arrive together
        if cache_dir not in _models:
            _models[cache_dir] = NliModel(cache_dir)
        return _models[cache_dir]
