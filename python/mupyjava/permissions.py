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


def action_preview(tools: WorkspaceTools, name: str, arguments: dict,
                   prepared_change: Optional[dict] = None) -> Tuple[str, str, Optional[str], Optional[Path]]:
    """Describe the exact proposed action and return a narrow session-grant key."""
    if name in {"write_file", "edit_file"}:
        target = tools._path(arguments["path"])
        relative = target.relative_to(tools.root)
        protected = (any(part in PROTECTED_PARTS for part in relative.parts)
                     or target.name == ".env" or target.name.startswith(".env."))
        grant = None if protected else "file:" + str(relative)
        change = prepared_change or tools.prepare_change(name, arguments)
        if change["path"] != str(relative):
            raise ValueError("Path changed while preparing approval")
        verb = "Edit" if name == "edit_file" else ("Replace" if change["existed"] else "Create")
        preview = change["diff"] or "(No content change)"
        return (f"{verb} {relative} ({change['after_bytes']} bytes)", preview, grant, target)
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
        self._expected: Dict[str, dict] = {}
        self._closed = False

    def request(self, request_id: str, tool_call_id: str, name: str, arguments: dict) -> bool:
        if name not in MUTATING_TOOLS:
            return True
        if (name == "run_command" and self.full_command) or (name != "run_command" and self.full_write):
            return True
        proposed = self.tools.prepare_change(name, arguments) if name in {"write_file", "edit_file"} else None
        summary, preview, grant, target = action_preview(self.tools, name, arguments, proposed)
        with self._lock:
            if self._closed:
                return False
            if grant is not None and grant in self._grants:
                if proposed is not None:
                    self._expected[tool_call_id] = {key: value for key, value in proposed.items() if key != "content"}
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
        if proposed is not None:
            current = self.tools.prepare_change(name, arguments)
            if any(current[key] != proposed[key] for key in ("path", "existed", "before_sha256", "after_sha256")):
                return False
        if pending.answer == "session" and grant is not None:
            with self._lock:
                if self._closed:
                    return False
                self._grants.add(grant)
        if proposed is not None:
            with self._lock:
                self._expected[tool_call_id] = {key: value for key, value in proposed.items() if key != "content"}
        return True

    def take_expected_change(self, tool_call_id: str) -> Optional[dict]:
        with self._lock:
            return self._expected.pop(tool_call_id, None)

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
            self._expected.clear()

    def cancel_request(self, request_id: str) -> None:
        resolved = []
        with self._lock:
            for approval_id, pending in list(self._pending.items()):
                if pending.request_id == request_id:
                    self._pending.pop(approval_id)
                    pending.answer = "deny"
                    pending.done.set()
                    resolved.append(approval_id)
        for approval_id in resolved:
            self.emit(request_id, "approval.resolved", "v1\t" + approval_id + "\tdeny")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for pending in self._pending.values():
                pending.answer = "deny"
                pending.done.set()
            self._pending.clear()
            self._expected.clear()
