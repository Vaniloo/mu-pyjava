import fnmatch
import io
import json
import tempfile
import unittest
from pathlib import Path

from mupyjava.agent import Agent
from mupyjava.cancel import CancellationToken, TurnCancelled
from mupyjava.judge import DecisionEngine
from mupyjava.permissions import ApprovalManager
from mupyjava.tools import WorkspaceTools


class VirtualWorkspace:
    """Alternate backend with no local source file or subprocess access."""

    def __init__(self, root):
        self.root = root.resolve()
        self.content = "value = 1\n"
        self.commands = 0
        self.git_calls = []

    def exists(self, path):
        return path in {self.root, self.root / "virtual.py"}

    def is_dir(self, path):
        return path == self.root

    def list_dir(self, path, cancel=None):
        return ["virtual.py"]

    def read_text(self, path):
        return self.content

    def open_text(self, path, cancel=None):
        return io.StringIO(self.content)

    def size(self, path):
        return len(self.content.encode("utf-8"))

    def replace_text(self, path, content):
        self.content = content

    def find(self, base, pattern, cancel):
        return (["virtual.py"] if fnmatch.fnmatch("virtual.py", pattern) or pattern == "**/*" else []), False

    def grep(self, paths, pattern, literal, ignore_case, context, max_lines, cancel):
        return [{"path": "virtual.py", "line": 1, "text": self.content.strip(), "kind": "match"}], False

    def inspect(self, args, cancel):
        self.git_calls.append(args)
        return " M virtual.py\n" if args[0] == "status" else "-value = 1\n+value = 2\n"

    def execute(self, argv, cwd, timeout, cancel, on_data):
        assert argv == ["verify"] and cwd == self.root and self.content == "value = 2\n"
        self.commands += 1
        on_data("检验通过\n".encode("utf-8") + b"x" * 14_000)
        return 0


class ScriptedModel:
    def __init__(self, calls):
        self.calls = iter(calls)

    def complete(self, messages, tools):
        call = next(self.calls, None)
        if call is None:
            return {"role": "assistant", "content": "Verified."}
        name, arguments = call
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-" + str(len(messages)), "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]}


class OperationsTests(unittest.TestCase):
    def make_tools(self, root, backend):
        return WorkspaceTools(root, allow_write=True, allow_command=True, file_ops=backend,
                              search_ops=backend, git_ops=backend, command_ops=backend,
                              output_root=root / "output")

    def test_agent_virtual_read_search_edit_git_command_and_output_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = VirtualWorkspace(root)
            tools = self.make_tools(root, backend)
            calls = [
                ("read_file", {"path": "virtual.py"}),
                ("find_files", {"path": ".", "glob": "*.py"}),
                ("grep_files", {"path": ".", "pattern": "value", "literal": True}),
                ("edit_file", {"path": "virtual.py", "old_text": "value = 1", "new_text": "value = 2"}),
                ("git_status", {}),
                ("git_diff", {"path": "virtual.py"}),
                ("run_command", {"command": "verify"}),
            ]
            approvals = []
            def emit(request_id, kind, payload):
                if kind == "approval.request":
                    approvals.append(payload)
                    manager.resolve(payload.split("\t")[1], "once")
            manager = ApprovalManager(tools, emit)
            updates, artifacts, changes = [], [], []
            agent = Agent(ScriptedModel(calls), tools, DecisionEngine("off"), max_steps=9)
            events = list(agent.run("Update the virtual source and verify it",
                approval=lambda call_id, name, args: manager.request("turn", call_id, name, args),
                expected_change=manager.take_expected_change,
                on_tool_update=lambda call_id, chunk: updates.append(chunk),
                on_tool_artifact=lambda call_id, artifact_id: artifacts.append(artifact_id),
                on_tool_event=lambda call_id, kind, payload: changes.append((kind, payload))))
            self.assertEqual(events[-1], ("assistant", "Verified."))
            results = [item["content"] for item in agent.messages if item["role"] == "tool"]
            self.assertEqual(len(results), 7)
            self.assertFalse(any("Tool error:" in result for result in results))
            self.assertEqual(json.loads(results[1])["paths"], ["virtual.py"])
            self.assertEqual(json.loads(results[2])["matches"][0]["line"], 1)
            self.assertEqual(backend.content, "value = 2\n")
            self.assertEqual(backend.commands, 1)
            self.assertEqual(len(approvals), 2)
            self.assertTrue(any(kind == "tool.change" for kind, _ in changes))
            self.assertIn("检验通过", "".join(updates))
            self.assertEqual(len(artifacts), 1)
            self.assertIn("Full output id: " + artifacts[0], results[-1])
            page = tools.execute("read_command_output", {"id": artifacts[0], "limit": 50})
            self.assertIn("检验通过", page)
            self.assertIn("use offset=50", page)
            self.assertFalse((root / "virtual.py").exists())

    def test_denied_alternate_edit_and_command_never_reach_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = VirtualWorkspace(root)
            model = ScriptedModel([
                ("edit_file", {"path": "virtual.py", "old_text": "value = 1", "new_text": "value = 2"}),
                ("run_command", {"command": "verify"}),
            ])
            agent = Agent(model, self.make_tools(root, backend), DecisionEngine("off"))
            list(agent.run("Update and verify", approval=lambda *args: False))
            results = [item["content"] for item in agent.messages if item["role"] == "tool"]
            self.assertTrue(all("User did not allow" in result for result in results))
            self.assertEqual(backend.content, "value = 1\n")
            self.assertEqual(backend.commands, 0)

    def test_alternate_search_cannot_return_paths_outside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = VirtualWorkspace(root)
            backend.find = lambda *args: (["../outside.py"], False)
            tools = self.make_tools(root, backend)
            with self.assertRaises(PermissionError):
                tools.execute("find_files", {"path": ".", "glob": "*.py"})
            backend.find = lambda *args: (["virtual.py"], False)
            backend.grep = lambda *args: ([{"path": "../outside.py", "line": 1,
                                          "text": "value", "kind": "match"}], False)
            with self.assertRaises(PermissionError):
                tools.execute("grep_files", {"path": ".", "pattern": "value"})

    def test_alternate_search_and_git_observe_cancellation_before_returning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = VirtualWorkspace(root)
            tools = self.make_tools(root, backend)
            token = CancellationToken()
            def find(*args):
                token.cancel()
                return ["virtual.py"], False
            backend.find = find
            with self.assertRaises(TurnCancelled):
                tools.execute("find_files", {"path": ".", "glob": "*.py"}, cancel=token)
            token = CancellationToken()
            def inspect(*args):
                token.cancel()
                return " M virtual.py\n"
            backend.inspect = inspect
            with self.assertRaises(TurnCancelled):
                tools.execute("git_status", {}, cancel=token)

    def test_alternate_command_cancellation_does_not_return_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = VirtualWorkspace(root)
            token = CancellationToken()
            def execute(argv, cwd, timeout, cancel, on_data):
                on_data(b"started\n")
                cancel.cancel()
                return 0
            backend.execute = execute
            tools = self.make_tools(root, backend)
            with self.assertRaises(TurnCancelled):
                tools.execute("run_command", {"command": "verify"}, cancel=token)


if __name__ == "__main__":
    unittest.main()
