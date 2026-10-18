"""Block mode (SPEC §6): a context file's agentos block, read from and written into a file's
bytes.

A block file holds, somewhere in it, exactly these bytes (written with LF):
    <!-- agentos:begin KEY -->\n{content}\n<!-- agentos:end KEY -->\n
The read-back is the bytes between "begin line + its newline" and "newline + end line", so any
content (LF, CRLF, no trailing newline, empty) round-trips byte for byte. If an editor converts
the whole file to CRLF, the separator newline is taken from the begin line. Missing or malformed
markers are a BlockError.
"""

from __future__ import annotations

from agentos_worker.text import decode_utf8


class BlockError(ValueError):
    """A block-mode file whose agentos markers are missing or malformed."""


def block_markers(key: str) -> tuple[bytes, bytes]:
    return f"<!-- agentos:begin {key} -->".encode(), f"<!-- agentos:end {key} -->".encode()


def _marker_lines(data: bytes, marker: bytes) -> list[tuple[int, int]]:
    """(start, stop) of every line that is exactly `marker`; `stop` includes the newline."""
    found: list[tuple[int, int]] = []
    i = data.find(marker)
    while i >= 0:
        j = i + len(marker)
        if i == 0 or data[i - 1] == 0x0A:
            if data.startswith(b"\r\n", j):
                found.append((i, j + 2))
            elif data.startswith(b"\n", j):
                found.append((i, j + 1))
            elif j == len(data):
                found.append((i, j))
        i = data.find(marker, i + 1)
    return found


def _block_span(data: bytes, key: str) -> tuple[int, int, int, int] | None:
    """(block start, content start, content end, block stop) for `key`; None if no markers."""
    begin, end = block_markers(key)
    begins, ends = _marker_lines(data, begin), _marker_lines(data, end)
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1:
        raise BlockError("expected exactly one begin and one end marker line")
    (b_start, b_stop), (e_start, e_stop) = begins[0], ends[0]
    if e_start < b_stop:
        raise BlockError("the end marker comes before the begin marker")
    inner = data[b_stop:e_start]
    if not inner:
        raise BlockError("no newline between the markers")
    newline = b"\r\n" if data[b_stop - 2 : b_stop] == b"\r\n" else b"\n"
    sep = newline if inner.endswith(newline) else b"\n"
    return b_start, b_stop, e_start - len(sep), e_stop


def block_read(data: bytes, key: str) -> str:
    """The block content for `key` in a file's bytes. Raises BlockError if missing/malformed."""
    span = _block_span(data, key)
    if span is None:
        raise BlockError("agentos markers are missing")
    text = decode_utf8(data[span[1] : span[2]])
    if text is None:
        raise BlockError("the block is not valid UTF-8")
    return text


def block_write(existing: bytes | None, key: str, content: str) -> bytes:
    """`existing` with any stale block for `key` removed and a fresh block appended."""
    begin, end = block_markers(key)
    body = content.encode("utf-8")
    if _marker_lines(body, begin) or _marker_lines(body, end):
        raise BlockError("the content contains an agentos marker line")
    data = existing or b""
    span = _block_span(data, key)
    if span is not None:
        start, stop = span[0], span[3]
        # Drop the separator newline this function put before the block.
        if data[start - 2 : start] == b"\r\n":
            start -= 2
        elif start > 0 and data[start - 1] == 0x0A:
            start -= 1
        data = data[:start] + data[stop:]
    block = begin + b"\n" + body + b"\n" + end + b"\n"
    return data + b"\n" + block if data else block
