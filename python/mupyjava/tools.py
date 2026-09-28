"""Workspace tools. Mutating tools require explicit launch flags."""

import json
import codecs
import difflib
import hashlib
import os
import fnmatch
import signal
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .cancel import CancellationToken, TurnCancelled
from .file_ops import FileMutationQueue, FileOperations, LocalFileOperations


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
            "description": "Edit a UTF-8 file with one or more unique, non-overlapping replacements from the original content. Prefer edits for multiple changes; old_text/new_text remains supported. Requires write permission.",
            "parameters": {
                "type": "object", "properties": {
                    "path": {"type": "string"}, "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "edits": {"type": "array", "items": {"type": "object", "properties": {
                        "old_text": {"type": "string"}, "new_text": {"type": "string"},
                    }, "required": ["old_text", "new_text"]}},
                }, "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_command_output",
            "description": "Read a truncated command's full output by id. offset and limit are byte counts; continue at next_offset.",
            "parameters": {"type": "object", "properties": {
                "id": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"},
            }, "required": ["id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run one command in the workspace without a shell. Requires permission. Long output is kept as a retrievable artifact.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
                "required": ["command"],
            },
        },
    },
]


class WorkspaceTools:
    def __init__(self, root: Path, allow_write: bool = False, allow_command: bool = False,
                 output_root: Optional[Path] = None, file_ops: Optional[FileOperations] = None):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace must be a directory")
        self.allow_write = allow_write
        self.allow_command = allow_command
        self.output_root = output_root or Path(tempfile.gettempdir()) / "mupyjava-output"
        self.file_ops = file_ops or LocalFileOperations()
        self.mutations = FileMutationQueue()

    def prepare_change(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Compute the exact proposed text and a revision for approval/revalidation."""
        if name not in {"write_file", "edit_file"}:
            raise ValueError("Not a file mutation: " + name)
        target = self._path(arguments["path"])
        relative = str(target.relative_to(self.root))
        existed = self.file_ops.exists(target)
        if name == "write_file":
            updated = arguments["content"]
            if not isinstance(updated, str) or len(updated.encode("utf-8")) > 1_000_000:
                raise ValueError("Content must be text of at most 1 MB")
            original = self.file_ops.read_text(target) if existed else ""
            edit_count = None
        else:
            edits = arguments.get("edits")
            if edits is None:
                edits = [{"old_text": arguments["old_text"], "new_text": arguments["new_text"]}]
            elif not isinstance(edits, list) or not edits:
                raise ValueError("edits must be a nonempty list")
            else:
                edits = list(edits)
                if "old_text" in arguments or "new_text" in arguments:
                    edits.append({"old_text": arguments["old_text"], "new_text": arguments["new_text"]})
            if self.file_ops.size(target) > 1_000_000:
                raise ValueError("File exceeds the 1 MB edit limit")
            original = self.file_ops.read_text(target)
            bom = "\ufeff" if original.startswith("\ufeff") else ""
            body = original[len(bom):]
            first_lf = body.find("\n")
            ending = "\r\n" if first_lf > 0 and body[first_lf - 1] == "\r" else "\n"
            normalized = body.replace("\r\n", "\n").replace("\r", "\n")
            matches = []
            for index, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    raise ValueError(f"edits[{index}] must be an object")
                old_text, new_text = edit.get("old_text"), edit.get("new_text")
                if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
                    raise ValueError("old_text must be nonempty and new_text must be text")
                old_text = old_text.replace("\r\n", "\n").replace("\r", "\n")
                new_text = new_text.replace("\r\n", "\n").replace("\r", "\n")
                if normalized.count(old_text) != 1:
                    label = "old_text" if len(edits) == 1 else f"edits[{index}].old_text"
                    raise ValueError(f"{label} must occur exactly once")
                matches.append((normalized.index(old_text), len(old_text), new_text, index))
            matches.sort()
            for left, right in zip(matches, matches[1:]):
                if left[0] + left[1] > right[0]:
                    raise ValueError(f"edits[{left[3]}] and edits[{right[3]}] overlap")
            revised = normalized
            for start, length, replacement, _ in reversed(matches):
                revised = revised[:start] + replacement + revised[start + length:]
            if revised == normalized:
                raise ValueError("Edits did not change the file")
            updated = bom + (revised.replace("\n", ending) if ending == "\r\n" else revised)
            edit_count = len(edits)
            if len(updated.encode("utf-8")) > 1_000_000:
                raise ValueError("Edited file exceeds the 1 MB limit")
        diff = "\n".join(difflib.unified_diff(
            original.splitlines(), updated.splitlines(), lineterm="",
            fromfile="a/" + relative if existed else "/dev/null", tofile="b/" + relative,
        ))
        if diff:
            diff += "\n"
        elif original != updated:
            diff = "Line-level representation (including endings):\n" + "\n".join(difflib.unified_diff(
                [repr(line) for line in original.splitlines(keepends=True)],
                [repr(line) for line in updated.splitlines(keepends=True)],
                lineterm="", fromfile="a/" + relative if existed else "/dev/null", tofile="b/" + relative,
            )) + "\n"
        def line_endings(value: str) -> str:
            crlf = value.count("\r\n")
            lf = value.count("\n") - crlf
            cr = value.count("\r") - crlf
            kinds = [f"CRLF={crlf}" if crlf else "", f"LF={lf}" if lf else "",
                     f"CR={cr}" if cr else ""]
            return ", ".join(part for part in kinds if part) or "none"
        if line_endings(original) != line_endings(updated):
            diff += f"Line endings: {line_endings(original)} → {line_endings(updated)}\n"
        if original.endswith("\n") != updated.endswith("\n"):
            diff += ("Final newline: " + ("yes" if original.endswith("\n") else "no") +
                     " → " + ("yes" if updated.endswith("\n") else "no") + "\n")
        return {"path": relative, "operation": name, "existed": existed,
                "before_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest() if existed else None,
                "after_sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
                "before_bytes": len(original.encode("utf-8")) if existed else 0,
                "after_bytes": len(updated.encode("utf-8")), "diff": diff, "content": updated,
                "edit_count": edit_count}

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

    def _read_page(self, path: Path, offset: int, limit: int,
                   cancel: Optional[CancellationToken] = None) -> str:
        lines = []
        byte_count = 0
        last_line = 0
        more = False
        with self.file_ops.open_text(path, cancel) as source:
            for number, line in enumerate(source, 1):
                if cancel is not None:
                    cancel.raise_if_cancelled()
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

    def execute(self, name: str, arguments: Dict[str, Any],
                cancel: Optional[CancellationToken] = None,
                on_update: Optional[Callable[[str], None]] = None,
                on_artifact: Optional[Callable[[str], None]] = None,
                output_dir: Optional[Path] = None,
                on_change: Optional[Callable[[Dict[str, Any]], None]] = None,
                expected_change: Optional[Dict[str, Any]] = None) -> str:
        if cancel is not None:
            cancel.raise_if_cancelled()
        if name == "list_files":
            target = self._path(arguments["path"])
            if not self.file_ops.is_dir(target):
                raise ValueError("Not a directory")
            return json.dumps(sorted(self.file_ops.list_dir(target, cancel))[:200])
        if name == "read_file":
            target = self._path(arguments["path"])
            offset = self._positive_int(arguments.get("offset", 1), "offset", 10_000_000)
            limit = self._positive_int(arguments.get("limit", 2000), "limit", 2000)
            return self._read_page(target, offset, limit, cancel)
        if name == "read_command_output":
            raw_id = arguments["id"]
            if not isinstance(raw_id, str):
                raise ValueError("Output id must be a UUID")
            output_id = str(uuid.UUID(raw_id))
            if raw_id != output_id:
                raise ValueError("Output id must be a canonical UUID")
            root = output_dir or self.output_root
            path = root / (output_id + ".log")
            if path.resolve().parent != root.resolve():
                raise PermissionError("Output artifact is outside its directory")
            offset = arguments.get("offset", 0)
            limit = arguments.get("limit", 12_000)
            if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000_000_000:
                raise ValueError("offset must be a byte position from 0 to 1,000,000,000,000")
            limit = self._positive_int(limit, "limit", 50_000)
            with path.open("rb") as file:
                file.seek(offset)
                data = file.read(limit + 1)
            more = len(data) > limit
            text = data[:limit].decode("utf-8", errors="replace")
            if more:
                text += f"\n[More output available; use offset={offset + limit}]"
            return text
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
        if name in {"write_file", "edit_file"}:
            if not self.allow_write:
                raise PermissionError("Writing is disabled; restart with --allow-write")
            target = self._path(arguments["path"])
            with self.mutations.hold(target):
                change = self.prepare_change(name, arguments)
                if expected_change is not None and (
                    change["path"], change["existed"], change["before_sha256"], change["after_sha256"]
                ) != (
                    expected_change["path"], expected_change["existed"],
                    expected_change["before_sha256"], expected_change["after_sha256"]
                ):
                    raise ValueError("File changed since approval; review the new diff")
                if cancel is not None:
                    cancel.raise_if_cancelled()
                self.file_ops.replace_text(target, change["content"])
                if on_change is not None:
                    try:
                        on_change({key: value for key, value in change.items() if key != "content"})
                    except Exception:
                        # Reporting cannot turn a committed write into an apparent failure.
                        pass
            return ("Wrote " if name == "write_file" else "Edited ") + change["path"]
        if name == "run_command":
            return self._run_command(arguments, cancel, on_update, on_artifact, output_dir)
        raise ValueError("Unknown tool: " + name)

    def _run_command(self, arguments: Dict[str, Any], cancel: Optional[CancellationToken],
                     on_update: Optional[Callable[[str], None]],
                     on_artifact: Optional[Callable[[str], None]], output_dir: Optional[Path]) -> str:
        if not self.allow_command:
            raise PermissionError("Commands are disabled; restart with --allow-command")
        command = arguments["command"]
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Command is required")
        timeout = self._positive_int(arguments.get("timeout", 30), "timeout", 120)
        argv = shlex.split(command, posix=os.name != "nt")
        if not argv:
            raise ValueError("Command is required")
        if cancel is not None:
            cancel.raise_if_cancelled()
        process = subprocess.Popen(argv, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        tail = bytearray()
        total = 0
        artifact_id = None
        artifact_file = None
        reader_error = []

        def update(value: str) -> None:
            if on_update is not None and value:
                try:
                    on_update(value)
                except Exception:
                    # A display failure must not stop draining the child pipe.
                    pass

        def drain() -> None:
            nonlocal total, artifact_id, artifact_file
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            pending = ""
            last_emit = 0.0
            try:
                while True:
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    if artifact_file is None and total + len(chunk) > 12_000:
                        directory = output_dir or self.output_root
                        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                        if os.name != "nt":
                            directory.chmod(0o700)
                        artifact_id = str(uuid.uuid4())
                        descriptor = os.open(directory / (artifact_id + ".log"),
                                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        artifact_file = os.fdopen(descriptor, "wb")
                        artifact_file.write(tail)
                        if on_artifact is not None:
                            try:
                                on_artifact(artifact_id)
                            except Exception:
                                pass
                    if artifact_file is not None:
                        artifact_file.write(chunk)
                    total += len(chunk)
                    tail.extend(chunk)
                    if len(tail) > 12_000:
                        del tail[:-12_000]
                    pending += decoder.decode(chunk)
                    if len(pending) > 8_000:
                        pending = pending[-8_000:]
                    now = time.monotonic()
                    if pending and (last_emit == 0.0 or now - last_emit >= 0.1):
                        update(pending)
                        pending = ""
                        last_emit = now
                pending += decoder.decode(b"", final=True)
                update(pending)
            except Exception as error:
                reader_error.append(error)
            finally:
                if artifact_file is not None:
                    try:
                        artifact_file.flush()
                        os.fsync(artifact_file.fileno())
                    except Exception as error:
                        reader_error.append(error)
                    finally:
                        artifact_file.close()

        reader = threading.Thread(target=drain, daemon=True, name="mu-command-output")
        reader.start()
        deadline = time.monotonic() + timeout
        stopped = None
        try:
            while True:
                if cancel is not None and cancel.is_cancelled():
                    stopped = "cancel"
                    break
                if reader_error:
                    stopped = "output-error"
                    break
                if time.monotonic() >= deadline:
                    stopped = "timeout"
                    break
                if process.poll() is not None and not reader.is_alive():
                    break
                time.sleep(0.05)
            if stopped is not None:
                self._kill_process_tree(process)
                process.wait()
        finally:
            reader.join(timeout=2)
            process.stdout.close()
        if reader.is_alive():
            raise OSError("Command output stream did not close")
        if reader_error:
            raise OSError("Could not capture command output: " + str(reader_error[0]))
        if stopped == "cancel" or cancel is not None and cancel.is_cancelled():
            if artifact_id:
                update("\n[Command cancelled; full output id: " + artifact_id + "]\n")
            raise TurnCancelled("Turn cancelled")
        if stopped == "timeout":
            if artifact_id:
                update("\n[Command timed out; full output id: " + artifact_id + "]\n")
            raise subprocess.TimeoutExpired(argv, timeout)
        output = bytes(tail).decode("utf-8", errors="replace")
        if artifact_id:
            output += "\n[Output truncated at 12 KB]\nFull output id: " + artifact_id
        return "Exit code: " + str(process.returncode) + "\n" + output

    @staticmethod
    def _kill_process_tree(process: subprocess.Popen) -> None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except OSError:
                pass
            if process.poll() is None:
                process.kill()
