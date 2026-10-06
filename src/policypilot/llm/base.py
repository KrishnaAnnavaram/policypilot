"""The minimal LLM interface the agents depend on, plus a scripted fake for tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


class LLMError(RuntimeError):
    """Transport or provider failure (after the client's own bounded retries)."""


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str: ...


@dataclass
class Call:
    system: str
    user: str
    json_mode: bool


@dataclass
class ScriptedLLM:
    """Test double: returns queued responses in order, or delegates to a function.

    ``responder(system, user) -> str`` is used when the queue is empty; an exception
    instance in the queue is raised instead of returned.
    """

    responses: list = field(default_factory=list)
    responder: Callable[[str, str], str] | None = None
    name: str = "scripted"
    calls: list[Call] = field(default_factory=list)

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        self.calls.append(Call(system, user, json_mode))
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if self.responder is not None:
            return self.responder(system, user)
        raise LLMError("ScriptedLLM has no response left")
