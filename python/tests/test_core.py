import json
import io
import os
import queue
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from mupyjava.agent import Agent
from mupyjava.cancel import CancellationToken, TurnCancelled
from mupyjava.judge import DecisionEngine, DecisionPoint, LayaBooleanJudge, LayaHttpBooleanJudge
from mupyjava.permissions import ApprovalManager
from mupyjava.permissions import action_preview
from mupyjava.file_ops import LocalFileOperations
from mupyjava.sessions import SessionStore
from mupyjava.tools import WorkspaceTools
from mupyjava.wire import decode_text, encode_text, event_line, parse_request


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    def complete(self, messages, tools):
        return next(self.replies)


class CoreTests(unittest.TestCase):
    def test_file_diff_preview_create_replace_edit_and_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root, allow_write=True)
            _, preview, _, _ = action_preview(tools, "write_file", {"path": "a.txt", "content": "one\n"})
            self.assertIn("--- /dev/null", preview)
            self.assertIn("+one", preview)
            tools.execute("write_file", {"path": "a.txt", "content": "one\n"})
            _, preview, _, _ = action_preview(tools, "write_file", {"path": "a.txt", "content": "two"})
            self.assertIn("-one", preview)
            self.assertIn("+two", preview)
            self.assertIn("Final newline: yes → no", preview)
            _, preview, _, _ = action_preview(tools, "edit_file", {
                "path": "a.txt", "old_text": "one", "new_text": "three"})
            self.assertIn("+three", preview)
            _, preview, _, _ = action_preview(tools, "write_file", {"path": "a.txt", "content": "one\r\n"})
            self.assertIn("Line-level representation", preview)
            self.assertIn("Line endings: LF=1 → CRLF=1", preview)

    def test_file_approval_rejects_changed_source_and_atomic_failure_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "a.txt"
            path.write_text("first\n")
            path.chmod(0o640)
            tools = WorkspaceTools(root, allow_write=True)
            events = queue.Queue()
            manager = ApprovalManager(tools, lambda request_id, kind, payload: events.put((kind, payload)))
            answers = []
            thread = threading.Thread(target=lambda: answers.append(manager.request(
                "turn", "call", "write_file", {"path": "a.txt", "content": "second\n"})))
            thread.start()
            _, payload = events.get(timeout=2)
            path.write_text("external\n")
            manager.resolve(payload.split("\t")[1], "once")
            thread.join(timeout=2)
            self.assertEqual(answers, [False])
            self.assertEqual(path.read_text(), "external\n")
            expected = tools.prepare_change("write_file", {"path": "a.txt", "content": "new\n"})
            path.write_text("newer source\n")
            with self.assertRaisesRegex(ValueError, "changed since approval"):
                tools.execute("write_file", {"path": "a.txt", "content": "new\n"}, expected_change=expected)
            self.assertEqual(path.read_text(), "newer source\n")
            path.write_text("external\n")
            with patch("mupyjava.file_ops.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    tools.execute("write_file", {"path": "a.txt", "content": "new\n"})
            self.assertEqual(path.read_text(), "external\n")
            self.assertEqual(list(root.glob(".mupyjava-*")), [])
            changes = []
            tools.execute("write_file", {"path": "a.txt", "content": "new\n"}, on_change=changes.append)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(changes[0]["before_bytes"], len("external\n"))
            self.assertEqual(changes[0]["after_bytes"], len("new\n"))
            self.assertNotIn("content", changes[0])

    def test_same_path_edits_are_serialized_through_operations_interface(self):
        class BlockingOperations(LocalFileOperations):
            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()

            def replace_text(self, path, content):
                if content == "B":
                    self.entered.set()
                    self.release.wait(timeout=2)
                super().replace_text(path, content)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("A")
            operations = BlockingOperations()
            tools = WorkspaceTools(root, allow_write=True, file_ops=operations)
            errors = []
            def edit(old, new):
                try:
                    tools.execute("edit_file", {"path": "a.txt", "old_text": old, "new_text": new})
                except Exception as error:
                    errors.append(error)
            first = threading.Thread(target=edit, args=("A", "B"))
            second = threading.Thread(target=edit, args=("B", "C"))
            first.start()
            self.assertTrue(operations.entered.wait(timeout=2))
            second.start()
            operations.release.set()
            first.join(timeout=2)
            second.join(timeout=2)
            self.assertEqual(errors, [])
            self.assertEqual((root / "a.txt").read_text(), "C")

    def test_multi_edit_matches_original_and_preserves_bom_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.py"
            path.write_bytes(b"\xef\xbb\xbffirst = 1\r\nsecond = 2\r\n")
            tools = WorkspaceTools(root, allow_write=True)
            arguments = {"path": "source.py", "edits": [
                {"old_text": "first = 1", "new_text": "first = 10"},
                {"old_text": "second = 2", "new_text": "second = 20"},
            ]}
            proposed = tools.prepare_change("edit_file", arguments)
            self.assertEqual(proposed["edit_count"], 2)
            self.assertIn("first = 10", proposed["diff"])
            self.assertIn("+second = 20", proposed["diff"])
            changes = []
            self.assertEqual(tools.execute("edit_file", arguments, on_change=changes.append), "Edited source.py")
            self.assertEqual(path.read_bytes(), b"\xef\xbb\xbffirst = 10\r\nsecond = 20\r\n")
            self.assertEqual(changes[0]["edit_count"], 2)

    def test_multi_edit_rejects_overlap_without_changing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.txt"
            path.write_text("abcdef")
            tools = WorkspaceTools(root, allow_write=True)
            with self.assertRaisesRegex(ValueError, "overlap"):
                tools.execute("edit_file", {"path": "source.txt", "edits": [
                    {"old_text": "abc", "new_text": "A"},
                    {"old_text": "bcd", "new_text": "B"},
                ]})
            self.assertEqual(path.read_text(), "abcdef")

    def test_multi_edit_matches_all_blocks_against_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.txt"
            path.write_text("alpha beta")
            tools = WorkspaceTools(root, allow_write=True)
            tools.execute("edit_file", {"path": "source.txt", "edits": [
                {"old_text": "alpha", "new_text": "beta"},
                {"old_text": "beta", "new_text": "gamma"},
            ]})
            self.assertEqual(path.read_text(), "beta gamma")

    def test_virtual_operations_support_list_read_and_edit_without_local_file(self):
        class VirtualOperations:
            def __init__(self, root):
                self.root = root.resolve()
                self.content = "alpha\nbeta\n"

            def exists(self, path):
                return path == self.root / "virtual.txt"

            def is_dir(self, path):
                return path == self.root

            def list_dir(self, path, cancel=None):
                return ["virtual.txt"]

            def read_text(self, path):
                return self.content

            def open_text(self, path, cancel=None):
                return io.StringIO(self.content)

            def size(self, path):
                return len(self.content.encode("utf-8"))

            def replace_text(self, path, content):
                self.content = content

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operations = VirtualOperations(root)
            tools = WorkspaceTools(root, allow_write=True, file_ops=operations)
            self.assertEqual(json.loads(tools.execute("list_files", {"path": "."})), ["virtual.txt"])
            self.assertFalse((root / "virtual.txt").exists())
            self.assertEqual(tools.execute("read_file", {"path": "virtual.txt", "offset": 2}), "beta\n")
            self.assertIn("use offset=2", tools.execute("read_file", {"path": "virtual.txt", "limit": 1}))
            tools.execute("edit_file", {"path": "virtual.txt", "edits": [
                {"old_text": "alpha", "new_text": "ALPHA"},
                {"old_text": "beta", "new_text": "BETA"},
            ]})
            self.assertEqual(operations.content, "ALPHA\nBETA\n")
            self.assertFalse((root / "virtual.txt").exists())

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "JDK is unavailable")
    def test_java_smoke_cancels_streaming_command_and_marks_turn_interrupted(self):
        command = shlex.quote(sys.executable) + " -u -c " + shlex.quote(
            "import time; print('started', flush=True); time.sleep(5)")

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if payload["messages"][-1]["role"] == "tool":
                    message = {"role": "assistant", "content": "Finished."}
                else:
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "command-1", "type": "function", "function": {"name": "run_command",
                            "arguments": json.dumps({"command": command})},
                    }]}
                body = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            repository = Path(__file__).resolve().parents[2]
            classes = root / "classes"
            classes.mkdir()
            try:
                subprocess.run(["javac", "-d", str(classes),
                                *map(str, (repository / "java/src/main/java/dev/mupyjava").glob("*.java"))],
                               check=True, capture_output=True, text=True)
                env = os.environ.copy()
                env.update({"MU_MODEL": "fixture", "MU_MODEL_BACKEND": "", "MU_JUDGE_MODE": "off",
                            "MU_PYTHON": sys.executable, "MU_SESSION_DIR": str(root / "sessions"),
                            "MU_API_BASE": f"http://127.0.0.1:{server.server_port}/v1"})
                result = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                         "--workspace", str(workspace), "--smoke", "Run the command",
                                         "--smoke-approval", "once", "--smoke-cancel-on-update"],
                                        cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("tool.update: started", result.stdout)
                self.assertIn("cancelled: Turn cancelled", result.stdout)
                self.assertNotIn("assistant: Finished.", result.stdout)
                store = SessionStore.open(workspace, root / "sessions", resume=True)
                self.assertEqual(len(store.restore_messages()), 1)
                kinds = [entry["type"] for entry in store.events()]
                self.assertIn("tool.started", kinds)
                self.assertIn("tool.cancelled", kinds)
                self.assertIn("turn.interrupted", kinds)
            finally:
                server.shutdown()
                server_thread.join(timeout=5)

    def test_streamed_command_output_can_be_read_after_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore.open(root, root / "state", resume=False)
            tools = WorkspaceTools(root, allow_command=True, output_root=store.output_dir)
            updates = []
            artifacts = []
            command = shlex.quote(sys.executable) + " -u -c " + shlex.quote(
                "import sys; sys.stdout.write('begin\\n' + 'x'*20000); sys.stdout.flush()")
            output = tools.execute("run_command", {"command": command}, on_update=updates.append,
                                   on_artifact=artifacts.append)
            self.assertIn("[Output truncated at 12 KB]", output)
            self.assertTrue(any("begin" in update for update in updates))
            output_id = output.split("Full output id: ")[1].strip()
            self.assertEqual(artifacts, [output_id])
            full = tools.execute("read_command_output", {"id": output_id, "offset": 0, "limit": 50_000})
            self.assertEqual(full, "begin\n" + "x" * 20_000)
            first = tools.execute("read_command_output", {"id": output_id, "offset": 0, "limit": 10})
            self.assertIn("offset=10", first)
            self.assertEqual(tools.execute("read_command_output", {"id": output_id, "offset": 10,
                                                                    "limit": 50_000}), full[10:])
            reopened = SessionStore.open(root, root / "state", resume=True)
            resumed_tools = WorkspaceTools(root, output_root=reopened.output_dir)
            self.assertEqual(resumed_tools.execute("read_command_output", {"id": output_id,
                                                                             "limit": 50_000}), full)

    def test_cancellation_stops_command_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "child-ran.txt"
            child_code = "import time; from pathlib import Path; time.sleep(0.7); Path(" + repr(str(marker)) + ").write_text('ran')"
            parent_code = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c',"
                           + repr(child_code) + "]); print('started',flush=True)")
            command = shlex.quote(sys.executable) + " -u -c " + shlex.quote(parent_code)
            token = CancellationToken()
            tools = WorkspaceTools(root, allow_command=True)
            started = time.monotonic()

            def update(chunk):
                if "started" in chunk:
                    token.cancel()

            with self.assertRaises(TurnCancelled):
                tools.execute("run_command", {"command": command}, cancel=token, on_update=update)
            self.assertLess(time.monotonic() - started, 2)
            time.sleep(0.9)
            self.assertFalse(marker.exists())

    def test_model_wait_can_be_cancelled_without_running_later_tools(self):
        started = threading.Event()

        class SlowModel:
            def complete(self, messages, tools):
                started.set()
                time.sleep(2)
                return {"role": "assistant", "content": "late"}

        with tempfile.TemporaryDirectory() as directory:
            token = CancellationToken()
            agent = Agent(SlowModel(), WorkspaceTools(Path(directory)), DecisionEngine())
            errors = []

            def run():
                try:
                    list(agent.run("hello", cancel=token))
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            self.assertTrue(started.wait(timeout=1))
            token.cancel()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(errors[0], TurnCancelled)

    def test_session_store_restores_completed_turns_and_marks_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            store = SessionStore.open(workspace, root / "state", resume=False)
            first_id = store.session_id
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            store.append("turn.started", {"request_id": "one"}, "turn-one")
            store.append("message", {"message": {"role": "user", "content": "hello"}}, "turn-one")
            store.append("message", {"message": {"role": "assistant", "content": "hi"}}, "turn-one")
            store.append("display", {"kind": "you", "text": "hello"}, "turn-one")
            store.append("judge.record", {"point": "tool.intent", "version": 2, "tool": "write_file",
                                          "answer": False, "outcome": True, "source": "fallback",
                                          "mode": "shadow", "latency_ms": 1.2}, "turn-one")
            store.append("turn.completed", {}, "turn-one")
            store.append("turn.started", {"request_id": "two"}, "turn-two")
            store.append("message", {"message": {"role": "user", "content": "unfinished"}}, "turn-two")
            reopened = SessionStore.open(workspace, root / "state", resume=True)
            self.assertEqual(reopened.session_id, first_id)
            self.assertEqual([message["content"] for message in reopened.restore_messages()[1:]], ["hello", "hi"])
            self.assertIn("turn.interrupted", [entry["type"] for entry in reopened.events()])
            history = reopened.history()
            self.assertTrue(any(kind == "history.judge" and "tool.intent" in text for kind, text in history))
            self.assertTrue(any("actions were not replayed" in text for _, text in history))
            fresh = SessionStore.open(workspace, root / "state", resume=False)
            self.assertNotEqual(fresh.session_id, first_id)
            self.assertEqual(len(fresh.restore_messages()), 1)

    def test_server_history_survives_restart_and_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = os.environ.copy()
            env.update({"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                        "MU_MODEL_BACKEND": "echo", "MU_JUDGE_MODE": "off",
                        "MU_SESSION_DIR": str(root / "sessions")})

            def launch():
                process = subprocess.Popen([sys.executable, "-m", "mupyjava", "--server",
                                            "--workspace", str(root / "workspace")],
                                           env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True)
                lines = queue.Queue()
                reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
                reader.start()
                return process, lines, reader

            def receive(lines, terminal):
                events = []
                while True:
                    fields = lines.get(timeout=5).rstrip("\n").split("\t")
                    self.assertEqual(fields[0], "EVENT")
                    events.append((fields[1], fields[2], decode_text(fields[3])))
                    if fields[2] == terminal:
                        return events

            def stop(process, reader):
                process.stdin.close()
                process.wait(timeout=5)
                reader.join(timeout=5)
                process.stdout.close()
                error = process.stderr.read()
                process.stderr.close()
                self.assertEqual(process.returncode, 0, error)

            (root / "workspace").mkdir()
            process, lines, reader = launch()
            try:
                process.stdin.write("CHAT\tone\t" + encode_text("hello") + "\n")
                process.stdin.flush()
                first = receive(lines, "done")
                self.assertIn(("one", "assistant", "Echo: hello"), first)
                process.stdin.write("HISTORY\thistory-one\n")
                process.stdin.flush()
                history = receive(lines, "history.done")
                session_id = next(text for _, kind, text in history if kind == "session.info")
            finally:
                stop(process, reader)

            process, lines, reader = launch()
            try:
                process.stdin.write("HISTORY\thistory-two\n")
                process.stdin.flush()
                history = receive(lines, "history.done")
                self.assertIn(("history-two", "session.info", session_id), history)
                self.assertTrue(any(kind == "history.transcript" and "Echo: hello" in text
                                    for _, kind, text in history))
                process.stdin.write("NEW\tnew-session\n")
                process.stdin.flush()
                new_events = receive(lines, "done")
                new_id = next(text for _, kind, text in new_events if kind == "session.info")
                self.assertNotEqual(new_id, session_id)
                process.stdin.write("HISTORY\thistory-new\n")
                process.stdin.flush()
                fresh = receive(lines, "history.done")
                self.assertFalse(any(kind == "history.transcript" for _, kind, _ in fresh))
            finally:
                stop(process, reader)

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "JDK is unavailable")
    def test_java_smoke_approval_round_trip(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                if payload["messages"][-1]["role"] == "tool" or isinstance(payload["messages"][-1]["content"], list):
                    message = {"role": "assistant", "content": "Finished."}
                else:
                    prompt = payload["messages"][-1]["content"]
                    if prompt == "Edit note.txt":
                        name, arguments = "edit_file", {"path": "note.txt", "old_text": "created", "new_text": "edited"}
                    elif prompt == "Fail edit note.txt":
                        name, arguments = "edit_file", {"path": "note.txt", "old_text": "missing", "new_text": "bad"}
                    elif prompt == "Batch edit note.txt":
                        name, arguments = "edit_file", {"path": "note.txt", "edits": [
                            {"old_text": "re", "new_text": "up"},
                            {"old_text": "placed", "new_text": "dated"},
                        ]}
                    elif prompt == "Replace note.txt":
                        name, arguments = "write_file", {"path": "note.txt", "content": "replaced"}
                    elif prompt == "Run Bash":
                        name, arguments = "bash", {"command": "printf shell-ok > shell.txt; cat shell.txt"}
                    elif prompt == "Run custom export":
                        name, arguments = "custom_export", {"path": "export.txt"}
                    elif prompt == "Read picture":
                        name, arguments = "read_file", {"path": "picture.png"}
                    elif prompt == "Fuzzy edit":
                        name, arguments = "edit_file", {"path": "fuzzy.txt", "old_text": 'value = "old"',
                                            "new_text": 'value = "new"', "allow_fuzzy": True}
                    else:
                        name, arguments = "write_file", {"path": "note.txt", "content": "created"}
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "write-1", "type": "function", "function": {"name": name,
                            "arguments": json.dumps(arguments)},
                    }]}
                body = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            repository = Path(__file__).resolve().parents[2]
            classes = Path(directory) / "classes"
            classes.mkdir()
            try:
                subprocess.run(["javac", "-d", str(classes),
                                *map(str, (repository / "java/src/main/java/dev/mupyjava").glob("*.java"))],
                               check=True, capture_output=True, text=True)
                env = os.environ.copy()
                env.update({"MU_MODEL": "fixture", "MU_MODEL_BACKEND": "", "MU_JUDGE_MODE": "off",
                            "MU_PYTHON": sys.executable,
                            "MU_SESSION_DIR": str(Path(directory) / "sessions"),
                            "MU_API_BASE": f"http://127.0.0.1:{server.server_port}/v1"})
                result = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                         "--workspace", directory, "--smoke", "Write note.txt"],
                                        cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("User did not allow this tool action", result.stdout)
                self.assertFalse((Path(directory) / "note.txt").exists())
                store = SessionStore.open(Path(directory), Path(directory) / "sessions", resume=True)
                store.append("judge.record", {"point": "tool.intent", "version": 2, "tool": "write_file",
                                              "answer": False, "outcome": True, "source": "fallback",
                                              "mode": "shadow", "latency_ms": 1.2})
                allowed = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                          "--workspace", directory, "--smoke", "Write note.txt",
                                          "--smoke-approval", "once"],
                                         cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(allowed.returncode, 0, allowed.stderr)
                self.assertIn("history.judge: tool.intent v2", allowed.stdout)
                self.assertIn("tool: write_file: Wrote note.txt", allowed.stdout)
                self.assertEqual((Path(directory) / "note.txt").read_text(), "created")
                for prompt, expected in (("Edit note.txt", "edited"), ("Replace note.txt", "replaced"),
                                         ("Batch edit note.txt", "updated")):
                    result = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                             "--workspace", directory, "--smoke", prompt,
                                             "--smoke-approval", "once"],
                                            cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("tool.change:", result.stdout)
                    self.assertEqual((Path(directory) / "note.txt").read_text(), expected)
                failed = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                         "--workspace", directory, "--smoke", "Fail edit note.txt",
                                         "--smoke-approval", "once"],
                                        cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(failed.returncode, 0, failed.stderr)
                self.assertIn("old_text must occur exactly once", failed.stdout)
                self.assertEqual((Path(directory) / "note.txt").read_text(), "updated")
                fuzzy_path = Path(directory) / "fuzzy.txt"
                fuzzy_original = 'keep “unchanged”  \nvalue = “old”  \n'
                fuzzy_path.write_text(fuzzy_original)
                for answer in ("deny", "once"):
                    fuzzy = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                            "--workspace", directory, "--smoke", "Fuzzy edit",
                                            "--smoke-approval", answer], cwd=repository, env=env,
                                           capture_output=True, text=True, timeout=15)
                    self.assertEqual(fuzzy.returncode, 0, fuzzy.stderr)
                    if answer == "deny":
                        self.assertEqual(fuzzy_path.read_text(), fuzzy_original)
                    else:
                        self.assertEqual(fuzzy_path.read_text(), 'keep “unchanged”  \nvalue = "new"\n')
                        self.assertIn("First changed line: 2", fuzzy.stdout)
                        self.assertIn("tool.detail: edit_file: fuzzy.txt:2", fuzzy.stdout)
                store = SessionStore.open(Path(directory), Path(directory) / "sessions", resume=True)
                edit_result = next(item["payload"] for item in reversed(store.events())
                                   if item["type"] == "tool.result" and item["payload"]["tool"] == "edit_file")
                self.assertTrue(edit_result["details"]["change"]["used_fuzzy_match"])
                self.assertTrue(edit_result["details"]["change"]["patch"].startswith("--- a/fuzzy.txt"))
                self.assertTrue(any("fuzzy.txt:2" in text for _, text in store.history()))
                if shutil.which("bash"):
                    for answer in ("deny", "once"):
                        shell = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                                "--workspace", directory, "--smoke", "Run Bash",
                                                "--smoke-approval", answer],
                                               cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                        self.assertEqual(shell.returncode, 0, shell.stderr)
                        if answer == "deny":
                            self.assertFalse((Path(directory) / "shell.txt").exists())
                        else:
                            self.assertEqual((Path(directory) / "shell.txt").read_text(), "shell-ok")
                            self.assertIn("tool.detail: bash: exit code 0", shell.stdout)
                    store = SessionStore.open(Path(directory), Path(directory) / "sessions", resume=True)
                    result = next(item["payload"] for item in reversed(store.events())
                                  if item["type"] == "tool.result" and item["payload"]["tool"] == "bash")
                    self.assertEqual(result["version"], 1)
                    self.assertEqual(result["details"]["mode"], "bash")
                    self.assertTrue(any(kind == "history.transcript" and "bash: exit code 0" in text
                                        for kind, text in store.history()))
                (Path(directory) / "fixture_tools.py").write_text(
                    "from mupyjava.registry import ToolDefinition\n"
                    "from mupyjava.tool_result import ToolResult\n"
                    "def export(args, context):\n"
                    "    context.check_cancelled()\n"
                    "    context.resolve_path(args['path']).write_text('exported')\n"
                    "    return ToolResult.from_text('exported', {'count': 1})\n"
                    "def register_tools(registry):\n"
                    "    registry.register(ToolDefinition('custom_export', 'Export a file.', "
                    "{'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}, export))\n")
                env.update({"MU_TOOL_MODULES": "fixture_tools", "PYTHONPATH": directory})
                for answer in ("deny", "once"):
                    custom = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                             "--workspace", directory, "--smoke", "Run custom export",
                                             "--smoke-approval", answer], cwd=repository, env=env,
                                            capture_output=True, text=True, timeout=15)
                    self.assertEqual(custom.returncode, 0, custom.stderr)
                    self.assertIn("custom_export", [tool["function"]["name"] for tool in requests[-1]["tools"]])
                    if answer == "deny":
                        self.assertFalse((Path(directory) / "export.txt").exists())
                    else:
                        self.assertEqual((Path(directory) / "export.txt").read_text(), "exported")
                        self.assertIn("tool.detail: custom_export: 1 results", custom.stdout)
                try:
                    from PIL import Image
                except ImportError:
                    Image = None
                if Image is not None:
                    Image.new("RGB", (12, 8), "blue").save(Path(directory) / "picture.png")
                    env["MU_MODEL_SUPPORTS_IMAGES"] = "true"
                    image = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                                            "--workspace", directory, "--smoke", "Read picture"],
                                           cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(image.returncode, 0, image.stderr)
                    self.assertIn("12×8 image", image.stdout)
                    self.assertEqual(requests[-1]["messages"][-1]["content"][1]["type"], "image_url")
                    store = SessionStore.open(Path(directory), Path(directory) / "sessions", resume=True)
                    self.assertTrue(any("image_blocks" in message for message in store.restore_messages()))
            finally:
                server.shutdown()
                server_thread.join(timeout=5)

    def test_server_approval_round_trip_denies_then_allows(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if payload["messages"][-1]["role"] == "tool":
                    message = {"role": "assistant", "content": "Finished."}
                else:
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "write-1", "type": "function", "function": {"name": "write_file",
                            "arguments": json.dumps({"path": "note.txt", "content": "created"})},
                    }]}
                body = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            env = os.environ.copy()
            env.update({"PYTHONPATH": str(Path(__file__).resolve().parents[1]), "MU_MODEL": "fixture",
                        "MU_MODEL_BACKEND": "", "MU_JUDGE_MODE": "off",
                        "MU_SESSION_DIR": str(Path(directory) / "sessions"),
                        "MU_API_BASE": f"http://127.0.0.1:{server.server_port}/v1"})
            process = subprocess.Popen([sys.executable, "-m", "mupyjava", "--server", "--workspace", directory],
                                       env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()

            def read_event():
                fields = lines.get(timeout=5).rstrip("\n").split("\t")
                self.assertEqual(fields[0], "EVENT")
                return fields[1], fields[2], decode_text(fields[3])

            try:
                for turn, answer in (("one", "deny"), ("two", "once")):
                    process.stdin.write("CHAT\t" + turn + "\t" + encode_text("Write note.txt") + "\n")
                    process.stdin.flush()
                    request_id, kind, payload = read_event()
                    while kind.startswith("context."):
                        request_id, kind, payload = read_event()
                    self.assertEqual((request_id, kind), (turn, "approval.request"))
                    approval_id = payload.split("\t")[1]
                    process.stdin.write("APPROVAL\t" + approval_id + "\t" + answer + "\n")
                    process.stdin.flush()
                    events = []
                    while True:
                        event = read_event()
                        events.append(event)
                        if event[1] == "done":
                            break
                    self.assertIn((turn, "approval.resolved", "v1\t" + approval_id + "\t" + answer), events)
                    self.assertTrue(any(item[1] == "tool" for item in events))
                    self.assertEqual((Path(directory) / "note.txt").exists(), answer == "once")
            finally:
                process.stdin.close()
                process.wait(timeout=5)
                reader.join(timeout=5)
                process.stdout.close()
                stderr_text = process.stderr.read()
                process.stderr.close()
                server.shutdown()
                server_thread.join(timeout=5)
            self.assertEqual(process.returncode, 0, stderr_text)

    def test_approval_denial_prevents_write_and_rejects_duplicate_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            emitted = queue.Queue()
            tools = WorkspaceTools(root, allow_write=True)
            manager = ApprovalManager(tools, lambda request_id, kind, value: emitted.put((kind, value)))
            model = ScriptedModel([
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "write-1", "type": "function", "function": {"name": "write_file", "arguments":
                        json.dumps({"path": "note.txt", "content": "secret"})},
                }]},
                {"role": "assistant", "content": "Stopped."},
            ])
            agent = Agent(model, tools, DecisionEngine())
            events = []
            worker = threading.Thread(target=lambda: events.extend(agent.run(
                "Write note.txt", approval=lambda call_id, name, args:
                    manager.request("turn-1", call_id, name, args))), daemon=True)
            worker.start()
            kind, payload = emitted.get(timeout=2)
            self.assertEqual(kind, "approval.request")
            fields = payload.split("\t")
            self.assertEqual(fields[0], "v1")
            self.assertEqual(decode_text(fields[2]), "write-1")
            self.assertEqual(fields[3], "deny,once,session")
            self.assertIn("secret", decode_text(fields[5]))
            self.assertTrue(manager.resolve(fields[1], "deny"))
            self.assertFalse(manager.resolve(fields[1], "once"))
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertFalse((root / "note.txt").exists())
            self.assertIn("User did not allow", events[0][1])

    def test_approval_denial_prevents_edit_and_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / "note.txt"
            note.write_text("before", encoding="utf-8")
            marker = root / "started.txt"
            command = shlex.quote(sys.executable) + " -c " + shlex.quote(
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')")
            for name, arguments in (
                ("edit_file", {"path": "note.txt", "old_text": "before", "new_text": "after"}),
                ("run_command", {"command": command}),
            ):
                with self.subTest(name=name):
                    emitted = queue.Queue()
                    tools = WorkspaceTools(root, allow_write=True, allow_command=True)
                    manager = ApprovalManager(tools, lambda request_id, kind, value: emitted.put((kind, value)))
                    model = ScriptedModel([
                        {"role": "assistant", "content": None, "tool_calls": [{"id": name, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)}}]},
                        {"role": "assistant", "content": "Finished."},
                    ])
                    agent = Agent(model, tools, DecisionEngine())
                    events = []
                    worker = threading.Thread(target=lambda: events.extend(agent.run(
                        "Do the action", approval=lambda call_id, tool_name, args:
                            manager.request("turn-1", call_id, tool_name, args))), daemon=True)
                    worker.start()
                    kind, payload = emitted.get(timeout=2)
                    self.assertEqual(kind, "approval.request")
                    self.assertTrue(manager.resolve(payload.split("\t")[1], "deny"))
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive())
                    self.assertIn("User did not allow", events[0][1])
                    self.assertEqual(note.read_text(encoding="utf-8"), "before")
                    self.assertFalse(marker.exists())

    def test_session_grant_is_scoped_and_protected_file_requires_fresh_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            emitted = queue.Queue()
            manager = ApprovalManager(WorkspaceTools(root, allow_write=True),
                                      lambda request_id, kind, value: emitted.put((kind, value)))
            answers = []

            def request(path):
                answers.append(manager.request("turn-1", "call-1", "write_file", {"path": path, "content": "x"}))

            worker = threading.Thread(target=request, args=("note.txt",), daemon=True)
            worker.start()
            _, payload = emitted.get(timeout=2)
            approval_id = payload.split("\t")[1]
            self.assertTrue(manager.resolve(approval_id, "session"))
            worker.join(timeout=2)
            self.assertEqual(answers, [True])
            self.assertEqual(emitted.get(timeout=2)[0], "approval.resolved")
            self.assertTrue(manager.request("turn-1", "call-2", "write_file",
                                            {"path": "note.txt", "content": "new"}))
            self.assertTrue(emitted.empty())
            manager.reset_grants()
            worker = threading.Thread(target=request, args=("note.txt",), daemon=True)
            worker.start()
            kind, payload = emitted.get(timeout=2)
            self.assertEqual(kind, "approval.request")
            self.assertTrue(manager.resolve(payload.split("\t")[1], "deny"))
            worker.join(timeout=2)
            self.assertEqual(answers, [True, False])
            self.assertEqual(emitted.get(timeout=2)[0], "approval.resolved")

            worker = threading.Thread(target=request, args=(".env",), daemon=True)
            worker.start()
            while True:
                kind, payload = emitted.get(timeout=2)
                if kind == "approval.request":
                    break
            fields = payload.split("\t")
            self.assertEqual(fields[3], "deny,once")
            self.assertTrue(manager.resolve(fields[1], "session"))
            worker.join(timeout=2)
            self.assertEqual(answers, [True, False, False])

    def test_disconnect_denies_pending_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            emitted = queue.Queue()
            manager = ApprovalManager(WorkspaceTools(Path(directory), allow_command=True),
                                      lambda request_id, kind, value: emitted.put((kind, value)))
            answers = []
            worker = threading.Thread(target=lambda: answers.append(manager.request(
                "turn-1", "call-1", "run_command", {"command": "python3 -V"})), daemon=True)
            worker.start()
            kind, payload = emitted.get(timeout=2)
            self.assertEqual(kind, "approval.request")
            self.assertEqual(payload.split("\t")[3], "deny,once")
            manager.close()
            self.assertFalse(manager.resolve(payload.split("\t")[1], "once"))
            worker.join(timeout=2)
            self.assertEqual(answers, [False])

    def test_cancel_request_denies_pending_approval_without_closing_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            emitted = queue.Queue()
            manager = ApprovalManager(WorkspaceTools(Path(directory), allow_command=True),
                                      lambda request_id, kind, value: emitted.put((kind, value)))
            answers = []
            worker = threading.Thread(target=lambda: answers.append(manager.request(
                "turn-1", "call-1", "run_command", {"command": "python3 -V"})), daemon=True)
            worker.start()
            _, payload = emitted.get(timeout=2)
            approval_id = payload.split("\t")[1]
            manager.cancel_request("turn-1")
            worker.join(timeout=2)
            self.assertEqual(answers, [False])
            self.assertFalse(manager.resolve(approval_id, "once"))
            self.assertEqual(emitted.get(timeout=2)[0], "approval.resolved")

    def test_wire_round_trip_preserves_unicode_and_newlines(self):
        message = "你好\nPython + Java"
        self.assertEqual(parse_request("CHAT\t42\t" + encode_text(message) + "\n"), ("CHAT", "42", message))
        self.assertEqual(decode_text(event_line("42", "assistant", message).split("\t")[3]), message)

    def test_workspace_blocks_parent_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            (root / "escape").symlink_to(Path(directory))
            tools = WorkspaceTools(root)
            with self.assertRaises(PermissionError):
                tools.execute("read_file", {"path": "../secret.txt"})
            with self.assertRaises(PermissionError):
                tools.execute("read_file", {"path": "escape/secret.txt"})
            with self.assertRaises(PermissionError):
                tools.execute("write_file", {"path": "inside.txt", "content": "x"})
            with self.assertRaises(PermissionError):
                tools.execute("run_command", {"command": "echo x"})

    def test_tool_call_returns_result_to_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("hello", encoding="utf-8")
            model = ScriptedModel([
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function", "function": {
                        "name": "read_file", "arguments": json.dumps({"path": "note.txt"})
                    }
                }]},
                {"role": "assistant", "content": "The note says hello."},
            ])
            agent = Agent(model, WorkspaceTools(root), DecisionEngine())
            events = list(agent.run("Read note.txt"))
            self.assertEqual(events[-1], ("assistant", "The note says hello."))
            self.assertEqual(agent.messages[-2]["content"], "hello")

    def test_find_grep_and_edit_across_languages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "Main.java").write_text("class Main { int count = 1; }\n", encoding="utf-8")
            (root / "src" / "worker.py").write_text("count = 1\n", encoding="utf-8")
            tools = WorkspaceTools(root, allow_write=True)
            self.assertEqual(json.loads(tools.execute("find_files", {"path": ".", "glob": "**/*.java"}))["paths"],
                             ["src/Main.java"])
            matches = json.loads(tools.execute("grep_files", {"path": "src", "pattern": "count"}))["matches"]
            self.assertEqual([(m["path"], m["line"]) for m in matches],
                             [("src/Main.java", 1), ("src/worker.py", 1)])
            self.assertEqual(tools.execute("edit_file", {"path": "src/Main.java", "old_text": "count = 1",
                                                         "new_text": "count = 2"}), "Edited src/Main.java")
            self.assertIn("count = 2", (root / "src" / "Main.java").read_text())

    def test_edit_rejects_ambiguous_match_and_outside_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            (root / "a.py").write_text("old old", encoding="utf-8")
            (root / "external").symlink_to(Path(directory))
            tools = WorkspaceTools(root, allow_write=True)
            with self.assertRaisesRegex(ValueError, "exactly once"):
                tools.execute("edit_file", {"path": "a.py", "old_text": "old", "new_text": "new"})
            with self.assertRaises(PermissionError):
                tools.execute("edit_file", {"path": "external/secret", "old_text": "x", "new_text": "y"})
            self.assertEqual(json.loads(tools.execute("find_files", {"path": ".", "glob": "**/*"}))["paths"], ["a.py"])

    def test_read_file_pages_large_utf8_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.py").write_text("".join(f"行 {i}\n" for i in range(2500)), encoding="utf-8")
            tools = WorkspaceTools(root)
            first = tools.execute("read_file", {"path": "large.py", "limit": 2})
            self.assertIn("行 0\n行 1\n", first)
            self.assertIn("offset=3", first)
            self.assertEqual(tools.execute("read_file", {"path": "large.py", "offset": 2500}), "行 2499\n")
            with self.assertRaisesRegex(ValueError, "beyond end"):
                tools.execute("read_file", {"path": "large.py", "offset": 2501})

    def test_agent_passes_complete_read_page_to_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text("line with data\n" * 1300, encoding="utf-8")
            model = ScriptedModel([
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "read-large", "type": "function", "function": {
                        "name": "read_file", "arguments": json.dumps({"path": "large.txt"}),
                    },
                }]},
                {"role": "assistant", "content": "Read."},
            ])
            agent = Agent(model, WorkspaceTools(root), DecisionEngine())
            list(agent.run("Read large.txt"))
            self.assertGreater(len(agent.messages[-2]["content"]), 12_000)
            self.assertIn("line with data", agent.messages[-2]["content"][-100:])

    def test_regex_search_respects_gitignore_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
            (root / "keep.py").write_text("count=12\nnext line\n", encoding="utf-8")
            (root / "ignored.py").write_text("count=99\n", encoding="utf-8")
            tools = WorkspaceTools(root)
            self.assertEqual(tools.execute("find_files", {"path": ".", "glob": "**/*.py"}),
                             '{"paths": ["keep.py"], "truncated": false}')
            result = json.loads(tools.execute("grep_files", {"path": ".", "pattern": "count=[0-9]+",
                                                           "glob": "**/*.py", "context": 1}))
            self.assertEqual([(m["path"], m["line"], m["kind"]) for m in result["matches"]],
                             [("keep.py", 1, "match"), ("keep.py", 2, "context")])
            self.assertEqual(json.loads(tools.execute("grep_files", {"path": ".", "pattern": "COUNT=12",
                                                                  "ignore_case": True}))["matches"][0]["path"], "keep.py")

    def test_search_truncation_keeps_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "many.py").write_text("".join(f"match_{i} " + "x" * 150 + "\n" for i in range(200)))
            output = WorkspaceTools(root).execute("grep_files", {"path": ".", "pattern": "match_"})
            result = json.loads(output)
            self.assertTrue(result["truncated"])
            self.assertLess(len(output.encode("utf-8")), 12_000)

    def test_command_output_is_bounded_and_timeout_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory), allow_command=True)
            python = shlex.quote(sys.executable)
            output = tools.execute("run_command", {"command": python + " -c 'print(\"x\"*20000)'"})
            self.assertIn("[Output truncated at 12 KB]", output)
            self.assertLess(len(output), 12_200)
            with self.assertRaises(subprocess.TimeoutExpired):
                tools.execute("run_command", {"command": python + " -c 'import time; time.sleep(3)'",
                                              "timeout": 1})

    def test_edit_action_is_judged_without_file_content(self):
        class CapturingJudge:
            state = None

            def answer(self, question, state):
                self.state = state
                return True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.py").write_text("private_old = 1", encoding="utf-8")
            judge = CapturingJudge()
            model = ScriptedModel([
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "edit-1", "type": "function", "function": {"name": "edit_file", "arguments": json.dumps({
                        "path": "note.py", "old_text": "private_old = 1", "new_text": "private_new = 2",
                    })},
                }]},
                {"role": "assistant", "content": "Done."},
            ])
            agent = Agent(model, WorkspaceTools(root, allow_write=True), DecisionEngine("shadow", judge))
            events = list(agent.run("Change note.py"))
            self.assertEqual(events[-1], ("assistant", "Done."))
            self.assertEqual(events[0][0], "judge")
            self.assertEqual(judge.state["tool"], "edit_file")
            self.assertNotIn("private_old", json.dumps(judge.state))
            self.assertIn("private_new", (root / "note.py").read_text())

    def test_active_judge_can_decline_edit_before_mutation(self):
        class NoJudge:
            def answer(self, question, state):
                return False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.py").write_text("value = 1", encoding="utf-8")
            model = ScriptedModel([
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "edit-2", "type": "function", "function": {"name": "edit_file", "arguments": json.dumps({
                        "path": "note.py", "old_text": "value = 1", "new_text": "value = 2",
                    })},
                }]},
                {"role": "assistant", "content": "Done."},
            ])
            events = list(Agent(model, WorkspaceTools(root, allow_write=True),
                                DecisionEngine("active", NoJudge())).run("Explain note.py without editing"))
            self.assertIn("Judge declined", events[1][1])
            self.assertEqual((root / "note.py").read_text(), "value = 1")

    def test_git_inspection_needs_no_command_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            file = root / "Main.java"
            file.write_text("class Main {}\n", encoding="utf-8")
            subprocess.run(["git", "add", "Main.java"], cwd=root, check=True)
            file.write_text("class Main { int n; }\n", encoding="utf-8")
            tools = WorkspaceTools(root)
            self.assertIn("Main.java", tools.execute("git_status", {}))
            self.assertIn("+class Main { int n; }", tools.execute("git_diff", {"path": "Main.java"}))
            with self.assertRaises(PermissionError):
                tools.execute("run_command", {"command": "git diff"})

    def test_judge_shadow_and_active_modes_record_distinct_outcomes(self):
        class YesJudge:
            def answer(self, question, state):
                return True

        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            point = DecisionPoint("example", 1, "Is it useful?", False)
            self.assertFalse(DecisionEngine("shadow", YesJudge(), ledger).decide(point, {}))
            self.assertTrue(DecisionEngine("active", YesJudge(), ledger).decide(point, {}))
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual([row["source"] for row in rows], ["fallback", "judge"])

    def test_laya_adapter_uses_typed_boolean_and_abstains(self):
        class FakeLaya:
            probability = 0.9

            def predict(self, state, questions):
                self.state = state
                self.questions = questions
                return {"answers": {"intent": {"noul": self.probability}}}

        fake = FakeLaya()
        judge = LayaBooleanJudge("unused", agent=fake)
        state = {"user_request": "Read the file", "tool": "write_file", "arguments": {"path": "x"}}
        self.assertTrue(judge.answer("Needed?", state))
        self.assertEqual(fake.questions["intent"]["type"], "noul")
        self.assertEqual(fake.state, state)
        fake.probability = 0.5
        self.assertIsNone(judge.answer("Needed?", state))
        fake.probability = 0.1
        self.assertFalse(judge.answer("Needed?", state))

    def test_http_judge_records_probability_in_shadow_ledger(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.received = payload
                body = b'{"probability": 0.97}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                backend = LayaHttpBooleanJudge(f"http://127.0.0.1:{server.server_port}")
                point = DecisionPoint("tool.intent", 2, "Needed?", False)
                engine = DecisionEngine("shadow", backend, Path(directory) / "ledger.jsonl")
                self.assertFalse(engine.decide(point, {"tool": "edit_file", "user_request": "Edit a file"}))
                record = json.loads((Path(directory) / "ledger.jsonl").read_text())
                self.assertEqual(record["probability"], 0.97)
                self.assertEqual(record["answer"], True)
                self.assertEqual(record["tool"], "edit_file")
                self.assertEqual(server.received["question"], "Needed?")
            finally:
                server.shutdown()
                worker.join()
        with self.assertRaises(ValueError):
            LayaHttpBooleanJudge("http://example.com:18765")

    def test_write_judgment_excludes_file_content(self):
        class CapturingJudge:
            state = None

            def answer(self, question, state):
                self.state = state
                return True

        with tempfile.TemporaryDirectory() as directory:
            judge = CapturingJudge()
            model = ScriptedModel([
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-2", "type": "function", "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "note.txt", "content": "private text"}),
                    },
                }]},
                {"role": "assistant", "content": "Done."},
            ])
            agent = Agent(model, WorkspaceTools(Path(directory), allow_write=True), DecisionEngine("active", judge))
            self.assertEqual(list(agent.run("Write note.txt"))[-1], ("assistant", "Done."))
            self.assertNotIn("private text", json.dumps(judge.state))
            self.assertEqual((Path(directory) / "note.txt").read_text(), "private text")


if __name__ == "__main__":
    unittest.main()
