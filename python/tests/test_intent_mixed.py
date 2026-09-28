import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from test_intent_experiment import training_module
from mupyjava.judge_samples import digest
from mupyjava.judge_data import read_jsonl, training_partitions, validate_manifest


class IntentMixedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = training_module('build_intent_mixed')
        cls.directory = tempfile.TemporaryDirectory()
        cls.rows, cls.manifest, cls.inventory = cls.module.build(Path(cls.directory.name) / 'mixed')

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_shared_arguments_have_all_three_targets(self):
        signatures = defaultdict(set)
        from mupyjava.tools import WorkspaceTools
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory))
            for _, _, _, state, label in self.module.generated_cases():
                signatures[digest({'tool': state['tool'], 'arguments': state['arguments']})].add(label)
                if state['tool'] in ('bash', 'powershell', 'run_command'):
                    tools.validate_call(state['tool'], state['arguments'])
        self.assertTrue(signatures)
        self.assertTrue(all(labels == {True, False, None} for labels in signatures.values()))

    def test_families_and_imported_source_partitions(self):
        self.assertEqual(self.module.canonical_family('synthetic/edit_file/goal-02'),
                         self.module.canonical_family('natural/edit_file/goal-02'))
        training_partitions(self.rows, True, True)
        validate_manifest(self.rows, self.manifest, True, True)
        dataset_root = Path(self.module.__file__).with_name('datasets')
        originals = {}
        for source in ('intent-v2-synthetic-r2', 'intent-uncertainty-r1'):
            originals[source] = {row['sample_id']: row for row in read_jsonl(dataset_root / source / 'intent-v2.jsonl.gz') if row['split'] == 'train'}
        for row in self.rows:
            tags = row['tags']
            if tags['source_round'] != 'mixed-r1':
                self.assertEqual(row['split'], 'train')
                self.assertIn(tags['source_sample_id'], originals[tags['source_round']])
                self.assertEqual(row['label'], originals[tags['source_round']][tags['source_sample_id']]['label'])
        rehearsal = [row for row in self.rows if row['tags'].get('path_transform') == 'none_v1']
        self.assertEqual(len(rehearsal), 349)
        self.assertEqual(sum(row['origin'] == 'teacher' for row in rehearsal), 37)
        for row in rehearsal:
            self.assertEqual(row['state'], originals['intent-uncertainty-r1'][row['tags']['source_sample_id']]['state'])

    def test_holdouts_have_multiple_unknown_families(self):
        for split in ('validation', 'calibration', 'test'):
            inventory = self.inventory['splits'][split]
            self.assertEqual(inventory['unknown_families'], 8)
            self.assertEqual(inventory['unknown_requests'], 16)
        self.assertEqual({row['split'] for row in self.rows if row['origin'] == 'teacher'}, {'train'})

    def test_injective_renaming_preserves_reference_and_wrong_path(self):
        state = {'user_request': 'Change src/target.py, not other/target.py.', 'tool': 'edit_file',
                 'arguments': {'path': 'other/target.py', 'old_text_bytes': 30}}
        renamed = self.module.rename_paths(state)
        path = renamed['arguments']['path']
        self.assertIn('not ' + path, renamed['user_request'])
        self.assertNotEqual(renamed['user_request'].split(',')[0].split()[-1], path)
        self.assertEqual(renamed['arguments']['old_text_bytes'], 30)

    def test_paired_intervention_is_label_preserving_and_bounded(self):
        module = training_module('intent_interventions')
        selected = [row for row in self.rows if row['split'] == 'test']
        controls = module.path_interventions(selected)
        lookup = {row['sample_id']: row for row in selected}
        for row in controls:
            original = lookup[row['tags']['parent_sample_id']]
            self.assertEqual(row['label'], original['label'])
            self.assertNotEqual(row['state'], original['state'])
            self.assertNotEqual(row['input_digest'], original['input_digest'])
            self.assertEqual(row['tags']['parent_input_digest'], original['input_digest'])
            self.assertEqual(row['state']['tool'], original['state']['tool'])
        first = controls[0]
        parent = first['tags']['parent_sample_id']
        baseline = {'checkpoint_sha256': 'same', 'predictions': [{'sample_id': parent, 'label': first['label'], 'answer': True, 'probability': .9}]}
        altered = {'checkpoint_sha256': 'same', 'predictions': [{'tags': {'parent_sample_id': parent}, 'label': first['label'], 'answer': None, 'probability': .5}]}
        report = module.paired_metrics(baseline, altered)
        self.assertEqual(report['answer_flips'], 1)
        altered['checkpoint_sha256'] = 'other'
        with self.assertRaises(ValueError):
            module.paired_metrics(baseline, altered)

    def test_metric_inventory_and_probability_count_validation(self):
        module = training_module('evaluate_intent')
        selected = [row for row in self.rows if row['split'] == 'test']
        inventory = module.coverage_inventory(selected)
        self.assertEqual(inventory['unknown_families'], 8)
        self.assertEqual(inventory['distinct_unknown_requests'], 16)
        for probabilities in ([], [float('nan')], [1.5]):
            with self.assertRaises(ValueError):
                module.metrics([{'label': True}], probabilities)

    def test_harness_control_labels_are_independent_of_observed_predictions(self):
        module = training_module('intent_interventions')
        sample = {'sample_id': 'captured', 'decision': {'state': {
            'user_request': 'Earlier request', 'tool': 'edit_file',
            'arguments': {'path': 'src/calc.py', 'edit_count': 2, 'old_text_bytes': 40}}},
            'observed': {'answer': False}}
        rows = module.harness_controls([sample])
        self.assertEqual([row['label'] for row in rows], [True, False, None])
        sample['observed']['answer'] = True
        self.assertEqual(rows, module.harness_controls([sample]))
        self.assertTrue(all(row['state']['arguments'] == sample['decision']['state']['arguments'] for row in rows))
        for row in rows:
            row['probability'] = .5 if row['label'] is None else float(row['label'])
        metric = module.harness_control_metrics(rows + rows)
        self.assertEqual((metric['raw_cases'], metric['unique_cases']), (6, 3))
        self.assertEqual(metric['overall']['unknown_cases'], 1)


if __name__ == '__main__':
    unittest.main()
