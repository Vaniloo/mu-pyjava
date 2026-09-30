"""Prepare and freeze planned runtime captures; split identities are never inferred from outcomes."""
import argparse
import gzip
import json
import tempfile
from collections import Counter
from pathlib import Path
from capture_harness_intake import collect, OneCall, contract_findings
from collect_planned_harness import validate_plan
from build_semantic_pilot import convert, quarantine, protected_inventory, rehearsal_rows
from training_design import load_design,audit_design
from tool_state_audit import command_state_issue,require_command_states
from judge_pipeline import save_new
from mupyjava.judge_data import load_samples,read_jsonl,training_partitions,validate_manifest
from mupyjava.judge_samples import digest,canonical

ROOT=Path(__file__).resolve().parents[1]


def prepare(capture,output):
    plan=json.loads((capture/'plan.json').read_text());validate_plan(plan)
    outcome=json.loads((capture/'outcomes.json').read_text())
    if digest(plan)!=outcome['plan_digest']:raise ValueError('Capture plan changed')
    output.mkdir(parents=True,exist_ok=False);cases=[];excluded={};raw=[]
    with tempfile.TemporaryDirectory(prefix='mu-r3-controls-') as directory:
        for task in plan['tasks']:
            name=task['id'];run=json.loads((capture/(name+'.json')).read_text())
            path=capture/(name+'.samples.jsonl');samples=load_samples(path) if path.exists() else []
            calls=run.get('proposed_mutations',[])
            if run.get('failure') or not samples or len(samples)!=len(calls):
                excluded[name]=['capture_failed_or_missing'];continue
            if any(contract_findings(s,c) for s,c in zip(samples,calls)):
                excluded[name]=['original_contract_failure'];continue
            family_cases=[];family_raw=[]
            for index,(sample,call) in enumerate(zip(samples,calls)):
                pair=name+'/'+str(index);family_raw.append(sample)
                for role,label,request in [('allow',True,task['prompt']),('decline',False,task['decline']),('unknown',None,task['unknown'])]:
                    if role=='allow':captured=sample
                    else:
                        root=Path(directory)/(name+'-'+str(index)+'-'+role);root.mkdir()
                        path=output/(name+'-'+str(index)+'-'+role+'.samples.jsonl')
                        result=collect(OneCall(call['tool'],call['arguments']),{'id':name,'prompt':request},root,path,source='planned_request_replay')
                        records=load_samples(path)
                        if len(records)!=1 or len(result['proposed_mutations'])!=1:raise ValueError('Replay count changed')
                        captured=records[0]
                        if contract_findings(captured,call):raise ValueError('Replay projection changed')
                        family_raw.append(captured)
                    state=captured['decision']['state']
                    if state['tool']!=sample['decision']['state']['tool'] or state['arguments']!=sample['decision']['state']['arguments']:
                        raise ValueError('Action differs across contrast requests')
                    family_cases.append({'sample_id':captured['sample_id'],'input_digest':captured['input_digest'],
                        'family':'captured-r3/'+name,'split':task['split'],'language':'zh' if any('\u4e00'<=c<='\u9fff' for c in request) else 'en',
                        'origin':'synthetic','label':label,'state':state,'pair':pair if label is not None else None,
                        'role':role,'source_sample_id':sample['sample_id'],'collection':'actor_fixture' if role=='allow' else 'authored_request_replay'})
            cases.extend(family_cases);raw.extend(family_raw)
    save_new(output/'review-inputs.jsonl',cases,jsonl=True)
    save_new(output/'raw-samples.jsonl',raw,jsonl=True)
    save_new(output/'preparation.json',{'plan_digest':digest(plan),'excluded':excluded,'submitted':len(cases),
              'submitted_families':len({r['family'] for r in cases}),'labels':'Authored hypotheses before blind teacher review, not actor or capture-backend predictions.'})
    print(json.dumps({'submitted':len(cases),'excluded':excluded}))


