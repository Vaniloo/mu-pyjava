"""Workspace tools. Mutating tools require explicit launch flags."""

import copy
import json
import difflib
import hashlib
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .cancel import CancellationToken, TurnCancelled
from .capabilities import ModelCapabilities
from .file_ops import FileMutationQueue, FileOperations, LocalFileOperations
from .command_ops import CommandOperations, CommandOutput, LocalCommandOperations
from .search_ops import GitOperations, LocalGitOperations, LocalSearchOperations, SearchOperations
from .shell_ops import LocalShellOperations, ShellOperations
from .image_ops import ImageOperations, PillowImageOperations, MAX_SOURCE_BYTES, detect_image_mime
from .registry import ToolContext, ToolRegistry, validate_arguments
from .tool_policy import COMMAND_TOOLS, MUTATING_TOOLS
from .tool_result import ToolResult, TextContent


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
            "description": "Read UTF-8 text or PNG/JPEG/GIF/WebP/BMP images. Text supports 1-based offset and line limit. Images are converted and attached only for models configured to accept them.",
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

TOOL_SCHEMAS.extend({
    "type": "function", "function": {
        "name": shell,
        "description": f"Execute a {shell} script in the workspace, including pipes and redirection. Requires command permission. Output streams and can be retrieved after truncation.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "timeout": {"type": "integer"},
        }, "required": ["command"]},
    },
} for shell in ("bash", "powershell"))


