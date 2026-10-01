"""File recovery closes the loop after an incorrectly allowed local write."""

import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from mupyjava.agent import Agent
from mupyjava.file_ops import LocalFileOperations
from mupyjava.judge import DecisionEngine
from mupyjava.permissions import ApprovalManager, action_preview
from mupyjava.tools import WorkspaceTools


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    def complete(self, messages, tools):
        return next(self.replies)


def call(name, arguments, call_id):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function", "function": {"name": name,
        "arguments": json.dumps(arguments)}}]}


class RecoveryTests(unittest.TestCase):
    def test_restore_requires_fresh_approval_even_with_full_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root, allow_write=True, recovery_enabled=True,
                                   output_root=root / "private")
            recovery_id = tools.execute_result("write_file", {"path": "a.txt", "content": "new"}).details["change"]["recovery_id"]
            events = []
            manager = ApprovalManager(tools, lambda *args: events.append(args), full_write=True)
            answers = []
            worker = threading.Thread(target=lambda: answers.append(manager.request(
                "turn", "restore", "restore_file_change", {"id": recovery_id})))
            worker.start()
            for _ in range(100):
                if events:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][1], "approval.request")
            self.assertIn("deny,once", events[0][2])
            manager.resolve(events[0][2].split("\t")[1], "deny")
            worker.join(timeout=2)
            self.assertEqual(answers, [False])
            self.assertEqual((root / "a.txt").read_text(), "new")

    def test_alternate_backend_does_not_advertise_local_recovery(self):
        class Backend(LocalFileOperations):
            pass

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "local file operations"):
                WorkspaceTools(Path(directory), recovery_enabled=True, file_ops=Backend())
            tools = WorkspaceTools(Path(directory), file_ops=Backend())
            self.assertNotIn("restore_file_change", [schema["function"]["name"] for schema in tools.schemas])

    def test_existing_file_roundtrip_and_changed_target_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.txt"
            source.write_bytes(b"old\r\n")
            source.chmod(0o640)
            tools = WorkspaceTools(root, allow_write=True, recovery_enabled=True,
                                   output_root=root / "private")
            result = tools.execute_result("write_file", {"path": "a.txt", "content": "new\n"})
            recovery_id = result.details["change"]["recovery_id"]
            receipt = root / "private" / "recovery" / (recovery_id + ".json")
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
            self.assertIn("restore_file_change", [item["function"]["name"] for item in tools.schemas])
            summary, preview, grant, _ = action_preview(tools, "restore_file_change", {"id": recovery_id})
            self.assertIn("a.txt", summary)
            self.assertIn("-new", preview)
            self.assertIsNone(grant)
            source.write_text("another\n")
            with self.assertRaisesRegex(ValueError, "changed after"):
                tools.execute("restore_file_change", {"id": recovery_id})
            self.assertEqual(source.read_text(), "another\n")
            source.write_text("new\n")
            change = tools.execute_result("restore_file_change", {"id": recovery_id}).details["change"]
            self.assertEqual(change["path"], "a.txt")
            self.assertEqual(source.read_bytes(), b"old\r\n")
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o640)
            with self.assertRaises(FileNotFoundError):
                tools.execute("restore_file_change", {"id": recovery_id})

    def test_new_file_removed_and_redirect_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root, allow_write=True, recovery_enabled=True,
                                   output_root=root / "private")
            result = tools.execute_result("write_file", {"path": "a.txt", "content": "new"})
            recovery_id = result.details["change"]["recovery_id"]
            tools.execute("restore_file_change", {"id": recovery_id})
            self.assertFalse((root / "a.txt").exists())
            result = tools.execute_result("write_file", {"path": "a.txt", "content": "new"})
            recovery_id = result.details["change"]["recovery_id"]
            (root / "a.txt").unlink()
            if os.name != "nt":
                (root / "a.txt").symlink_to(root / "private" / "other.txt")
                with self.assertRaisesRegex(ValueError, "redirected"):
                    tools.execute("restore_file_change", {"id": recovery_id})

    def test_agent_can_undo_after_wrong_write_with_separate_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("old")
            tools = WorkspaceTools(root, allow_write=True, recovery_enabled=True,
                                   output_root=root / "private")
            model = ScriptedModel([call("write_file", {"path": "a.txt", "content": "wrong"}, "write"),
                                   {"role": "assistant", "content": "done"}])
            agent = Agent(model, tools, DecisionEngine())
            approved = []
            list(agent.run("Update a.txt", approval=lambda *args: approved.append(args[1]) or True))
            recovery_id = next(path.stem for path in (root / "private" / "recovery").glob("*.json"))
            self.assertEqual((root / "a.txt").read_text(), "wrong")
            agent.model = ScriptedModel([call("restore_file_change", {"id": recovery_id}, "restore"),
                                         {"role": "assistant", "content": "undone"}])
            list(agent.run("Undo that write", approval=lambda *args: approved.append(args[1]) or True))
            self.assertEqual((root / "a.txt").read_text(), "old")
            self.assertEqual(approved, ["write_file", "restore_file_change"])


if __name__ == "__main__":
    unittest.main()
