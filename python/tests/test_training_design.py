import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_intent_experiment import training_module
from mupyjava.decision_points import TOOL_INTENT


class TrainingDesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = training_module('training_design')
        cls.language = training_module('compare_language_judge')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.old = {'sample_id': 'old', 'split': 'test', 'question': TOOL_INTENT.question,
                    'state': {'user_request': 'old inspected request', 'tool': 'bash', 'arguments': {'command': 'true'}}, 'label': True}
        (self.root / 'old.jsonl').write_text(json.dumps(self.old)+'\n')
        self.config = {'schema_version': 1, 'name': 'test', 'weighting': 'family_request',
                       'rehearsal_fraction': .25, 'patience': 1, 'min_delta': .001,
                       'protected_inputs': [{'path': 'old.jsonl', 'selection': 'nontrain'}]}
        self.rows = []
        for split in ('train', 'validation', 'calibration', 'test'):
            for label in (True, False, None):
                self.rows.append({'sample_id': split+str(label), 'split': split, 'label': label,
                    'question': TOOL_INTENT.question, 'group_id': split,
                    'state': {'user_request': split+str(label), 'tool': 'bash', 'arguments': {'command': 'command -v python3'}},
                    'tags': {'semantic_family': split, 'rehearsal': False,
                             **({'contrast_pair': split} if label is not None else {})}})

    def tearDown(self):
        self.directory.cleanup()

    def test_argument_expansion_shares_request_mass_and_families_have_equal_mass(self):
        rows = [copy.deepcopy(self.rows[0]) for _ in range(100)] + [self.rows[1], self.rows[3], self.rows[4]]
        weights = self.design.example_weights(rows, 'family_request')
        self.assertAlmostEqual(sum(weights), len(rows))
        self.assertAlmostEqual(sum(weights[:100]), weights[100])
        self.assertAlmostEqual(sum(weights[:101]), sum(weights[101:]))

    def test_rehearsal_mass_is_capped_even_with_many_repeated_old_rows(self):
        rows = [copy.deepcopy(self.rows[0]) for _ in range(100)] + [copy.deepcopy(self.rows[1])]
        for row in rows[:100]:
            row['tags']['rehearsal'] = True
        weights = self.design.design_weights(rows, self.config)
        self.assertAlmostEqual(sum(weights[:100])/sum(weights), .25)
        with self.assertRaisesRegex(ValueError, 'rehearsal alone'):
            self.design.design_weights(rows[:100], self.config)

    def test_semantic_family_and_contrast_pair_cannot_cross_splits_or_actions(self):
        audit = self.design.audit_design(self.rows, self.config, self.root)
        self.assertEqual(audit['contrast_pairs'], 4)
        changed = copy.deepcopy(self.rows)
        changed[3]['tags']['semantic_family'] = 'train'
        with self.assertRaisesRegex(ValueError, 'crosses'):
            self.design.audit_design(changed, self.config, self.root)
        changed = copy.deepcopy(self.rows)
        changed[1]['state']['arguments']['command'] = 'git push'
        with self.assertRaisesRegex(ValueError, 'Contrast pairs'):
            self.design.audit_design(changed, self.config, self.root)

    def test_old_inspected_cases_are_rejected_in_every_new_partition(self):
        for split in ('train', 'validation', 'calibration', 'test'):
            changed = copy.deepcopy(self.rows)
            changed.append({**self.old, 'split': split})
            with self.assertRaisesRegex(ValueError, 'protected'):
                self.design.audit_design(changed, self.config, self.root)

    def test_prior_training_cannot_be_mislabeled_fresh_or_replayed_with_changed_target(self):
        prior = {**self.rows[0], 'split': 'train'}
        (self.root / 'old.jsonl').write_text(json.dumps(prior)+'\n')
        with self.assertRaisesRegex(ValueError, 'described as fresh'):
            self.design.audit_design(self.rows, self.config, self.root)
        changed = copy.deepcopy(self.rows)
        changed[0]['tags']['rehearsal'] = True
        self.design.audit_design(changed, self.config, self.root)
        changed[0]['label'] = False
        with self.assertRaisesRegex(ValueError, 'Rehearsal must preserve'):
            self.design.audit_design(changed, self.config, self.root)

    def test_early_stop_can_keep_epoch_zero_and_reject_nonfinite_losses(self):
        stop = self.design.ValidationStop(.2)
        self.assertEqual(stop.observe(1, .3), (False, True))
        self.assertEqual(stop.best_epoch, 0)
        stop = self.design.ValidationStop(.3)
        self.assertEqual(stop.observe(1, .2), (True, False))
        self.assertEqual(stop.observe(2, .21), (False, True))
        self.assertEqual(stop.best_epoch, 1)
        with self.assertRaises(ValueError):
            stop.observe(3, float('nan'))

    def test_capability_prompt_shows_exact_state_without_labels_or_review_hints(self):
        state = self.rows[0]['state']
        payload = self.language.payload(state, 'fixture-model')
        self.assertEqual(json.loads(payload['messages'][1]['content']), state)
        system = payload['messages'][0]['content']
        self.assertIn(TOOL_INTENT.question, system)
        self.assertNotIn('revocation', system)
        self.assertNotIn('rationale', system)
        self.assertNotIn('contrast_pair', str(payload))

    def test_provider_failures_are_not_counted_as_successful_unknown_abstentions(self):
        report = self.language.summarize([{'label': None, 'answer': None, 'failure': 'TimeoutError'},
                                         {'label': None, 'answer': None, 'failure': None},
                                         {'label': True, 'answer': None, 'failure': 'ValueError'}])
        self.assertEqual((report['unknown_abstention'], report['known_abstention'], report['failures']), (1, 0, 2))
        body = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"answer": 1}'}}]}
        with self.assertRaises(ValueError):
            self.language.parse_answer(body)
        body['choices'][0]['message']['content'] = '{"answer":true}'
        body['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(ValueError):
            self.language.parse_answer(body)


if __name__ == '__main__':
    unittest.main()
