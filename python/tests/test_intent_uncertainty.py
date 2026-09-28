import copy
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from mupyjava.judge_data import export_intent, training_partitions, validate_manifest
from test_intent_experiment import training_module
import test_judge_data as fixture_module


class IntentUncertaintyTests(unittest.TestCase):
    def test_unknown_export_is_explicit_and_provenance_gated(self):
        fixture = fixture_module.JudgeDataTests()
        fixture.setUp()
        try:
            samples = [fixture.sample(group=f"unknown-{i}", inputs={"tool": "write_file", "index": i})[0] for i in range(4)]
            labels = fixture.labels(samples, [None, True, False, None], origin="synthetic")
            split_map = dict(zip((s["group_id"] for s in samples), ("train", "validation", "calibration", "test")))
            default, _ = export_intent(samples, labels, allow_synthetic_eval=True, split_by_group=split_map)
            self.assertEqual(len(default), 2)
            rows, manifest = export_intent(samples, labels, allow_synthetic_eval=True, split_by_group=split_map, include_unknown=True)
            self.assertEqual(len(rows), 4)
            with self.assertRaises(ValueError):
                training_partitions(rows, True)
            with self.assertRaises(ValueError):
                validate_manifest(rows, manifest, True)
            training_partitions(rows, True, True)
            validate_manifest(rows, manifest, True, True)
            damaged = copy.deepcopy(manifest)
            damaged.pop("unknown_target")
            with self.assertRaises(ValueError):
                validate_manifest(rows, damaged, True, True)
            rows[0].pop("label")
            with self.assertRaises(ValueError):
                training_partitions(rows, True, True)
        finally:
            fixture.tearDown()

    def test_uniform_target_is_not_negative_and_rejects_numeric_labels(self):
        target = training_module("intent_targets").target_probability
        self.assertEqual((target(False), target(None), target(True)), (0., .5, 1.))
        for invalid in (0, 1, .5, "false", [], {}):
            with self.assertRaises(ValueError):
                target(invalid)

    def test_legacy_unknown_cannot_bypass_manifest(self):
        rows = [{"state": {}, "split": "train", "label": None},
                {"state": {}, "split": "validation", "label": True}]
        with self.assertRaisesRegex(ValueError, "frozen manifest"):
            training_partitions(rows, train_unknown=True)

    def test_rehearsal_has_only_original_training_inputs(self):
        module = training_module("build_intent_uncertainty")
        cases = list(module.cases())
        rehearsal = [case for case in cases if case[3] == "rehearsal"]
        self.assertEqual(len(rehearsal), 349)
        self.assertEqual(sum(case[-1] == "teacher" for case in rehearsal), 37)
        from mupyjava.judge_samples import digest
        training = {digest(row["state"]) for row in module.legacy_rows() if row["split"] == "train"}
        validation = {digest(row["state"]) for row in module.legacy_rows() if row["split"] != "train"}
        actual = {digest(case[4]) for case in rehearsal}
        self.assertTrue(training <= actual)
        self.assertFalse(validation & actual)

    def test_natural_and_unknown_cases_have_real_command_schema(self):
        module = training_module("build_intent_uncertainty")
        from mupyjava.tools import WorkspaceTools
        families = defaultdict(set)
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory))
            for family, language, _, scenario, state, label, _ in module.cases():
                families[family].add(language)
                if scenario == "missing_context":
                    self.assertIsNone(label)
                if state["tool"] in {"bash", "powershell", "run_command"}:
                    tools.validate_call(state["tool"], state["arguments"])
                if scenario in {"implicit_action", "brief_clear_action", "implicit_check"}:
                    self.assertTrue(label)
                    self.assertNotIn(state["tool"], state["user_request"])
        self.assertTrue(all(languages == {"en", "zh"} for family, languages in families.items() if not family.startswith("rehearsal/")))


if __name__ == "__main__":
    unittest.main()
