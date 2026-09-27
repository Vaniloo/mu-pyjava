"""Workspace tools. Mutating tools require explicit launch flags."""

import json
import os
import fnmatch
import signal
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List


TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files under a directory in the workspace.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read UTF-8 text, optionally from a 1-based line offset with a line limit. Large files can be read in pages.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"},
            }, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find workspace files by glob, honoring .gitignore. Returns up to 200 paths.",
            "parameters": {
                "type": "object", "properties": {
                    "path": {"type": "string"}, "glob": {"type": "string"},
                }, "required": ["path", "glob"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "Search files with regex or literal text, honoring .gitignore. Returns up to 100 matches.",
            "parameters": {
                "type": "object", "properties": {
                    "path": {"type": "string"}, "pattern": {"type": "string"},
                    "glob": {"type": "string"}, "literal": {"type": "boolean"},
                    "ignore_case": {"type": "boolean"}, "context": {"type": "integer"},
                    "limit": {"type": "integer"},
                }, "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show short Git status for the workspace. Does not require command permission.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show Git diff for a workspace path, optionally staged. Does not require command permission.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "staged": {"type": "boolean"},
            }},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file in the workspace. Requires write permission.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exactly one occurrence of old_text in a UTF-8 file. Requires write permission.",
            "parameters": {
                "type": "object", "properties": {
                    "path": {"type": "string"}, "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                }, "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run one command in the workspace without a shell. Requires command permission. Output is bounded.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
                "required": ["command"],
            },
        },
    },
]


