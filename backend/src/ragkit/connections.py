"""Model connections: Claude, OpenAI, Gemini, Ollama or any OpenAI-compatible server, added in the app.

API keys are encrypted at rest (Fernet) with a key from ``RAG_SECRET_KEY`` or, if unset, one generated into the
state folder. Backups leave that key out, so a restored install asks for provider keys again instead of shipping
them in the backup file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

from ragkit.settings import Settings

Kind = Literal["anthropic", "openai", "gemini", "ollama", "compatible"]


@dataclass(frozen=True)
class KindInfo:
    label: str
    base_url: str  # empty: the person must give one
    needs_key: bool
    chat: bool = True
    embeddings: bool = True


KINDS: dict[str, KindInfo] = {
    "anthropic": KindInfo("Claude", "https://api.anthropic.com", True, embeddings=False),
    "openai": KindInfo("OpenAI", "https://api.openai.com/v1", True),
    "gemini": KindInfo("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", True),
    "ollama": KindInfo("Ollama", "http://localhost:11434", False),
    "compatible": KindInfo("OpenAI-compatible server", "", False),
}


def _fernet(settings: Settings) -> Fernet:
    if settings.secret_key:
        return Fernet(settings.secret_key.encode())
    path = settings.state_dir / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # readable by Archie's user only
        except FileExistsError:
            pass  # another Archie process made it first
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
    return Fernet(Path(path).read_bytes().strip())


def encrypt(settings: Settings, secret: str) -> str:
    return _fernet(settings).encrypt(secret.encode()).decode()


def decrypt(settings: Settings, token: str) -> str | None:
    """The stored secret, or None when it was encrypted with another key (a restore onto a new machine)."""
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def masked(secret: str) -> str:
    return f"…{secret[-4:]}" if len(secret) > 8 else "…"
