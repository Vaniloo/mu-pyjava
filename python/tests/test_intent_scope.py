import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from test_intent_experiment import training_module
from mupyjava.judge_data import read_jsonl, training_partitions, validate_manifest
from mupyjava.judge_samples import digest
from mupyjava.tools import WorkspaceTools


class IntentScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = training_module('build_intent_scope')
        cls.directory = tempfile.TemporaryDirectory()
        cls.rows, cls.manifest, cls.inventory = cls.module.build(Path(cls.directory.name) / 'scope')

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_fresh_signature_roles_and_command_schema(self):
        roles = defaultdict(set)
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory))
            for family, split, language, state, label in self.module.generated_cases():
                roles[digest({'tool': state['tool'], 'arguments': state['arguments']})].add(label)
                if state['tool'] in ('run_command', 'bash', 'powershell'):
                    tools.validate_call(state['tool'], state['arguments'])
        self.assertTrue(all(labels == {True, False, None} for labels in roles.values()))

    def test_file_and_command_template_families_do_not_cross_partitions(self):
        for bank in (self.module.FILE_STYLES, self.module.COMMAND_STYLES):
            owners = {}
            for split, styles in bank.items():
                for style in styles:
                    for request in style:
                        self.assertNotIn(request, owners)
                        owners[request] = split
        training_partitions(self.rows, True, True)
        validate_manifest(self.rows, self.manifest, True, True)
        self.assertEqual({row['split'] for row in self.rows if row['origin'] == 'teacher'}, {'train'})

    def test_all_prior_train_states_and_provenance_are_preserved(self):
        source = Path(self.module.__file__).with_name('datasets') / 'intent-mixed-r1/intent-v2.jsonl.gz'
        originals = {row['sample_id']: row for row in read_jsonl(source) if row['split'] == 'train'}
        imported = [row for row in self.rows if row['tags']['source_round'] == 'intent-mixed-r1']
        self.assertEqual(len(imported), len(originals))
        for row in imported:
            prior = originals[row['tags']['source_sample_id']]
            self.assertEqual(row['split'], 'train')
            self.assertEqual((row['state'], row['label'], row['origin']), (prior['state'], prior['label'], prior['origin']))

    def test_exact_old_regressions_and_new_challenge_cannot_leak(self):
        blocked = self.module.blocked_inputs(Path(self.module.__file__).with_name('datasets'))
        self.assertTrue(all(digest({'state': row['state'], 'question': row['question']}) not in blocked for row in self.rows))
        challenge = read_jsonl(Path(self.module.__file__).with_name('scope_challenge.jsonl'))
        self.assertEqual(len(challenge), 24)
        self.assertTrue(all(row['origin'] == 'synthetic' and row['split'] == 'challenge' for row in challenge))
        with self.assertRaises(ValueError):
            training_partitions(challenge, True, True)

    def test_new_holdout_labels_balanced_and_artifact_validated(self):
        for split in ('validation', 'calibration', 'test'):
            counts = self.inventory['splits'][split]['labels']
            self.assertEqual(len(set(counts.values())), 1)
            self.assertGreaterEqual(self.inventory['splits'][split]['unknown_requests'], 20)
        manifest = json.loads(json.dumps(self.manifest))
        manifest['dataset_digest'] = 'changed'
        with self.assertRaises(ValueError):
            validate_manifest(self.rows, manifest, True, True)


if __name__ == '__main__':
    unittest.main()
