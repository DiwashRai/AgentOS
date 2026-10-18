"""Context sync (SPEC §9.4.1): `SyncState` knows what this run has sent of its context, so
each `sync` record carries only what changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentos_worker.blocks import BlockError, block_read
from agentos_worker.files import read_regular, walk_files
from agentos_worker.package import ContextFile, Package
from agentos_worker.text import decode_utf8


@dataclass
class SyncBatch:
    files: list[dict[str, Any]]
    warnings: list[str]


class SyncState:
    """What this run has sent of its context (`known`), so sync sends only what changed.

    After each stage it reads exactly the write context entries and the notes (block files: only
    the block) and sends those that changed since they were last sent; a deleted replace file goes
    as `content: null`. Nothing else is sent: other files under `workspace.protect` are named in
    one "not kept" warning.
    """

    def __init__(self, package: Package) -> None:
        self.package = package
        self.sync_files = {f.path: f for f in package.files if f.access == "write"}
        self.declared_lower = {f.path.lower() for f in package.files}
        self.known: dict[str, str | None] = {p: f.content for p, f in self.sync_files.items()}
        self.unsynced: set[str] = set()  # declared files that couldn't be written
        self.rewritten: set[str] = set()  # notes already warned about

    def _declared_content(self, f: ContextFile, data: bytes) -> tuple[str, str | None]:
        """(content to sync, why not) for a declared write file's bytes."""
        limit = self.package.limits.max_file_bytes
        if f.mode == "block":
            try:
                content = block_read(data, str(f.block))
            except BlockError as exc:
                return "", str(exc)
        else:
            decoded = decode_utf8(data)
            if decoded is None:
                return "", "is not valid UTF-8"
            content = decoded
        if len(content.encode("utf-8")) > limit:
            return "", f"is over {limit} bytes"
        return content, None

    def collect(self, root: Path) -> SyncBatch:
        batch = SyncBatch([], [])
        for path, f in self.sync_files.items():
            if path in self.unsynced:
                continue
            data, err = read_regular(root, path)
            if err:
                batch.warnings.append(f"not synced: {path} {err}")
                continue
            if data is None:
                if f.mode == "block":
                    batch.warnings.append(f"not synced: {path} is gone, and its agentos block too")
                elif self.known.get(path) is not None:
                    batch.files.append({"path": path, "content": None, "mode": "replace"})
                    self.known[path] = None
                continue
            content, err = self._declared_content(f, data)
            if err:
                batch.warnings.append(f"not synced: {path}: {err}")
                continue
            if (
                f.kind == "note"
                and path not in self.rewritten
                and not content.startswith(f.content)
            ):
                self.rewritten.add(path)
                batch.warnings.append(f"{path} was rewritten, not appended to")
            if self.known.get(path) != content:
                batch.files.append({"path": path, "content": content, "mode": f.mode})
                self.known[path] = content

        not_kept: list[str] = []
        for folder in self.package.workspace.protect:
            files, skipped = walk_files(root, folder)
            not_kept += [p for p in files if p.lower() not in self.declared_lower] + skipped
        if not_kept:
            more = f" and {len(not_kept) - 20} more" if len(not_kept) > 20 else ""
            batch.warnings.append(f"not kept: {', '.join(not_kept[:20])}{more}")
        return batch
