"""PII detection and redaction.

Detectors are regex candidates confirmed by validators (Luhn, IBAN mod-97, IP parsing), which keeps book
text such as ISBNs, years and page numbers from being redacted. Placeholders are numbered per value and
stay consistent within one ``Redactor``, so a model can still tell two redacted emails apart.
shortcut: no person-name detection; add an NER detector (e.g. Presidio) when names must be removed.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterable

Detector = tuple[re.Pattern[str], Callable[[str], bool]]


def _luhn_ok(candidate: str) -> bool:
    digits = [int(ch) for ch in candidate if ch.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        checksum += digit
    return checksum % 10 == 0


def _iban_ok(candidate: str) -> bool:
    compact = candidate.replace(" ", "").upper()
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return 15 <= len(compact) <= 34 and int(numeric) % 97 == 1


def _ip_ok(candidate: str) -> bool:
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


_NATIONAL_PHONE = re.compile(
    r"^(?:\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}"  # North American: 555-123-4567, (555) 123 4567
    r"|0\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4})$"  # trunk-prefixed national: 020 7946 0958, 0300-1234567
)


def _phone_ok(candidate: str) -> bool:
    candidate = candidate.strip()
    digits = re.sub(r"\D", "", candidate)
    if not 10 <= len(digits) <= 15:
        return False
    if candidate.startswith("+"):
        return True
    return bool(_NATIONAL_PHONE.match(candidate))


def _always(_: str) -> bool:
    return True


DETECTORS: dict[str, Detector] = {
    "email": (re.compile(r"\b[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b"), _always),
    "card": (re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])"), _luhn_ok),
    "iban": (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), _iban_ok),
    "national_id": (
        re.compile(r"\b(?:\d{3}-\d{2}-\d{4}|\d{5}-\d{7}-\d|[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D])\b"),
        _always,
    ),
    "phone": (re.compile(r"(?<![\w-])\+?\(?\d[\d ().-]{8,}\d(?![\w-])"), _phone_ok),
    "ip": (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"), _ip_ok),
}


class Redactor:
    """Replaces detected values with ``[REDACTED_<KIND>_<n>]``; one instance per request or document."""

    def __init__(self, kinds: Iterable[str]) -> None:
        self.kinds = [kind for kind in DETECTORS if kind in set(kinds)]
        self._placeholders: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}

    def redact(self, text: str) -> str:
        spans = self._find(text)
        if not spans:
            return text
        parts, cursor = [], 0
        for start, end, kind in spans:
            parts.append(text[cursor:start])
            parts.append(self._placeholder(kind, text[start:end]))
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts)

    def _find(self, text: str) -> list[tuple[int, int, str]]:
        found: list[tuple[int, int, str]] = []
        for kind in self.kinds:  # DETECTORS order = priority: an IBAN beats the phone number inside it
            pattern, is_valid = DETECTORS[kind]
            for match in pattern.finditer(text):
                start, end = match.span()
                if is_valid(match.group()) and not any(s < end and start < e for s, e, _ in found):
                    found.append((start, end, kind))
        return sorted(found)

    def _placeholder(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self._placeholders:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._placeholders[key] = f"[REDACTED_{kind.upper()}_{self._counts[kind]}]"
        return self._placeholders[key]


def redact(text: str, kinds: Iterable[str]) -> str:
    return Redactor(kinds).redact(text)
