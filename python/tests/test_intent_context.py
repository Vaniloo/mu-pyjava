import copy
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from mupyjava.agent import Agent
from mupyjava.intent_context import (CONTEXT_POINT, capture_context, context_state,
                                    serialized_state_digest)
from mupyjava.judge import DecisionEngine
from mupyjava.judge_data import load_samples, read_jsonl
from mupyjava.judge_samples import JudgeSampler
from mupyjava.task_frame import TaskFrame
from mupyjava.tools import WorkspaceTools


def training_module(name):
    directory = Path(__file__).resolve().parents[2] / "training"
    specification = importlib.util.spec_from_file_location(name, directory / (name + ".py"))
    module = importlib.util.module_from_spec(specification)
    sys.path.insert(0, str(directory))
    try:
        specification.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class OneWrite:
    def __init__(self):
        self.called = False

    def complete(self, messages, tools, **kwargs):
        if self.called:
            return {"role": "assistant", "content": "done"}
        self.called = True
        return {"role": "assistant", "tool_calls": [{"id": "write", "function": {
            "name": "write_file", "arguments": json.dumps({"path": "draft.txt", "content": "private body"})}}]}


class IntentContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_runtime_capture_and_blind_packet_keep_v2_unchanged(self):
        raw = self.root / "raw.jsonl"
        sampler = JudgeSampler(raw, "project/task-1")
        agent = Agent(OneWrite(), WorkspaceTools(self.root, allow_write=True),
                      DecisionEngine(sampler=sampler))
        prompt = ("Please update draft.txt.\n" + "background detail " * 80
                  + "\nConstraint: leave protected.txt alone")
        list(agent.run(prompt, approval=lambda *_: False))
        self.assertFalse((self.root / "draft.txt").exists())
        samples = load_samples(raw)
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample["decision"]["state"], {
            "user_request": prompt[:1000], "tool": "write_file",
            "arguments": {"path": "draft.txt", "content_bytes": 12}})
        self.assertEqual(sample["source"]["intent_context"]["latest_user_message"], prompt)
        self.assertEqual(sample["source"]["intent_context"]["task_frame"]["constraints"][0]["text"],
                         "Constraint: leave protected.txt alone")
        self.assertNotIn("private body", raw.read_text())
        self.assertEqual(raw.stat().st_mode & 0o777, 0o600)

        packet_dir = self.root / "review"
        manifest = training_module("prepare_intent_context").prepare([raw], packet_dir)
        packet = read_jsonl(packet_dir / "packet.jsonl")
        labels = read_jsonl(packet_dir / "labels.jsonl")
        self.assertEqual(len(packet), 1)
        row = packet[0]
        self.assertEqual(row["point"], CONTEXT_POINT)
        self.assertEqual(row["state"]["latest_user_message"], prompt)
        self.assertEqual(row["state"]["user_constraints"][0]["text"],
                         "Constraint: leave protected.txt alone")
        self.assertEqual(row["state"]["arguments"], {"path": "draft.txt", "content_bytes": 12})
        self.assertEqual(row["serialized_state_sha256"], serialized_state_digest(row["state"]))
        self.assertEqual(labels[0]["input_digest"], row["input_digest"])
        self.assertLess(list(row["state"]).index("tool"), list(row["state"]).index("latest_user_message"))
        self.assertNotIn("observed", row)
        self.assertNotIn("prediction", row)
        self.assertIsNone(labels[0]["answer"])
        self.assertFalse(labels[0]["reviewed"])
        self.assertFalse(manifest["training_allowed"])
        self.assertFalse(manifest["inference_allowed_without_token_fit"])
        self.assertEqual(manifest["groups"]["project/task-1"]["kind"], "unattested")
        self.assertEqual(packet_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual((packet_dir / "packet.jsonl").stat().st_mode & 0o777, 0o600)

    def test_context_binding_rejects_tampering_and_accepts_new_task_reset(self):
        prompt = "Update draft.txt\nConstraint: don't touch protected.txt"
        frame = TaskFrame().advance("Old goal").advance(prompt, "new_task")
        evidence = capture_context(prompt, frame)
        v2_state = {"user_request": prompt[:1000], "tool": "write_file",
                    "arguments": {"path": "draft.txt", "content_bytes": 1}}
        self.assertEqual(context_state(v2_state, evidence)["task_goal_excerpt"], prompt[:600])
        broken = copy.deepcopy(evidence)
        broken["latest_user_message"] += " extra"
        with self.assertRaises(ValueError):
            context_state(v2_state, broken)
        broken = copy.deepcopy(evidence)
        broken["task_frame"]["constraints"][0]["source_turn"] = 999
        with self.assertRaises(ValueError):
            context_state(v2_state, broken)
        with self.assertRaises(ValueError):
            context_state({**v2_state, "user_request": "other"}, evidence)

    def test_token_audit_uses_candidate_question_and_checks_serialization(self):
        class FakeTokenizer:
            mask_token = "[MASK]"

            def __call__(self, value, add_special_tokens=False):
                return {"input_ids": list(value.encode("utf-8"))}

        transformers = types.ModuleType("transformers")
        transformers.AutoTokenizer = type("AutoTokenizer", (), {
            "from_pretrained": staticmethod(lambda _: FakeTokenizer())})
        laya = types.ModuleType("laya")
        common = types.ModuleType("laya.common")
        common.serialize_state = lambda state: json.dumps(state, ensure_ascii=False)
        common.build_sequence = lambda tokenizer, state, question, max_len, head_max_len, state_ids: (
            [0] * (30 + len(question["ins"])), None)
        checkpoint = self.root / "checkpoint"
        checkpoint.mkdir()
        (checkpoint / "rl_agent_config.json").write_text('{"max_len": 256, "head_max_len": 256}')
        state = {"tool": "write_file", "arguments": {"path": "draft.txt"}}
        row = {"sample_id": "one", "question": "Check the candidate input?", "state": state,
               "serialized_state_sha256": serialized_state_digest(state)}
        data = self.root / "packet.jsonl"
        data.write_text(json.dumps(row) + "\n")
        with patch.dict(sys.modules, {"transformers": transformers, "laya": laya,
                                      "laya.common": common}):
            audit = training_module("audit_intent_token_fit").audit
            result = audit(checkpoint, data)
            self.assertEqual(result["state_token_room"], 256 - 30 - len(row["question"]))
            self.assertTrue(result["status"]["one"]["fit"])
            row["serialized_state_sha256"] = "0" * 64
            data.write_text(json.dumps(row) + "\n")
            with self.assertRaisesRegex(ValueError, "differs from Laya"):
                audit(checkpoint, data)


if __name__ == "__main__":
    unittest.main()
