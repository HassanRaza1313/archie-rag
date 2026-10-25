from __future__ import annotations

import pytest

from ragkit.privacy import DETECTORS, Redactor, redact

ALL = list(DETECTORS)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("mail me at jane.doe+rag@example.co.uk now", "EMAIL"),
        ("call +1 (555) 123-4567 today", "PHONE"),
        ("call 555-123-4567 today", "PHONE"),
        ("office 020 7946 0958 ext", "PHONE"),
        ("mobile 0300-1234567 ok", "PHONE"),
        ("card 4111 1111 1111 1111 expires", "CARD"),
        ("card 4111-1111-1111-1111 expires", "CARD"),
        ("iban GB82 WEST 1234 5698 7654 32 please", "IBAN"),
        ("server 192.168.10.4 is down", "IP"),
        ("ssn 123-45-6789 on file", "NATIONAL_ID"),
        ("cnic 12345-1234567-1 on file", "NATIONAL_ID"),
    ],
)
def test_detects_personal_data(text: str, kind: str) -> None:
    assert f"[REDACTED_{kind}_1]" in redact(text, ALL)


@pytest.mark.parametrize(
    "text",
    [
        "ISBN 978-0-671-02703-2 is the 1936 edition",
        "Published in 1936, 1964, 1981 and 1998",
        "card 4111 1111 1111 1112 fails the Luhn check",
        "iban GB00 WEST 1234 5698 7654 32 has a bad checksum",
        "version 999.1.1.1 is not an address",
        "see pages 12-14 and chapter 4567",
        "the 2-for-1 deal on 3 items",
        "order 1234 5698 7654 32 shipped",
    ],
)
def test_ignores_lookalikes(text: str) -> None:
    assert redact(text, ALL) == text


def test_placeholders_are_consistent_per_value() -> None:
    redactor = Redactor(ALL)
    first = redactor.redact("a@x.io wrote to b@y.io")
    second = redactor.redact("reply to a@x.io")
    assert first == "[REDACTED_EMAIL_1] wrote to [REDACTED_EMAIL_2]"
    assert second == "reply to [REDACTED_EMAIL_1]"


def test_only_selected_kinds_are_redacted() -> None:
    assert redact("a@x.io 192.168.0.1", ["ip"]) == "a@x.io [REDACTED_IP_1]"


def test_overlapping_matches_prefer_higher_priority_kind() -> None:
    text = "iban GB82 WEST 1234 5698 7654 32"
    assert redact(text, ALL) == "iban [REDACTED_IBAN_1]"


def test_empty_and_clean_text() -> None:
    assert redact("", ALL) == ""
    assert redact("nothing personal here", ALL) == "nothing personal here"
