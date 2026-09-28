import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mupyjava.agent import Agent
from mupyjava.editing import change_metadata
from mupyjava.judge import DecisionEngine
from mupyjava.permissions import ApprovalManager
from mupyjava.tools import WorkspaceTools


class EditingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "Git is needed to verify exported patches")
    def test_standard_patch_reconstructs_actual_bytes(self):
        cases = [
            ("file.txt", "one\ntwo\n", "one\nTWO\n", True),
            ("file.txt", "one", "one\n", True),
            ("file.txt", "one\n", "one", True),
            ("file.txt", "one", "two", True),
            ("file.txt", "one\ntwo\n", "", True),
            ("file.txt", "", "new file\n", False),
            ("file.txt", "", "new file", False),
            ("file.txt", "", "new", True),
            ("file.txt", "\ufeffone\r\ntwo\r\n", "\ufeffone\r\nTWO\r\n", True),
            ("dir/note with spaces.txt", "old\n", "new\n", True),
            ('dir/注释\t"name".txt', "old", "new", True),
        ]
        for name, before, after, existed in cases:
            with self.subTest(name=name, before=before, after=after), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if existed:
                    target.write_bytes(before.encode("utf-8"))
                metadata = change_metadata(name, before, after, existed)
                patch_path = root / "change.patch"
                patch_path.write_bytes(metadata["patch"].encode("utf-8"))
                result = subprocess.run(["git", "apply", "--check", str(patch_path)], cwd=root,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                subprocess.run(["git", "apply", str(patch_path)], cwd=root, check=True, capture_output=True)
                self.assertEqual(target.read_bytes(), after.encode("utf-8"))

    def test_patch_navigation_multiple_hunks_and_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = "".join(f"line {index}\n" for index in range(1, 31))
            target = root / "source.txt"
            target.write_text(before)
            tools = WorkspaceTools(root, allow_write=True)
            result = tools.execute_result("edit_file", {"path": "source.txt", "edits": [
                {"old_text": "line 3\n", "new_text": "changed 3\ninserted\n"},
                {"old_text": "line 25\n", "new_text": "changed 25\n"},
            ]})
            change = result.details["change"]
            self.assertEqual(change["first_changed_line"], 3)
            self.assertEqual(len(change["hunks"]), 2)
            self.assertIn("+ 3 changed 3", change["display_diff"])
            self.assertIn("+26 changed 25", change["display_diff"])
            self.assertFalse(change["used_fuzzy_match"])
            self.assertEqual(change["after_bytes"], len(target.read_bytes()))
            deletion = change_metadata("file.txt", "a\nb", "a\n")
            self.assertEqual(deletion["first_changed_line"], 2)
            self.assertEqual(change_metadata("file.txt", "only", "")["first_changed_line"], 1)
            noop = tools.prepare_change("write_file", {"path": "empty.txt", "content": ""})
            self.assertFalse(noop["existed"])
            self.assertEqual(noop["patch"], "")
            self.assertIsNone(noop["first_changed_line"])
            self.assertEqual(noop["hunks"], [])

    def test_fuzzy_opt_in_preserves_untouched_unicode_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "source.txt"
            original = '\ufeffuntouched “quote”  \r\nvalue = “old”  \r\nuntouched “quote”  \r\ncount = 1\r\n'
            target.write_bytes(original.encode())
            tools = WorkspaceTools(root, allow_write=True)
            arguments = {"path": "source.txt", "edits": [
                {"old_text": 'value = "old"', "new_text": 'value = "new"'},
                {"old_text": "count = 1", "new_text": "count = 2"},
            ]}
            with self.assertRaisesRegex(ValueError, "exactly once"):
                tools.execute_result("edit_file", arguments)
            self.assertEqual(target.read_bytes(), original.encode())
            arguments["allow_fuzzy"] = True
            proposed = tools.prepare_change("edit_file", arguments)
            result = tools.execute_result("edit_file", arguments, expected_change=proposed)
            self.assertEqual(target.read_bytes(), '\ufeffuntouched “quote”  \r\nvalue = "new"\r\nuntouched “quote”  \r\ncount = 2\r\n'.encode())
            self.assertEqual(result.details["change"], {key: value for key, value in proposed.items() if key != "content"})
            self.assertEqual(proposed["fuzzy_edit_indices"], [0])
            self.assertEqual(proposed["normalized_line_ranges"], [
                {"start_line": 2, "end_line": 2}, {"start_line": 4, "end_line": 4}])
            target.write_text('before “stay”  \nﬁrst = “x”; count = 1  \nafter “stay”  \n')
            result = tools.execute_result("edit_file", {"path": "source.txt", "allow_fuzzy": True, "edits": [
                {"old_text": 'first = "x"', "new_text": "first = LONG"},
                {"old_text": "count = 1", "new_text": "count = 2"},
            ]})
            self.assertEqual(target.read_text(), 'before “stay”  \nfirst = LONG; count = 2\nafter “stay”  \n')
            self.assertEqual(result.details["change"]["normalized_line_ranges"], [{"start_line": 2, "end_line": 2}])

    def test_fuzzy_nfkc_spaces_dashes_and_cross_line_replacements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "text"
            tools = WorkspaceTools(root, allow_write=True)
            for before, old, new, expected in [
                ("guard ‘stay’  \nﬁle — Ａ\u00a0B  \nend ‘stay’  \n", "file - A B", "updated",
                 "guard ‘stay’  \nupdated\nend ‘stay’  \n"),
                ("before\nleft  \nright  \nafter  \n", "left\nright", "joined", "before\njoined\nafter  \n"),
                ("left  \nright  \n", "left\n", "new\n", "new\nright  \n"),
            ]:
                with self.subTest(before=before):
                    target.write_text(before)
                    tools.execute_result("edit_file", {"path": "text", "old_text": old,
                                         "new_text": new, "allow_fuzzy": True})
                    self.assertEqual(target.read_text(), expected)

    def test_ambiguity_overlaps_and_empty_normalized_needles_leave_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "text"
            tools = WorkspaceTools(root, allow_write=True)
            cases = [
                ("aaa", [{"old_text": "aa", "new_text": "b"}], False),
                ('“same”\n"same"\n', [{"old_text": "‘same’", "new_text": "b"}], True),
                ('“same”\n“same”\n', [{"old_text": '"same"', "new_text": "b"}], True),
                ('value = “old”\n', [{"old_text": 'value = "old"', "new_text": "a"},
                                     {"old_text": '"old"', "new_text": "b"}], True),
                ('"same"\n“same”\n“other”\n', [{"old_text": '"same"', "new_text": "a"},
                                              {"old_text": '"other"', "new_text": "b"}], True),
                ("abc", [{"old_text": "  ", "new_text": "b"}], True),
                ("  indent\n", [{"old_text": "\tindent", "new_text": "x"}], True),
            ]
            for before, edits, fuzzy in cases:
                with self.subTest(before=before, edits=edits):
                    target.write_text(before)
                    with self.assertRaises(ValueError):
                        tools.execute_result("edit_file", {"path": "text", "edits": edits, "allow_fuzzy": fuzzy})
                    self.assertEqual(target.read_text(), before)
            for invalid in ("true", 1, None):
                with self.assertRaisesRegex(ValueError, "boolean"):
                    tools.execute_result("edit_file", {"path": "text", "old_text": "indent", "new_text": "x", "allow_fuzzy": invalid})

    def test_fuzzy_fresh_approval_ignores_existing_file_grant_and_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "text"
            target.write_text('stable A\ncan’t\n')
            tools = WorkspaceTools(root, allow_write=True)
            payloads = []
            answers = iter(("session", "once", "deny"))
            def emit(request, kind, payload):
                if kind == "approval.request":
                    payloads.append(payload)
                    manager.resolve(payload.split("\t")[1], next(answers))
            manager = ApprovalManager(tools, emit)
            exact = {"path": "text", "old_text": "stable A", "new_text": "stable B"}
            self.assertTrue(manager.request("turn", "exact", "edit_file", exact))
            tools.execute_result("edit_file", exact, expected_change=manager.take_expected_change("exact"))
            fuzzy = {"path": "text", "old_text": "can't", "new_text": "won’t", "allow_fuzzy": True}
            self.assertTrue(manager.request("turn", "fuzzy", "edit_file", fuzzy))
            tools.execute_result("edit_file", fuzzy, expected_change=manager.take_expected_change("fuzzy"))
            current = target.read_bytes()
            self.assertFalse(manager.request("turn", "denied", "edit_file", {**fuzzy, "old_text": "won't", "new_text": "bad"}))
            self.assertEqual(target.read_bytes(), current)
            self.assertEqual([payload.split("\t")[3] for payload in payloads], ["deny,once,session", "deny,once", "deny,once"])
            self.assertIn("Fuzzy normalization used on lines", base64.b64decode(payloads[1].split("\t")[5]).decode())

    def test_fuzzy_revision_checks_reject_source_changes_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "text"
            target.write_text('can’t\n')
            tools = WorkspaceTools(root, allow_write=True)
            arguments = {"path": "text", "old_text": "can't", "new_text": "updated", "allow_fuzzy": True}
            proposed = tools.prepare_change("edit_file", arguments)
            target.write_text("can't\n")
            with self.assertRaisesRegex(ValueError, "since approval"):
                tools.execute_result("edit_file", arguments, expected_change=proposed)
            self.assertEqual(target.read_text(), "can't\n")
            target.write_text('can’t\n')
            def emit(request, kind, payload):
                if kind == "approval.request":
                    target.write_text("can't\n")
                    manager.resolve(payload.split("\t")[1], "once")
            manager = ApprovalManager(tools, emit)
            self.assertFalse(manager.request("turn", "changed-during-approval", "edit_file", arguments))
            self.assertIsNone(manager.take_expected_change("changed-during-approval"))
            self.assertEqual(target.read_text(), "can't\n")

    def test_agent_gets_navigation_and_fuzzy_metadata(self):
        class Model:
            def complete(self, messages, tools):
                if messages[-1]["role"] == "tool":
                    self.received = messages[-1]["content"]
                    return {"role": "assistant", "content": "Done."}
                return {"role": "assistant", "content": None, "tool_calls": [{"id": "edit", "type": "function",
                    "function": {"name": "edit_file", "arguments": json.dumps({"path": "text", "old_text": '"old"',
                                 "new_text": '"new"', "allow_fuzzy": True})}}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "text").write_text('keep\n“old”\n')
            model = Model()
            events = []
            list(Agent(model, WorkspaceTools(root, allow_write=True), DecisionEngine("off")).run("Update old",
                 approval=lambda *args: True, on_tool_event=lambda call, kind, payload: events.append((kind, payload))))
            self.assertIn("First changed line: 2", model.received)
            self.assertIn("Fuzzy normalization used", model.received)
            change = next(payload for kind, payload in events if kind == "tool.change")
            self.assertIn('"new"', change["patch"])
            self.assertEqual(change["first_changed_line"], 2)
