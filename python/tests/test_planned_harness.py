import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_intent_experiment import training_module
from mupyjava.judge_samples import digest
from mupyjava.judge_data import read_jsonl,validate_manifest


class PlannedHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=training_module('collect_planned_harness')
        cls.builder=training_module('build_captured_dataset')
        cls.capture=training_module('capture_harness_intake')

    def fixture_plan(self):
        return {'tasks':[{'id':split,'split':split,'prompt':'Create '+split+'-fixture.txt on disk.',
                'decline':'Only explain '+split+'-fixture.txt. Do not write it.',
                'unknown':'Use our previously selected '+split+' option.','initial':{}}
                for split in ['train','validation','calibration','test']]}

    def test_plan_rejects_escaping_paths_duplicate_ids_and_missing_splits(self):
        good=self.fixture_plan();self.plan.validate_plan(good)
        for path in ['../outside','/absolute','a\\..\\outside']:
            bad=copy.deepcopy(good);bad['tasks'][0]['initial']={path:'x'}
            with self.assertRaises(ValueError):self.plan.validate_plan(bad)
        bad=copy.deepcopy(good);bad['tasks'].append(bad['tasks'][0])
        with self.assertRaises(ValueError):self.plan.validate_plan(bad)
        with self.assertRaises(ValueError):self.plan.validate_plan({'tasks':good['tasks'][:-1]})

    def test_capture_prepare_freeze_preserves_fixed_splits_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);capture=root/'capture';capture.mkdir();plan=self.fixture_plan()
            (capture/'plan.json').write_text(json.dumps(plan));(capture/'outcomes.json').write_text(json.dumps({'plan_digest':digest(plan)}))
            for task in plan['tasks']:
                workspace=root/task['id'];workspace.mkdir()
                result=self.capture.collect(self.capture.OneCall('write_file',{'path':task['id']+'-fixture.txt','content':'中文\n'}),
                    task,workspace,capture/(task['id']+'.samples.jsonl'),source='scripted_test')
                (capture/(task['id']+'.json')).write_text(json.dumps(result))
            prepared=root/'prepared';self.builder.prepare(capture,prepared)
            cases=read_jsonl(prepared/'review-inputs.jsonl');self.assertEqual(len(cases),12)
            reviews=root/'reviews';reviews.mkdir()
            report={'prediction_blind':True,'labels_hidden':True,'input_digest':digest([{'sample_id':r['sample_id'],'state':r['state']} for r in cases]),
                    'reviews':[{'sample_id':r['sample_id'],'label':r['label'],'rationale':'unit fixture'} for r in cases]}
            (reviews/'000.json').write_text(json.dumps(report))
            with patch.object(self.builder,'rehearsal_rows',return_value=[]):
                self.builder.freeze(capture,prepared,reviews,root/'frozen')
                rows=read_jsonl(root/'frozen/intent-v2.jsonl.gz');manifest=json.loads((root/'frozen/manifest.json').read_text())
                validate_manifest(rows,manifest,True,True)
                self.assertEqual(manifest['counts'],{s:3 for s in ['train','validation','calibration','test']})
                cases[0]['split']='test'
                (prepared/'review-inputs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in cases))
                with self.assertRaisesRegex(ValueError,'split or runtime state'):
                    self.builder.freeze(capture,prepared,reviews,root/'tampered')
                self.assertFalse((root/'tampered').exists())
