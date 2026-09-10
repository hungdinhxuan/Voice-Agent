from __future__ import annotations

import html
import re
import unicodedata


_MARKDOWN_LINK = re.compile(r"\[([^\]]+)]\([^)]*\)")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_UNIT = re.compile(r"(?<=\d)\s*(kg|km|cm|mm|gb|mb|ghz|mhz|°c)\b", re.IGNORECASE)
_ELLIPSIS = re.compile(r"(?:\.{2,}|…+)")
_REPEATED_PUNCTUATION = re.compile(r"([,!?:;])(?:\s*\1)+")
_MARKUP = str.maketrans("", "", "*_#`~\"'“”‘’")
_BRACKETS = str.maketrans({character: " " for character in "[]{}()<>|\\/"})
_SPOKEN_SYMBOLS = str.maketrans({"&": " và ", "%": " phần trăm ", "+": " cộng ", "=": " bằng "})
_SPOKEN_SYMBOLS_EN = str.maketrans(
    {"&": " and ", "%": " percent ", "+": " plus ", "=": " equals "}
)
_PROSODY = frozenset(".,!?;:")
_UNITS = {
    "kg": " ki lô gam",
    "km": " ki lô mét",
    "cm": " xen ti mét",
    "mm": " mi li mét",
    "gb": " gi ga bai",
    "mb": " mê ga bai",
    "ghz": " gi ga héc",
    "mhz": " mê ga héc",
    "°c": " độ xê",
}
_ABBREVIATIONS = {
    "AI": "ây ai",
    "ASR": "ây ét a",
    "CPU": "si pi iu",
    "GPU": "gi pi iu",
    "LLM": "eo eo em",
    "TTS": "ti ti ét",
}


def prepare_for_speech(text: str, language: str = "vi") -> str:
    """Keep words and useful prosody while removing markup TTS may pronounce."""

    normalized = unicodedata.normalize("NFC", html.unescape(text))
    normalized = _MARKDOWN_LINK.sub(r"\1", normalized)
    if language == "en":
        normalized = _EMAIL.sub(" email address ", normalized)
        normalized = _URL.sub(" link ", normalized)
        spoken_symbols = _SPOKEN_SYMBOLS_EN
    else:
        normalized = _EMAIL.sub(" địa chỉ email ", normalized)
        normalized = _URL.sub(" đường dẫn ", normalized)
        normalized = _DATE.sub(r"ngày \1 tháng \2 năm \3", normalized)
        normalized = _UNIT.sub(lambda match: _UNITS[match.group(1).casefold()], normalized)
        normalized = re.sub(r"\$(\d[\d.,]*)", r"\1 đô la", normalized)
        for abbreviation, spoken in _ABBREVIATIONS.items():
            normalized = re.sub(rf"\b{abbreviation}\b", spoken, normalized)
        spoken_symbols = _SPOKEN_SYMBOLS
    normalized = re.sub(r"\s*\n+\s*", ". ", normalized)
    normalized = _ELLIPSIS.sub(".", normalized)
    normalized = normalized.translate(spoken_symbols).translate(_MARKUP).translate(_BRACKETS)

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
