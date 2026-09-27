"""Workspace tools. Mutating tools require explicit launch flags."""

import json
import os
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
