import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mupyjava.agent import Agent
from mupyjava.cancel import CancellationToken, TurnCancelled
from mupyjava.judge import DecisionEngine
from mupyjava.permissions import ApprovalManager
from mupyjava.shell_ops import LocalShellOperations
from mupyjava.tools import WorkspaceTools


class OneCallModel:
    def __init__(self, tool, arguments):
        self.tool, self.arguments = tool, arguments

    def complete(self, messages, tools):
        if messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": "Done."}
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": "shell-1", "type": "function", "function": {
                "name": self.tool, "arguments": json.dumps(self.arguments),
            },
        }]}


class ShellTests(unittest.TestCase):
    def test_result_paging_metadata_and_text_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("one\ntwo\n")
            tools = WorkspaceTools(root)
            result = tools.execute_result("read_file", {"path": "a.txt", "limit": 1})
            self.assertEqual(result.details["next_offset"], 2)
            self.assertTrue(result.details["truncated"])
            self.assertEqual(result.details["output_bytes"], 4)
            self.assertEqual(result.to_payload()["version"], 1)
            self.assertEqual(result.to_payload()["content"][0]["type"], "text")
            self.assertEqual(result.text, tools.execute("read_file", {"path": "a.txt", "limit": 1}))

    @unittest.skipUnless(shutil.which("bash"), "Bash is unavailable")
    def test_bash_pipeline_redirection_and_nonzero_structured_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root, allow_command=True)
            result = tools.execute_result("bash", {"command": "printf hello | tr a-z A-Z > result.txt; cat result.txt"})
            self.assertEqual((root / "result.txt").read_text(), "HELLO")
            self.assertIn("HELLO", result.text)
            self.assertEqual(result.details["exit_code"], 0)
            self.assertEqual(result.details["mode"], "bash")
            self.assertFalse(result.is_error)
            failed = tools.execute_result("bash", {"command": "printf failed; exit 7"})
            self.assertEqual(failed.details["exit_code"], 7)
            self.assertTrue(failed.is_error)

    @unittest.skipUnless(shutil.which("bash"), "Bash is unavailable")
    def test_bash_long_output_metadata_and_artifact_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root, allow_command=True, output_root=root / "output")
            result = tools.execute_result("bash", {"command": "printf '%014000d' 0"})
            self.assertEqual(result.details["output_bytes"], 14_000)
            self.assertTrue(result.details["truncated"])
            artifact = result.details["artifact_id"]
            page = tools.execute_result("read_command_output", {"id": artifact, "limit": 50})
            self.assertEqual(page.details["next_offset"], 50)
            self.assertEqual(len((root / "output" / (artifact + ".log")).read_bytes()), 14_000)

    def test_shells_require_command_approval_even_with_full_write_grant(self):
        class NeverRun:
            def execute(self, *args):
                raise AssertionError("Denied shell reached the transport")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root, allow_command=True, shell_ops=NeverRun())
            events = []
            def emit(request_id, kind, payload):
                if kind == "approval.request":
                    self.assertEqual(payload.split("\t")[3], "deny,once")
                    manager.resolve(payload.split("\t")[1], "deny")
            manager = ApprovalManager(tools, emit, full_write=True)
            for shell in ("bash", "powershell"):
                agent = Agent(OneCallModel(shell, {"command": "write > target"}), tools, DecisionEngine("off"))
                list(agent.run("Run the script", approval=lambda call_id, name, args:
                    manager.request("turn", call_id, name, args),
                    on_tool_event=lambda call_id, kind, payload: events.append((kind, payload))))
                result = next(payload for kind, payload in reversed(events) if kind == "tool.result")
                self.assertTrue(result["is_error"])
                self.assertEqual(result["details"]["error_type"], "PermissionError")
                self.assertIn("User did not allow", result["content"][0]["text"])
            self.assertFalse((root / "target").exists())

    def test_active_judge_can_veto_shell_before_approval(self):
        class NoJudge:
            def answer(self, question, state):
                return False
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory), allow_command=True)
            def approval(*args):
                raise AssertionError("Judge veto should precede approval")
            for shell in ("bash", "powershell"):
                agent = Agent(OneCallModel(shell, {"command": "echo unexpected"}), tools,
                              DecisionEngine("active", NoJudge()))
                events = list(agent.run("Explain without executing", approval=approval))
                self.assertTrue(any("Judge declined" in text for kind, text in events))

    @unittest.skipUnless(shutil.which("bash"), "Bash is unavailable")
    def test_bash_cancel_stops_background_children_and_timeout_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root, allow_command=True)
            token = CancellationToken()
            with self.assertRaises(TurnCancelled):
                tools.execute_result("bash", {"command": "(sleep 1; printf late > late.txt) & printf started; wait"},
                                     cancel=token, on_update=lambda text: token.cancel())
            time.sleep(1.1)
            self.assertFalse((root / "late.txt").exists())
            records = []
            agent = Agent(OneCallModel("bash", {"command": "sleep 3", "timeout": 1}), tools, DecisionEngine("off"))
            list(agent.run("Run the wait", on_tool_event=lambda call_id, kind, payload: records.append((kind, payload))))
            self.assertIn("tool.timed_out", [kind for kind, _ in records])
            result = next(payload for kind, payload in records if kind == "tool.result")
            self.assertTrue(result["is_error"])
            self.assertEqual(result["details"]["error_type"], "TimeoutExpired")

    def test_powershell_transport_preserves_script_and_missing_shell_is_clear(self):
        with patch("mupyjava.shell_ops.shutil.which", return_value="/mock/pwsh"):
            argv = LocalShellOperations.argv("powershell", "Write-Output '中文' | ForEach-Object { $_ }")
        self.assertIn("-NoProfile", argv)
        self.assertIn("-NonInteractive", argv)
        self.assertIn("[Console]::OutputEncoding", argv[-1])
        self.assertTrue(argv[-1].endswith("Write-Output '中文' | ForEach-Object { $_ }"))
        with patch("mupyjava.shell_ops.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "PowerShell is unavailable"):
                LocalShellOperations.argv("powershell", "Get-Location")

        class AlternateShell:
            def execute(self, shell, script, cwd, timeout, cancel, on_data):
                self.seen = (shell, script, timeout)
                on_data("中文\n".encode("utf-8"))
                return 0
        with tempfile.TemporaryDirectory() as directory:
            backend = AlternateShell()
            tools = WorkspaceTools(Path(directory), allow_command=True, shell_ops=backend)
            script = "Write-Output '中文' | ForEach-Object { $_ }"
            result = tools.execute_result("powershell", {"command": script, "timeout": 5})
            self.assertEqual(backend.seen, ("powershell", script, 5))
            self.assertEqual(result.details["mode"], "powershell")
            self.assertIn("中文", result.text)

    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "Native PowerShell is unavailable")
    def test_native_powershell_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            result = WorkspaceTools(Path(directory), allow_command=True).execute_result(
                "powershell", {"command": "Write-Output 'hello' | ForEach-Object { $_.ToUpper() }"})
            self.assertEqual(result.details["exit_code"], 0)
            self.assertIn("HELLO", result.text)


if __name__ == "__main__":
    unittest.main()
