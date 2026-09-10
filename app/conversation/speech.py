from __future__ import annotations

import html
import re
import unicodedata


_MARKDOWN_LINK = re.compile(r"\[([^\]]+)]\([^)]*\)")
_ELLIPSIS = re.compile(r"(?:\.{2,}|…+)")
_REPEATED_PUNCTUATION = re.compile(r"([,!?:;])(?:\s*\1)+")
_MARKUP = str.maketrans("", "", "*_#`~\"'“”‘’")
_BRACKETS = str.maketrans({character: " " for character in "[]{}()<>|\\/"})
_SPOKEN_SYMBOLS = str.maketrans({"&": " và ", "%": " phần trăm ", "+": " cộng ", "=": " bằng "})
_PROSODY = frozenset(".,!?;:")


def prepare_for_speech(text: str) -> str:
    """Keep words and useful prosody while removing markup TTS may pronounce."""

    normalized = unicodedata.normalize("NFC", html.unescape(text))
    normalized = _MARKDOWN_LINK.sub(r"\1", normalized)
    normalized = re.sub(r"\s*\n+\s*", ". ", normalized)
    normalized = _ELLIPSIS.sub(".", normalized)
    normalized = normalized.translate(_SPOKEN_SYMBOLS).translate(_MARKUP).translate(_BRACKETS)

    safe: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if character.isspace() or character in _PROSODY or category[0] in {"L", "M", "N"}:
            safe.append(character)
        else:
            safe.append(" ")

    spoken = "".join(safe)
    spoken = _REPEATED_PUNCTUATION.sub(r"\1", spoken)
    spoken = re.sub(r"\s+([,.!?;:])", r"\1", spoken)
    spoken = re.sub(r"([,.!?;:])(?=[^\s,.!?;:])", r"\1 ", spoken)
    return " ".join(spoken.split()).strip(" ,;:")
