import json
import tempfile
import unittest
from pathlib import Path

from mupyjava.agent import Agent
from mupyjava.judge import DecisionEngine, DecisionPoint, LayaBooleanJudge
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
