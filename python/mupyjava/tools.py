"""Workspace tools. Mutating tools require explicit launch flags."""

import json
import os
import fnmatch
import shlex
import subprocess
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
            "description": "Read a UTF-8 text file in the workspace.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find workspace files by glob, for example **/*.py. Returns up to 200 paths.",
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
            "description": "Search UTF-8 workspace files for literal text. Returns up to 100 matching lines.",
            "parameters": {
                "type": "object", "properties": {
                    "path": {"type": "string"}, "pattern": {"type": "string"},
                    "glob": {"type": "string"},
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
            "description": "Run one command in the workspace without a shell. Requires command permission.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
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

    def _files(self, raw: str):
        target = self._path(raw)
        if target.is_file():
            return [target]
        if not target.is_dir():
            raise ValueError("Not a file or directory")
        files = []
        for folder, directories, names in os.walk(target, followlinks=False):
            directories[:] = sorted(name for name in directories if name != ".git" and not (Path(folder) / name).is_symlink())
            for name in sorted(names):
                candidate = Path(folder) / name
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                files.append(candidate)
                if len(files) >= 10_000:
                    return files
        return files

    @staticmethod
    def _matches_glob(path: str, pattern: str) -> bool:
        return fnmatch.fnmatch(path, pattern) or (pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]))

    def _git(self, args):
        result = subprocess.run(["git", "--no-pager", "-c", "core.quotePath=false", *args],
                                cwd=self.root, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise ValueError("Git failed: " + result.stderr.strip()[:500])
        return result.stdout[:12_000]

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        if name == "list_files":
            target = self._path(arguments["path"])
            if not target.is_dir():
                raise ValueError("Not a directory")
            return json.dumps(sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())[:200])
        if name == "read_file":
            target = self._path(arguments["path"])
            if target.stat().st_size > 1_000_000:
                raise ValueError("File exceeds the 1 MB read limit")
            return target.read_text(encoding="utf-8")
        if name == "find_files":
            pattern = arguments["glob"]
            if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
                raise ValueError("Glob must be a nonempty string of at most 200 characters")
            base = self._path(arguments["path"])
            paths = [str(path.relative_to(self.root)) for path in self._files(arguments["path"])
                     if self._matches_glob(str(path.relative_to(base)) if base.is_dir() else path.name, pattern)]
            return json.dumps(paths[:200], ensure_ascii=False)
        if name == "grep_files":
            pattern = arguments["pattern"]
            file_glob = arguments.get("glob", "**/*")
            if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
                raise ValueError("Pattern must be nonempty text of at most 200 characters")
            if not isinstance(file_glob, str) or not file_glob or len(file_glob) > 200:
                raise ValueError("Glob must be a nonempty string of at most 200 characters")
            base = self._path(arguments["path"])
            matches = []
            for path in self._files(arguments["path"]):
                relative = str(path.relative_to(base)) if base.is_dir() else path.name
                if not self._matches_glob(relative, file_glob) or path.stat().st_size > 1_000_000:
                    continue
                try:
                    with path.open(encoding="utf-8") as file:
                        for line_number, line in enumerate(file, 1):
                            if pattern in line:
                                matches.append({"path": str(path.relative_to(self.root)),
                                                "line": line_number, "text": line.rstrip("\r\n")[:300]})
                                if len(matches) >= 100:
                                    return json.dumps(matches, ensure_ascii=False)
                except UnicodeDecodeError:
                    continue
            return json.dumps(matches, ensure_ascii=False)
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
            argv = shlex.split(command, posix=os.name != "nt")
            result = subprocess.run(argv, cwd=self.root, capture_output=True, text=True, timeout=30, check=False)
            output = (result.stdout + result.stderr)[:12_000]
            return "Exit code: " + str(result.returncode) + "\n" + output
        raise ValueError("Unknown tool: " + name)
