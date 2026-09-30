import copy
import unittest

from test_intent_experiment import training_module


class ScopeTripletTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder=training_module('build_scope_triplets')

    def cases(self):
        proposal=self.builder.plan()
        cases=[]
        for task in proposal['tasks']:
            for role,request in task['requests'].items():
                cases.append({'family':task['family'],'split':task['split'],'language':task['language'],
                    'role':role,'label':None if role=='unknown' else role!='forbidden',
                    'state':{'tool':task['tool'],'arguments':task['arguments'],'user_request':request}})
        return proposal,cases

    def test_plan_balances_tools_languages_and_restriction_positions(self):
        from collections import Counter
        proposal,cases=self.cases()
        self.builder.audit_triplets(cases,proposal)
        self.assertEqual(len(proposal['tasks']),40)
        self.assertEqual(Counter(t['split'] for t in proposal['tasks']),
                         {'train':20,'validation':5,'calibration':5,'test':10})
        for split in ('train','validation','calibration','test'):
            self.assertEqual({t['tool'] for t in proposal['tasks'] if t['split']==split},set(self.builder.TASKS))
            self.assertEqual({t['language'] for t in proposal['tasks'] if t['split']==split},{'en','zh'})
            self.assertEqual({t['restriction_position'] for t in proposal['tasks'] if t['split']==split},{'before','after'})

    def test_action_or_request_drift_rejected(self):
        proposal,cases=self.cases()
        changed=copy.deepcopy(cases)
        changed[1]['state']['arguments']={'command':'another action'}
        with self.assertRaisesRegex(ValueError,'Action changed'):
            self.builder.audit_triplets(changed,proposal)
        changed=copy.deepcopy(cases)
        changed[1]['state']['user_request']='Different request entirely.'
        with self.assertRaisesRegex(ValueError,'Restricted request changed'):
            self.builder.audit_triplets(changed,proposal)

    def test_frozen_capture_matches_plan_and_tool_contracts(self):
        from pathlib import Path
        source=Path(__file__).resolve().parents[2]/'training/datasets/scope-triplet-r4/capture'
        proposal,cases,raw,calls=self.builder.verify_capture(source)
        self.assertEqual((len(proposal['tasks']),len(cases),len(raw),len(calls)),(40,160,160,160))
        self.assertTrue(all(not call['executed'] for call in calls.values()))
