"""Accounts: the people who sign in, their roles, and what each role may do.

Passwords are hashed with scrypt from the standard library. Sessions and invite links are random tokens and only
their SHA-256 is stored, so a copy of the database cannot be used to sign in.

Roles, least to most: a **viewer** asks chatbots questions; an **editor** also creates, configures, builds and
shares them and reads activity; an **owner** also manages people, reads the audit log and deletes chatbots.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

ROLES = ("viewer", "editor", "owner")
SESSION_DAYS = 14
INVITE_DAYS = 7
MIN_PASSWORD = 10
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}

# Everything a viewer may call; anything not listed needs an editor (or an owner, below).
_VIEWER = re.compile(
    r"GET /api/kbs(/[^/]+(/suggestions)?)?"
    r"|POST /api/kbs/[^/]+/chat"
    r"|DELETE /api/kbs/[^/]+/sessions/[^/]+"
    r"|GET /api/system/(health|ready|capabilities)"
)
_OWNER = re.compile(r"[A-Z]+ /api/(users|audit)(/.*)?|DELETE /api/kbs/[^/]+")


@dataclass(frozen=True)
class Identity:
    """Who is making a request: a signed-in person, the admin API key, or anyone on an open local install."""

    name: str
    role: str
    user_id: str | None = None


def rank(role: str) -> int:
    return ROLES.index(role)


def required_role(method: str, path: str) -> str:
    request = f"{method} {path.rstrip('/')}"
    if _OWNER.fullmatch(request):
        return "owner"
    return "viewer" if _VIEWER.fullmatch(request) else "editor"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    scheme, _, rest = (stored or "").partition("$")
    salt, _, digest = rest.partition("$")
    if scheme != "scrypt" or not salt or not digest:
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(candidate.hex(), digest)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    return None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """A random token for a cookie or link, and the hash that is stored."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)
