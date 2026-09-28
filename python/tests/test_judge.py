import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from mupyjava.agent import Agent
from mupyjava.cancel import CancellationToken, TurnCancelled
from mupyjava.decision_points import TOOL_INTENT, TOOL_REVIEW, TOOL_RISK_SCORE, builtin_registry
from mupyjava.judge import (BooleanJudgment, DecisionEngine, DecisionPoint, DecisionPolicy,
                           DecisionRegistry, LayaBooleanJudge, LayaHttpBooleanJudge, ModelTypedJudge, TypedJudgment)
from mupyjava.judge_config import engine_from_environment
from mupyjava.sessions import SessionStore
from mupyjava.tools import WorkspaceTools


class FixedJudge:
    def __init__(self, judgment):
        self.judgment = judgment
        self.calls = []

    def evaluate_point(self, point, state):
        self.calls.append((point.id, state))
        return self.judgment


class Replies:
    def __init__(self, *messages):
        self.messages = iter(messages)

    def complete(self, messages, tools):
        return next(self.messages)


def writing_model():
    return Replies({"role": "assistant", "tool_calls": [{"id": "write", "function": {
        "name": "write_file", "arguments": json.dumps({"path": "note.txt", "content": "private-body"})
    }}]}, {"role": "assistant", "content": "done"})


