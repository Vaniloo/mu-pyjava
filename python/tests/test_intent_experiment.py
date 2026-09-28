import importlib.util
import gzip
import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from mupyjava.judge_data import export_intent, read_jsonl, training_partitions, validate_manifest
from mupyjava.tools import WorkspaceTools
import test_judge_data as fixture_module


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


class SyntheticProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.JudgeDataTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_synthetic_holdouts_require_explicit_opt_in(self):
        samples = []
        for index in range(4):
            sample, _, _, _ = self.fixture.sample(group=f"g{index}", inputs={"tool": "write_file", "index": index})
            samples.append(sample)
        labels = self.fixture.labels(samples, [True, False, True, False], origin="synthetic")
        splits = dict(zip((sample["group_id"] for sample in samples), ("train", "validation", "calibration", "test")))
        rows, manifest = export_intent(samples, labels, allow_synthetic_eval=True, split_by_group=splits)
        self.assertEqual(manifest["evaluation_basis"], "synthetic_experiment")
        with self.assertRaises(ValueError):
            training_partitions(rows)
        with self.assertRaises(ValueError):
            validate_manifest(rows, manifest)
        self.assertTrue(training_partitions(rows, True)[1])
        validate_manifest(rows, manifest, True)
        for label in labels.values():
            label["origin"] = "teacher"
        rows, _ = export_intent(samples, labels, allow_synthetic_eval=True, split_by_group=splits)
        self.assertTrue(all(row["split"] == "train" for row in rows))

    def test_explicit_splits_cannot_separate_duplicate_groups(self):
        one, _, _, _ = self.fixture.sample(group="a")
        two, _, _, _ = self.fixture.sample(group="b")
        samples = [one, two]
        labels = self.fixture.labels(samples, [True, True])
        with self.assertRaises(ValueError):
            export_intent(samples, labels, split_by_group={"a": "train", "b": "test"})
        with self.assertRaises(ValueError):
            export_intent(samples, labels, split_by_group={"a": "train"})

    def test_compressed_frozen_dataset_is_readable_without_changing_rows(self):
        path = self.fixture.root / "cases.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as file:
            file.write('{"label":true,"split":"train"}\n')
        self.assertEqual(read_jsonl(path), [{"label": True, "split": "train"}])

    def test_serving_threshold_metrics_distinguish_unknown_and_errors(self):
        metric = training_module("evaluate_intent").metrics
        rows = [{"label": value} for value in (False, False, True, True, None)]
        result = metric(rows, [.85, .5, .8, .2, .95])
        self.assertEqual(result["false_allow"], 1)
        self.assertEqual(result["false_decline"], 1)
        self.assertEqual(result["accepted"], 3)
        self.assertEqual(result["unknown_non_abstention"], 1)
        self.assertEqual(result["false_allow_rate"], .5)
        self.assertEqual(metric([{"label": None}], [.5])["coverage"], None)

    def test_controlled_cases_use_real_metadata_shapes_and_shared_bilingual_groups(self):
        cases = list(training_module("build_intent_v2").cases())
        self.assertEqual(len(cases), 4240)
        counts = Counter(state["tool"] for _, _, _, _, state, _ in cases)
        self.assertEqual(set(counts), {"write_file", "edit_file", "run_command", "bash", "powershell",
                                       "rename_path", "format_file", "apply_patch"})
        languages = defaultdict(set)
        tools = WorkspaceTools(self.fixture.root)
        for family, language, _, scenario, state, label in cases:
            self.assertIs(type(label), bool)
            self.assertEqual(label, scenario == "requested")
            languages[family].add(language)
            arguments = state["arguments"]
            if state["tool"] == "edit_file":
                self.assertEqual(set(arguments), {"path", "edit_count", "allow_fuzzy", "old_text_bytes", "new_text_bytes"})
            if state["tool"] == "write_file":
                self.assertEqual(set(arguments), {"path", "content_bytes"})
            if state["tool"] in {"run_command", "bash", "powershell"}:
                tools.validate_call(state["tool"], arguments)
                self.assertEqual(set(arguments), {"command", "timeout"})
        self.assertEqual(len(languages), 56)
        self.assertTrue(all(value == {"en", "zh"} for value in languages.values()))


if __name__ == "__main__":
    unittest.main()
