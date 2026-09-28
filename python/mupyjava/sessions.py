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
from .judge import format_judgment
from .context import context_status, format_context
from .task_frame import restore_frame, format_frame
from .summary import validate_summary, format_summary


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
        self._lock = threading.RLock()
        self._leaf_id = None
        self._tail_id = None
        self._workspace_notice = False
        self._persist = True

    @property
    def output_dir(self) -> Path:
        return self.path.parent / (self.session_id + ".outputs")

    @property
    def leaf_id(self):
        return self._leaf_id

    @staticmethod
    def _uuid(value):
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError("Expected a canonical UUID")
        return value

    @classmethod
    def _directory(cls, workspace, root=None):
        workspace = workspace.resolve()
        base = root or default_session_root()
        digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:24]
        directory = base / digest
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        return directory

    @staticmethod
    def _state(directory):
        try:
            path = directory / ".active.json"
            if path.resolve().parent != directory.resolve():
                return {}
            state = json.loads(path.read_text())
            if isinstance(state, dict) and state.get("version") == 1 and isinstance(state.get("positions"), dict):
                return state
        except (OSError, ValueError):
            pass
        return {}

    def _save_position(self, activate=False):
        from .file_ops import LocalFileOperations
        state = self._state(self.path.parent)
        positions = state.setdefault("positions", {})
        positions[self.session_id] = {"leaf_id": self._leaf_id,
            "tail_id": self._tail_id, "workspace_notice": self._workspace_notice,
            "updated_at": datetime.now(timezone.utc).isoformat()}
        if activate:
            state["session_id"] = self.session_id
        state["version"] = 1
        LocalFileOperations().replace_text(self.path.parent / ".active.json", json.dumps(state))

    @staticmethod
    def _index(entries):
        index = {}
        previous = None
        for entry in entries:
            event_id = SessionStore._uuid(entry.get("event_id"))
            parent = entry.get("parent_id", previous)
            if (event_id in index or not isinstance(entry.get("payload"), dict)
                    or (parent is None and previous is not None)
                    or (parent is not None and (not isinstance(parent, str) or parent not in index))):
                raise ValueError("Invalid session event ancestry")
            index[event_id] = (entry, parent)
            previous = event_id
        return index

    @classmethod
    def load(cls, workspace, directory, session_id):
        cls._uuid(session_id)
        path = directory / (session_id + ".jsonl")
        if path.resolve().parent != directory.resolve():
            raise ValueError("Session path is outside its workspace catalog")
        store = cls(workspace, path, session_id)
        entries = store.events()
        index = cls._index(entries)
        if (not entries or entries[0].get("type") != "session.created"
                or entries[0]["payload"].get("workspace") != str(workspace.resolve())):
            raise ValueError("Invalid session header or workspace")
        origin = entries[0]["payload"].get("forked_from")
        if origin is not None:
            if not isinstance(origin, dict):
                raise ValueError("Invalid fork origin")
            cls._uuid(origin.get("session_id"))
            cls._uuid(origin.get("point_id"))
        store._leaf_id = store._tail_id = entries[-1]["event_id"]
        store._workspace_notice = bool(entries[0]["payload"].get("forked_from"))
        position = cls._state(directory).get("positions", {}).get(session_id)
        if (isinstance(position, dict) and isinstance(position.get("leaf_id"), str)
                and isinstance(position.get("tail_id"), str)
                and position["leaf_id"] in index and position["tail_id"] in index):
            store._leaf_id = position["leaf_id"]
            store._workspace_notice = bool(position.get("workspace_notice")) or store._workspace_notice
            # Recover appends committed before their selector was updated. Only
            # follow this path; recovery records on other branches are ignored.
            tail_seen = False
            for entry in entries:
                if tail_seen and index[entry["event_id"]][1] == store._leaf_id:
                    store._leaf_id = entry["event_id"]
                if entry["event_id"] == position["tail_id"]:
                    tail_seen = True
        return store

    @classmethod
    def open(cls, workspace: Path, root: Optional[Path] = None, resume: bool = True) -> "SessionStore":
        workspace = workspace.resolve()
        directory = cls._directory(workspace, root)
        if resume:
            selected = cls._state(directory).get("session_id")
            candidates = ([directory / (selected + ".jsonl")] if isinstance(selected, str) else [])
            candidates += sorted(directory.glob("*.jsonl"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
            for path in dict.fromkeys(candidates):
                try:
                    store = cls.load(workspace, directory, path.stem)
                    store.mark_interrupted()
                    store.activate()
                    return store
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return cls._create(workspace, directory)

    @classmethod
    def create(cls, workspace: Path, root: Optional[Path] = None, activate: bool = True):
        """Create a fresh journal; optionally leave it inactive until context is prepared."""
        return cls._create(workspace.resolve(), cls._directory(workspace, root), activate=activate)

    @classmethod
    def _create(cls, workspace, directory, forked_from=None, activate=True, staged=False):
        session_id = str(uuid.uuid4())
        path = directory / (session_id + (".pending" if staged else ".jsonl"))
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        store = cls(workspace, path, session_id)
        store._persist = False
        payload = {"workspace": str(workspace.resolve())}
        if forked_from:
            payload["forked_from"] = forked_from
            store._workspace_notice = True
        store.append("session.created", payload)
        if activate:
            store.activate()
        return store

    def activate(self):
        with self._lock:
            self._save_position(activate=True)
            self._persist = True

    def append(self, kind: str, payload: Dict[str, Any], turn_id: Optional[str] = None,
               tool_call_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            entry = {"version": self.VERSION, "event_id": str(uuid.uuid4()), "session_id": self.session_id,
                     "parent_id": self._leaf_id, "turn_id": turn_id, "tool_call_id": tool_call_id,
                     "at": datetime.now(timezone.utc).isoformat(), "type": kind, "payload": payload}
            line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self.path.open("a", encoding="utf-8") as file:
                if file.tell() > 0:
                    with self.path.open("rb") as check:
                        check.seek(-1, os.SEEK_END)
                        if check.read(1) != b"\n":
                            file.write("\n")
                file.write(line)
                file.flush()
                os.fsync(file.fileno())
            self._leaf_id = self._tail_id = entry["event_id"]
            if self._persist:
                self._save_position()
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

    def branch_events(self, leaf_id=None):
        entries = self.events()
        index = self._index(entries)
        leaf = self._leaf_id if leaf_id is None else leaf_id
        path = []
        while leaf is not None:
            if leaf not in index:
                raise ValueError("Session point is missing")
            entry, leaf = index[leaf]
            path.append(entry)
        return list(reversed(path))

    def select(self, point_id, persist=True):
        self._uuid(point_id)
        index = self._index(self.events())
        if point_id not in index or index[point_id][0]["type"] not in {"session.created", "turn.completed"}:
            raise ValueError("Select the beginning or a completed turn")
        self._leaf_id = point_id
        self._workspace_notice = True
        if persist:
            self.activate()
        return self

    def points(self, limit=200):
        entries = self.events()
        self._index(entries)
        prompts = {}
        points = [{"point_id": entries[0]["event_id"], "title": "Beginning of conversation", "at": entries[0]["at"]}]
        for entry in entries:
            if entry["type"] == "message":
                message = entry["payload"].get("message", {})
                if isinstance(message, dict) and message.get("role") == "user":
                    prompts[entry["turn_id"]] = str(message.get("content", ""))[:100]
            elif entry["type"] == "turn.completed":
                points.append({"point_id": entry["event_id"], "title": prompts.get(entry["turn_id"], "Completed turn"),
                               "at": entry["at"]})
        return points[:1] + points[1:][-limit:]

    @classmethod
    def catalog(cls, workspace, root=None, limit=200):
        directory = cls._directory(workspace, root)
        summaries = []
        files = sorted(directory.glob("*.jsonl"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for path in files:
            try:
                store = cls.load(workspace, directory, path.stem)
                branch = store.branch_events()
                title = next((str(item["payload"]["message"].get("content", ""))[:100]
                    for item in reversed(branch) if item["type"] == "message"
                    and isinstance(item["payload"].get("message"), dict)
                    and item["payload"]["message"].get("role") == "user"), "New conversation")
                summaries.append({"session_id": store.session_id, "title": title,
                    "updated_at": branch[-1]["at"], "leaf_id": store.leaf_id,
                    "forked_from": branch[0]["payload"].get("forked_from")})
                if len(summaries) >= limit:
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return summaries

    def fork(self, point_id, activate=True):
        self._uuid(point_id)
        index = self._index(self.events())
        if point_id not in index or index[point_id][0]["type"] not in {"session.created", "turn.completed"}:
            raise ValueError("Fork the beginning or a completed turn")
        path = self.branch_events(point_id)
        target = self._create(self.workspace, self.path.parent,
                             {"session_id": self.session_id, "point_id": point_id}, activate=False, staged=True)
        try:
            import shutil
            for entry in path[1:]:
                target.append(entry["type"], entry["payload"], entry["turn_id"], entry.get("tool_call_id"))
                # Original entries stay unchanged; new entry/parent IDs make the
                # copied conversation path an independent tree.
            artifacts = {entry["payload"].get("id") for entry in path if entry["type"] == "tool.artifact"}
            if artifacts:
                target.output_dir.mkdir(mode=0o700)
            for artifact_id in artifacts:
                self._uuid(artifact_id)
                source = self.output_dir / (artifact_id + ".log")
                if source.resolve().parent != self.output_dir.resolve():
                    raise ValueError("Output artifact is outside its directory")
                destination = target.output_dir / (artifact_id + ".log")
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
                with destination.open("rb") as copied_output:
                    os.fsync(copied_output.fileno())
            final_path = target.path.with_suffix(".jsonl")
            os.replace(target.path, final_path)
            target.path = final_path
            if activate:
                target.activate()
            return target
        except Exception:
            import shutil
            shutil.rmtree(target.output_dir, ignore_errors=True)
            target.path.unlink(missing_ok=True)
            raise

    def mark_interrupted(self) -> None:
        entries = self.events()
        started = {item["turn_id"] for item in entries if item["type"] == "turn.started"}
        finished = {item["turn_id"] for item in entries if item["type"] in {"turn.completed", "turn.interrupted"}}
        selected, persist = self._leaf_id, self._persist
        self._persist = False
        try:
            for turn_id in started - finished:
                tail = next(item["event_id"] for item in reversed(entries) if item["turn_id"] == turn_id)
                self._leaf_id = tail
                entry = self.append("turn.interrupted", {"reason": "Backend stopped before turn completion"}, turn_id)
                if selected == tail:
                    selected = entry["event_id"]
        finally:
            self._leaf_id, self._persist = selected, persist
        if persist:
            self._save_position()

    def restore_messages(self) -> List[Dict[str, Any]]:
        entries = self.branch_events()
        completed = {item["turn_id"] for item in entries if item["type"] == "turn.completed"}
        latest_outcome = next((item["type"] for item in reversed(entries)
                               if item["type"] in {"turn.completed", "turn.interrupted"}), None)
        system = SYSTEM_MESSAGE
        if latest_outcome == "turn.interrupted":
            system += " A previous turn was interrupted. Inspect the workspace before repeating an action."
        if self._workspace_notice:
            system += " This conversation path was selected or forked. Workspace files were not rolled back. Inspect relevant files before making changes."
        messages = [{"role": "system", "content": system}]
        for item in entries:
            if item["type"] == "message" and item["turn_id"] in completed:
                message = item["payload"].get("message")
                if isinstance(message, dict):
                    messages.append(message)
        return messages

    def restore_frame(self):
        return restore_frame(self.branch_events())

    def summary_records(self):
        messages = self.restore_messages()
        records = []
        for entry in self.branch_events():
            if entry["type"] == "context.summary":
                try:
                    records.append(validate_summary(entry["payload"], messages))
                except (ValueError, TypeError, KeyError):
                    continue
        return records

    def history(self) -> List[Tuple[str, str]]:
        history = []
        last_context = None
        entries = self.branch_events()
        frames = {}
        restore_frame(entries, lambda event_id, frame: frames.update({event_id: frame}))
        messages = self.restore_messages()
        for item in entries:
            kind = item["type"]
            payload = item["payload"]
            if kind == "display":
                history.append(("history.transcript", "[" + str(payload.get("kind")) + "] "
                                + str(payload.get("text")) + "\n\n"))
            elif kind == "context.budget" and payload.get("version") == 1:
                history.append(("history.context", format_context(payload)))
                last_context = payload
            elif kind == "judge.record":
                history.append(("history.judge", format_judgment(payload)))
            elif kind == "task.frame":
                if item["event_id"] in frames:
                    history.append(("history.frame", format_frame(frames[item["event_id"]].payload())))
            elif kind == "context.summary":
                try:
                    record = validate_summary(payload, messages)
                    history.append(("history.summary", format_summary(record)))
                except (ValueError, TypeError, KeyError):
                    continue
            elif kind == "tool.artifact":
                history.append(("history.transcript", "[output] Full output id: " + str(payload.get("id")) + "\n\n"))
            elif kind == "turn.interrupted":
                history.append(("history.transcript", "[session] Previous turn was interrupted; its actions were not replayed.\n\n"))
        if last_context is not None:
            history.append(("context.status", context_status(last_context)))
        return history
