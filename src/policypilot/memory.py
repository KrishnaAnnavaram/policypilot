"""Per-user conversation memory with server-generated, unguessable session IDs."""
from __future__ import annotations

import re
import threading
import uuid
from collections import OrderedDict, deque

_SESSION_RE = re.compile(r"^[0-9a-f]{32}$")


def new_session_id() -> str:
    return uuid.uuid4().hex


def is_valid_session_id(value: str | None) -> bool:
    return bool(value) and bool(_SESSION_RE.match(value or ""))


class SessionStore:
    """Bounded in-process store: at most ``max_sessions`` sessions (LRU) and ``max_turns`` turns each."""

    def __init__(self, max_sessions: int = 1000, max_turns: int = 6):
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self._data: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()

    def history(self, session_id: str) -> list[tuple[str, str]]:
        with self._lock:
            turns = self._data.get(session_id)
            return list(turns) if turns else []

    def add(self, session_id: str, question: str, answer: str) -> None:
        if not is_valid_session_id(session_id):
            raise ValueError("invalid session id")
        with self._lock:
            turns = self._data.pop(session_id, None) or deque(maxlen=self.max_turns)
            turns.append((question, answer))
            self._data[session_id] = turns
            while len(self._data) > self.max_sessions:
                self._data.popitem(last=False)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)
