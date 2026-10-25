"""Bounded per-session conversation memory: recent turns and the user's profile.

Bounded in both size (turns per session) and count (LRU over sessions, idle TTL).
shortcut: in-process; move to Redis or the database when running several API replicas.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field


@dataclass
class Session:
    turns: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=50))
    last_seen: float = field(default_factory=time.monotonic)


class SessionMemory:
    def __init__(self, max_sessions: int = 2000, ttl_s: float = 12 * 3600) -> None:
        self.max_sessions = max_sessions
        self.ttl_s = ttl_s
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def turns(self, session_id: str, limit: int) -> list[tuple[str, str]]:
        session = self._touch(session_id, create=False)
        return list(session.turns)[-limit:] if session else []

    def add_turn(self, session_id: str, question: str, answer: str) -> None:
        session = self._touch(session_id, create=True)
        assert session is not None
        session.turns.append((question, answer))

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)

    def _touch(self, session_id: str, create: bool) -> Session | None:
        now = time.monotonic()
        while self._sessions:
            oldest_id, oldest = next(iter(self._sessions.items()))
            if now - oldest.last_seen <= self.ttl_s:
                break
            del self._sessions[oldest_id]
        session = self._sessions.get(session_id)
        if session is None:
            if not create:
                return None
            session = self._sessions[session_id] = Session()
            if len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
        session.last_seen = now
        self._sessions.move_to_end(session_id)
        return session