class WorkspaceTools:
    def __init__(self, root: Path, allow_write: bool = False, allow_command: bool = False,
                 output_root: Optional[Path] = None, file_ops: Optional[FileOperations] = None,
                 search_ops: Optional[SearchOperations] = None, git_ops: Optional[GitOperations] = None,
                 command_ops: Optional[CommandOperations] = None,
                 shell_ops: Optional[ShellOperations] = None,
                 image_ops: Optional[ImageOperations] = None, allow_custom: bool = False):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace must be a directory")
        self.allow_write = allow_write
        self.allow_command = allow_command
        self.output_root = output_root or Path(tempfile.gettempdir()) / "mupyjava-output"
        self.file_ops = file_ops or LocalFileOperations()
        self.search_ops = search_ops or LocalSearchOperations(self.root)
        self.git_ops = git_ops or LocalGitOperations(self.root)
        self.command_ops = command_ops or LocalCommandOperations()
        self.shell_ops = shell_ops or LocalShellOperations()
        self.image_ops = image_ops or PillowImageOperations()
        self.allow_custom = allow_custom
        self.registry = ToolRegistry(schema["function"]["name"] for schema in TOOL_SCHEMAS)
        self.mutations = FileMutationQueue()

    @property
    def schemas(self):
        return copy.deepcopy(TOOL_SCHEMAS) + self.registry.schemas()

    def is_mutating(self, name: str) -> bool:
        definition = self.registry.resolve(name)
        return name in MUTATING_TOOLS or (definition is not None and definition.effect == "mutation")

    def validate_call(self, name: str, arguments: dict):
        definition = self.registry.resolve(name)
        if definition is not None:
            json.dumps(arguments, allow_nan=False)
            validate_arguments(arguments, definition.parameters)

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
    def _positive_int(value, name: str, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}")
        return value

    def _search_paths(self, base: Path, pattern: str, cancel: Optional[CancellationToken]):
        paths, incomplete = self.search_ops.find(base, pattern, cancel)
        if cancel is not None:
            cancel.raise_if_cancelled()
        if not isinstance(paths, list) or not isinstance(incomplete, bool):
            raise ValueError("Search operations returned an invalid listing")
        return [str(self._path(path).relative_to(self.root)) for path in paths], incomplete

    def _git(self, args, cancel, details):
        result = self.git_ops.inspect(args, cancel)
        if cancel is not None:
            cancel.raise_if_cancelled()
        if not isinstance(result, str):
            raise ValueError("Git operations must return text")
        text = result[:12_000]
        details.update(truncated=len(result) > len(text), output_bytes=len(text.encode("utf-8")))
        return text

    def _read_page(self, path: Path, offset: int, limit: int,
                   cancel: Optional[CancellationToken], details: dict) -> str:
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
        details.update(offset=offset, output_lines=len(lines), output_bytes=byte_count,
                       truncated=more, next_offset=offset + len(lines) if more else None)
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
        return self.execute_result(name, arguments, cancel=cancel, on_update=on_update,
                                   on_artifact=on_artifact, output_dir=output_dir,
                                   on_change=on_change, expected_change=expected_change).text

    def execute_result(self, name: str, arguments: Dict[str, Any], **options) -> ToolResult:
        capabilities = options.pop("capabilities", ModelCapabilities())
        cancel = options.get("cancel")
        if cancel is not None:
            cancel.raise_if_cancelled()
        definition = self.registry.resolve(name)
        if definition is not None:
            self.validate_call(name, arguments)
            if definition.effect == "mutation" and not self.allow_custom:
                raise PermissionError("Custom mutation tools require approval or --allow-custom-tools")
            context = ToolContext(self.root, self._path, cancel, options.get("on_update"),
                                  options.get("on_artifact"), options.get("output_dir") or self.output_root,
                                  capabilities)
            try:
                result = definition.execute(copy.deepcopy(arguments), context)
                context.check_cancelled()
                if not isinstance(result, ToolResult):
                    raise ValueError("Custom tools must return a ToolResult")
                return result
            except TurnCancelled:
                raise
            except Exception as error:
                context.check_cancelled()
                return ToolResult.failed(error)
        if name == "read_file":
            # Old text-only operation adapters remain usable. Binary support is an
            # explicit extension to their contract, with no local-file fallback.
            read_bytes = getattr(self.file_ops, "read_bytes", None)
            if read_bytes is not None:
                target = self._path(arguments["path"])
                header = read_bytes(target, 32, cancel)
                if not isinstance(header, bytes) or len(header) > 32:
                    raise ValueError("Binary operations must return bounded bytes")
                mime = detect_image_mime(header)
                if mime is not None:
                    if "offset" in arguments or "limit" in arguments:
                        raise ValueError("offset and limit apply only to text files")
                    if not capabilities.supports_images:
                        return ToolResult.from_text(
                            f"Read image file [{mime}]\n[Image omitted: current model accepts text only.]",
                            {"path": str(target.relative_to(self.root)), "source_mime_type": mime,
                             "image_omitted": True, "omission_reason": "text_only_model"})
                    data = read_bytes(target, MAX_SOURCE_BYTES + 1, cancel)
                    if not isinstance(data, bytes) or len(data) > MAX_SOURCE_BYTES:
                        raise ValueError("Image exceeds the bounded binary-read limit (20 MiB)")
                    result = self.image_ops.process(data, mime, capabilities, cancel)
                    if cancel is not None:
                        cancel.raise_if_cancelled()
                    if not isinstance(result, ToolResult):
                        raise ValueError("Image operations must return a ToolResult")
                    return ToolResult(result.content, {**result.details, "path": str(target.relative_to(self.root))}, result.is_error)
        details = {}
        text = self._execute_text(name, arguments, details=details, **options)
        return ToolResult((TextContent(text),), details, details.get("exit_code", 0) != 0)

    def _execute_text(self, name: str, arguments: Dict[str, Any],
                      cancel: Optional[CancellationToken] = None,
                      on_update: Optional[Callable[[str], None]] = None,
                      on_artifact: Optional[Callable[[str], None]] = None,
                      output_dir: Optional[Path] = None,
                      on_change: Optional[Callable[[Dict[str, Any]], None]] = None,
                      expected_change: Optional[Dict[str, Any]] = None,
                      details: Optional[dict] = None) -> str:
        if details is None:
            details = {}
        if cancel is not None:
            cancel.raise_if_cancelled()
        if name == "list_files":
            target = self._path(arguments["path"])
            if not self.file_ops.is_dir(target):
                raise ValueError("Not a directory")
            entries = sorted(self.file_ops.list_dir(target, cancel))
            details.update(count=min(200, len(entries)), truncated=len(entries) > 200)
            return json.dumps(entries[:200])
        if name == "read_file":
            target = self._path(arguments["path"])
            offset = self._positive_int(arguments.get("offset", 1), "offset", 10_000_000)
            limit = self._positive_int(arguments.get("limit", 2000), "limit", 2000)
            return self._read_page(target, offset, limit, cancel, details)
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
            details.update(artifact_id=output_id, offset=offset, output_bytes=min(limit, len(data)),
                           truncated=more, next_offset=offset + limit if more else None)
            text = data[:limit].decode("utf-8", errors="replace")
            if more:
                text += f"\n[More output available; use offset={offset + limit}]"
            return text
        if name == "find_files":
            pattern = arguments["glob"]
            if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
                raise ValueError("Glob must be a nonempty string of at most 200 characters")
            base = self._path(arguments["path"])
            if not self.file_ops.exists(base):
                raise ValueError("Search path does not exist")
            paths, incomplete = self._search_paths(base, pattern, cancel)
            shown = []
            size = 0
            for path in paths[:200]:
                size += len(path.encode("utf-8")) + 4
                if size > 10_000:
                    break
                shown.append(path)
            details.update(count=len(shown), truncated=incomplete or len(shown) < len(paths))
            return json.dumps({"paths": shown, "truncated": details["truncated"]}, ensure_ascii=False)
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
            if not self.file_ops.exists(base):
                raise ValueError("Search path does not exist")
            paths, incomplete = self._search_paths(base, file_glob, cancel)
            matches = []
            match_count = 0
            truncated = incomplete
            output_bytes = 0
            output_full = False
            for start in range(0, len(paths), 100):
                records, batch_truncated = self.search_ops.grep(
                    paths[start:start + 100], pattern, literal, ignore_case, context,
                    min(500, (limit - match_count) * (2 * context + 3) + 10), cancel,
                )
                if cancel is not None:
                    cancel.raise_if_cancelled()
                if not isinstance(records, list) or not isinstance(batch_truncated, bool):
                    raise ValueError("Search operations returned invalid matches")
                truncated = truncated or batch_truncated
                for item in records:
                    if (not isinstance(item, dict) or item.get("kind") not in {"match", "context"}
                            or not isinstance(item.get("text"), str) or isinstance(item.get("line"), bool)
                            or not isinstance(item.get("line"), int) or item["line"] < 1):
                        raise ValueError("Search operations returned a malformed match")
                    kind = item["kind"]
                    if kind == "match":
                        if match_count >= limit:
                            truncated = True
                            break
                    record = {"path": str(self._path(item["path"]).relative_to(self.root)), "line": item["line"],
                              "text": item["text"].rstrip("\r\n")[:500], "kind": kind}
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
            details.update(count=match_count, record_count=len(matches), truncated=truncated)
            return json.dumps({"matches": matches, "truncated": truncated}, ensure_ascii=False)
        if name == "git_status":
            return self._git(["status", "--short", "--untracked-files=normal", "--", "."], cancel, details)
        if name == "git_diff":
            raw_path = arguments.get("path", ".")
            selected = self._path(raw_path)
            staged = arguments.get("staged", False)
            if not isinstance(staged, bool):
                raise ValueError("staged must be a boolean")
            path = str(selected.relative_to(self.root)) or "."
            return self._git(["diff", "--no-ext-diff", *(["--staged"] if staged else []), "--", path], cancel, details)
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
                details["change"] = {key: value for key, value in change.items() if key != "content"}
                if on_change is not None:
                    try:
                        on_change({key: value for key, value in change.items() if key != "content"})
                    except Exception:
                        # Reporting cannot turn a committed write into an apparent failure.
                        pass
            return ("Wrote " if name == "write_file" else "Edited ") + change["path"]
        if name in COMMAND_TOOLS:
            return self._run_command(name, arguments, cancel, on_update, on_artifact, output_dir, details)
        raise ValueError("Unknown tool: " + name)

    def _run_command(self, name: str, arguments: Dict[str, Any], cancel: Optional[CancellationToken],
                     on_update: Optional[Callable[[str], None]],
                     on_artifact: Optional[Callable[[str], None]], output_dir: Optional[Path], details: dict) -> str:
        if not self.allow_command:
            raise PermissionError("Commands are disabled; restart with --allow-command")
        command = arguments["command"]
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Command is required")
        timeout = self._positive_int(arguments.get("timeout", 30), "timeout", 120)
        capture = CommandOutput(output_dir or self.output_root, on_update, on_artifact)
        try:
            if name == "run_command":
                argv = shlex.split(command, posix=os.name != "nt")
                if not argv:
                    raise ValueError("Command is required")
                code = self.command_ops.execute(argv, self.root, timeout, cancel, capture.feed)
            else:
                code = self.shell_ops.execute(name, command, self.root, timeout, cancel, capture.feed)
            if cancel is not None:
                cancel.raise_if_cancelled()
        except (TurnCancelled, subprocess.TimeoutExpired) as error:
            if capture.artifact_id:
                reason = "cancelled" if isinstance(error, TurnCancelled) else "timed out"
                capture.update("\n[Command " + reason + "; full output id: " + capture.artifact_id + "]\n")
            raise
        finally:
            capture.close()
        text = capture.result(code)
        details.update(exit_code=code, output_bytes=capture.total, truncated=capture.artifact_id is not None,
                       artifact_id=capture.artifact_id, mode="exec" if name == "run_command" else name)
        return text
