import copy
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from test_intent_experiment import training_module
from mupyjava.judge_samples import digest
from mupyjava.judge_data import read_jsonl, training_partitions


class IntentAblationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = training_module('intent_ablation')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.module.__file__).parent
        cls.output = Path(cls.temporary.name) / 'single'
        cls.rows, cls.manifest = cls.module.build(cls.root, cls.output)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_shared_arguments_support_all_targets_and_controls_cannot_train(self):
        signatures = defaultdict(set)
        for row in self.rows:
            signatures[digest({'tool': row['state']['tool'], 'arguments': row['state']['arguments']})].add(row['label'])
        self.assertTrue(all(labels == {True, False, None} for labels in signatures.values()))
        self.assertEqual(len(set(Counter(row['label'] for row in self.rows).values())), 1)
        self.assertFalse(self.manifest['ready_for_training'])
        with self.assertRaises(ValueError):
            training_partitions(self.rows, True, True)

    def test_frozen_artifact_is_reproducible_and_deduplicated(self):
        other = Path(self.temporary.name) / 'repeat'
        rows, manifest = self.module.build(self.root, other)
        self.assertEqual(rows, read_jsonl(self.output / 'controls.jsonl.gz'))
        self.assertEqual(manifest, self.manifest)
        self.assertEqual((other / 'controls.jsonl.gz').read_bytes(), (self.output / 'controls.jsonl.gz').read_bytes())
        self.assertEqual(len(rows), len({digest(row['state']) for row in rows}))
        with self.assertRaises(FileExistsError):
            self.module.build(self.root, self.output)

    def test_pair_validation_rejects_additional_changes_and_changed_labels(self):
        pair = next(pair for pair in self.manifest['pairs'] if pair['axis'] == 'old_text_bytes')
        lookup = {row['sample_id']: row for row in self.rows}
        rows = [copy.deepcopy(lookup[pair['parent']]), copy.deepcopy(lookup[pair['child']])]
        rows[1]['state']['arguments']['new_text_bytes'] += 1
        with self.assertRaises(ValueError):
            self.module.validate_controls(rows, [pair])
        rows[1] = copy.deepcopy(lookup[pair['child']])
        rows[1]['label'] = not rows[0]['label']
        with self.assertRaises(ValueError):
            self.module.validate_controls(rows, [pair])

    def test_joint_extension_is_separately_identified(self):
        rows, manifest = self.module.build(self.root, Path(self.temporary.name) / 'joint', True)
        self.assertEqual(set(manifest['axis_pairs']), {'joint_byte_counts'})
        self.assertEqual(manifest['experiment'], 'intent-ablation-joint-r1')
        self.assertEqual(len(manifest['seeds']), 2)
        self.assertTrue(all(row['state']['tool'] == 'edit_file' for row in rows))
        self.assertIn('designed after inspecting', manifest['limitations'][-1])

    def fake_report(self):
        predictions = [{**copy.deepcopy(row), 'probability': .5 if row['label'] is None else float(row['label']),
                        'answer': row['label']} for row in self.rows]
        return {'dataset_file_sha256': self.manifest['dataset_sha256'], 'checkpoint_sha256': 'fixture',
                'predictions': predictions, 'overall': self.module.metrics(predictions, [r['probability'] for r in predictions])}

    def test_summary_rejects_mismatched_identity_state_and_answers(self):
        report = self.fake_report()
        for mutation in ('hash', 'state', 'answer', 'missing', 'aggregate'):
            broken = copy.deepcopy(report)
            if mutation == 'hash':
                broken['dataset_file_sha256'] = 'other'
            elif mutation == 'state':
                broken['predictions'][0]['state']['arguments']['path'] = 'other'
            elif mutation == 'answer':
                broken['predictions'][0]['answer'] = None
            elif mutation == 'missing':
                broken['predictions'].pop()
            else:
                broken['overall']['false_allow'] += 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.module.analyze(self.rows, self.manifest, {'fixture': broken})

    def test_summary_counts_one_wrong_allow_and_preserves_pair_denominators(self):
        report = self.fake_report()
        pair = next(pair for pair in self.manifest['pairs'] if pair['axis'] == 'wording'
                    and next(r for r in self.rows if r['sample_id'] == pair['child'])['label'] is False)
        lookup = {row['sample_id']: row for row in report['predictions']}
        lookup[pair['child']]['answer'], lookup[pair['child']]['probability'] = True, .9
        report['overall'] = self.module.metrics(report['predictions'], [r['probability'] for r in report['predictions']])
        summary = self.module.analyze(self.rows, self.manifest, {'fixture': report})['models']['fixture']
        self.assertEqual(summary['overall']['false_allow'], 1)
        self.assertEqual(summary['by_axis']['wording']['answer_flips'], 1)
        self.assertEqual(summary['by_axis']['wording']['pairs'], self.manifest['axis_pairs']['wording'])


if __name__ == '__main__':
    unittest.main()
