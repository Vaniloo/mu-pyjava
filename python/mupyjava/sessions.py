"""Append-only, versioned local session journal and safe context restoration."""

import hashlib
import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .agent import SYSTEM_MESSAGE


def default_session_root() -> Path:
    configured = os.environ.get("MU_SESSION_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mu-pyjava" / "sessions"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mu-pyjava" / "sessions"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "mu-pyjava" / "sessions"


class SessionStore:
    VERSION = 1

    def __init__(self, workspace: Path, path: Path, session_id: str):
        self.workspace = workspace.resolve()
        self.path = path
        self.session_id = session_id
        self._lock = threading.Lock()

    @classmethod
    def open(cls, workspace: Path, root: Optional[Path] = None, resume: bool = True) -> "SessionStore":
        workspace = workspace.resolve()
        base = root or default_session_root()
        digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:24]
        directory = base / digest
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        if resume:
            candidates = sorted(directory.glob("*.jsonl"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
            for path in candidates:
                try:
                    session_id = str(uuid.UUID(path.stem))
                    store = cls(workspace, path, session_id)
                    entries = store.events()
                    if entries and entries[0]["type"] == "session.created" and entries[0]["session_id"] == session_id:
                        store.mark_interrupted()
                        return store
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        session_id = str(uuid.uuid4())
        path = directory / (session_id + ".jsonl")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        store = cls(workspace, path, session_id)
        store.append("session.created", {"workspace": str(workspace)})
        return store

    def append(self, kind: str, payload: Dict[str, Any], turn_id: Optional[str] = None,
               tool_call_id: Optional[str] = None) -> Dict[str, Any]:
        entry = {"version": self.VERSION, "event_id": str(uuid.uuid4()), "session_id": self.session_id,
                 "turn_id": turn_id, "tool_call_id": tool_call_id,
                 "at": datetime.now(timezone.utc).isoformat(), "type": kind, "payload": payload}
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                if file.tell() > 0:
                    with self.path.open("rb") as check:
                        check.seek(-1, os.SEEK_END)
                        if check.read(1) != b"\n":
                            file.write("\n")
                file.write(line)
                file.flush()
                os.fsync(file.fileno())
        return entry

    def events(self) -> List[Dict[str, Any]]:
        with self._lock:
            with self.path.open(encoding="utf-8") as file:
                lines = file.readlines()
        entries = []
        for line in lines:
            try:
                entry = json.loads(line)
                if (isinstance(entry, dict) and entry.get("version") == self.VERSION
                        and entry.get("session_id") == self.session_id):
                    entries.append(entry)
            except json.JSONDecodeError:
                continue
        return entries

    def mark_interrupted(self) -> None:
        entries = self.events()
        started = {item["turn_id"] for item in entries if item["type"] == "turn.started"}
        finished = {item["turn_id"] for item in entries if item["type"] in {"turn.completed", "turn.interrupted"}}
        for turn_id in started - finished:
            self.append("turn.interrupted", {"reason": "Backend stopped before turn completion"}, turn_id)

    def restore_messages(self) -> List[Dict[str, Any]]:
        entries = self.events()
        completed = {item["turn_id"] for item in entries if item["type"] == "turn.completed"}
        latest_outcome = next((item["type"] for item in reversed(entries)
                               if item["type"] in {"turn.completed", "turn.interrupted"}), None)
        interrupted = latest_outcome == "turn.interrupted"
        system = SYSTEM_MESSAGE
        if interrupted:
            system += " A previous turn was interrupted. Inspect the workspace before repeating an action."
        messages = [{"role": "system", "content": system}]
        for item in entries:
            if item["type"] == "message" and item["turn_id"] in completed:
                message = item["payload"].get("message")
                if isinstance(message, dict):
                    messages.append(message)
        return messages

    def history(self) -> List[Tuple[str, str]]:
        history = []
        for item in self.events():
            kind = item["type"]
            payload = item["payload"]
            if kind == "display":
                history.append(("history.transcript", "[" + str(payload.get("kind")) + "] "
                                + str(payload.get("text")) + "\n\n"))
            elif kind == "judge.record":
                history.append(("history.judge", format_judgment(payload)))
            elif kind == "turn.interrupted":
                history.append(("history.transcript", "[session] Previous turn was interrupted; its actions were not replayed.\n\n"))
        return history


def format_judgment(record: Dict[str, Any]) -> str:
    answer = record.get("answer")
    verdict = "allow" if answer is True else "decline" if answer is False else "undecided"
    probability = record.get("probability")
    confidence = f" (p={probability:.3f})" if isinstance(probability, (float, int)) else ""
    failure = f"; fallback reason: {record['failure']}" if record.get("failure") else ""
    return (f"{record.get('point')} v{record.get('version')} · {record.get('tool')}: {verdict}{confidence}\n"
            f"Outcome: {record.get('outcome')} ({record.get('source')}, {record.get('mode')})"
            f"; {record.get('latency_ms')} ms{failure}\n\n")
