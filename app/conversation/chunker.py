from __future__ import annotations

import re

from app.config import ChunkerConfig


_STRONG_BOUNDARY = re.compile(r"[.!?;:\n]+[\"'”’)]*\s*")
_COMMA_BOUNDARY = re.compile(r"[,，]\s+")
_SPACE_BOUNDARY = re.compile(r"\s+")


class StreamingTextChunker:
    def __init__(self, config: ChunkerConfig) -> None:
        self.config = config
        self._buffer = ""

    def push(self, text: str) -> list[str]:
        self._buffer += text
        chunks: list[str] = []
        while True:
            boundary = self._select_boundary()
            if boundary is None:
                break
            chunk = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if chunk:
                chunks.append(chunk)
        return chunks

    def flush(self) -> list[str]:
        chunk = self._buffer.strip()
        self._buffer = ""
        return [chunk] if chunk else []

    def _select_boundary(self) -> int | None:
        if len(self._buffer) < self.config.min_chars:
            return None
        strong = [m.end() for m in _STRONG_BOUNDARY.finditer(self._buffer)]
        usable = [end for end in strong if self.config.min_chars <= end <= self.config.max_chars]
        if usable:
            return usable[0]
        if len(self._buffer) < self.config.preferred_chars:
            return None
        commas = [m.end() for m in _COMMA_BOUNDARY.finditer(self._buffer) if m.end() <= self.config.max_chars]
        if commas:
            return commas[-1]
        if len(self._buffer) < self.config.max_chars:
            return None
        spaces = [m.end() for m in _SPACE_BOUNDARY.finditer(self._buffer[: self.config.max_chars + 1])]
        return spaces[-1] if spaces else self.config.max_chars
