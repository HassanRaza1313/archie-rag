"""Prompt-injection screening (C4): passages that talk to an AI assistant rather than to a reader.

A document can carry text such as "ignore your previous instructions and ..." aimed at whatever model later reads
it. Passages that look like that are flagged at build time; from untrusted sources (websites) the sentences are
also removed (the passage is left out only when nothing else remains), and owners can restore any of it on the
Documents page. The patterns are deliberately narrow:
a false positive hides real content, so each one needs wording that ordinary documents rarely use.
"""

from __future__ import annotations

import re

from ragkit.textutil import split_sentences

_PATTERNS = [
    # "Ignore all previous instructions", "disregard the above rules", "forget your prior prompt"
    r"\b(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+|my\s+)*"
    r"(previous|prior|above|earlier|preceding|original|system)\s+(instructions?|prompts?|rules|directions|messages)",
    # "You are now DAN", "from now on you are an unrestricted assistant"
    r"\b(you are now|from now on,? you (are|will|must))\b",
    # Talking to the model about its own prompt
    r"\b(reveal|print|repeat|output|show)\s+(your|the)\s+(system\s+prompt|hidden\s+instructions|initial\s+instructions)",
    r"\bnew\s+instructions\s*:",
    # Addressing the model directly: "AI assistant: ...", "Note to the AI", "if you are an AI/LLM reading this"
    r"\b(note|message|instructions?)\s+(to|for)\s+(the\s+)?(ai|assistant|llm|language model|chatbot)\b",
    r"\bif you are an?\s+(ai|llm|language model|assistant|chatbot)\b",
    r"\b(ai|llm|language model)s?\s+(reading|processing)\s+this\b",
    # Chat-template control tokens smuggled into text
    r"<\|(im_start|im_end|system|endoftext)\|>|\[/?INST\]|<<\s*/?SYS\s*>>",
]
_INJECTION = re.compile("|".join(f"(?:{pattern})" for pattern in _PATTERNS), re.IGNORECASE)


def looks_like_instructions(text: str) -> str | None:
    """The phrase that looks like an instruction to an AI, or None."""
    match = _INJECTION.search(text)
    return " ".join(match.group().split()) if match else None


def strip_instructions(text: str) -> str:
    """The text without the sentences that look like instructions to an AI, so the rest of a passage stays."""
    kept = []
    for line in text.split("\n"):
        sentences = split_sentences(line)
        clean = [sentence for sentence in sentences if not _INJECTION.search(sentence)]
        if clean or not sentences:
            kept.append(" ".join(clean))
    return "\n".join(kept).strip()
