"""Replaceable search and read-only Git transports."""

import codecs
import fnmatch
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Tuple

from .cancel import CancellationToken
from .command_ops import LocalCommandOperations


class SearchOperations(Protocol):
    def find(self, base: Path, pattern: str, cancel: Optional[CancellationToken]) -> Tuple[List[str], bool]: ...
    def grep(self, paths: List[str], pattern: str, literal: bool, ignore_case: bool,
             context: int, max_lines: int, cancel: Optional[CancellationToken]) -> Tuple[List[Dict], bool]: ...


class GitOperations(Protocol):
    def inspect(self, args: List[str], cancel: Optional[CancellationToken]) -> str: ...


class _SearchLimitReached(Exception):
    pass


class LocalSearchOperations:
    def __init__(self, root: Path):
        self.root = root
        self.commands = LocalCommandOperations(merge_stderr=False)

    def _rg_lines(self, args, max_lines, max_bytes, cancel):
        executable = shutil.which("rg")
        if executable is None:
            raise ValueError("ripgrep (rg) is required for find_files and grep_files")
        lines = []
        size = 0
        pending = ""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")

        def add_line(line):
            nonlocal size
            encoded_size = len(line.encode("utf-8"))
            if len(lines) >= max_lines or size + encoded_size > max_bytes:
                raise _SearchLimitReached()
            lines.append(line)
            size += encoded_size

        def feed(chunk):
            nonlocal pending
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                add_line(line + "\n")
            if len(pending.encode("utf-8")) > max_bytes:
                raise _SearchLimitReached()

        try:
            code = self.commands.execute([executable, *args], self.root, 20, cancel, feed)
            pending += decoder.decode(b"", final=True)
            if pending:
                add_line(pending)
        except _SearchLimitReached:
            return lines, True
        if code not in {0, 1}:
            raise ValueError("ripgrep failed; check the search pattern and path")
        return lines, False

    def find(self, base: Path, pattern: str, cancel: Optional[CancellationToken]):
        relative = str(base.relative_to(self.root)) or "."
        lines, incomplete = self._rg_lines(["--files", "--hidden", "-g", "!.git", "--", relative],
                                           10_000, 1_000_000, cancel)
        files = []
        for line in lines:
            if cancel is not None:
                cancel.raise_if_cancelled()
            path = (self.root / line.rstrip("\r\n")).resolve()
            if path != self.root and self.root not in path.parents:
                continue
            if base.is_dir():
                if path != base and base not in path.parents:
                    continue
                local = str(path.relative_to(base))
            else:
                local = path.name
            if fnmatch.fnmatch(local, pattern) or (pattern.startswith("**/") and fnmatch.fnmatch(local, pattern[3:])):
                files.append(str(path.relative_to(self.root)))
        return files, incomplete

    def grep(self, paths, pattern, literal, ignore_case, context, max_lines, cancel):
        args = ["--json", "--sort", "path", "--max-filesize", "1M", "--max-columns", "500", "--max-columns-preview"]
        if literal:
            args.append("--fixed-strings")
        if ignore_case:
            args.append("--ignore-case")
        if context:
            args.extend(["--context", str(context)])
        lines, truncated = self._rg_lines([*args, "--", pattern, *paths], max_lines, 10_000, cancel)
        records = []
        for line in lines:
            item = json.loads(line)
            if item.get("type") not in {"match", "context"}:
                continue
            data = item["data"]
            path, content = data["path"].get("text"), data["lines"].get("text")
            if path is not None and content is not None:
                records.append({"path": path, "line": data["line_number"],
                                "text": content.rstrip("\r\n"), "kind": item["type"]})
        return records, truncated


class LocalGitOperations:
    def __init__(self, root: Path):
        self.root = root
        self.commands = LocalCommandOperations()

    def inspect(self, args, cancel):
        output = bytearray()

        def feed(chunk):
            if len(output) < 48_000:
                output.extend(chunk[:48_000 - len(output)])

        code = self.commands.execute(["git", "--no-pager", "-c", "core.quotePath=false", *args],
                                     self.root, 30, cancel, feed)
        text = output.decode("utf-8", errors="replace")
        if code:
            raise ValueError("Git failed: " + text.strip()[:500])
        return text
