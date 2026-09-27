"""Per-action permission gate used by the interactive backend."""

import base64
import os
import shlex
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .tools import WorkspaceTools


MUTATING_TOOLS = frozenset({"write_file", "edit_file", "run_command"})
PROTECTED_PARTS = frozenset({".git", ".mu", ".pi", ".codex"})


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def action_preview(tools: WorkspaceTools, name: str, arguments: dict) -> Tuple[str, str, Optional[str], Optional[Path]]:
    """Describe the exact proposed action and return a narrow session-grant key."""
    if name in {"write_file", "edit_file"}:
        target = tools._path(arguments["path"])
        relative = target.relative_to(tools.root)
        protected = (any(part in PROTECTED_PARTS for part in relative.parts)
                     or target.name == ".env" or target.name.startswith(".env."))
        grant = None if protected else "file:" + str(relative)
        if name == "write_file":
            content = arguments["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > 1_000_000:
                raise ValueError("Content must be text of at most 1 MB")
            verb = "Replace" if target.exists() else "Create"
            return (f"{verb} {relative} ({len(content.encode('utf-8'))} bytes)",
                    content, grant, target)
        old_text, new_text = arguments["old_text"], arguments["new_text"]
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise ValueError("old_text must be nonempty and new_text must be text")
        if target.stat().st_size > 1_000_000:
            raise ValueError("File exceeds the 1 MB edit limit")
        if target.read_text(encoding="utf-8").count(old_text) != 1:
            raise ValueError("old_text must occur exactly once")
        return (f"Edit {relative}", f"Replace this exact text:\n{old_text}\n\nWith:\n{new_text}",
                grant, target)
    if name == "run_command":
        command = arguments["command"]
        if not isinstance(command, str) or not command.strip() or not shlex.split(command, posix=os.name != "nt"):
            raise ValueError("Command is required")
        tools._positive_int(arguments.get("timeout", 30), "timeout", 120)
        return ("Run a command in " + str(tools.root), command, None, None)
    raise ValueError("Not a mutating tool: " + name)


def request_payload(approval_id: str, tool_call_id: str, options: str, summary: str, preview: str) -> str:
    """Versioned fields inside the existing Base64 EVENT text envelope."""
    return "\t".join(("v1", approval_id, _encoded(tool_call_id), options,
                      _encoded(summary), _encoded(preview)))


@dataclass
class PendingApproval:
    request_id: str
    grant: Optional[str]
    done: threading.Event = field(default_factory=threading.Event)
    answer: str = "deny"


class ApprovalManager:
    def __init__(self, tools: WorkspaceTools, emit: Callable[[str, str, str], None],
                 full_write: bool = False, full_command: bool = False):
        self.tools = tools
        self.emit = emit
        self.full_write = full_write
        self.full_command = full_command
        self._lock = threading.Lock()
        self._pending: Dict[str, PendingApproval] = {}
        self._grants = set()
        self._closed = False

    def request(self, request_id: str, tool_call_id: str, name: str, arguments: dict) -> bool:
        if name not in MUTATING_TOOLS:
            return True
        if (name == "run_command" and self.full_command) or (name != "run_command" and self.full_write):
            return True
        summary, preview, grant, target = action_preview(self.tools, name, arguments)
        with self._lock:
            if self._closed:
                return False
            if grant is not None and grant in self._grants:
                return True
            approval_id = str(uuid.uuid4())
            pending = PendingApproval(request_id, grant)
            self._pending[approval_id] = pending
        options = "deny,once,session" if grant else "deny,once"
        try:
            self.emit(request_id, "approval.request",
                      request_payload(approval_id, tool_call_id, options, summary, preview))
        except OSError:
            self.resolve(approval_id, "deny")
        pending.done.wait()
        if pending.answer == "deny":
            return False
        # A symlink or path may have changed while the user was deciding.
        if target is not None and self.tools._path(arguments["path"]) != target:
            return False
        if pending.answer == "session" and grant is not None:
            with self._lock:
                if self._closed:
                    return False
                self._grants.add(grant)
        return True

    def resolve(self, approval_id: str, answer: str) -> bool:
        if answer not in {"deny", "once", "session"}:
            return False
        with self._lock:
            pending = self._pending.pop(approval_id, None)
            if pending is None:
                return False
            if answer == "session" and pending.grant is None:
                answer = "deny"
            pending.answer = answer
            pending.done.set()
        self.emit(pending.request_id, "approval.resolved", "v1\t" + approval_id + "\t" + answer)
        return True

    def reset_grants(self) -> None:
        with self._lock:
            if self._pending:
                raise RuntimeError("Cannot reset grants during a pending approval")
            self._grants.clear()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for pending in self._pending.values():
                pending.answer = "deny"
                pending.done.set()
            self._pending.clear()
