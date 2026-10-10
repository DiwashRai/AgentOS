"""Block mode (SPEC §6): byte-exact write/read round trip and stale-block replacement."""

from __future__ import annotations

import pytest

import worker

KEY = "AOS-42"
BEGIN, END = worker.block_markers(KEY)

CONTENTS = {
    "lf": "line one\nline two\n",
    "crlf": "line one\r\nline two\r\n",
    "no_trailing_newline": "line one\nline two",
    "crlf_no_trailing_newline": "line one\r\nline two",
    "empty": "",
    "only_newline": "\n",
    "blank_lines_around": "\n\nmiddle\n\n",
    "lone_cr_at_end": "text\r",
    "unicode": "café ✓ 日本\n",
}
EXISTING = {
    "absent": None,
    "empty_file": b"",
    "lf_file": b"# Repo agents\nBe nice.\n",
    "crlf_file": b"# Repo agents\r\nBe nice.\r\n",
    "no_trailing_newline_file": b"# Repo agents",
}


@pytest.mark.parametrize("content", CONTENTS.values(), ids=CONTENTS.keys())
@pytest.mark.parametrize("existing", EXISTING.values(), ids=EXISTING.keys())
def test_round_trip_is_byte_exact(existing: bytes | None, content: str) -> None:
    written = worker.block_write(existing, KEY, content)
    assert worker.block_read(written, KEY) == content
    # Writing again (a stale block for this key) replaces it and changes nothing else.
    assert worker.block_write(written, KEY, content) == written
    # The rest of the file survives untouched.
    assert written.startswith(existing or b"")


def test_new_file_holds_only_the_block() -> None:
    assert worker.block_write(None, KEY, "hi") == BEGIN + b"\nhi\n" + END + b"\n"


def test_appends_after_existing_content_with_a_separator() -> None:
    assert worker.block_write(b"top\n", KEY, "x") == b"top\n\n" + BEGIN + b"\nx\n" + END + b"\n"


def test_stale_block_is_replaced_and_surroundings_kept() -> None:
    first = worker.block_write(b"top\n", KEY, "old")
    edited = first + b"agent added this\n"
    second = worker.block_write(edited, KEY, "new")
    assert worker.block_read(second, KEY) == "new"
    assert second.startswith(b"top\n")
    assert b"agent added this\n" in second
    assert b"old" not in second


def test_edits_outside_the_block_do_not_change_the_read_back() -> None:
    written = worker.block_write(b"top\n", KEY, "keep\r\nme")
    edited = b"prepended\n" + written + b"appended without newline"
    assert worker.block_read(edited, KEY) == "keep\r\nme"


def test_whole_file_converted_to_crlf_reads_back_the_converted_content() -> None:
    written = worker.block_write(b"top\n", KEY, "a\nb")
    converted = written.replace(b"\n", b"\r\n")
    assert worker.block_read(converted, KEY) == "a\r\nb"


def test_other_keys_blocks_are_ignored() -> None:
    other = worker.block_write(None, "AOS-7", "theirs")
    mine = worker.block_write(other, KEY, "mine")
    assert worker.block_read(mine, KEY) == "mine"
    assert worker.block_read(mine, "AOS-7") == "theirs"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"no markers at all\n", id="missing"),
        pytest.param(BEGIN + b"\ncontent\n", id="no_end"),
        pytest.param(b"content\n" + END + b"\n", id="no_begin"),
        pytest.param(END + b"\nx\n" + BEGIN + b"\n", id="reversed"),
        pytest.param(BEGIN + b"\n" + END + b"\n", id="no_newline_between"),
        pytest.param(BEGIN + b"\na\n" + END + b"\n" + BEGIN + b"\nb\n" + END + b"\n", id="twice"),
        pytest.param(BEGIN + b"\n\xff\xfe\n" + END + b"\n", id="not_utf8"),
    ],
)
def test_missing_or_malformed_markers_raise(data: bytes) -> None:
    with pytest.raises(worker.BlockError):
        worker.block_read(data, KEY)


def test_markers_must_be_whole_lines() -> None:
    data = b"see " + BEGIN + b" inline\n"
    with pytest.raises(worker.BlockError, match="missing"):
        worker.block_read(data, KEY)


def test_content_containing_a_marker_line_is_refused() -> None:
    with pytest.raises(worker.BlockError):
        worker.block_write(None, KEY, "a\n" + END.decode() + "\nb")


def test_writing_over_a_malformed_block_is_refused() -> None:
    with pytest.raises(worker.BlockError):
        worker.block_write(BEGIN + b"\ndangling\n", KEY, "x")
