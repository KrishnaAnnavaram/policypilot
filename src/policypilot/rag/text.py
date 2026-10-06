"""Language-aware text utilities: tokenization, language detection and chunking."""
from __future__ import annotations

import re
from dataclasses import dataclass

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+|(?<=[。！？])")
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its my of on or the "
    "this to was what when where which who will with you your".split()
)


def detect_language(text: str) -> str:
    """Very small heuristic: 'zh' when CJK characters dominate letters, else 'en'."""
    cjk = len(_CJK.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk and cjk >= latin / 3 else "en"


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens (minus stop words) plus CJK character unigrams and bigrams."""
    lowered = text.lower()
    tokens = [w for w in _WORD.findall(lowered) if w not in STOPWORDS]
    for run in re.findall(r"[㐀-䶿一-鿿豈-﫿]+", text):
        tokens.extend(run)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    source: str
    text: str
    language: str
    position: int


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]


def chunk_text(text: str, size: int = 700, overlap: int = 120) -> list[str]:
    """Pack whole sentences into chunks of at most ``size`` characters, carrying ``overlap``
    characters of trailing context into the next chunk. Paragraph breaks are respected."""
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    chunks: list[str] = []
    heading = ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        if paragraph.startswith("#"):                  # keep Markdown headings with their section
            heading = paragraph.lstrip("#").strip()
            continue
        if heading:
            end = "" if heading[-1:] in ".!?:。" else ("。" if detect_language(heading) == "zh" else ".")
            paragraph = f"{heading}{end} {paragraph}"
            heading = ""
        current = ""
        for sentence in split_sentences(paragraph):
            while len(sentence) > size:                       # hard-split very long sentences
                head, sentence = sentence[:size], sentence[size - overlap:]
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(head)
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= size:
                current = candidate
            else:
                chunks.append(current)
                tail = current[-overlap:] if overlap else ""
                current = f"{tail} {sentence}".strip() if tail else sentence
                if len(current) > size:
                    current = sentence
        if current:
            chunks.append(current)
    if heading:
        chunks.append(heading)
    return chunks
