"""Pull a SQL statement or a JSON value out of free-form LLM output.

JSON is located with :meth:`json.JSONDecoder.raw_decode` starting at each opening
bracket, so nested arrays such as ``{"$in": [1, 2]}`` inside a pipeline are parsed
completely (a lazy regex like ``\\[.*?\\]`` stops at the first ``]``). Every failure
raises :class:`ExtractionError` with a message that can be fed back to the model.
"""
from __future__ import annotations

import json
import re

_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n?(.*?)```", re.DOTALL)
_SQL_START = re.compile(r"\b(SELECT|WITH)\b", re.IGNORECASE)


class ExtractionError(ValueError):
    pass


def strip_reasoning(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _fenced_blocks(text: str) -> list[tuple[str, str]]:
    return [(m.group(1).lower(), m.group(2).strip()) for m in _FENCE.finditer(text)]


def extract_sql(text: str) -> str:
    text = strip_reasoning(text)
    if not text:
        raise ExtractionError("the model returned an empty response; return one SQL SELECT statement")
    blocks = _fenced_blocks(text)
    candidates = [b for lang, b in blocks if lang in {"sql", "postgresql", "sqlite"}] or [b for _, b in blocks]
    body = candidates[0] if candidates else text
    match = _SQL_START.search(body)
    if not match:
        raise ExtractionError("no SELECT statement found; return exactly one SQL SELECT statement")
    return body[match.start():].strip().rstrip(";").strip()


def _decode_from(text: str, opener: str, want: type) -> object:
    decoder = json.JSONDecoder()
    idx = text.find(opener)
    while idx != -1:
        try:
            value, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(value, want):
                return value
        idx = text.find(opener, idx + 1)
    raise ExtractionError(f"no valid JSON {want.__name__} found")


def _candidates(text: str) -> list[str]:
    text = strip_reasoning(text)
    if not text:
        raise ExtractionError("the model returned an empty response")
    return [b for _, b in _fenced_blocks(text)] + [text]


def extract_json_object(text: str) -> dict:
    for candidate in _candidates(text):
        try:
            return _decode_from(candidate, "{", dict)  # type: ignore[return-value]
        except ExtractionError:
            continue
    raise ExtractionError("no valid JSON object found; reply with a single JSON object")


def extract_pipeline(text: str) -> list:
    """Return an aggregation pipeline from ``[...]``, ``{"pipeline": [...]}`` or ``db.x.aggregate([...])``."""
    for candidate in _candidates(text):
        stripped = candidate.lstrip()
        if stripped.startswith("{"):
            try:
                obj = _decode_from(stripped, "{", dict)
            except ExtractionError:
                obj = None
            if isinstance(obj, dict) and isinstance(obj.get("pipeline"), list):
                return obj["pipeline"]
        try:
            return _decode_from(candidate, "[", list)  # type: ignore[return-value]
        except ExtractionError:
            continue
    raise ExtractionError(
        "no valid JSON aggregation pipeline found; reply with a JSON array of stages, "
        'e.g. [{"$match": {"car_use": "Commercial"}}, {"$count": "n"}] (double-quoted keys)'
    )
