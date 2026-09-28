"""Request budgets preserve instructions and protocol, and archive omitted text."""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from mupyjava.agent import Agent, SYSTEM_MESSAGE
from mupyjava.cancel import CancellationToken, TurnCancelled
from mupyjava.capabilities import ModelCapabilities
from mupyjava.context import ContextBudget, ContextOverflow, ContextSettings, validate_chain
from mupyjava.judge import DecisionEngine
from mupyjava.model import ChatCompletionsModel
from mupyjava.registry import ToolDefinition
from mupyjava.sessions import SessionStore
from mupyjava.tool_result import ToolResult
from mupyjava.tools import WorkspaceTools

REPOSITORY = Path(__file__).resolve().parents[2]


def call(call_id, name="read_file", args=None):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


def exchange(prompt, text, call_id="read"):
    return [{"role": "user", "content": prompt},
            {"role": "assistant", "content": None, "tool_calls": [call(call_id)]},
            {"role": "tool", "tool_call_id": call_id, "content": text},
            {"role": "assistant", "content": "Read it"}]


class ContextTests(unittest.TestCase):
    def test_settings_and_environment_validation(self):
        with patch.dict(os.environ, {"MU_CONTEXT_TOKENS": "9000", "MU_RESPONSE_TOKENS": "1000",
                                     "MU_TOOL_RESULT_TOKENS": "512", "MU_IMAGE_TOKENS": "2048"}, clear=True):
            configured = ContextSettings.from_environment()
        self.assertEqual(configured.input_tokens, 8000)
        self.assertEqual(configured.tool_tokens, 512)
        for kwargs in ({"max_tokens": True}, {"tool_tokens": 1}, {"reserve_tokens": 0},
                       {"max_tokens": 100, "reserve_tokens": 100}, {"image_tokens": 2_000_001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ContextSettings(**kwargs)
        with patch.dict(os.environ, {"MU_CONTEXT_TOKENS": "abc"}, clear=True), self.assertRaisesRegex(ValueError, "MU_CONTEXT_TOKENS"):
            ContextSettings.from_environment()

    def test_whole_old_turns_are_evicted_without_changing_canonical_history(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = ContextBudget(ContextSettings(max_tokens=1200, reserve_tokens=200))
            messages = [{"role": "system", "content": "Keep these exact workspace and permission instructions."}]
            messages += exchange("old instructions", "o" * 8000, "old")
            messages += exchange("recent instructions", "r" * 600, "recent")
            messages += [{"role": "user", "content": "Latest request stays verbatim: 汉字 🌱"}]
            before = copy.deepcopy(messages)
            projected = budget.prepare(messages, [], Path(directory))
            self.assertEqual(projected.record["omitted_turns"], 1)
            self.assertLessEqual(projected.record["estimated_tokens"], 1000)
            self.assertEqual(projected.messages[-1], before[-1])
            self.assertIn(before[0]["content"], projected.messages[0]["content"])
            self.assertIn("No summary was generated", projected.messages[0]["content"])
            self.assertNotIn("old instructions", str(projected.messages))
            self.assertIn("recent instructions", str(projected.messages))
            validate_chain(projected.messages)
            self.assertEqual(messages, before)
            self.assertEqual(projected.artifacts, [])

    def test_unicode_tool_text_keeps_head_tail_and_full_archive_is_paged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "START\n" + "汉字🌱 \"x\"\n" * 3000 + "MIDDLE_NEEDLE" + "rows\n" * 3000 + "ERROR: final failure\n"
            messages = [{"role": "system", "content": "rules"}] + exchange("read", text)[:-1]
            original = copy.deepcopy(messages)
            budget = ContextBudget(ContextSettings(max_tokens=4000, reserve_tokens=500, tool_tokens=256))
            projected = budget.prepare(messages, [], root / "outputs")
            item = projected.record["shortened_tools"][0]
            visible = projected.messages[-1]["content"]
            self.assertTrue(visible.startswith("START\n"))
            self.assertTrue(visible.endswith("ERROR: final failure\n"))
            self.assertNotIn("MIDDLE_NEEDLE", visible)
            self.assertLessEqual(item["admitted_bytes"], 1024)
            archive = root / "outputs" / (item["artifact_id"] + ".log")
            self.assertEqual(archive.read_bytes(), text.encode("utf-8"))
            self.assertEqual(messages, original)
            tools = WorkspaceTools(root, output_root=archive.parent)
            offset = text.encode("utf-8").index(b"MIDDLE_NEEDLE")
            result = tools.execute_result("read_tool_output", {"id": item["artifact_id"], "offset": offset, "limit": 20})
            self.assertIn("MIDDLE_NEEDLE", result.text)
            self.assertEqual(result.details["next_offset"], offset + 20)
            self.assertEqual(tools.execute("read_command_output", {"id": item["artifact_id"], "offset": offset, "limit": 20}), result.text)
            self.assertEqual(len(projected.artifacts), 1)
            again = budget.prepare(messages, [], archive.parent)
            self.assertEqual(again.artifacts, [])
            self.assertEqual(again.messages, projected.messages)
            budget.begin_turn()
            self.assertEqual(budget.prepare(messages, [], archive.parent).artifacts, projected.artifacts)
            other = budget.prepare(messages, [], root / "other-session")
            self.assertNotEqual(other.artifacts[0][1], item["artifact_id"])

    def test_multiple_current_tool_replies_shrink_under_request_limit_as_a_complete_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = ContextBudget(ContextSettings(max_tokens=650, reserve_tokens=100, tool_tokens=1000))
            calls = [call("first"), call("second")]
            messages = [{"role": "system", "content": "permissions unchanged"}, {"role": "user", "content": "latest request"},
                        {"role": "assistant", "content": "checking", "tool_calls": calls},
                        {"role": "tool", "tool_call_id": "first", "content": "first-head\n" + "a" * 6000 + "first-tail"},
                        {"role": "tool", "tool_call_id": "second", "content": "second-head\n" + "b" * 6000 + "second-tail"}]
            projected = budget.prepare(messages, [], Path(directory))
            self.assertLessEqual(projected.record["estimated_tokens"], 550)
            self.assertEqual(projected.messages[2], messages[2])
            self.assertEqual([message["tool_call_id"] for message in projected.messages[3:]], ["first", "second"])
            self.assertEqual(len(projected.record["shortened_tools"]), 2)
            self.assertTrue(any(item["reason"] == "request_limit" for item in projected.record["shortened_tools"]))
            validate_chain(projected.messages)

    def test_mandatory_prompt_arguments_and_schemas_overflow_without_archiving_or_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = ContextSettings(max_tokens=300, reserve_tokens=100, tool_tokens=256)
            budget = ContextBudget(settings)
            base = [{"role": "system", "content": "rules"}, {"role": "user", "content": "request"}]
            variants = [([{**base[0]}, {"role": "user", "content": "p" * 2000}], []),
                        (base + [{"role": "assistant", "content": None, "tool_calls": [call("huge", args={"text": "x" * 3000})]},
                                 {"role": "tool", "tool_call_id": "huge", "content": "tool output" * 1000}], []),
                        (base, [{"definition": "schema" * 1000}])]
            for messages, schemas in variants:
                with self.subTest(schemas=bool(schemas)), self.assertRaises(ContextOverflow) as caught:
                    budget.prepare(messages, schemas, root / "archive")
                self.assertTrue(caught.exception.record["blocked"])
                self.assertFalse((root / "archive").exists())
            class NeverModel:
                def complete(self, messages, tools):
                    raise AssertionError("Overflow was sent to the model")
            agent = Agent(NeverModel(), WorkspaceTools(root), DecisionEngine(), context_settings=settings)
            records = []
            with self.assertRaises(ContextOverflow):
                list(agent.run("too big", on_context=records.append))
            self.assertTrue(records[-1]["blocked"])

    def test_image_reserve_counts_images_without_counting_base64_as_text(self):
        vision = ModelCapabilities(supports_images=True)
        budget = ContextBudget(ContextSettings(max_tokens=5000, reserve_tokens=500, image_tokens=2048))
        small = [{"role": "system", "content": "rules"}] + exchange("image", "image read")[:-1]
        small[-1]["image_blocks"] = [{"type": "image", "mime_type": "image/png", "data": "aW1hZ2U="}]
        large = copy.deepcopy(small)
        large[-1]["image_blocks"][0]["data"] = "YWJj" * 100_000
        self.assertEqual(budget.estimate(small, [], vision), budget.estimate(large, [], vision))
        self.assertGreater(budget.estimate(small, [], vision), budget.estimate(small, [], ModelCapabilities()) + 2000)
        with tempfile.TemporaryDirectory() as directory:
            projection = budget.prepare(large, [], Path(directory), vision)
            self.assertEqual(projection.messages[-1]["image_blocks"], large[-1]["image_blocks"])
            tight = ContextBudget(ContextSettings(max_tokens=1000, reserve_tokens=100, image_tokens=2048))
            with self.assertRaises(ContextOverflow):
                tight.prepare(large, [], Path(directory), vision)
            self.assertFalse(list(Path(directory).glob("*.log")))

    def test_cancel_or_archive_failure_blocks_projection_and_cleans_partial_file(self):
        class CancelDuringArchive(CancellationToken):
            def __init__(self):
                super().__init__()
                self.checks = 0
            def raise_if_cancelled(self):
                self.checks += 1
                if self.checks == 3:
                    raise TurnCancelled("cancelled during archive")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            messages = [{"role": "system", "content": "rules"}] + exchange("read", "x" * 300_000)[:-1]
            budget = ContextBudget(ContextSettings(tool_tokens=256))
            with self.assertRaises(TurnCancelled):
                budget.prepare(messages, [], root, cancel=CancelDuringArchive())
            self.assertEqual(list(root.glob("*.log")), [])
            with patch("mupyjava.context.os.open", side_effect=OSError("archive unavailable")), self.assertRaises(OSError):
                budget.prepare(messages, [], root)
            self.assertEqual(list(root.glob("*.log")), [])
            # A later archive failure must not mark earlier unreturned IDs as
            # delivered; a retry still needs to register both references.
            paired = [{"role": "system", "content": "rules"}, {"role": "user", "content": "read both"},
                      {"role": "assistant", "content": None, "tool_calls": [call("a"), call("b")]},
                      {"role": "tool", "tool_call_id": "a", "content": "x" * 300_000},
                      {"role": "tool", "tool_call_id": "b", "content": "y" * 300_000}]
            original_open = os.open
            opens = []
            def fail_second(path, flags, mode):
                opens.append(path)
                if len(opens) == 2:
                    raise OSError("second archive unavailable")
                return original_open(path, flags, mode)
            with patch("mupyjava.context.os.open", side_effect=fail_second), self.assertRaises(OSError):
                budget.prepare(paired, [], root)
            retried = budget.prepare(paired, [], root)
            self.assertEqual(len(retried.artifacts), 2)
            self.assertEqual(len(list(root.glob("*.log"))), 2)

    def test_invalid_or_duplicate_tool_ids_are_rejected_before_a_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ran = []
            tools = WorkspaceTools(root, allow_custom=True)
            tools.registry.register(ToolDefinition("mutation", "Mutate", {"type": "object", "properties": {}},
                lambda args, ctx: (ran.append(True) or ToolResult.from_text("done"))))
            class InvalidModel:
                def complete(self, messages, tools):
                    return {"role": "assistant", "content": None, "tool_calls": [call("same", "mutation"), call("same", "mutation")]}
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                list(Agent(InvalidModel(), tools, DecisionEngine()).run("do it"))
            self.assertEqual(ran, [])
            for messages in ([{"role": "tool", "tool_call_id": "orphan", "content": "x"}],
                             [{"role": "assistant", "tool_calls": [call("pending")]}],
                             [{"role": "assistant", "tool_calls": [call("pending")]}, {"role": "user", "content": "new"}]):
                with self.assertRaises(ValueError):
                    validate_chain(messages)


class ContextIntegrationTests(unittest.TestCase):
    def test_real_http_tool_archive_retrieval_restart_and_independent_fork(self):
        text = "BEGIN\n" + "汉字 \"test\"\n" * 700 + "MIDDLE_NEEDLE" + "data\n" * 700 + "FINAL ERROR\n"
        offset = text.encode("utf-8").index(b"MIDDLE_NEEDLE")
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                messages = payload["messages"]
                if messages[-1]["role"] == "user" and messages[-1]["content"] == "continue":
                    reply = {"role": "assistant", "content": "Resumed without rereading source."}
                elif messages[-1]["role"] == "user":
                    reply = {"role": "assistant", "content": None, "tool_calls": [call("file", args={"path": "large.txt"})]}
                elif messages[-1]["tool_call_id"] == "file":
                    archive_id = re.search(r"Full text id: ([a-f0-9-]{36})", messages[-1]["content"]).group(1)
                    reply = {"role": "assistant", "content": None, "tool_calls": [call("retrieve", "read_tool_output",
                             {"id": archive_id, "offset": offset, "limit": 64})]}
                else:
                    reply = {"role": "assistant", "content": "Found MIDDLE_NEEDLE."}
                body = json.dumps({"choices": [{"message": reply}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args):
                pass
        with HTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                root = Path(directory)
                # read_file retains only one page; choose the first page large enough for admission.
                source = root / "large.txt"
                source.write_text(text)
                store = SessionStore.open(root, root / "sessions")
                settings = ContextSettings(max_tokens=8000, reserve_tokens=600, tool_tokens=256)
                model = ChatCompletionsModel(f"http://127.0.0.1:{server.server_port}/v1", "", "fixture", max_output_tokens=9999)
                tools = WorkspaceTools(root, output_root=store.output_dir)
                agent = Agent(model, tools, DecisionEngine(), context_settings=settings)
                def run_turn(agent, store, prompt):
                    tid = str(len(store.events()))
                    store.append("turn.started", {}, tid)
                    events = list(agent.run(prompt,
                        on_message=lambda message: store.append("message", {"message": message}, tid),
                        on_context=lambda record: store.append("context.budget", record, tid),
                        on_tool_artifact=lambda call_id, artifact: store.append("tool.artifact", {"id": artifact}, tid, call_id),
                        on_tool_event=lambda call_id, kind, payload: store.append(kind, payload, tid, call_id),
                        output_dir=store.output_dir))
                    store.append("turn.completed", {}, tid)
                    return events
                events = run_turn(agent, store, "Inspect large.txt and recover the middle")
                self.assertIn(("assistant", "Found MIDDLE_NEEDLE."), events)
                self.assertIn("MIDDLE_NEEDLE", requests[2]["messages"][-1]["content"])
                self.assertNotIn("MIDDLE_NEEDLE", requests[1]["messages"][-1]["content"])
                self.assertEqual(requests[0]["max_tokens"], 600)
                self.assertEqual(store.restore_messages(), agent.messages)
                for payload in requests:
                    validate_chain(payload["messages"])
                    self.assertLessEqual(agent.context.estimate(payload["messages"], payload["tools"]), settings.input_tokens)
                copied = store.fork(store.leaf_id)
                artifacts = {item["payload"]["id"] for item in copied.events() if item["type"] == "tool.artifact"}
                self.assertTrue(artifacts)
                for artifact in artifacts:
                    self.assertEqual((copied.output_dir / (artifact + ".log")).read_text(), text)
                self.assertTrue(any(kind == "history.context" for kind, _ in copied.history()))
                source.unlink()
                shutil.rmtree(store.output_dir)
                resumed = Agent(model, WorkspaceTools(root, output_root=copied.output_dir), DecisionEngine(), context_settings=settings)
                reopened = SessionStore.open(root, root / "sessions")
                resumed.messages = reopened.restore_messages()
                before = len(requests)
                run_turn(resumed, reopened, "continue")
                self.assertEqual(len(requests), before + 1)
                self.assertIn("Resumed without rereading", resumed.messages[-1]["content"])
                for artifact in artifacts:
                    self.assertIn("MIDDLE_NEEDLE", resumed.tools.execute("read_tool_output", {"id": artifact, "offset": offset, "limit": 64}))
            finally:
                server.shutdown(); thread.join(timeout=5)

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK unavailable")
    def test_java_desktop_process_reports_context_and_restores_it(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); requests.append(payload)
                last = payload["messages"][-1]
                if last["role"] == "user" and last["content"] != "continue":
                    reply = {"role": "assistant", "content": None, "tool_calls": [call("read", args={"path": "large.txt"})]}
                else:
                    reply = {"role": "assistant", "content": "Done"}
                body = json.dumps({"choices": [{"message": reply}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args):
                pass
        with tempfile.TemporaryDirectory() as directory, HTTPServer(("127.0.0.1", 0), Handler) as server:
            root = Path(directory); workspace = root / "workspace"; workspace.mkdir()
            (workspace / "large.txt").write_text("HEAD\n" + "row data\n" * 1000 + "FAILURE tail\n")
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                classes = root / "classes"
                compilation = subprocess.run(["javac", "-d", str(classes),
                    *map(str, (REPOSITORY / "java/src/main/java/dev/mupyjava").glob("*.java"))], capture_output=True, text=True, timeout=30)
                self.assertEqual(compilation.returncode, 0, compilation.stderr)
                env = os.environ.copy()
                env.update({"MU_MODEL_BACKEND": "", "MU_MODEL": "fixture", "MU_JUDGE_MODE": "off", "MU_TOOL_MODULES": "",
                    "MU_PYTHON": sys.executable, "MU_API_BASE": f"http://127.0.0.1:{server.server_port}/v1", "MU_SESSION_DIR": str(root / "sessions"),
                    "MU_CONTEXT_TOKENS": "8000", "MU_RESPONSE_TOKENS": "600", "MU_TOOL_RESULT_TOKENS": "256"})
                for prompt in ("Read large.txt", "continue"):
                    result = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main", "--workspace", str(workspace), "--smoke", prompt],
                        cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("context.detail: Context:", result.stdout)
                    self.assertIn("1 tool outputs shortened", result.stdout)
                    self.assertIn("1 outputs shortened", result.stdout)
                    if prompt == "continue":
                        self.assertIn("history.context: Context:", result.stdout)
                    else:
                        (workspace / "large.txt").unlink()
                blocked = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main", "--workspace", str(workspace),
                    "--smoke", "x" * 40_000], cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(blocked.returncode, 0, blocked.stderr)
                self.assertIn("too large to send", blocked.stdout)
                self.assertIn("this model request was not sent", blocked.stdout)
                self.assertEqual(len(requests), 3)
                self.assertTrue(all(payload["max_tokens"] == 600 for payload in requests))
                restored = SessionStore.open(workspace, root / "sessions").restore_messages()
                self.assertFalse(any(message.get("content") == "x" * 40_000 for message in restored))
            finally:
                server.shutdown(); thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
