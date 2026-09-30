import copy
import unittest
from test_intent_experiment import training_module
from mupyjava.judge import _laya_judgment


class RestrictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder=training_module('build_restriction_probe');cls.metrics=training_module('restriction_metrics')

    def predictions(self):
        rows=[]
        for role,p,label in [('plain',.9,True),('restricted',.1,True),('decline',.1,False),('unknown',.5,None)]:
            rows.append({'label':label,'probability':p,'answer':_laya_judgment(p).answer,
                'state':{'tool':'run_command','arguments':{'command':'javac -version'},'user_request':'Query compiler.'+(' Do not install.' if role=='restricted' else '')},
                'tags':{'restriction_pair':'compiler/en','role':role}})
        return rows

    def test_added_restriction_can_separately_cause_abstention_or_wrong_decline(self):
        rows=self.predictions();r=self.metrics.restriction_metrics(rows)
        self.assertEqual((r['lost_allow'],r['new_wrong_decline']),(1,1))
        rows[1].update(probability=.5,answer=None);r=self.metrics.restriction_metrics(rows)
        self.assertEqual((r['lost_allow'],r['new_wrong_decline']),(1,0))

    def test_mismatched_actions_missing_conditions_and_unrelated_request_edits_fail(self):
        rows=self.predictions()
        with self.assertRaises(ValueError):self.metrics.restriction_metrics(rows[:-1])
        changed=copy.deepcopy(rows);changed[1]['state']['arguments']['command']='git status'
        with self.assertRaises(ValueError):self.metrics.restriction_metrics(changed)
        changed=copy.deepcopy(rows);changed[1]['state']['user_request']='Unrelated task.'
        with self.assertRaises(ValueError):self.metrics.restriction_metrics(changed)

    def test_plan_has_bilingual_fixed_action_conditions_and_real_transport_contracts(self):
        audit=training_module('tool_state_audit');plan=self.builder.plan()
        self.assertEqual(len(plan['tasks']),20)
        for t in plan['tasks']:
            self.assertEqual(set(t['requests']),{'plain','restricted','decline','unknown'})
            self.assertTrue(t['requests']['restricted'].startswith(t['requests']['plain']+' '))
            self.assertIsNone(audit.command_state_issue(t))

    def test_future_designs_exclude_this_probe_and_keep_old_designs_reproducible(self):
        from pathlib import Path
        from mupyjava.judge_data import read_jsonl
        module=training_module('training_design');root=Path(__file__).resolve().parents[2]
        rows=read_jsonl(root/'training/datasets/restriction-probe-r1/cases.jsonl')
        for filename in ['intent-semantic-r4.json','intent-semantic-r4-uniform.json']:
            design=module.load_design(root/'training/designs'/filename)
            with self.assertRaisesRegex(ValueError,'protected'):
                module.audit_design(rows,design,root)
        old=module.load_design(root/'training/designs/intent-semantic-r3.json')
        self.assertFalse(any(r['path'].endswith('restriction-probe-r1/cases.jsonl') for r in old['protected_inputs']))
