import copy
import unittest
from test_intent_experiment import training_module


class SemanticPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder=training_module('build_semantic_pilot')

    def test_one_review_disagreement_removes_entire_family_without_relabeling(self):
        cases=self.builder.authored_cases()[:12]
        original=copy.deepcopy(cases)
        reviews={row['sample_id']:{'label':row['label']} for row in cases}
        reviews[cases[0]['sample_id']]['label']=False
        accepted,reasons=self.builder.quarantine(cases,reviews,set(),{})
        self.assertEqual(len(accepted),6)
        self.assertEqual(set(reasons),{cases[0]['family']})
        self.assertEqual(cases,original)

    def test_missing_review_protected_and_duplicate_inputs_quarantine_families(self):
        cases=self.builder.authored_cases()[:18]
        cases[12]['state']=copy.deepcopy(cases[6]['state'])
        reviews={row['sample_id']:{'label':row['label']} for row in cases}
        del reviews[cases[0]['sample_id']]
        protected={self.builder.protected_key(cases[1])}
        accepted,reasons=self.builder.quarantine(cases,reviews,protected,{})
        self.assertFalse(accepted)
        self.assertIn('protected_input',reasons[cases[0]['family']])
        self.assertIn('missing_review',reasons[cases[0]['family']])
        self.assertIn('duplicate_input',reasons[cases[6]['family']])
        self.assertIn('duplicate_input',reasons[cases[12]['family']])

    def test_authored_holdouts_are_synthetic_with_pairs_in_every_partition(self):
        cases=self.builder.authored_cases()
        self.assertEqual(len(cases),144)
        self.assertEqual(len({row['family'] for row in cases}),24)
        rows=[self.builder.convert(row) for row in cases]
        for row in rows:self.assertEqual(row['origin'],'synthetic')
        pairs={}
        for row in rows:
            key=row['tags'].get('contrast_pair')
            if key:pairs.setdefault(key,[]).append(row)
        self.assertEqual(len(pairs),48)
        for values in pairs.values():
            self.assertEqual({row['label'] for row in values},{True,False})
            self.assertEqual(len({row['split'] for row in values}),1)
            self.assertEqual(values[0]['state']['arguments'],values[1]['state']['arguments'])

    def test_pair_order_is_not_decisive_correctness_and_actions_must_match(self):
        module=training_module('semantic_pair_metrics')
        rows=[self.builder.convert(row) for row in self.builder.authored_cases()[:2]]
        rows[0]['probability']=.6;rows[1]['probability']=.4
        result=module.pair_metrics(rows)
        self.assertEqual(result['correct_order'],1)
        self.assertEqual(result['both_correct_decisive'],0)
        rows[1]['state']=copy.deepcopy(rows[1]['state'])
        rows[1]['state']['arguments']['path']='different.py'
        with self.assertRaises(ValueError):module.pair_metrics(rows)

    def test_direct_argv_rejects_shell_builtins_and_compounds_but_accepts_quoted_args(self):
        module=training_module('tool_state_audit')
        def state(command,tool='run_command'):return {'tool':tool,'arguments':{'command':command}}
        for command in ['command -v python3','git status; git push','git status | cat','echo $(pwd)']:
            self.assertIsNotNone(module.command_state_issue(state(command)))
        for command in ['git status --short','python3 -m unittest','bash -c "git status; git diff"','printf ";"']:
            self.assertIsNone(module.command_state_issue(state(command)))
        self.assertIsNone(module.command_state_issue(state('command -v python3; git status','bash')))
        with self.assertRaisesRegex(ValueError,'recollect'):
            module.require_command_states([{'state':state('command -v python3')}])