class WorkspaceTools:
    def __init__(self, root: Path, allow_write: bool = False, allow_command: bool = False):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace must be a directory")
        self.allow_write = allow_write
        self.allow_command = allow_command

    def _path(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Path is required")
        target = (self.root / raw).resolve()
        if target != self.root and self.root not in target.parents:
            raise PermissionError("Path is outside the workspace")
        return target

    @staticmethod
    def _matches_glob(path: str, pattern: str) -> bool:
        return fnmatch.fnmatch(path, pattern) or (pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]))

    def _git(self, args):
        result = subprocess.run(["git", "--no-pager", "-c", "core.quotePath=false", *args],
                                cwd=self.root, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise ValueError("Git failed: " + result.stderr.strip()[:500])
        return result.stdout[:12_000]

    @staticmethod
    def _positive_int(value, name: str, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}")
        return value

    def _rg_lines(self, args, max_lines: int, max_bytes: int = 120_000):
        executable = shutil.which("rg")
        if executable is None:
            raise ValueError("ripgrep (rg) is required for find_files and grep_files")
        process = subprocess.Popen([executable, *args], cwd=self.root, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        timed_out = threading.Event()

        def kill_on_timeout():
            if process.poll() is None:
                timed_out.set()
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

        timer = threading.Timer(20, kill_on_timeout)
        timer.start()
        lines = []
        size = 0
        truncated = False
        try:
            for line in process.stdout:
                encoded_size = len(line.encode("utf-8"))
                if len(lines) >= max_lines or size + encoded_size > max_bytes:
                    truncated = True
                    process.kill()
                    break
                lines.append(line)
                size += encoded_size
            process.wait()
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
        if timed_out.is_set():
            raise subprocess.TimeoutExpired(args, 20)
        if not truncated and process.returncode not in {0, 1}:
            raise ValueError("ripgrep failed; check the search pattern and path")
        return lines, truncated

    def _search_files(self, base: Path, pattern: str):
        relative = str(base.relative_to(self.root)) or "."
        lines, incomplete = self._rg_lines(["--files", "--hidden", "-g", "!.git", "--", relative],
                                           10_000, 1_000_000)
        files = []
        for line in lines:
            path = (self.root / line.strip()).resolve()
            if path != self.root and self.root not in path.parents:
                continue
            local = str(path.relative_to(base)) if base.is_dir() else path.name
            if self._matches_glob(local, pattern):
                files.append(str(path.relative_to(self.root)))
        return files, incomplete

    def _read_page(self, path: Path, offset: int, limit: int) -> str:
        lines = []
        byte_count = 0
        last_line = 0
        more = False
        with path.open(encoding="utf-8") as file:
            for number, line in enumerate(file, 1):
                last_line = number
                if number < offset:
                    continue
                line_bytes = len(line.encode("utf-8"))
                if len(lines) >= limit or byte_count + line_bytes > 50 * 1024:
                    more = True
                    break
                lines.append(line)
                byte_count += line_bytes
        if last_line == 0 and offset == 1:
            return ""
        if last_line < offset:
            raise ValueError(f"Offset {offset} is beyond end of file")
        if not lines:
            raise ValueError(f"Line {offset} exceeds the 50 KiB output limit")
        content = "".join(lines)
        if more:
            return content + ("" if content.endswith("\n") else "\n") + f"[More lines available; use offset={offset + len(lines)}]"
        return content

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        if name == "list_files":
            target = self._path(arguments["path"])
            if not target.is_dir():
                raise ValueError("Not a directory")
            return json.dumps(sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())[:200])
        if name == "read_file":
            target = self._path(arguments["path"])
            offset = self._positive_int(arguments.get("offset", 1), "offset", 10_000_000)
            limit = self._positive_int(arguments.get("limit", 2000), "limit", 2000)
            return self._read_page(target, offset, limit)
        if name == "find_files":
            pattern = arguments["glob"]
            if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
                raise ValueError("Glob must be a nonempty string of at most 200 characters")
            base = self._path(arguments["path"])
            if not base.exists():
                raise ValueError("Search path does not exist")
            paths, incomplete = self._search_files(base, pattern)
            shown = []
            size = 0
            for path in paths[:200]:
                size += len(path.encode("utf-8")) + 4
                if size > 10_000:
                    break
                shown.append(path)
            return json.dumps({"paths": shown, "truncated": incomplete or len(shown) < len(paths)}, ensure_ascii=False)
        if name == "grep_files":
            pattern = arguments["pattern"]
            file_glob = arguments.get("glob", "**/*")
            if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
                raise ValueError("Pattern must be nonempty text of at most 200 characters")
            if not isinstance(file_glob, str) or not file_glob or len(file_glob) > 200:
                raise ValueError("Glob must be a nonempty string of at most 200 characters")
            literal = arguments.get("literal", False)
            ignore_case = arguments.get("ignore_case", False)
            if not isinstance(literal, bool) or not isinstance(ignore_case, bool):
                raise ValueError("literal and ignore_case must be booleans")
            context = arguments.get("context", 0)
            if isinstance(context, bool) or not isinstance(context, int) or not 0 <= context <= 5:
                raise ValueError("context must be an integer from 0 to 5")
            limit = self._positive_int(arguments.get("limit", 100), "limit", 100)
            base = self._path(arguments["path"])
            if not base.exists():
                raise ValueError("Search path does not exist")
            paths, incomplete = self._search_files(base, file_glob)
            args = ["--json", "--sort", "path", "--max-filesize", "1M", "--max-columns", "500", "--max-columns-preview"]
            if literal:
                args.append("--fixed-strings")
            if ignore_case:
                args.append("--ignore-case")
            if context:
                args.extend(["--context", str(context)])
            matches = []
            match_count = 0
            truncated = incomplete
            output_bytes = 0
            output_full = False
            for start in range(0, len(paths), 100):
                lines, batch_truncated = self._rg_lines(
                    [*args, "--", pattern, *paths[start:start + 100]],
                    min(500, (limit - match_count) * (2 * context + 3) + 10),
                    10_000,
                )
                truncated = truncated or batch_truncated
                for line in lines:
                    item = json.loads(line)
                    if item.get("type") not in {"match", "context"}:
                        continue
                    data = item["data"]
                    path = data["path"].get("text")
                    content = data["lines"].get("text")
                    if path is None or content is None:
                        continue
                    kind = item["type"]
                    if kind == "match":
                        if match_count >= limit:
                            truncated = True
                            break
                    record = {"path": str(Path(path)), "line": data["line_number"],
                              "text": content.rstrip("\r\n")[:500], "kind": kind}
                    size = len(json.dumps(record, ensure_ascii=False).encode("utf-8")) + 2
                    if output_bytes + size > 9_000:
                        truncated = True
                        output_full = True
                        break
                    output_bytes += size
                    matches.append(record)
                    if kind == "match":
                        match_count += 1
                if output_full or match_count >= limit:
                    truncated = truncated or start + 100 < len(paths)
                    break
            return json.dumps({"matches": matches, "truncated": truncated}, ensure_ascii=False)
        if name == "git_status":
            return self._git(["status", "--short", "--untracked-files=normal", "--", "."])
        if name == "git_diff":
            raw_path = arguments.get("path", ".")
            selected = self._path(raw_path)
            staged = arguments.get("staged", False)
            if not isinstance(staged, bool):
                raise ValueError("staged must be a boolean")
            path = str(selected.relative_to(self.root)) or "."
            return self._git(["diff", "--no-ext-diff", *(["--staged"] if staged else []), "--", path])
        if name == "write_file":
            if not self.allow_write:
                raise PermissionError("Writing is disabled; restart with --allow-write")
            target = self._path(arguments["path"])
            content = arguments["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > 1_000_000:
                raise ValueError("Content must be text of at most 1 MB")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return "Wrote " + str(target.relative_to(self.root))
        if name == "edit_file":
            if not self.allow_write:
                raise PermissionError("Writing is disabled; restart with --allow-write")
            target = self._path(arguments["path"])
            old_text, new_text = arguments["old_text"], arguments["new_text"]
            if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
                raise ValueError("old_text must be nonempty and new_text must be text")
            if target.stat().st_size > 1_000_000:
                raise ValueError("File exceeds the 1 MB edit limit")
            content = target.read_text(encoding="utf-8")
            if content.count(old_text) != 1:
                raise ValueError("old_text must occur exactly once")
            updated = content.replace(old_text, new_text, 1)
            if len(updated.encode("utf-8")) > 1_000_000:
                raise ValueError("Edited file exceeds the 1 MB limit")
            target.write_text(updated, encoding="utf-8")
            return "Edited " + str(target.relative_to(self.root))
        if name == "run_command":
            if not self.allow_command:
                raise PermissionError("Commands are disabled; restart with --allow-command")
            command = arguments["command"]
            if not isinstance(command, str) or not command.strip():
                raise ValueError("Command is required")
            timeout = self._positive_int(arguments.get("timeout", 30), "timeout", 120)
            argv = shlex.split(command, posix=os.name != "nt")
            process = subprocess.Popen(argv, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
            chunks = []
            captured = 0
            truncated = False

            def drain():
                nonlocal captured, truncated
                try:
                    while True:
                        chunk = process.stdout.read(4096)
                        if not chunk:
                            break
                        room = max(0, 12_000 - captured)
                        if len(chunk) > room:
                            truncated = True
                        if room:
                            kept = chunk[:room]
                            chunks.append(kept)
                            captured += len(kept)
                except (OSError, ValueError):
                    pass

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()
                reader.join(timeout=1)
                process.stdout.close()
                raise
            reader.join(timeout=1)
            process.stdout.close()
            output = b"".join(chunks).decode("utf-8", errors="replace")
            if truncated:
                output += "\n[Output truncated at 12 KB]"
            return "Exit code: " + str(process.returncode) + "\n" + output
        raise ValueError("Unknown tool: " + name)
