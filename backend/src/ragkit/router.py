"""Question router: picks which source groups (chatbots) to search for a question, locally and deterministically.

1. A question that names a group goes to that group.
2. Otherwise a small naive Bayes classifier over each group's vocabulary (how many of its passages use each word)
   scores the groups. A clear winner is searched alone; when unsure, the likely groups are searched together and
   their results merged, so a wrong guess never hides the answer. Words found in no group are ignored; a question
   with no known words searches every group.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ragkit.textutil import keyword_terms

SURE = 0.9  # one group alone needs this probability; otherwise groups are added until together they reach it
MAX_GROUPS = 3
PRIOR_PASSAGES = 50  # every group counts as at least this big, so a few passages are weak evidence


@dataclass
class Profile:
    """A group's vocabulary: in how many of its (sampled) passages each word appears."""

    passages: int = 0
    terms: Counter[str] = field(default_factory=Counter)


def profile_of(payloads: list[dict[str, Any]]) -> Profile:
    profile = Profile(passages=len(payloads))
    for payload in payloads:
        text = " ".join(str(payload.get(key) or "") for key in ("title", "section", "text"))
        profile.terms.update(set(keyword_terms(text)))
    return profile


@dataclass
class Route:
    chosen: list[str]  # group names, most likely first
    reason: str


def _names(question: str, names: list[str]) -> list[str]:
    return [n for n in names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", question, re.IGNORECASE)]


def route(question: str, profiles: dict[str, Profile]) -> Route:
    names = list(profiles)
    if len(names) <= 1:
        return Route(names, "one group")
    named = _names(question, names)
    if named:
        return Route(named, f"the question names {', '.join(named)}")
    terms = [t for t in set(keyword_terms(question)) if any(p.terms[t] for p in profiles.values())]
    if not terms:
        return Route(names, "no question words appear in any group, so all were searched")
    # Each word's share of a group's passages; the prior keeps a small or empty group from winning on words it lacks.
    scores = {
        name: sum(math.log((p.terms[t] + 0.1) / (p.passages + PRIOR_PASSAGES)) for t in terms)
        for name, p in profiles.items()
    }
    top = max(scores.values())
    weights = {name: math.exp(score - top) for name, score in scores.items()}
    total = sum(weights.values())
    ranked = sorted(names, key=lambda n: weights[n], reverse=True)
    chosen: list[str] = []
    share = 0.0
    for name in ranked[:MAX_GROUPS]:
        chosen.append(name)
        share += weights[name] / total
        if share >= SURE:
            break
    words = ", ".join(sorted(terms)[:5])
    percent = round(100 * weights[ranked[0]] / total)
    if len(chosen) == 1:
        return Route(chosen, f"{chosen[0]} is the likely group ({percent}%, from: {words})")
    return Route(chosen, f"unsure ({ranked[0]} {percent}%), so searched {', '.join(chosen)} (from: {words})")
