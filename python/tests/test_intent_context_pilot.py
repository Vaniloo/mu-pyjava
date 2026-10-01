import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mupyjava.judge_data import read_jsonl
from mupyjava.judge import _laya_judgment


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


class ContextPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def row(self, sample_id, split="train", label=False):
        return {"sample_id": sample_id, "input_digest": "source-" + sample_id,
                "group_id": "family-" + sample_id, "split": split, "origin": "synthetic",
                "tags": {"language": "en"}, "label": label,
                "state": {"user_request": "Explain the proposed edit; do not change any file.\n"
                          "Constraint: leave app.py unchanged",
                          "tool": "edit_file", "arguments": {"path": "app.py", "edit_count": 1}}}

    def test_projected_followup_preserves_prior_user_constraint_and_label(self):
        module = training_module("build_intent_context_pilot")
        source = self.root / "source.jsonl"
        source.write_text(json.dumps(self.row("one")) + "\n")
        manifest = module.build(source, self.root / "output")
        rows = read_jsonl(self.root / "output" / "cases.jsonl")
        self.assertEqual([row["tags"]["context_variant"] for row in rows],
                         ["direct", "followup"])
        self.assertTrue(all(row["label"] is False for row in rows))
        self.assertEqual(rows[0]["state"]["latest_user_message"],
                         self.row("one")["state"]["user_request"])
        self.assertEqual(rows[1]["state"]["latest_user_message"], "Continue the current task.")
        self.assertIn("do not change", rows[1]["state"]["task_goal_excerpt"])
        self.assertEqual(rows[1]["state"]["user_constraints"][0]["text"],
                         "Constraint: leave app.py unchanged")
        self.assertEqual(rows[0]["group_id"], rows[1]["group_id"])
        self.assertEqual(rows[0]["split"], rows[1]["split"])
        self.assertEqual(manifest["independent_human_gold"], 0)
        self.assertEqual((self.root / "output" / "cases.jsonl").stat().st_mode & 0o777, 0o600)

    def test_token_audit_binding_rejects_clipped_candidate(self):
        builder = training_module("build_intent_context_pilot")
        evaluator = training_module("evaluate_intent_context")
        source = self.root / "source.jsonl"
        source.write_text(json.dumps(self.row("one")) + "\n")
        builder.build(source, self.root / "output")
        data = self.root / "output" / "cases.jsonl"
        rows = read_jsonl(data)
        audit = {"dataset_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                 "status": {row["sample_id"]: {"fit": True} for row in rows}}
        audit_path = self.root / "audit.json"
        audit_path.write_text(json.dumps(audit))
        self.assertEqual(len(evaluator.validated_rows(data, audit_path, "all")[0]), 2)
        audit["status"][rows[0]["sample_id"]]["fit"] = False
        audit_path.write_text(json.dumps(audit))
        with self.assertRaisesRegex(ValueError, "clipped"):
            evaluator.validated_rows(data, audit_path, "all")

    def test_comparison_recomputes_answers_and_rejects_input_mismatch(self):
        builder = training_module("build_intent_context_pilot")
        comparison = training_module("compare_intent_context")
        metric = training_module("evaluate_intent").metrics
        source = self.root / "source.jsonl"
        source.write_text(json.dumps(self.row("one")) + "\n")
        builder.build(source, self.root / "output")
        data = self.root / "output" / "cases.jsonl"
        rows = read_jsonl(data)
        audit = {"dataset_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                 "status": {row["sample_id"]: {"fit": True} for row in rows}}
        audit_path = self.root / "audit.json"
        audit_path.write_text(json.dumps(audit))
        probabilities = [.1, .9]
        predictions = [{"sample_id": row["sample_id"], "input_digest": row["input_digest"],
                        "label": row["label"], "probability": probability,
                        "answer": _laya_judgment(probability).answer}
                       for row, probability in zip(rows, probabilities)]
        report = {"purpose": "synthetic_context_format_diagnostic",
                  "dataset_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                  "token_audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
                  "split": "all", "checkpoint_sha256": "fixture", "noul_temperature": 1.,
                  "overall": metric(rows, probabilities),
                  "by_variant": {variant: metric([row], [probability]) for variant, row, probability
                                 in zip(("direct", "followup"), rows, probabilities)},
                  "predictions": predictions}
        result = comparison.compare(data, audit_path, "all", {"model": report})
        self.assertEqual(result["models"]["model"]["overall"]["false_allow"], 1)
        report["predictions"][0]["input_digest"] = "tampered"
        with self.assertRaisesRegex(ValueError, "Prediction changed"):
            comparison.compare(data, audit_path, "all", {"model": report})


if __name__ == "__main__":
    unittest.main()
