import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_intent_experiment import training_module
from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge import DecisionEngine
from mupyjava.judge_data import label_templates, load_samples, read_jsonl, training_partitions
from mupyjava.judge_samples import JudgeSampler, digest
from mupyjava.tools import WorkspaceTools


class IntentReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = training_module('intent_review')
        cls.boundary = training_module('build_intent_boundary')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.raw = self.root / 'raw.jsonl'
        sampler = JudgeSampler(self.raw, 'repo/task-family')
        sampler.context = {'secret_source': 'PREDICTION_SENTINEL'}
        engine = DecisionEngine(sampler=sampler)
        engine.decide(TOOL_INTENT, {'user_request': 'Explain a fix in this conversation only.',
                                   'tool': 'write_file', 'arguments': {'path': 'app.py', 'content_bytes': 48}})
        self.samples = load_samples(self.raw)
        self.packet = self.root / 'packet'
        self.labels = self.root / 'labels.jsonl'
        self.exclusion = self.root / 'excluded.jsonl'
        self.write(self.exclusion, [{'state': {'user_request': 'An unrelated prior input', 'tool': 'bash', 'arguments': {'command': 'true'}}}])
        self.groups = {'repo/task-family': {'kind': 'real_project', 'description': 'Unit-test fixture attestation', 'attested_by': 'fixture-human'}}

    def tearDown(self):
        self.directory.cleanup()

    def write(self, path, rows):
        path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))

    def reviewed_labels(self, origin='manual', answer=False):
        labels = label_templates(self.samples)
        for label in labels:
            label.update(reviewed=True, origin=origin, reviewer='fixture-reviewer', rationale='The request forbids disk writes.')
            label['answers'][TOOL_INTENT.id] = answer
        self.write(self.labels, labels)
        return labels

    def prepare(self, groups=None):
        return self.review.prepare([self.raw], self.packet, self.groups if groups is None else groups)

    def audit(self, exclusions=True):
        return self.review.audit([self.raw], self.packet, self.labels, [self.exclusion] if exclusions else [])

    def test_packet_omits_predictions_and_metadata_without_changing_state(self):
        self.prepare()
        packet = read_jsonl(self.packet / 'packet.jsonl')
        self.assertEqual(packet[0]['state'], self.samples[0]['decision']['state'])
        self.assertEqual(packet[0]['input_digest'], self.samples[0]['input_digest'])
        self.assertNotIn('PREDICTION_SENTINEL', (self.packet / 'packet.jsonl').read_text())
        self.assertTrue(set(packet[0]).isdisjoint({'observed', 'policy', 'source', 'answer', 'probability'}))
        self.assertFalse(read_jsonl(self.packet / 'labels.jsonl')[0]['reviewed'])
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_unreviewed_teacher_and_constructed_data_do_not_count_as_real_gold(self):
        self.prepare()
        self.labels.write_bytes((self.packet / 'labels.jsonl').read_bytes())
        self.assertEqual(self.audit()['excluded'], {'unreviewed': 1})
        self.reviewed_labels(origin='teacher')
        self.assertEqual(self.audit()['excluded'], {'nonmanual_label': 1})
        self.reviewed_labels()
        self.assertEqual(self.audit()['reviewed_real_project_unique'], 1)
        second = self.root / 'constructed'
        self.review.prepare([self.raw], second, {'repo/task-family': {'kind': 'constructed_harness', 'description': 'Constructed fixture', 'attested_by': 'fixture'}})
        result = self.review.audit([self.raw], second, self.labels, [self.exclusion])
        self.assertEqual(result['reviewed_real_project_unique'], 0)
        self.assertEqual(result['excluded'], {'not_attested_real_project': 1})
        self.assertFalse(result['ready_for_retraining'])

    def test_exact_overlap_and_missing_exclusions_reject_independent_intake(self):
        self.prepare()
        self.reviewed_labels(answer=None)
        result = self.audit()
        self.assertEqual((result['known_cases'], result['unknown_cases']), (0, 1))
        self.assertEqual(self.audit(False)['excluded'], {'missing_prior_input_exclusions': 1})
        self.write(self.exclusion, [{'state': self.samples[0]['decision']['state']}])
        self.assertEqual(self.audit()['excluded'], {'previously_seen_exact_input': 1})

    def test_known_synthetic_cannot_be_reclassified_as_real(self):
        row = copy.deepcopy(self.samples[0])
        row['source']['origin'] = 'synthetic_harness_probe'
        self.write(self.raw, [row])
        with self.assertRaisesRegex(ValueError, 'constructed'):
            self.prepare()
        self.assertFalse(self.packet.exists())

    def test_frozen_packet_and_label_binding_tampering_is_rejected(self):
        self.prepare()
        labels = self.reviewed_labels()
        labels[0]['input_digest'] = 'wrong'
        self.write(self.labels, labels)
        with self.assertRaisesRegex(ValueError, 'different input'):
            self.audit()
        self.reviewed_labels()
        rows = read_jsonl(self.packet / 'packet.jsonl')
        rows[0]['state']['user_request'] = 'Change everything'
        self.write(self.packet / 'packet.jsonl', rows)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.audit()

    def test_duplicate_inputs_cannot_have_conflicting_reviewed_labels(self):
        second = copy.deepcopy(self.samples[0])
        second['sample_id'] = 'another-case'
        self.samples.append(second)
        self.write(self.raw, self.samples)
        self.prepare()
        labels = self.reviewed_labels()
        self.assertEqual(self.audit()['reviewed_real_project_unique'], 1)
        labels[1]['answers'][TOOL_INTENT.id] = True
        self.write(self.labels, labels)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.audit()

    def test_boundary_pairs_use_real_schemas_and_cannot_enter_training(self):
        rows, manifest = self.boundary.build(self.root / 'boundary')
        self.assertEqual((len(rows), manifest['families']), (72, 12))
        roles = {}
        tools = WorkspaceTools(self.root)
        for row in rows:
            tools.validate_call(row['state']['tool'], row['state']['arguments'])
            key = digest({'family': row['group_id'], 'language': row['tags']['language'], 'arguments': row['state']['arguments']})
            roles.setdefault(key, set()).add(row['label'])
        self.assertTrue(all(values == {True, False, None} for values in roles.values()))
        with self.assertRaises(ValueError):
            training_partitions(rows, True, True)
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            self.boundary.build(self.root / 'overlap', [self.root / 'boundary/cases.jsonl'])


if __name__ == '__main__':
    unittest.main()
