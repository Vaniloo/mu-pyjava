import copy
import unittest

from test_intent_experiment import training_module
from mupyjava.judge import _laya_judgment


class ScopeTripletMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.metrics=training_module('scope_triplet_metrics')

    def rows(self):
        result=[]
        for role,request,label,p in [
            ('plain','Check the status.',True,.91),
            ('restricted','Do not stage files. Check the status.',True,.05),
            ('forbidden','Explain the status check; do not run it.',False,.01),
            ('unknown','Continue the earlier status choice.',None,.5)]:
            result.append({'tags':{'scope_triplet':'status','role':role,'language':'en','restriction_position':'before'},
              'state':{'tool':'run_command','arguments':{'command':'git status --short'},'user_request':request},
              'label':label,'probability':p,'answer':_laya_judgment(p).answer})
        return result

    def test_counts_restriction_flip_separately_from_explicit_denial(self):
        summary=self.metrics.triplet_metrics(self.rows())['overall']
        self.assertEqual((summary['families'],summary['lost_allow'],summary['new_wrong_decline'],summary['forbidden_correct']),
                         (1,1,1,1))

    def test_rejects_changed_action_or_nonrestriction_rewrite(self):
        rows=self.rows()
        changed=copy.deepcopy(rows);changed[1]['state']['arguments']['command']='git diff'
        with self.assertRaisesRegex(ValueError,'Action differs'):
            self.metrics.triplet_metrics(changed)
        changed=copy.deepcopy(rows);changed[1]['state']['user_request']='Unrelated instruction.'
        with self.assertRaisesRegex(ValueError,'restriction-only'):
            self.metrics.triplet_metrics(changed)
