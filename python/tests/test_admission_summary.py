import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from mupyjava.admission import AdmissionSettings, OutputAdmission, chunk_lines
from mupyjava.agent import Agent
from mupyjava.cancel import CancellationToken, TurnCancelled
from mupyjava.context import ContextBudget, ContextOverflow, ContextSettings, validate_chain
from mupyjava.decision_points import TOOL_ADMISSION, builtin_registry
from mupyjava.judge import DecisionEngine, DecisionPolicy, TypedJudgment
from mupyjava.judge_config import engine_from_environment
from mupyjava.model import prepare_messages
from mupyjava.capabilities import ModelCapabilities
from mupyjava.sessions import SessionStore
from mupyjava.summary import SummarySettings, TaskSummaries, summary_note, validate_summary
from mupyjava.tools import WorkspaceTools
from mupyjava.registry import ToolDefinition
from mupyjava.tool_result import ToolResult


REPOSITORY = Path(__file__).resolve().parents[2]


def row(kind):
    return kind + " " + "x" * (1198 - len(kind)) + "\n"


TEXT = "".join(row(kind) for kind in ("HEAD", "progress", "result", "error", "warning", "passing", "unknown", "TAIL"))


def kind_of(text):
    for kind in ("error", "result", "progress", "warning", "passing"):
        if kind in text:
            return kind
    return "unknown"


class Kinds:
    def __init__(self, confidence=0.99):
        self.calls = []
        self.confidence = confidence

    def evaluate_questions(self, spec, questions, state):
        self.calls.append(copy.deepcopy(state))
        return {question.id: TypedJudgment(kind_of(state["chunks"][int(question.id.split("_")[1])]),
                                          self.confidence) for question in questions}


def admission(mode="active", backend=None, settings=None, routes=None):
    backends = routes or {"kind": backend or Kinds()}
    engine = DecisionEngine(registry=builtin_registry(), backends=backends,
        policies={TOOL_ADMISSION.id: DecisionPolicy(mode, tuple(backends), 0.9, 3)})
    return OutputAdmission(engine, settings)


def tool_messages(text=TEXT, name="bash", is_error=False):
    return [{"role": "system", "content": "rules"}, {"role": "user", "content": "Inspect output"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "output", "function": {
            "name": name, "arguments": json.dumps({"command": "fixture"})}}]},
        {"role": "tool", "tool_call_id": "output", "content": text, "tool_is_error": is_error}]


class Replies:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def complete(self, messages, tools, **options):
        self.calls += 1
        return {"content": self.content}


