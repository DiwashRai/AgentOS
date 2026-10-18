"""Text helpers: cutting a message short (`clip`) and decoding UTF-8 strictly
(`decode_utf8`: the text, or None when the bytes aren't UTF-8).
"""

from __future__ import annotations

MAX_ERROR_CHARS = 2000


def clip(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def decode_utf8(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