class JudgeTests(unittest.TestCase):
    def engine(self, point, backends, policy):
        registry = DecisionRegistry()
        registry.register(point)
        return DecisionEngine(backends=backends, policies={point.id: policy}, registry=registry)

    def test_spec_rejects_ambiguous_choices_wrong_fallback_and_invalid_revision(self):
        invalid = [dict(version=True), dict(version=0), dict(id="bad id"), dict(fallback="true"),
                   dict(kind="choice", choices=("allow", "deny"), fallback="allow"),
                   dict(kind="choice", choices=("unknown", "unknown"), fallback="unknown"),
                   dict(kind="score", fallback=True), dict(kind="score", fallback=float("nan")),
                   dict(kind="score", fallback=2), dict(kind="score", score_max=0),
                   dict(allowed_modes=("other",)), dict(default_mode="other")]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                DecisionPoint(**{**dict(id="test", version=1, question="Needed?", fallback=False), **changes})

    def test_registry_rejects_conflicts_and_unknown_frozen_points(self):
        registry = DecisionRegistry()
        registry.register(TOOL_INTENT)
        registry.freeze()
        self.assertIs(registry.register(TOOL_INTENT), TOOL_INTENT)
        with self.assertRaises(ValueError):
            registry.register(DecisionPoint("tool.intent", 3, "Changed?", True))
        with self.assertRaises(ValueError):
            registry.register(TOOL_REVIEW)
        with self.assertRaises(ValueError):
            registry.resolve("missing")

    def test_invalid_answers_cannot_truthily_authorize(self):
        for answer in ("YES", "false", 1, 0, [], {"answer": True}):
            backend = FixedJudge(TypedJudgment(answer, 0.99))
            point = DecisionPoint("test", 1, "Needed?", False)
            engine = self.engine(point, {"a": backend}, DecisionPolicy("active", ("a",)))
            with self.subTest(answer=answer):
                self.assertIs(engine.decide(point, {}), False)
                self.assertEqual(engine.last_record["failure"], "ValueError")
                self.assertNotIn("answer", engine.last_record["attempts"][0])

    def test_typed_choice_and_score_preserve_decline_and_zero(self):
        points = [(TOOL_REVIEW, "decline"),
                  (DecisionPoint("score", 1, "Score?", 0.5, kind="score"), 0)]
        for point, answer in points:
            engine = self.engine(point, {"a": FixedJudge(TypedJudgment(answer, 0.9))},
                                 DecisionPolicy("active", ("a",), 0.8))
            self.assertEqual(engine.decide(point.id, {}), answer)
            self.assertEqual(engine.last_record["source"], "judge")
        for value in (True, -0.1, 1.1, float("inf"), float("nan"), "0.8"):
            point = points[1][0]
            engine = self.engine(point, {"a": FixedJudge(TypedJudgment(value))}, DecisionPolicy("active", ("a",)))
            self.assertEqual(engine.decide(point, {}), 0.5)

    def test_choice_escape_and_low_confidence_follow_routes(self):
        backends = {"uncertain": FixedJudge(TypedJudgment("unknown", 1)),
                    "weak": FixedJudge(TypedJudgment("decline", 0.6)),
                    "strong": FixedJudge(TypedJudgment("continue", 0.9))}
        engine = self.engine(TOOL_REVIEW, backends, DecisionPolicy("active", tuple(backends), 0.8))
        self.assertEqual(engine.decide(TOOL_REVIEW, {}), "continue")
        self.assertEqual(engine.last_record["backend"], "strong")
        self.assertEqual([attempt["reason"] for attempt in engine.last_record["attempts"]],
                         ["abstain", "low_confidence", "accepted"])

    def test_boolean_probability_confidence_and_consistency(self):
        class ProbabilityJudge:
            def evaluate(self, question, state):
                return BooleanJudgment(False, 0.08)
        point = DecisionPoint("test", 1, "Needed?", True)
        engine = self.engine(point, {"a": ProbabilityJudge()}, DecisionPolicy("active", ("a",), 0.9))
        self.assertIs(engine.decide(point, {}), False)
        self.assertAlmostEqual(engine.last_record["confidence"], 0.92)
        for judgment in (TypedJudgment(True, True), TypedJudgment(True, 1.1),
                         TypedJudgment(True, 0.9, 0.1), TypedJudgment(True, float("nan"))):
            engine = self.engine(point, {"a": FixedJudge(judgment)}, DecisionPolicy("active", ("a",)))
            self.assertEqual(engine.decide(point, {}), point.fallback)
            self.assertEqual(engine.last_record["failure"], "ValueError")
        engine = self.engine(point, {"a": FixedJudge(TypedJudgment(False))}, DecisionPolicy("active", ("a",), 0.8))
        self.assertIs(engine.decide(point, {}), True)
        self.assertEqual(engine.last_record["failure"], "low_confidence")

    def test_off_shadow_and_active_policies_are_independent(self):
        backend = FixedJudge(TypedJudgment("decline", 1))
        engine = DecisionEngine("off", backends={"reviewer": backend}, registry=builtin_registry(), policies={
            "tool.review": DecisionPolicy("shadow", ("reviewer",))})
        self.assertTrue(engine.decide(TOOL_INTENT, {}))
        self.assertEqual(backend.calls, [])
        self.assertEqual(engine.decide(TOOL_REVIEW, {}), "continue")
        self.assertEqual(engine.last_record["answer"], "decline")
        self.assertEqual(engine.last_record["fallback_reason"], "shadow")

    def test_cascade_isolates_state_and_falls_back_without_logging_secrets(self):
        class MutatingFailure:
            def evaluate_point(self, point, state):
                state["arguments"]["path"] = "changed"
                raise RuntimeError("sensitive-token")
        second = FixedJudge(TypedJudgment(False, 1))
        point = DecisionPoint("test", 1, "Needed?", True)
        engine = self.engine(point, {"bad": MutatingFailure(), "good": second},
                             DecisionPolicy("active", ("bad", "good")))
        state = {"tool": "write_file", "arguments": {"path": "original"}, "token": "sensitive-token"}
        self.assertFalse(engine.decide(point, state))
        self.assertEqual(state["arguments"]["path"], "original")
        self.assertEqual(second.calls[0][1]["arguments"]["path"], "original")
        self.assertNotIn("sensitive-token", json.dumps(engine.last_record))

    def test_timeout_uses_next_route_and_late_result_cannot_replace_record(self):
        release = threading.Event()
        finished = threading.Event()
        class SlowJudge:
            def evaluate_point(self, point, state):
                release.wait(2)
                finished.set()
                return TypedJudgment(True, 1)
        point = DecisionPoint("test", 1, "Needed?", True)
        engine = self.engine(point, {"slow": SlowJudge(), "fast": FixedJudge(TypedJudgment(False, 1))},
                             DecisionPolicy("active", ("slow", "fast"), timeout_seconds=0.04))
        try:
            self.assertFalse(engine.decide(point, {}))
            before = json.dumps(engine.last_record)
            release.set()
            self.assertTrue(finished.wait(1))
            self.assertEqual(json.dumps(engine.last_record), before)
            self.assertEqual(engine.last_record["attempts"][0]["reason"], "TimeoutError")
        finally:
            release.set()

    def test_abandoned_workers_are_bounded(self):
        release = threading.Event()
        class Blocked:
            def evaluate_point(self, point, state):
                release.wait(2)
                return TypedJudgment(True)
        point = DecisionPoint("test", 1, "Needed?", False)
        engine = self.engine(point, {"a": Blocked()}, DecisionPolicy("active", ("a",), timeout_seconds=0.02))
        try:
            self.assertFalse(engine.decide(point, {}))
            self.assertFalse(engine.decide(point, {}))
            self.assertFalse(engine.decide(point, {}))
            self.assertEqual(engine.last_record["failure"], "RuntimeError")
        finally:
            release.set()

    def test_cancel_wait_prevents_approval_mutation_and_late_record(self):
        entered, release = threading.Event(), threading.Event()
        class Blocked:
            def answer(self, question, state):
                entered.set()
                release.wait(2)
                return True
        with tempfile.TemporaryDirectory() as directory:
            token = CancellationToken()
            engine = DecisionEngine("active", Blocked())
            agent = Agent(writing_model(), WorkspaceTools(Path(directory), allow_write=True), engine)
            errors, records, approvals = [], [], []
            def run():
                try:
                    list(agent.run("Write note.txt", cancel=token, on_judgment=records.append,
                                   approval=lambda *args: approvals.append(args) or True))
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                token.cancel()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors[0], TurnCancelled)
                self.assertEqual(records, [])
                self.assertEqual(approvals, [])
                self.assertFalse((Path(directory) / "note.txt").exists())
                self.assertIsNone(engine.last_record)
            finally:
                release.set()

    def test_broken_ledger_and_oversized_state_follow_declared_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            point = DecisionPoint("test", 1, "Needed?", True)
            backend = FixedJudge(TypedJudgment(False, 1))
            engine = self.engine(point, {"a": backend}, DecisionPolicy("active", ("a",)))
            engine.ledger = Path(directory)  # Cannot append JSONL to a directory.
            self.assertFalse(engine.decide(point, {}))
            self.assertIn("ledger_failure", engine.last_record)
            self.assertTrue(engine.decide(point, {"data": "x" * 65536}))
            self.assertEqual(len(backend.calls), 1)

    def test_laya_shadow_guard_cannot_be_bypassed_through_routes(self):
        laya = LayaHttpBooleanJudge("http://127.0.0.1:12345")
        with self.assertRaisesRegex(ValueError, "shadow-only"):
            self.engine(TOOL_INTENT, {"trained": laya}, DecisionPolicy("active", ("trained",)))
        with self.assertRaisesRegex(ValueError, "does not support"):
            self.engine(TOOL_REVIEW, {"trained": laya}, DecisionPolicy("shadow", ("trained",)))
        with self.assertRaisesRegex(ValueError, "not allowed"):
            self.engine(TOOL_RISK_SCORE, {"a": FixedJudge(TypedJudgment(0.8))}, DecisionPolicy("active", ("a",)))
        class InvalidLaya:
            def predict(self, state, questions):
                return {"answers": {"intent": {"noul": True}}}
        with self.assertRaisesRegex(ValueError, "nonnumeric"):
            LayaBooleanJudge("unused", agent=InvalidLaya()).answer("Needed?", {})

    def test_cancel_after_judge_event_does_not_ask_for_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            token = CancellationToken()
            approvals = []
            engine = DecisionEngine("active", backends={"default": FixedJudge(TypedJudgment(True, 1))})
            agent = Agent(writing_model(), WorkspaceTools(Path(directory), allow_write=True), engine)
            events = agent.run("Write note.txt", cancel=token,
                               approval=lambda *args: approvals.append(args) or True)
            self.assertEqual(next(events)[0], "judge")
            token.cancel()
            with self.assertRaises(TurnCancelled):
                next(events)
            self.assertEqual(approvals, [])
            self.assertFalse((Path(directory) / "note.txt").exists())

    def test_agent_review_veto_and_continue_preserve_human_and_workspace_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            for answer, approve, permission in (("decline", True, True), ("continue", False, True),
                                                ("continue", True, False), ("continue", True, True)):
                with self.subTest(answer=answer, approve=approve, permission=permission):
                    path = Path(directory) / "note.txt"
                    path.unlink(missing_ok=True)
                    backend = FixedJudge(TypedJudgment(answer, 1))
                    engine = DecisionEngine(backends={"review": backend}, registry=builtin_registry(), policies={
                        "tool.review": DecisionPolicy("active", ("review",))})
                    records, approvals = [], []
                    list(Agent(writing_model(), WorkspaceTools(Path(directory), allow_write=permission), engine).run(
                        "Write note.txt", approval=lambda *args: approvals.append(args) or approve,
                        on_judgment=records.append))
                    self.assertEqual(path.exists(), answer == "continue" and approve and permission)
                    self.assertEqual(bool(approvals), answer == "continue")
                    self.assertEqual([record["point"] for record in records], ["tool.review"])
                    self.assertNotIn("private-body", json.dumps(backend.calls))

    def test_each_action_point_emits_its_own_snapshot(self):
        class MultiJudge:
            def evaluate_point(self, point, state):
                return TypedJudgment({"tool.intent": True, "tool.review": "continue", "tool.risk_score": 0.2}[point.id], 0.95)
        engine = DecisionEngine(backends={"all": MultiJudge()}, registry=builtin_registry(), policies={
            point.id: DecisionPolicy("shadow", ("all",)) for point in (TOOL_INTENT, TOOL_REVIEW, TOOL_RISK_SCORE)})
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore.open(Path(directory), Path(directory) / "sessions", resume=False)
            turn = "one"
            store.append("turn.started", {}, turn)
            def save(record):
                store.append("judge.record", record, turn)
                record["point"] = "changed-by-callback"
            list(Agent(writing_model(), WorkspaceTools(Path(directory), allow_write=True), engine).run(
                "Write note.txt", on_judgment=save))
            store.append("turn.completed", {}, turn)
            reopened = SessionStore.open(Path(directory), Path(directory) / "sessions", resume=True)
            judgments = [text for kind, text in reopened.history() if kind == "history.judge"]
            self.assertEqual(len(judgments), 3)
            self.assertIn("tool.intent v2", judgments[0])
            self.assertIn("tool.review v1", judgments[1])
            self.assertIn("0.2", judgments[2])
            self.assertEqual(engine.last_record["point"], "tool.risk_score")

    def test_model_adapter_requires_strict_json_envelope(self):
        for payload in ('YES', '{"answer":true}', '{"answer":true,"confidence":1,"extra":1}',
                        '{"answer":false,"answer":true,"confidence":1}', '{"answer":NaN,"confidence":1}',
                        '```json\n{"answer":true,"confidence":1}\n```'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ModelTypedJudge(Replies({"content": payload})).evaluate_point(TOOL_INTENT, {})
        result = ModelTypedJudge(Replies({"content": '{"answer":"decline","confidence":0.9}'})).evaluate_point(TOOL_REVIEW, {})
        self.assertEqual(result.answer, "decline")

    def test_strict_configuration_and_environment_only_keys(self):
        base = {"version": 1, "backends": {"typed": {"type": "model", "model": "fixture",
                "api_base": "http://127.0.0.1:12345/v1", "api_key_env": "JUDGE_TEST_KEY"}},
                "points": {"tool.review": {"mode": "shadow", "routes": ["typed"], "min_confidence": 0.8}}}
        invalid = [{**base, "version": True}, {**base, "typo": 1}, {**base, "points": {"unknown": {}}},
                   {**base, "points": {"tool.review": {"mode": "shadow", "routes": ["missing"]}}},
                   {**base, "points": {"tool.review": {"mode": "active", "routes": []}}},
                   {**base, "points": {"tool.review": {"mode": "shadow", "routes": ["typed"], "typo": 1}}},
                   {**base, "points": {"tool.risk_score": {"mode": "active", "routes": ["typed"]}}},
                   {**base, "backends": {"typed": {"type": "model", "model": "fixture", "api_key": "literal-secret"}}},
                   {**base, "points": {"tool.review": {"mode": "shadow", "routes": ["typed"], "min_confidence": True}}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "judge.json"
            path.write_text(json.dumps(base))
            engine = engine_from_environment(environ={"MU_JUDGE_CONFIG": str(path), "JUDGE_TEST_KEY": "test-only"})
            self.assertEqual(engine.policy_for(TOOL_REVIEW).mode, "shadow")
            self.assertEqual(engine.backends["typed"].model.api_key, "test-only")
            self.assertEqual(engine.backends["typed"].model.max_output_tokens, 256)
            for config in invalid:
                path.write_text(json.dumps(config))
                with self.subTest(config=config), self.assertRaises(ValueError):
                    engine_from_environment(environ={"MU_JUDGE_CONFIG": str(path)})
            for content in ('{"version":1,"version":1}', '{"version":1,"default_mode":NaN}'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    engine_from_environment(environ={"MU_JUDGE_CONFIG": str(path)})
            with self.assertRaises(ValueError):
                engine_from_environment(environ={"MU_JUDGE_MODE": "shadow"})
            with self.assertRaisesRegex(ValueError, "shadow-only"):
                engine_from_environment(environ={"MU_JUDGE_MODE": "active", "MU_JUDGE_LAYA_URL": "http://localhost:12345"})

    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "Java is unavailable")
    def test_java_live_typed_judgments_restore_with_backend_attempts(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if payload["model"] == "judge-fixture":
                    decision = json.loads(payload["messages"][-1]["content"])["decision"]
                    answer = "continue" if decision["kind"] == "choice" else 0.7
                    message = {"role": "assistant", "content": json.dumps({"answer": answer, "confidence": 0.95})}
                elif payload["messages"][-1]["role"] == "tool":
                    message = {"role": "assistant", "content": "done"}
                else:
                    message = {"role": "assistant", "tool_calls": [{"id": "write", "type": "function", "function": {
                        "name": "write_file", "arguments": json.dumps({"path": "note.txt", "content": "written"})}}]}
                body = json.dumps({"choices": [{"message": message}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            repository = Path(__file__).resolve().parents[2]
            root = Path(directory)
            classes = root / "classes"
            classes.mkdir()
            config_path = root / "judge.json"
            config_path.write_text(json.dumps({"version": 1,
                "backends": {"typed": {"type": "model", "model": "judge-fixture"}},
                "points": {point: {"mode": "shadow", "routes": ["typed"], "min_confidence": 0.8}
                           for point in ("tool.review", "tool.risk_score")}}))
            env = {key: value for key, value in os.environ.items() if not key.startswith("MU_")}
            env.update({"MU_MODEL": "agent-fixture", "MU_PYTHON": sys.executable, "MU_API_KEY": "",
                        "MU_API_BASE": f"http://127.0.0.1:{server.server_port}/v1",
                        "MU_JUDGE_CONFIG": str(config_path), "MU_SESSION_DIR": str(root / "sessions")})
            try:
                subprocess.run(["javac", "-d", str(classes), *map(str,
                    (repository / "java/src/main/java/dev/mupyjava").glob("*.java"))], check=True, capture_output=True)
                command = ["java", "-cp", str(classes), "dev.mupyjava.Main", "--workspace", str(root),
                           "--smoke", "Write note.txt", "--smoke-approval", "once"]
                first = subprocess.run(command, cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertIn("judge: tool.review v1", first.stdout)
                self.assertIn("judge: tool.risk_score v1", first.stdout)
                self.assertIn("backend: typed", first.stdout)
                self.assertEqual((root / "note.txt").read_text(), "written")
                second = subprocess.run(command, cwd=repository, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertIn("history.judge: tool.review v1", second.stdout)
                self.assertIn("history.judge: tool.risk_score v1", second.stdout)
                self.assertIn("typed (accepted)", second.stdout)
                store = SessionStore.open(root, root / "sessions", resume=True)
                self.assertEqual(len([event for event in store.events() if event["type"] == "judge.record"]), 4)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
