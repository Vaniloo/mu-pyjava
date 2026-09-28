import copy
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
from mupyjava.decision_points import (TASK_FRAME, TOOL_ADMISSION, TOOL_CONSTRAINT,
                                    TOOL_INTENT, TOOL_RISK_SCORE, builtin_registry)
from mupyjava.judge import BooleanJudgment, DecisionEngine, DecisionPolicy, LayaHttpBooleanJudge, TypedJudgment
from mupyjava.judge_config import engine_from_environment
from mupyjava.judge_data import (evaluate, export_intent, label_templates, load_labels,
                                load_samples, replay_sample, training_partitions, validate_manifest)
from mupyjava.judge_samples import JudgeSampler, canonical, digest, private_append
from mupyjava.tools import WorkspaceTools


class Replies:
    def __init__(self, value):
        self.value = value
        self.states = []

    def evaluate_point(self, point, state):
        self.states.append(copy.deepcopy(state))
        return self.value

    def evaluate_questions(self, spec, questions, state):
        self.states.append(copy.deepcopy(state))
        return {q.id: self.value[q.id] for q in questions if q.id in self.value}


class JudgeDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "samples.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def sample(self, point=TOOL_INTENT, value=BooleanJudgment(True, .95), inputs=None,
               mode="shadow", group="repo/task-family", confidence=None, backends=None, routes=("default",)):
        state = inputs or {"user_request": "解释代码，不要修改", "tool": "edit_file",
                           "arguments": {"path": "Main.java", "edit_count": 1}}
        sampler = JudgeSampler(self.path, group)
        sampler.begin_turn()
        backend = Replies(value)
        policy = DecisionPolicy(mode, routes, point.min_confidence if confidence is None else confidence)
        engine = DecisionEngine(mode, backends=backends or {"default": backend},
                                policies={point.id: policy}, registry=builtin_registry(), sampler=sampler)
        outcome = engine.decide(point, state)
        return load_samples(self.path)[-1], engine, backend, outcome

    def labels(self, samples, answers, origin="manual", reviewed=True):
        rows = label_templates(samples)
        for row, answer in zip(rows, answers):
            row.update(reviewed=reviewed, origin=origin, reviewer="fixture-reviewer", rationale="fixture only")
            row["answers"] = answer if isinstance(answer, dict) else {samples[0]["decision"]["point"]: answer}
        path = self.root / "labels.jsonl"
        path.write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
        return load_labels(path, samples)

    def test_input_fidelity_and_private_storage(self):
        sample, engine, backend, outcome = self.sample()
        self.assertEqual(sample["decision"]["state"], backend.states[0])
        self.assertEqual(sample["decision"]["questions"][0]["question"], TOOL_INTENT.question)
        self.assertEqual(sample["input_digest"], digest(sample["decision"]))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(outcome)  # Shadow retains the declared fallback.
        self.assertNotIn("state", engine.last_record)
        self.assertNotIn("label", sample)

    def test_dynamic_questions_and_state_captured_once(self):
        inputs = {"tool": "run_command", "call": "git push", "constraints": ["不要推送", "别修改文件"],
                  "constraint_offset": 4, "frame_version": 7}
        sample, _, backend, _ = self.sample(TOOL_CONSTRAINT,
            {"constraint_0": BooleanJudgment(True, .9), "constraint_1": BooleanJudgment(False, .1)}, inputs)
        self.assertEqual(sample["decision"]["state"], {"tool_call": "run_command: git push"})
        self.assertEqual(sample["decision"]["inputs"], inputs)
        self.assertIn("不要推送", sample["decision"]["questions"][0]["question"])
        self.assertEqual(len(backend.states), 1)
        replay = replay_sample(sample)
        self.assertEqual(replay["judged"], {"broken": [0]})
        self.assertEqual(replay["observed_outcome"], {"broken": []})

    def test_off_sampling_makes_no_model_call(self):
        sample, _, backend, _ = self.sample(mode="off")
        self.assertEqual(backend.states, [])
        self.assertEqual(replay_sample(sample)["answers"], {})

    def test_sampling_failure_does_not_change_decision(self):
        self.path.mkdir()
        sampler = JudgeSampler(self.path, "repo/task")
        engine = DecisionEngine("shadow", Replies(BooleanJudgment(False, .05)), sampler=sampler)
        self.assertTrue(engine.decide(TOOL_INTENT, {"tool": "write_file"}))
        self.assertEqual(engine.last_record["sample_failure"], "IsADirectoryError")

    def test_oversize_and_public_files_not_written(self):
        self.path.write_text("", encoding="utf-8")
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            private_append(self.path, {"secret": "do-not-store"})
        self.assertEqual(self.path.read_text(), "")
        engine = DecisionEngine(sampler=JudgeSampler(self.root / "large.jsonl", "group"))
        self.assertTrue(engine.decide(TOOL_INTENT, {"body": "x" * 70_000}))
        self.assertEqual(engine.last_record["sample_failure"], "ValueError")
        self.assertFalse((self.root / "large.jsonl").exists())

    def test_symlink_file_is_rejected(self):
        target = self.root / "target"
        target.touch(mode=0o600)
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            private_append(self.path, {"body": "private"})
        self.assertEqual(target.read_text(), "")

    def test_metadata_ledger_contains_only_sample_reference(self):
        ledger = self.root / "ledger.jsonl"
        engine = DecisionEngine(ledger=ledger, sampler=JudgeSampler(self.path, "task"))
        engine.decide(TOOL_INTENT, {"tool": "write_file", "user_request": "PRIVATE_REQUEST"})
        self.assertNotIn("PRIVATE_REQUEST", ledger.read_text())
        self.assertIn("sample_id", ledger.read_text())
        self.assertIn("PRIVATE_REQUEST", self.path.read_text())

    def test_cancelled_decision_is_not_saved_as_completed(self):
        cancel = CancellationToken()
        cancel.cancel()
        engine = DecisionEngine(sampler=JudgeSampler(self.path, "task"))
        with self.assertRaises(TurnCancelled):
            engine.decide(TOOL_INTENT, {"tool": "write_file"}, cancel=cancel)
        self.assertFalse(self.path.exists())

    def test_configuration_requires_group_and_separate_file(self):
        with self.assertRaises(ValueError):
            engine_from_environment(environ={"MU_JUDGE_SAMPLES": str(self.path)})
        env = {"MU_JUDGE_SAMPLES": str(self.path), "MU_JUDGE_SAMPLE_GROUP": "task"}
        with self.assertRaises(ValueError):
            engine_from_environment(self.path, env)
        self.assertIsNone(engine_from_environment(environ={}).sampler)

    def test_annotation_binding_types_and_provenance(self):
        sample, _, _, _ = self.sample()
        rows = label_templates([sample])
        self.assertFalse(rows[0]["reviewed"])
        self.assertIsNone(rows[0]["answers"][TOOL_INTENT.id])
        for mutation in ({"input_digest": "wrong"}, {"reviewed": True},
                         {"answers": {TOOL_INTENT.id: 1}}, {"answers": {"unknown_id": True}}):
            broken = {**rows[0], **mutation}
            path = self.root / "bad.jsonl"
            path.write_text(canonical(broken) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_labels(path, [sample])

    def test_specification_and_payload_drift_rejected(self):
        sample, _, _, _ = self.sample()
        for key, value in (("version", 1), ("state", {"modified": True})):
            altered = copy.deepcopy(sample)
            altered["decision"][key] = value
            altered["input_digest"] = digest(altered["decision"])
            path = self.root / "altered.jsonl"
            path.write_text(canonical(altered) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_samples(path)

    def test_runtime_abstention_false_allow_and_brier(self):
        first, _, _, _ = self.sample(value=BooleanJudgment(True, .85))
        second, _, _, _ = self.sample(value=BooleanJudgment(None, .5))
        samples = [first, second]
        report = evaluate(samples, self.labels(samples, [False, False]))
        metric = report["metrics"]["tool.intent/boolean/edit_file"]
        self.assertEqual(metric["false_allow"], 1)
        self.assertEqual(metric["abstained"], 1)
        self.assertEqual(metric["coverage"], .5)
        self.assertAlmostEqual(metric["brier"], (.85**2 + .5**2) / 2)
        self.assertTrue(report["replays"][1]["fallback"])

    def test_confidence_and_partial_route_cascade_replay(self):
        inputs = {"tool": "run_command", "call": "git push", "constraints": ["禁止推送", "禁止写入"]}
        backends = {"first": Replies({"constraint_0": BooleanJudgment(True, .9),
                                      "constraint_1": BooleanJudgment(False, .1)}),
                    "second": Replies({"constraint_0": BooleanJudgment(False, .01),
                                       "constraint_1": BooleanJudgment(True, .99)})}
        sample, _, _, _ = self.sample(TOOL_CONSTRAINT, inputs=inputs, confidence=.95,
                                     backends=backends, routes=("first", "second"))
        replay = replay_sample(sample)
        self.assertEqual(replay["judged"], {"broken": [1]})
        self.assertEqual(replay["answers"]["constraint_0"]["backend"], "second")

    def test_admission_harmful_drop_versus_retention(self):
        inputs = {"tool": "run_command", "call": "test", "chunks": ["ERROR", "output"]}
        sample, _, _, _ = self.sample(TOOL_ADMISSION,
            {"chunk_0": TypedJudgment("passing", .99), "chunk_1": TypedJudgment("progress", .85)}, inputs)
        report = evaluate([sample], self.labels([sample], [{"chunk_0": "error", "chunk_1": "result"}]))
        metric = report["metrics"]["tool.admission/choice/run_command"]
        self.assertEqual(metric["harmful_drop"], 1)
        self.assertEqual(metric["abstained"], 1)
        self.assertEqual(metric["harmful_drop_rate"], .5)
        self.assertFalse(report["replays"][0]["judged"][1]["drop"])

    def test_none_choice_and_zero_score_remain_answers(self):
        frame, _, _, _ = self.sample(TASK_FRAME, TypedJudgment("none", .9), {"user_message": "继续"})
        score, _, _, _ = self.sample(TOOL_RISK_SCORE, TypedJudgment(0., .9))
        self.assertEqual(replay_sample(frame)["judged"], "none")
        self.assertEqual(replay_sample(score)["judged"], 0.)

    def test_teacher_predictions_do_not_become_independent_metrics(self):
        sample, _, _, _ = self.sample()
        labels = self.labels([sample], [False], origin="teacher")
        self.assertEqual(evaluate([sample], labels)["metrics"], {})
        labels = self.labels([sample], [False], reviewed=False)
        self.assertEqual(evaluate([sample], labels)["metrics"], {})

    def test_duplicate_groups_connected_and_conflicting_labels_rejected(self):
        first, _, _, _ = self.sample(group="group-a")
        second, _, _, _ = self.sample(group="group-b")
        samples = [first, second]
        rows, manifest = export_intent(samples, self.labels(samples, [False, False]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(manifest["groups"]["group-a"], manifest["groups"]["group-b"])
        self.assertEqual(manifest["group_components"]["group-a"], manifest["group_components"]["group-b"])
        with self.assertRaises(ValueError):
            export_intent(samples, self.labels(samples, [False, True]))

    def test_export_all_four_splits_and_teacher_holdout_excluded(self):
        samples = []
        # Many independent fixture groups exercise every deterministic bucket.
        for index in range(40):
            sample, _, _, _ = self.sample(group=f"repo-{index}", mode="off", inputs={
                "tool": "edit_file", "user_request": f"fix file {index}", "arguments": {"path": f"{index}.py"}})
            samples.append(sample)
        labels = self.labels(samples, [True] * len(samples))
        rows, manifest = export_intent(samples, labels)
        self.assertTrue(manifest["ready_for_training"])
        partitions, versioned = training_partitions(rows)
        self.assertTrue(versioned)
        self.assertEqual(set(partitions), {"train", "validation", "calibration", "test"})
        validate_manifest(rows, manifest)
        modified = copy.deepcopy(rows)
        modified[0]["label"] = False
        with self.assertRaises(ValueError):
            validate_manifest(modified, manifest)
        self.assertEqual(export_intent(list(reversed(samples)), labels), (rows, manifest))
        for label in labels.values():
            label["origin"] = "teacher"
        teacher_rows, teacher_manifest = export_intent(samples, labels)
        self.assertTrue(all(row["split"] == "train" for row in teacher_rows))
        self.assertFalse(teacher_manifest["ready_for_training"])

    def test_training_preflight_rejects_leaks_duplicates_and_missing_splits(self):
        sample, _, _, _ = self.sample()
        rows, _ = export_intent([sample], self.labels([sample], [False]))
        with self.assertRaises(ValueError):
            training_partitions(rows)
        # Construct otherwise valid four-partition rows from this fixture.
        rows = [{**rows[0], "split": split, "group_id": split,
                 "state": {"request": split}} for split in ("train", "validation", "calibration", "test")]
        self.assertTrue(training_partitions(rows)[1])
        for modification in ({"group_id": "train"}, {"state": rows[0]["state"]}, {"origin": "teacher"}):
            bad = copy.deepcopy(rows)
            bad[1].update(modification)
            with self.assertRaises(ValueError):
                training_partitions(bad)

    def test_fresh_inference_shadow_only_and_laya_guard_preserved(self):
        sample, _, _, _ = self.sample(mode="off")
        engine = DecisionEngine("off", Replies(BooleanJudgment(False, .05)), registry=builtin_registry())
        replay = replay_sample(sample, engine)
        self.assertEqual(replay["judged"], False)
        self.assertEqual(replay["mode"], "shadow")
        self.assertTrue(replay["observed_outcome"])
        laya = LayaHttpBooleanJudge("http://127.0.0.1:9999")
        with self.assertRaises(ValueError):
            DecisionEngine("active", laya).policy_for(TOOL_INTENT)
        score, _, _, _ = self.sample(TOOL_RISK_SCORE, TypedJudgment(0., .9))
        with self.assertRaises(ValueError):
            replay_sample(score, DecisionEngine("off", laya, registry=builtin_registry()))

    def test_agent_sampling_keeps_permission_floor(self):
        class Model:
            def __init__(self):
                self.called = False

            def complete(self, messages, tools, **kwargs):
                if self.called:
                    return {"role": "assistant", "content": "done"}
                self.called = True
                return {"role": "assistant", "tool_calls": [{"id": "write", "function": {
                    "name": "write_file", "arguments": json.dumps({"path": "x.txt", "content": "body"})}}]}

        engine = DecisionEngine(sampler=JudgeSampler(self.path, "task"))
        agent = Agent(Model(), WorkspaceTools(self.root), engine)
        events = list(agent.run("write x"))
        self.assertFalse((self.root / "x.txt").exists())
        self.assertEqual(len(load_samples(self.path)), 1)
        self.assertIn("turn_id", load_samples(self.path)[0]["source"])
        failure = next(message for message in agent.messages if message.get("role") == "tool")
        self.assertTrue(failure["tool_is_error"])
        self.assertIn("Writing is disabled", failure["content"])

    @unittest.skipUnless(shutil.which("java") and shutil.which("javac"), "JDK unavailable")
    def test_java_http_sampling_denial_restart_and_session_identity(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/judge":
                    reply = {"probability": .05}
                else:
                    message = ({"role": "assistant", "tool_calls": [{"id": "write", "function": {
                        "name": "write_file", "arguments": json.dumps({"path": "x.txt", "content": "fixture"})}}]}
                        if payload["messages"][-1]["role"] == "user" else {"role": "assistant", "content": "done"})
                    reply = {"choices": [{"message": message}]}
                body = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        repository = Path(__file__).resolve().parents[2]
        classes, workspace = self.root / "classes", self.root / "workspace"
        workspace.mkdir()
        sources = (repository / "java/src/main/java/dev/mupyjava").glob("*.java")
        compiled = subprocess.run(["javac", "-d", str(classes), *map(str, sources)], capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {key: value for key, value in os.environ.items() if not key.startswith("MU_")}
        address = "http://127.0.0.1:" + str(server.server_port)
        env.update(MU_MODEL="fixture", MU_API_BASE=address, MU_API_KEY="fixture",
                   MU_JUDGE_MODE="shadow", MU_JUDGE_LAYA_URL=address,
                   MU_JUDGE_SAMPLES=str(self.path), MU_JUDGE_SAMPLE_GROUP="fixture/repo")
        try:
            for answer in ("deny", "once"):
                result = subprocess.run(["java", "-cp", str(classes), "dev.mupyjava.Main",
                    "--workspace", str(workspace), "--smoke", "write x", "--smoke-approval", answer],
                    cwd=repository, env=env, capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((workspace / "x.txt").exists(), answer == "once")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        samples = load_samples(self.path)
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0]["source"]["session_id"], samples[1]["source"]["session_id"])
        self.assertNotEqual(samples[0]["source"]["turn_id"], samples[1]["source"]["turn_id"])
        self.assertTrue(all(row["group_id"] == "fixture/repo" for row in samples))
        self.assertTrue(all(row["observed"]["answer"] is False for row in samples))
        # A shadow false prediction is not a manual label or a permission denial.
        self.assertTrue(all(row["observed"]["outcome"] is True for row in samples))

    def test_cli_annotations_replay_export_and_no_overwrite(self):
        sample, _, _, _ = self.sample()
        repository = Path(__file__).resolve().parents[2]
        env = {**os.environ, "PYTHONPATH": str(repository / "python")}

        def run(action, output, extra=()):
            return subprocess.run([sys.executable, "training/judge_pipeline.py", action,
                "--samples", str(self.path), "--output", str(output), *extra],
                cwd=repository, env=env, capture_output=True, text=True)

        annotations = self.root / "annotations.jsonl"
        self.assertEqual(run("annotate", annotations).returncode, 0)
        self.labels([sample], [False])
        extra = ("--labels", str(self.root / "labels.jsonl"))
        report = self.root / "report.json"
        result = run("replay", report, extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(report.read_text())["samples"], 1)
        original = report.read_bytes()
        self.assertNotEqual(run("replay", report, extra).returncode, 0)
        self.assertEqual(report.read_bytes(), original)
        exported = self.root / "dataset"
        self.assertEqual(run("export", exported, extra).returncode, 0)
        self.assertFalse(json.loads((exported / "manifest.json").read_text())["ready_for_training"])


if __name__ == "__main__":
    unittest.main()