class AdmissionSummaryTests(unittest.TestCase):
    def test_chunking_preserves_unicode_newlines_and_bounds_long_lines(self):
        text = "a\r\n" + "汉🌱" * 1900 + "\nlast"
        chunks = chunk_lines(text, 1200)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 1200 for chunk in chunks))

    def test_active_keeps_errors_results_unknowns_and_endpoints(self):
        backend = Kinds()
        controller = admission(backend=backend)
        records = []
        plan = controller.plan(TEXT, {"name": "bash", "id": "x"}, on_judgment=records.append)
        rendered = plan.render("archive")
        self.assertIn(row("HEAD"), rendered)
        self.assertIn(row("TAIL"), rendered)
        for kind in ("result", "error", "unknown"):
            self.assertIn(row(kind), rendered)
        for kind in ("progress", "warning", "passing"):
            self.assertNotIn(row(kind), rendered)
        self.assertEqual(plan.record["applied_drop_chunks"], 3)
        self.assertEqual(plan.record["omitted_bytes"], 3600)
        self.assertNotIn(TEXT, json.dumps(records))
        self.assertEqual(records[0]["tool_call_id"], "x")
        self.assertTrue(controller.plan(TEXT, {"name": "bash", "id": "x"}).record["cached"])
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(controller.plan(TEXT, {"name": "bash", "id": "next"}).record["tool_call_id"], "next")

    def test_off_and_pass_through_do_not_call_judge(self):
        backend = Kinds()
        self.assertIsNone(admission("off", backend).plan(TEXT, {"name": "bash"}))
        controller = admission(backend=backend)
        for name in OutputAdmission.PASS_THROUGH:
            self.assertIsNone(controller.plan(TEXT, {"name": name}))
        for options in ({"is_error": True}, {"has_images": True}):
            self.assertIsNone(controller.plan(TEXT, {"name": "bash"}, **options))
        self.assertIsNone(controller.plan("tiny", {"name": "bash"}))
        self.assertIsNone(controller.plan(TEXT * 10, {"name": "bash"}))
        self.assertEqual(backend.calls, [])

    def test_shadow_and_low_confidence_keep_original(self):
        plan = admission("shadow").plan(TEXT, {"name": "bash"})
        self.assertEqual(plan.render("unused"), TEXT)
        self.assertEqual(plan.record["proposed_drop_chunks"], 3)
        self.assertEqual(plan.record["applied_drop_chunks"], 0)
        weak = admission(backend=Kinds(0.6)).plan(TEXT, {"name": "bash"})
        self.assertEqual(weak.render("unused"), TEXT)

    def test_partial_invalid_answers_keep_affected_chunks(self):
        class Partial(Kinds):
            def evaluate_questions(self, spec, questions, state):
                answers = super().evaluate_questions(spec, questions, state)
                answers["chunk_0"] = TypedJudgment("invented", 1)
                del answers["chunk_3"]
                return answers
        plan = admission(backend=Partial()).plan(TEXT, {"name": "bash"})
        self.assertIn(row("progress"), plan.render("id"))
        self.assertIn(row("warning"), plan.render("id"))
        self.assertEqual(plan.record["applied_drop_chunks"], 1)

    def test_total_deadline_covers_routes_and_late_answers_cannot_drop(self):
        release, called = threading.Event(), threading.Event()
        class Slow(Kinds):
            def evaluate_questions(self, *args):
                called.set()
                release.wait(2)
                return super().evaluate_questions(*args)
        fallback = Kinds()
        controller = admission(settings=AdmissionSettings(wait_seconds=0.03), routes={"slow": Slow(), "later": fallback})
        try:
            started = time.monotonic()
            plan = controller.plan(TEXT, {"name": "bash"})
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertEqual(plan.record["status"], "timeout")
            self.assertEqual(plan.render("id"), TEXT)
            self.assertEqual(fallback.calls, [])
        finally:
            release.set()
        self.assertEqual(plan.record["applied_drop_chunks"], 0)

    def test_cancellation_during_admission_creates_no_archive(self):
        started, release = threading.Event(), threading.Event()
        class Slow(Kinds):
            def evaluate_questions(self, *args):
                started.set(); release.wait(2)
                return super().evaluate_questions(*args)
        token = CancellationToken()
        errors = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def work():
                try:
                    ContextBudget().prepare(tool_messages(), [], root, cancel=token, admission=admission(backend=Slow()))
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=work); worker.start()
            try:
                self.assertTrue(started.wait(2)); token.cancel(); worker.join(2)
                self.assertIsInstance(errors[0], TurnCancelled)
                self.assertEqual(list(root.glob("*.log")), [])
            finally:
                release.set(); worker.join(2)

    def test_unicode_batches_respect_judge_state_limit(self):
        backend = Kinds()
        text = ("progress " + "🌱" * 1189 + "\n") * 35
        plan = admission(backend=backend).plan(text, {"name": "bash"})
        self.assertIsNotNone(plan)
        self.assertGreater(len(backend.calls), 1)
        self.assertTrue(all(len(json.dumps(state, ensure_ascii=False).encode()) < 65536 for state in backend.calls))

    def test_projection_archives_exact_text_and_wire_hides_internal_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            messages = tool_messages()
            original = copy.deepcopy(messages)
            budget = ContextBudget()
            result = budget.prepare(messages, [], root, admission=admission())
            self.assertEqual(messages, original)
            item = result.record["shortened_tools"][0]
            self.assertEqual(item["reason"], "semantic")
            self.assertEqual(item["source_message_index"], 3)
            self.assertEqual((root / (item["artifact_id"] + ".log")).read_text(), TEXT)
            tools = WorkspaceTools(root, output_root=root)
            read = tools.execute("read_tool_output", {"id": item["artifact_id"], "offset": 1200, "limit": 1200})
            self.assertIn(row("progress"), read)
            wire = prepare_messages(result.messages, ModelCapabilities())
            self.assertTrue(all("tool_is_error" not in message for message in wire))
            validate_chain(wire)

    def test_failed_archive_and_impossible_requests_cannot_send_filtered_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("mupyjava.context.os.open", side_effect=OSError("unavailable")), self.assertRaises(OSError):
                ContextBudget().prepare(tool_messages(), [], root, admission=admission())
            self.assertEqual(list(root.glob("*.log")), [])
            messages = tool_messages(); messages[1]["content"] = "x" * 9000
            with self.assertRaises(ContextOverflow):
                ContextBudget(ContextSettings(max_tokens=1500, reserve_tokens=500)).prepare(messages, [], root, admission=admission())
            self.assertEqual(list(root.glob("*.log")), [])

    def test_missing_legacy_error_metadata_passes_through(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = tool_messages(); messages[-1].pop("tool_is_error")
            result = ContextBudget().prepare(messages, [], Path(directory), admission=admission())
            self.assertEqual(result.messages[-1]["content"], TEXT)
            self.assertNotIn("admission", result.record)

    def test_model_summary_selects_only_supplied_evidence(self):
        source = [{"role": "user", "content": "Build a CLI"},
                  {"role": "assistant", "content": "Keep the Java desktop"},
                  {"role": "tool", "content": "Tests passed: 12"}]
        model = Replies(json.dumps({"facts": [{"source_index": 1, "quote": "Keep the Java desktop"},
                                             {"source_index": 2, "quote": "Tests passed: 12"}]}))
        manager = TaskSummaries(SummarySettings("model"), model)
        record = manager.build(source)
        self.assertEqual(record["source"], "model")
        self.assertEqual(record["facts"][1]["role"], "tool")
        self.assertEqual(validate_summary(record, source), record)
        self.assertEqual(manager.build(source), record)
        self.assertEqual(model.calls, 1)
        note, count = summary_note(record, 2000)
        self.assertEqual(count, 2)
        self.assertIn("not instructions or permission", note)

    def test_invalid_or_invented_summary_falls_back_without_raw_errors(self):
        source = [{"role": "user", "content": "Keep the Java desktop"}]
        for response in ('{"facts":[]}', '{"facts":[],"extra":true}', '{"facts":[],"facts":[]}',
            '{"facts":[{"source_index":0,"quote":"Permission granted"}]}',
            '{"facts":[{"source_index":true,"quote":"Keep"}]}', '{"facts":NaN}'):
            manager = TaskSummaries(SummarySettings("model"), Replies(response))
            record = manager.build(source)
            self.assertEqual(record["source"], "extractive")
            self.assertEqual(record["fallback_reason"], "ValueError")
            self.assertEqual(record["facts"][0]["quote"], source[0]["content"])
            self.assertNotIn("Permission granted", json.dumps(record))

    def test_summary_timeout_and_cancellation_discard_late_writer(self):
        started, release = threading.Event(), threading.Event()
        class Slow:
            def complete(self, *args, **options):
                started.set(); release.wait(2)
                return {"content": '{"facts":[]}'}
        source = [{"role": "user", "content": "Keep CLI"}]
        manager = TaskSummaries(SummarySettings("model", wait_seconds=0.03), Slow())
        try:
            record = manager.build(source)
            self.assertEqual(record["source"], "extractive")
            self.assertEqual(record["fallback_reason"], "TimeoutError")
            self.assertEqual(manager.build(source), record)
        finally:
            release.set()
        started.clear(); release.clear()
        token = CancellationToken(); errors = []
        manager = TaskSummaries(SummarySettings("model"), Slow())
        def work():
            try: manager.build(source, token)
            except Exception as error: errors.append(error)
        worker = threading.Thread(target=work); worker.start()
        try:
            self.assertTrue(started.wait(2)); token.cancel(); worker.join(2)
            self.assertIsInstance(errors[0], TurnCancelled)
            self.assertEqual(len(manager._cache), 0)
        finally:
            release.set(); worker.join(2)

    def test_summary_digest_rejects_changed_prefix_and_fabricated_role_or_quote(self):
        source = [{"role": "user", "content": "Do not publish"}]
        record = TaskSummaries().build(source)
        for key, value in (("role", "tool"), ("quote", "Publish now"), ("source_index", True)):
            changed = copy.deepcopy(record); changed["facts"][0][key] = value
            with self.assertRaises(ValueError): validate_summary(changed, source)
        with self.assertRaises(ValueError): validate_summary(record, [{"role": "user", "content": "Different branch"}])

    def test_omission_retains_summary_under_budget_without_changing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = [{"role": "system", "content": "rules"},
                {"role": "user", "content": "Decision: keep Java desktop"},
                {"role": "assistant", "content": "Completed Python wiring. " + "x" * 8000 + " Tests passed: 12"},
                {"role": "user", "content": "Continue"}]
            original = copy.deepcopy(messages); saved = []
            budget = ContextBudget(ContextSettings(max_tokens=1700, reserve_tokens=500, tool_tokens=256))
            result = budget.prepare(messages, [], Path(directory), summaries=TaskSummaries(), on_summary=saved.append)
            self.assertEqual(messages, original)
            self.assertEqual(result.record["omitted_turns"], 1)
            self.assertLessEqual(result.record["estimated_tokens"], 1200)
            self.assertIn("keep Java desktop", str(result.messages))
            self.assertIn("Tests passed: 12", str(result.messages))
            self.assertEqual(saved[0]["source_count"], 2)
            self.assertGreater(saved[0]["included_facts"], 0)

    def test_no_omission_does_not_invoke_summary_writer(self):
        writer = Replies('{"facts":[]}')
        with tempfile.TemporaryDirectory() as directory:
            result = ContextBudget().prepare([{ "role": "system", "content": "rules"},
                {"role": "user", "content": "Hello"}], [], Path(directory),
                summaries=TaskSummaries(SummarySettings("model"), writer))
        self.assertEqual(writer.calls, 0)
        self.assertNotIn("summary", result.record)

    def test_optional_summary_can_be_removed_to_fit_latest_mandatory_input(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = [{"role": "system", "content": "rules"},
                {"role": "user", "content": "Keep old task details"},
                {"role": "assistant", "content": "Old observations: " + "x" * 4000},
                {"role": "user", "content": "Latest request " + "n" * 1900}]
            budget = ContextBudget(ContextSettings(max_tokens=1200, reserve_tokens=500))
            result = budget.prepare(messages, [], Path(directory), summaries=TaskSummaries())
            self.assertFalse(result.record["blocked"])
            self.assertEqual(result.messages[-1], messages[-1])
            self.assertLessEqual(result.record["estimated_tokens"], 700)
            self.assertEqual(result.record["summary"]["included_facts"], 0)

    def test_summary_render_honors_character_and_byte_limits(self):
        record = TaskSummaries().build([{"role": "user", "content": "汉🌱" * 1000},
                                       {"role": "assistant", "content": "Build completed"}])
        note, _ = summary_note(record, 1400, 512)
        self.assertLessEqual(len(note), 512)
        self.assertLessEqual(len(note.encode()), 1400)

    def test_shadow_records_without_semantic_archives_and_budget_still_applies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = ContextBudget().prepare(tool_messages(), [], root, admission=admission("shadow"))
            self.assertEqual(result.artifacts, [])
            self.assertEqual(result.messages[-1]["content"], TEXT)
            self.assertEqual(result.record["admission"][0]["proposed_drop_chunks"], 3)
            tight = ContextBudget(ContextSettings(tool_tokens=256)).prepare(tool_messages(), [], root,
                admission=admission("shadow"))
            self.assertTrue(tight.artifacts)
            self.assertEqual(tight.record["shortened_tools"][0]["reason"], "tool_limit")
            self.assertEqual(tight.record["admission"][0]["applied_drop_chunks"], 0)

    def test_frame_note_and_summary_keep_canonical_archive_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); backend = Kinds()
            engine = admission(backend=backend).judge
            class Model:
                calls = 0
                def complete(self, messages, tools):
                    self.calls += 1
                    if self.calls == 2:
                        return {"tool_calls": [{"id": "log", "function": {"name": "emit_log", "arguments": "{}"}}]}
                    return {"content": "done"}
            settings = ContextSettings(max_tokens=5000, reserve_tokens=500, tool_tokens=256)
            tools = WorkspaceTools(root, output_root=root / "outputs")
            tools.registry.register(ToolDefinition("emit_log", "Fixture log", {"type": "object", "properties": {}},
                lambda args, context: ToolResult.from_text(TEXT), effect="read"))
            agent = Agent(Model(), tools, engine, context_settings=settings)
            list(agent.run("Constraint: preserve database schema"))
            agent.messages[-1]["content"] += "x" * 24000
            records = []
            list(agent.run("Continue", on_context=records.append))
            self.assertIn("preserve database schema", agent.frame.note())
            self.assertEqual(len([item for item in agent.messages if item["role"] == "system"]), 1)
            self.assertTrue(records[0]["summary"]["included_facts"])
            item = records[-1]["shortened_tools"][0]
            self.assertEqual(item["source_message_index"], 5)
            self.assertEqual(agent.messages[5]["content"], TEXT)
            self.assertEqual((tools.output_root / (item["artifact_id"] + ".log")).read_text(), TEXT)
            self.assertEqual(item["reason"], "semantic+tool_limit")

    def test_summary_restart_branch_fork_and_corrupt_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = SessionStore.create(root, root / "sessions")
            def turn(prompt, reply, tid, summary=None):
                store.append("turn.started", {}, tid)
                for message in ({"role": "user", "content": prompt}, {"role": "assistant", "content": reply}):
                    store.append("message", {"message": message}, tid)
                if summary is not None:
                    store.append("context.summary", {**summary, "included_facts": 2}, tid)
                return store.append("turn.completed", {}, tid)["event_id"]
            point = turn("Keep Java", "Wiring done", "one")
            source = store.restore_messages()
            record = TaskSummaries().build(source)
            turn("Next", "Done", "two", record)
            fork = store.fork(store.leaf_id)
            # A summary recorded inside a later turn is copied only with that completed path.
            self.assertEqual(len(store.summary_records()), 1)
            self.assertEqual(len(fork.summary_records()), 1)
            self.assertTrue(any(kind == "history.summary" for kind, _ in fork.history()))
            reopened = SessionStore.load(root, store.path.parent, store.session_id)
            self.assertEqual(reopened.summary_records(), store.summary_records())
            manager = TaskSummaries(); manager.restore(store.restore_messages(), store.summary_records())
            self.assertEqual(manager.build(source)["source_digest"], record["source_digest"])
            self.assertTrue(any(kind == "history.summary" for kind, _ in store.history()))
            bad = copy.deepcopy(record); bad["facts"][0]["quote"] = "Fabricated"
            store.append("context.summary", bad)
            self.assertEqual(len(store.summary_records()), 1)
            store.select(point)
            self.assertEqual(store.summary_records(), [])
            fresh = SessionStore.create(root, root / "sessions")
            self.assertEqual(fresh.summary_records(), [])

    def test_settings_and_laya_cannot_be_used_for_admission(self):
        for settings in ({"batch_chunks": 33}, {"chunk_chars": 1201}, {"wait_seconds": float("nan")}):
            with self.assertRaises(ValueError): AdmissionSettings(**settings)
        for settings in ({"mode": "invalid"}, {"max_chars": True}, {"wait_seconds": -1}):
            with self.assertRaises(ValueError): SummarySettings(**settings)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "judge.json"
            path.write_text(json.dumps({"version": 1, "backends": {"lab": {"type": "laya_http", "url": "http://127.0.0.1:18765"}},
                "points": {"tool.admission": {"mode": "shadow", "routes": ["lab"]}}}))
            with self.assertRaises(ValueError): engine_from_environment(environ={"MU_JUDGE_CONFIG": str(path)})

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK unavailable")
    def test_java_http_flow_recovers_semantic_output_summarizes_and_forks(self):
        requests, admitted = [], []
        fixture_text = TEXT.replace("progress " + "x" * 12, "progress NOISE_NEEDLE", 1)
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                if payload["model"] == "classifier":
                    state = json.loads(payload["messages"][-1]["content"])
                    answers = {key: {"answer": kind_of(state["state"]["chunks"][int(key.split("_")[1])]),
                                     "confidence": 0.99} for key in state["decision"]["questions"]}
                    reply = {"content": json.dumps({"answers": answers})}
                elif payload["model"] == "writer":
                    candidates = json.loads(payload["messages"][-1]["content"])
                    candidate = next(item for item in reversed(candidates) if item["role"] == "assistant")
                    reply = {"content": json.dumps({"facts": [{"source_index": candidate["source_index"],
                                                               "quote": candidate["quote"]}]})}
                else:
                    last = payload["messages"][-1]
                    if last["role"] == "user" and last["content"] == "Run fixture":
                        reply = {"tool_calls": [{"id": "log", "function": {"name": "emit_log", "arguments": "{}"}}]}
                    elif last["role"] == "tool" and "Semantic admission:" in last["content"]:
                        admitted.append(last["content"])
                        match = re.search(r"UTF-8 bytes (\d+)\.\.(\d+) omitted\. Full text id: ([\w-]+)", last["content"])
                        reply = {"tool_calls": [{"id": "recover", "function": {"name": "read_tool_output",
                            "arguments": json.dumps({"id": match[3], "offset": int(match[1]),
                                                     "limit": int(match[2]) - int(match[1])})}}]}
                    elif last["role"] == "tool":
                        reply = {"content": "Recovered NOISE_NEEDLE" if "NOISE_NEEDLE" in last["content"] else "Recovery failed"}
                    else:
                        reply = {"content": "Continued from saved task evidence"}
                body = json.dumps({"choices": [{"message": reply}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            def log_message(self, *args):
                pass
        with tempfile.TemporaryDirectory() as directory, HTTPServer(("127.0.0.1", 0), Handler) as server:
            root = Path(directory); workspace = root / "workspace"; workspace.mkdir()
            module = root / "output_fixture.py"
            module.write_text("from mupyjava.registry import ToolDefinition\nfrom mupyjava.tool_result import ToolResult\n"
                "def register_tools(registry):\n"
                "    registry.register(ToolDefinition('emit_log', 'Fixture log', "
                "{'type':'object','properties':{},'additionalProperties':False}, "
                f"lambda args, context: ToolResult.from_text({fixture_text!r}), effect='read'))\n")
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            config = root / "judge.json"
            config.write_text(json.dumps({"version": 1, "backends": {"kind": {"type": "model", "model": "classifier", "api_base": endpoint}},
                "points": {"tool.admission": {"mode": "active", "routes": ["kind"]}}}))
            classes = root / "classes"
            compiled = subprocess.run(["javac", "-d", str(classes), *map(str,
                (REPOSITORY / "java/src/main/java/dev/mupyjava").glob("*.java"))], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            env = os.environ.copy()
            env.update({"MU_MODEL_BACKEND": "", "MU_MODEL": "main", "MU_API_BASE": endpoint, "MU_API_KEY": "",
                "MU_JUDGE_CONFIG": str(config), "MU_JUDGE_MODE": "off", "MU_JUDGE_LAYA_PATH": "", "MU_JUDGE_LAYA_URL": "",
                "MU_JUDGE_MODEL": "", "MU_TOOL_MODULES": "output_fixture", "PYTHONPATH": str(root),
                "MU_PYTHON": sys.executable, "MU_SESSION_DIR": str(root / "sessions"), "MU_CONTEXT_TOKENS": "9000",
                "MU_RESPONSE_TOKENS": "600", "MU_TOOL_RESULT_TOKENS": "8192", "MU_SUMMARY_MODE": "model",
                "MU_SUMMARY_MODEL": "writer", "MU_SUMMARY_API_BASE": endpoint, "MU_SUMMARY_API_KEY": ""})
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            def run(prompt):
                result = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main", "--workspace", str(workspace),
                    "--smoke", prompt], cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("error:", result.stdout)
                return result.stdout
            try:
                first = run("Run fixture")
                self.assertIn("Recovered NOISE_NEEDLE", first)
                self.assertTrue(admitted)
                self.assertNotIn("NOISE_NEEDLE", admitted[0])
                self.assertIn(row("error"), admitted[0])
                second = run("Continue next step: " + "n" * 18000)
                self.assertIn("summary.detail: History summary", second)
                store = SessionStore.open(workspace, root / "sessions")
                self.assertTrue(store.summary_records())
                self.assertEqual(store.summary_records()[-1]["source"], "model")
                fork = store.fork(store.leaf_id)
                self.assertEqual(fork.summary_records(), store.summary_records())
                shutil.rmtree(store.output_dir); module.unlink(); env["MU_TOOL_MODULES"] = ""
                third = run("continue")
                self.assertIn("history.summary: History summary", third)
                artifacts = {entry["payload"]["id"] for entry in fork.events() if entry["type"] == "tool.artifact"}
                self.assertTrue(any((fork.output_dir / (artifact + ".log")).read_text() == fixture_text for artifact in artifacts))
                self.assertTrue(any(entry["type"] == "judge.record" and entry["payload"].get("point") == "tool.admission"
                                    for entry in fork.events()))
                for request in requests:
                    self.assertTrue(all("tool_is_error" not in message for message in request["messages"]))
                    if request["model"] == "main":
                        validate_chain(request["messages"])
                        self.assertEqual(request["max_tokens"], 600)
            finally:
                server.shutdown(); thread.join(5)


if __name__ == "__main__":
    unittest.main()
