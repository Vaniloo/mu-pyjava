import copy
import tempfile
import unittest
from pathlib import Path
from test_intent_experiment import training_module
from mupyjava.judge_data import load_samples


class HarnessIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.module=training_module('capture_harness_intake')

    def capture(self,tool,arguments):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'target.txt';target.write_text('旧值\n')
            before=target.read_bytes();samples=root/'samples.jsonl'
            result=self.capture_module(tool,arguments,root,samples)
            self.assertEqual(before,target.read_bytes())
            self.assertEqual(len(result['proposed_mutations']),1)
            self.assertFalse(result['proposed_mutations'][0]['executed'])
            sample=load_samples(samples)[0]
            return sample,result['proposed_mutations'][0]

    def capture_module(self,tool,arguments,root,samples):
        return self.__class__.module.collect(self.__class__.module.OneCall(tool,arguments),
            {'id':'test-fixture','prompt':'A collection fixture request.'},root,samples,source='scripted_test')

    def test_file_states_use_actual_utf8_sizes_before_execution_is_blocked(self):
        module=self.__class__.module
        for tool,arguments in [('write_file',{'path':'target.txt','content':'新值\n'}),
            ('edit_file',{'path':'target.txt','old_text':'旧值','new_text':'中文替换'})]:
            sample,call=self.capture(tool,arguments)
            self.assertEqual(module.contract_findings(sample,call),[])
            key='content_bytes' if tool=='write_file' else 'new_text_bytes'
            self.assertEqual(sample['decision']['state']['arguments'][key],len(('新值\n' if tool=='write_file' else '中文替换').encode()))
            wrong=copy.deepcopy(sample);wrong['decision']['state']['arguments'][key]+=1
            self.assertTrue(module.contract_findings(wrong,call))

    def test_direct_shell_mismatch_is_captured_and_flagged_without_execution(self):
        module=self.__class__.module
        sample,call=self.capture('run_command',{'command':'command -v python3; touch SHOULD_NOT_EXIST'})
        self.assertIn('shell_builtin_without_shell',module.contract_findings(sample,call))
        for tool in ['bash','powershell','run_command']:
            command='Get-Date' if tool=='powershell' else 'python3 --version'
            sample,call=self.capture(tool,{'command':command})
            self.assertEqual(sample['decision']['state']['arguments'],call['arguments'])
            self.assertEqual(module.contract_findings(sample,call),[])

    def test_invalid_raw_schema_is_rejected_before_metadata_checks(self):
        sample,call=self.capture('write_file',{'path':'target.txt','content':'text'})
        call['arguments']['content']=None
        self.assertEqual(self.module.contract_findings(sample,call),['invalid_tool_schema'])

    def test_all_mutating_dispatch_is_blocked_after_sampling(self):
        from unittest.mock import patch
        from mupyjava.tools import WorkspaceTools
        with patch.object(WorkspaceTools,'execute_result',side_effect=AssertionError('must not execute')):
            sample,call=self.capture('bash',{'command':'touch SHOULD_NOT_EXIST'})
        self.assertEqual(sample['decision']['state']['tool'],'bash')
        self.assertFalse(call['executed'])

    def test_next_design_protects_the_entire_inspected_pilot(self):
        module=training_module('training_design')
        from mupyjava.judge_data import read_jsonl
        root=Path(__file__).resolve().parents[2]
        rows=read_jsonl(root/'training/datasets/intent-semantic-r2/intent-v2.jsonl.gz')
        designs=[module.load_design(root/'training/designs'/name) for name in ['intent-semantic-r3.json','intent-semantic-r3-uniform.json']]
        for design in designs:
            with self.assertRaisesRegex(ValueError,'protected'):
                module.audit_design(rows,design,root)
        left,right=({k:v for k,v in d.items() if k not in {'name','weighting'}} for d in designs)
        self.assertEqual(left,right)

    def test_diagnostic_intake_cannot_be_used_as_training_partitions(self):
        from mupyjava.judge_data import read_jsonl,training_partitions
        root=Path(__file__).resolve().parents[2]
        rows=read_jsonl(root/'training/datasets/harness-intake-r1/cases.jsonl')
        with self.assertRaises(ValueError):training_partitions(rows,True,True)