def freeze(capture,prepared,reviews,output):
    plan=json.loads((capture/'plan.json').read_text());validate_plan(plan)
    preparation=json.loads((prepared/'preparation.json').read_text())
    if preparation['plan_digest']!=digest(plan):raise ValueError('Prepared plan changed')
    cases=read_jsonl(prepared/'review-inputs.jsonl');raw={r['sample_id']:r for r in load_samples(prepared/'raw-samples.jsonl')}
    planned={ 'captured-r3/'+t['id']:t['split'] for t in plan['tasks']}
    for row in cases:
        if (row['family']!='captured-r3/'+raw[row['sample_id']]['group_id']
            or row['split']!=planned[row['family']] or row['state']!=raw[row['sample_id']]['decision']['state']
            or row['input_digest']!=raw[row['sample_id']]['input_digest']):
            raise ValueError('Case split or runtime state changed')
    reviewed={};batches=[]
    for path in sorted(reviews.glob('[0-9][0-9][0-9].json')):
        body=json.loads(path.read_text());index=int(path.stem);batch=cases[index*24:index*24+24]
        if body.get('labels_hidden') is not True or body.get('prediction_blind') is not True or body['input_digest']!=digest([{'sample_id':r['sample_id'],'state':r['state']} for r in batch]):raise ValueError('Review input mismatch')
        if {r['sample_id'] for r in body['reviews']}!={r['sample_id'] for r in batch}:raise ValueError('Review IDs mismatch')
        for item in body['reviews']:
            if item['sample_id'] in reviewed:raise ValueError('Repeated review')
            reviewed[item['sample_id']]=item
        batches.append(body)
    design=load_design(ROOT/'training/designs/intent-semantic-r3.json');protected,prior=protected_inventory(design)
    accepted,reasons=quarantine(cases,reviewed,protected,prior)
    rows=[]
    for case in accepted:
        row=convert(case);row['input_digest']=case['input_digest']
        row['tags'].update({'capture_method':case['collection'],'source_sample_id':case['source_sample_id']})
        rows.append(row)
    rehearsal=rehearsal_rows(protected,prior)
    rejected_rehearsal=[r['sample_id'] for r in rehearsal if command_state_issue(r['state'])]
    rows.extend(r for r in rehearsal if not command_state_issue(r['state']))
    require_command_states(rows);training_partitions(rows,True,True)
    groups={r['group_id']:r['split'] for r in rows};counts=dict(Counter(r['split'] for r in rows))
    manifest={'schema_version':1,'seed':'captured-harness-r3','groups':groups,'group_components':{g:g for g in groups},
        'split_strategy':'prespecified_workflow_plan','counts':counts,'dataset_digest':digest(rows),'plan_digest':digest(plan),
        'evaluation_basis':'synthetic_experiment','unknown_target':'uniform_boolean_distribution','ready_for_training':True}
    validate_manifest(rows,manifest,True,True)
    audits={name:audit_design(rows,load_design(ROOT/'training/designs'/name),ROOT) for name in ['intent-semantic-r3.json','intent-semantic-r3-uniform.json']}
    output.mkdir(parents=True,exist_ok=False)
    (output/'intent-v2.jsonl.gz').write_bytes(gzip.compress((''.join(canonical(r)+'\n' for r in rows)).encode(),mtime=0))
    save_new(output/'manifest.json',manifest);save_new(output/'design-audits.json',audits);save_new(output/'blind-reviews.json',batches)
    save_new(output/'submitted-cases.jsonl',cases,jsonl=True)
    report={'plan_digest':digest(plan),'preparation_exclusions':preparation['excluded'],'review_exclusions':reasons,
        'submitted_rows':len(cases),'accepted_fresh':len(accepted),'fresh_families_by_split':dict(Counter(planned[f] for f in {r['family'] for r in accepted})),
        'accepted_fresh_by_split':dict(Counter(r['split'] for r in accepted)),
        'teacher_agreement':sum(reviewed.get(r['sample_id'],{}).get('label','missing') is r['label'] for r in cases),
        'rehearsal_rows':len(rehearsal)-len(rejected_rehearsal),'rehearsal_contract_exclusions':rejected_rehearsal,
        'human_gold':0,'real_user_project_traces':0,'candidate_inference':False,'ready_for_training':True}
    save_new(output/'intake-audit.json',report)
    evidence={'plan':plan,'outcomes':json.loads((capture/'outcomes.json').read_text()),
              'runs':{t['id']:json.loads((capture/(t['id']+'.json')).read_text()) for t in plan['tasks']},'raw_samples':list(raw.values())}
    (output/'capture-evidence.json.gz').write_bytes(gzip.compress(canonical(evidence).encode(),mtime=0))
    print(json.dumps(report))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','freeze'])
    for name in ['capture','output']:p.add_argument('--'+name,type=Path,required=True)
    for name in ['prepared','reviews']:p.add_argument('--'+name,type=Path)
    a=p.parse_args()
    if a.action=='prepare':prepare(a.capture,a.output)
    else:freeze(a.capture,a.prepared,a.reviews,a.output)

if __name__=='__main__':main()
