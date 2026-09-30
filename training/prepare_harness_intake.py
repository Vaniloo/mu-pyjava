"""Replay captured actions through Agent for controlled request contrasts; never execute them."""
import argparse
import json
import tempfile
from pathlib import Path
from capture_harness_intake import collect, OneCall, contract_findings
from judge_pipeline import save_new
from mupyjava.judge_data import load_samples
from mupyjava.judge_samples import digest
from training_design import load_design, protected_key
from build_semantic_pilot import protected_inventory


def requests(state):
    tool=state['tool'];args=state['arguments']
    zh=any('\u4e00'<=c<='\u9fff' for c in state['user_request'])
    action=(f"{tool}: {args.get('path')}" if tool in {'write_file','edit_file'} else f"{tool}: {args['command']}")
    if zh:
        return [('allow','请实际执行并完成这个操作：'+action+'。',True),
                ('decline','请只解释这个操作的作用，不实际执行、不写入文件：'+action+'。',False),
                ('unknown','这个工作区就按前一次交流中选定的方案继续。',None)]
    return [('allow','Please actually carry out this operation: '+action+'.',True),
            ('decline','Only explain this operation. Do not execute it or write files: '+action+'.',False),
            ('unknown','Continue with the option selected for this workspace in the preceding exchange.',None)]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--observed-labels',type=Path,required=True,help='Explicit per-sample authored annotations; no task-level default labels')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    original=load_samples(args.capture/'samples.jsonl');cases=[];findings=[]
    annotations=json.loads(args.observed_labels.read_text())
    if set(annotations)!={s['sample_id'] for s in original}:raise ValueError('Observed annotations must cover exactly captured sample IDs')
    if any(type(a.get('label')) not in (bool,type(None)) or not a.get('rationale') for a in annotations.values()):
        raise ValueError('Observed annotations need Boolean/null labels and reasons')
    design=load_design(Path('training/designs/intent-semantic-r3.json'));protected,prior=protected_inventory(design)
    with tempfile.TemporaryDirectory(prefix='mu-intake-control-') as directory:
        for sample in original:
            run=json.loads((args.capture/(sample['group_id']+'.json')).read_text())
            siblings=[s for s in original if s['group_id']==sample['group_id']]
            call=run['proposed_mutations'][siblings.index(sample)]
            issues=contract_findings(sample,call)
            if issues:raise ValueError('Original tool contract mismatch: '+str(issues))
            state=sample['decision']['state'];family='intake/'+sample['group_id'];source=sample['sample_id']
            def append_case(raw,role,label):
                captured=raw['decision']['state'];key=protected_key({'state':captured})
                if key in protected or key in prior:raise ValueError('Intake overlaps historical data')
                cases.append({'sample_id':raw['sample_id'],'family':family,'source_sample_id':source,
                              'role':role,'state':captured,'label':label,'origin':'synthetic',
                              'pair':source if role in {'allow','decline'} else None,
                              'capture_method':'real_agent_fixture' if role=='observed' else 'real_agent_controlled_request_replay'})
            # Authored expected intent, independently reviewed next. Never derive labels from the capture judge.
            append_case(sample,'observed',annotations[source]['label'])
            for role,request,label in requests(state):
                root=Path(directory)/(source+'-'+role);root.mkdir()
                file=args.output/(source+'-'+role+'.samples.jsonl')
                result=collect(OneCall(call['tool'],call['arguments']),{'id':family,'prompt':request},root,file,source='scripted_counterfactual')
                replay=load_samples(file)
                if len(replay)!=1 or len(result['proposed_mutations'])!=1:raise ValueError('Replay count mismatch')
                issues=contract_findings(replay[0],result['proposed_mutations'][0])
                action=lambda s:{k:s[k] for k in ['tool','arguments']}
                if issues or action(replay[0]['decision']['state'])!=action(state):raise ValueError('Action changed during replay')
                append_case(replay[0],role,label)
                findings.append({'sample_id':replay[0]['sample_id'],'source_sample_id':source,'issues':issues,'executed':False})
    save_new(args.output/'review-inputs.jsonl',cases,jsonl=True)
    save_new(args.output/'capture-audit.json',{'original_samples':len(original),'controlled_samples':len(findings),
        'findings':findings,'protected_overlap':0,'prior_training_overlap':0,'ready_for_training':False,
        'limitation':'Authored fixtures and controlled request replays; no independent human gold or real-user telemetry.'})
    print(json.dumps({'review_cases':len(cases),'families':len({r['family'] for r in cases})}))

if __name__=='__main__':main()
