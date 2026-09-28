"""Branch ancestry, crash recovery and the desktop's session controls."""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from mupyjava.sessions import SessionStore
from mupyjava.tools import WorkspaceTools
from mupyjava.wire import decode_text, encode_text

REPOSITORY = Path(__file__).resolve().parents[2]


def turn(store, prompt, extra=None):
    turn_id = str(uuid.uuid4())
    store.append("turn.started", {}, turn_id)
    store.append("message", {"message": {"role": "user", "content": prompt}}, turn_id)
    store.append("display", {"kind": "you", "text": prompt}, turn_id)
    if extra:
        extra(store, turn_id)
    store.append("message", {"message": {"role": "assistant", "content": "reply: " + prompt}}, turn_id)
    return store.append("turn.completed", {}, turn_id)["event_id"]


def prompts(store):
    return [item["content"] for item in store.restore_messages() if item["role"] == "user"]


class SessionTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        self.root = Path(self.temp.name) / "sessions"
        self.store = SessionStore.open(self.workspace, self.root)

    def test_sibling_paths_are_preserved_and_selected_path_resumes(self):
        first = turn(self.store, "first")
        old_tip = turn(self.store, "original continuation")
        before = self.store.path.read_bytes()
        self.store.select(first)
        self.assertEqual(self.store.path.read_bytes(), before)
        new_tip = turn(self.store, "alternative continuation")
        self.assertEqual(prompts(self.store), ["first", "alternative continuation"])
        self.assertIn("not rolled back", self.store.restore_messages()[0]["content"])
        self.store.select(old_tip)
        self.assertEqual(prompts(self.store), ["first", "original continuation"])
        self.assertNotIn("alternative continuation", str(self.store.history()))
        self.store.select(new_tip)
        # A newer journal must not override the explicit active session selector.
        SessionStore.open(self.workspace, self.root, resume=False)
        self.store.activate()
        restored = SessionStore.open(self.workspace, self.root)
        self.assertEqual(restored.session_id, self.store.session_id)
        self.assertEqual(restored.leaf_id, new_tip)
        self.assertEqual(prompts(restored), ["first", "alternative continuation"])
        self.assertEqual(len(self.store.points()), 4)

    def test_beginning_creates_an_empty_path_and_empty_independent_fork(self):
        root = self.store.points()[0]["point_id"]
        original = turn(self.store, "original")
        self.store.select(root)
        self.assertEqual(prompts(self.store), [])
        self.assertEqual(self.store.history(), [])
        alternative = turn(self.store, "fresh path")
        self.assertEqual(prompts(self.store), ["fresh path"])
        empty = self.store.fork(root)
        self.assertEqual(len(empty.events()), 1)
        self.assertEqual(prompts(empty), [])
        self.assertEqual(empty.history(), [])
        self.store.select(original)
        self.assertEqual(prompts(self.store), ["original"])
        self.store.select(alternative)
        self.assertEqual(prompts(self.store), ["fresh path"])

    def test_legacy_linear_journal_is_read_without_rewriting(self):
        first = turn(self.store, "legacy one")
        turn(self.store, "legacy two")
        entries = self.store.events()
        for entry in entries:
            entry.pop("parent_id")
        self.store.path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
        before = self.store.path.read_bytes()
        restored = SessionStore.open(self.workspace, self.root)
        self.assertEqual(prompts(restored), ["legacy one", "legacy two"])
        self.assertEqual(restored.path.read_bytes(), before)
        restored.select(first)
        turn(restored, "new sibling")
        self.assertEqual(prompts(SessionStore.open(self.workspace, self.root)), ["legacy one", "new sibling"])
        self.assertTrue(restored.path.read_bytes().startswith(before))

    def test_recovery_follows_only_the_selected_path_and_handles_stale_selector(self):
        first = turn(self.store, "completed")
        self.store.append("turn.started", {}, "abandoned")
        abandoned_tail = self.store.append("message", {"message": {"role": "user", "content": "abandoned"}}, "abandoned")
        self.store.select(first)
        # Simulate a process that fsynced its append but failed before saving the selector.
        self.store._persist = False
        self.store.append("turn.started", {}, "active-interruption")
        active_tail = self.store.append("message", {"message": {"role": "user", "content": "unfinished"}}, "active-interruption")
        restored = SessionStore.open(self.workspace, self.root)
        self.assertEqual(prompts(restored), ["completed"])
        interruptions = [item for item in restored.events() if item["type"] == "turn.interrupted"]
        self.assertEqual({item["parent_id"] for item in interruptions},
                         {abandoned_tail["event_id"], active_tail["event_id"]})
        self.assertEqual([item["turn_id"] for item in restored.branch_events() if item["type"] == "turn.interrupted"],
                         ["active-interruption"])
        self.assertIn("previous turn was interrupted", restored.restore_messages()[0]["content"].lower())
        self.assertEqual(len(SessionStore.open(self.workspace, self.root).events()), len(restored.events()))

    def test_point_and_catalog_reject_invalid_paths_workspace_and_partial_turn(self):
        checkpoint = turn(self.store, "valid")
        mid = self.store.append("turn.started", {}, "pending")["event_id"]
        for invalid in (mid, str(uuid.uuid4()), "../outside", "1-1-1-1-1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.select(invalid)
            with self.assertRaises(ValueError):
                self.store.fork(invalid)
        with self.assertRaises(ValueError):
            SessionStore.load(self.workspace, self.store.path.parent, "../../outside")
        other = self.workspace / "other"
        other.mkdir()
        with self.assertRaises(ValueError):
            SessionStore.load(other, self.store.path.parent, self.store.session_id)
        self.store.select(checkpoint)
        state = json.loads((self.store.path.parent / ".active.json").read_text())
        state["positions"][self.store.session_id]["leaf_id"] = []
        (self.store.path.parent / ".active.json").write_text(json.dumps(state))
        # Corrupt metadata falls back to a valid journal and cannot escape the directory.
        self.assertEqual(len(SessionStore.catalog(self.workspace, self.root)), 1)
        self.assertEqual(prompts(SessionStore.open(self.workspace, self.root)), ["valid"])

    def test_catalog_skips_malformed_headers_and_invalid_ancestry(self):
        good = self.store.session_id
        for corrupt in ("payload", "origin", "ancestry"):
            broken = SessionStore.open(self.workspace, self.root, resume=False)
            entries = broken.events()
            if corrupt == "payload":
                entries[0]["payload"] = []
            elif corrupt == "origin":
                entries[0]["payload"]["forked_from"] = "outside"
            else:
                entries[0]["parent_id"] = entries[0]["event_id"]
            broken.path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
            with self.assertRaises(ValueError):
                SessionStore.load(self.workspace, broken.path.parent, broken.session_id)
        self.assertEqual([item["session_id"] for item in SessionStore.catalog(self.workspace, self.root)], [good])
        self.assertEqual(SessionStore.open(self.workspace, self.root).session_id, good)

    def test_fork_copies_visible_messages_images_judgments_and_output_bytes(self):
        artifact = str(uuid.uuid4())
        image = {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"}
        def add(store, turn_id):
            store.output_dir.mkdir(mode=0o700)
            (store.output_dir / (artifact + ".log")).write_bytes(b"full output\x00\xff")
            store.append("tool.artifact", {"id": artifact}, turn_id, "cmd")
            store.append("message", {"message": {"role": "tool", "tool_call_id": "read", "content": "picture",
                                                   "image_blocks": [image]}}, turn_id, "read")
            store.append("judge.record", {"point": "tool.intent", "version": 1, "answer": True,
                                           "outcome": "shadow", "source": "fixture", "mode": "shadow"}, turn_id)
        first = turn(self.store, "picture and output", add)
        old_tip = turn(self.store, "later source-only")
        source_bytes = self.store.path.read_bytes()
        copied = self.store.fork(first, activate=False)
        self.assertEqual(SessionStore.open(self.workspace, self.root).session_id, self.store.session_id)
        copied.activate()
        self.assertNotEqual(copied.session_id, self.store.session_id)
        self.assertEqual(self.store.path.read_bytes(), source_bytes)
        self.assertEqual(self.store.leaf_id, old_tip)
        self.assertEqual(prompts(copied), ["picture and output"])
        self.assertTrue(any(item.get("image_blocks") == [image] for item in copied.restore_messages()))
        self.assertTrue(any(kind == "history.judge" for kind, _ in copied.history()))
        source_ids = {item["event_id"] for item in self.store.events()}
        self.assertTrue(source_ids.isdisjoint(item["event_id"] for item in copied.events()))
        self.assertEqual(copied.events()[0]["payload"]["forked_from"],
                         {"session_id": self.store.session_id, "point_id": first})
        shutil.rmtree(self.store.output_dir)
        self.assertEqual((copied.output_dir / (artifact + ".log")).read_bytes(), b"full output\x00\xff")
        tools = WorkspaceTools(self.workspace, output_root=copied.output_dir)
        self.assertIn("full output", tools.execute("read_command_output", {"id": artifact}))
        self.assertEqual(SessionStore.open(self.workspace, self.root).session_id, copied.session_id)
        # Fork again: copied artifact references stay usable.
        again = copied.fork(copied.leaf_id)
        self.assertEqual((again.output_dir / (artifact + ".log")).read_bytes(), b"full output\x00\xff")

    def test_interrupted_fork_is_not_visible_in_catalog(self):
        artifact = str(uuid.uuid4())
        def add(store, tid):
            store.output_dir.mkdir()
            (store.output_dir / (artifact + ".log")).write_text("output")
            store.append("tool.artifact", {"id": artifact}, tid)
        point = turn(self.store, "source", add)
        # Simulate abrupt process exit, which bypasses ordinary exception cleanup.
        with patch("shutil.copyfile", side_effect=SystemExit), self.assertRaises(SystemExit):
            self.store.fork(point)
        self.assertEqual(len(list(self.store.path.parent.glob("*.pending"))), 1)
        self.assertEqual([item["session_id"] for item in SessionStore.catalog(self.workspace, self.root)],
                         [self.store.session_id])
        self.assertEqual(SessionStore.open(self.workspace, self.root).session_id, self.store.session_id)

    def test_missing_artifact_rolls_back_fork_and_keeps_active_selector(self):
        point = turn(self.store, "missing output", lambda store, tid:
                     store.append("tool.artifact", {"id": str(uuid.uuid4())}, tid))
        before = (self.store.path.parent / ".active.json").read_bytes()
        with self.assertRaises(FileNotFoundError):
            self.store.fork(point)
        self.assertEqual((self.store.path.parent / ".active.json").read_bytes(), before)
        self.assertEqual(list(self.store.path.parent.glob("*.jsonl")), [self.store.path])
        self.assertEqual(list(self.store.path.parent.glob("*.outputs")), [])


class SessionProtocolTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK is unavailable")
    def test_java_client_catalog_select_fork_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            classes = root / "classes"
            fixture = Path(__file__).parent / "fixtures" / "SessionClientSmoke.java"
            sources = list((REPOSITORY / "java/src/main/java/dev/mupyjava").glob("*.java"))
            compile_result = subprocess.run(["javac", "-d", str(classes), *map(str, sources), str(fixture)],
                                            capture_output=True, text=True, timeout=30)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            env = os.environ.copy()
            env.update({"MU_MODEL_BACKEND": "echo", "MU_JUDGE_MODE": "off", "MU_TOOL_MODULES": "",
                        "MU_SESSION_DIR": str(root / "sessions"), "MU_PYTHON": sys.executable})
            result = subprocess.run(["java", "-cp", str(classes), "SessionClientSmoke", str(REPOSITORY), str(workspace)],
                                    cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Java session catalog/select/fork/restart passed", result.stdout)

    def test_controls_do_not_replay_tools_reset_grants_and_reject_busy_switch(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                last = payload["messages"][-1]
                if last["role"] == "tool":
                    message = {"role": "assistant", "content": "Done"}
                elif last["content"].startswith("write"):
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "write", "type": "function",
                               "function": {"name": "write_file", "arguments": json.dumps({"path": "note.txt", "content": last["content"]})}}]}
                elif last["content"] == "command":
                    code = "from pathlib import Path; p=Path('runs.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
                    import shlex
                    command = shlex.quote(sys.executable) + " -c " + shlex.quote(code)
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "command", "type": "function",
                               "function": {"name": "run_command", "arguments": json.dumps({"command": command})}}]}
                else:
                    message = {"role": "assistant", "content": "Plain reply"}
                body = json.dumps({"choices": [{"message": message}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args):
                pass
        with tempfile.TemporaryDirectory() as temporary, HTTPServer(("127.0.0.1", 0), Handler) as server:
            root = Path(temporary)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            env = os.environ.copy()
            env.update({"PYTHONPATH": str(REPOSITORY / "python"), "MU_MODEL": "fixture", "MU_MODEL_BACKEND": "",
                        "MU_JUDGE_MODE": "off", "MU_TOOL_MODULES": "", "MU_API_BASE": f"http://127.0.0.1:{server.server_port}/v1",
                        "MU_SESSION_DIR": str(root / "sessions")})
            process = subprocess.Popen([sys.executable, "-m", "mupyjava", "--server", "--workspace", temporary],
                                       env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            def send(kind, *fields):
                request = str(uuid.uuid4())
                process.stdin.write("\t".join([kind, request, *fields]) + "\n"); process.stdin.flush()
                return request
            def read():
                fields = lines.get(timeout=8).rstrip("\n").split("\t")
                return fields[1], fields[2], decode_text(fields[3])
            def finish(request, answer="once", terminal="done"):
                events = []
                while True:
                    event = read()
                    self.assertEqual(event[0], request)
                    events.append(event)
                    if event[1] == "approval.request":
                        process.stdin.write("APPROVAL\t" + event[2].split("\t")[1] + "\t" + answer + "\n"); process.stdin.flush()
                    if event[1] == terminal:
                        return events
            try:
                finish(send("CHAT", encode_text("plain prefix")))
                first_store = SessionStore.open(root, root / "sessions")
                first_id, first_point = first_store.session_id, first_store.leaf_id
                events = finish(send("CHAT", encode_text("write original")), "session")
                self.assertTrue(any(kind == "approval.request" for _, kind, _ in events))
                events = finish(send("CHAT", encode_text("write granted")))
                self.assertFalse(any(kind == "approval.request" for _, kind, _ in events))
                finish(send("CHAT", encode_text("command")))
                store = SessionStore.open(root, root / "sessions")
                original_tip = store.leaf_id
                count = len(requests)
                events = finish(send("SESSIONS"))
                self.assertTrue(any(kind == "session.item" and first_id in text for _, kind, text in events))
                events = finish(send("POINTS", first_id))
                self.assertEqual(sum(kind == "session.point" for _, kind, _ in events), 5)
                events = finish(send("SELECT", first_id, first_point))
                self.assertIn((events[0][0], "session.reset", ""), events)
                self.assertNotIn("write original", str(events))
                self.assertEqual(len(requests), count)
                self.assertEqual((root / "note.txt").read_text(), "write granted")
                self.assertEqual((root / "runs.txt").read_text(), "x")
                # New branch requires an approval even though the source path held a grant.
                request = send("CHAT", encode_text("write branch"))
                approval = read()
                while approval[1].startswith("context.") or approval[1] == "frame.detail":
                    approval = read()
                self.assertEqual(approval[1], "approval.request")
                for kind, fields in (("SELECT", (first_id, original_tip)), ("FORK", (first_id, first_point)), ("NEW", ())):
                    rejected = finish(send(kind, *fields))
                    self.assertTrue(any(event[1] == "error" and "already running" in event[2] for event in rejected))
                    self.assertFalse(any(event[1] == "session.reset" for event in rejected))
                process.stdin.write("APPROVAL\t" + approval[2].split("\t")[1] + "\tonce\n"); process.stdin.flush()
                finish(request)
                visible_users = [item["content"] for item in requests[-2]["messages"] if item["role"] == "user"]
                self.assertEqual(visible_users, ["plain prefix", "write branch"])
                events = finish(send("FORK", first_id, original_tip))
                copied_id = next(text for _, kind, text in events if kind == "session.info")
                self.assertNotEqual(copied_id, first_id)
                self.assertIn("write granted", str(events))
                self.assertNotIn("write branch", str(events))
                self.assertEqual((root / "note.txt").read_text(), "write branch")
                self.assertEqual((root / "runs.txt").read_text(), "x")
                count = len(requests)
                invalid = finish(send("SELECT", "../../outside", first_point))
                self.assertTrue(any(kind == "error" for _, kind, _ in invalid))
                self.assertFalse(any(kind == "session.reset" for _, kind, _ in invalid))
                history = finish(send("HISTORY"), terminal="history.done")
                self.assertIn((history[0][0], "session.info", copied_id), history)
                self.assertEqual(len(requests), count)
                self.assertEqual(SessionStore.open(root, root / "sessions").session_id, copied_id)
            finally:
                process.stdin.close(); process.wait(timeout=8); reader.join(timeout=5)
                process.stdout.close(); errors = process.stderr.read(); process.stderr.close()
                server.shutdown(); server_thread.join(timeout=5)
            self.assertEqual(process.returncode, 0, errors)


if __name__ == "__main__":
    unittest.main()
