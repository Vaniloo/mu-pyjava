import json
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from mupyjava.agent import Agent
from mupyjava.judge import DecisionEngine, DecisionPoint, LayaBooleanJudge, LayaHttpBooleanJudge
from mupyjava.tools import WorkspaceTools
from mupyjava.wire import decode_text, encode_text, event_line, parse_request


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    def complete(self, messages, tools):
        return next(self.replies)


class CoreTests(unittest.TestCase):
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
