"""Freeze the semantic pilot after label-blind teacher review and whole-family quarantine."""
import argparse
import gzip
import json
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from judge_pipeline import save_new
from semantic_holdouts import HOLDOUTS
from tool_state_audit import require_command_states
from training_design import load_design, protected_key, audit_design
from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge import LayaBooleanJudge
from mupyjava.judge_data import read_jsonl, training_partitions, validate_manifest
from mupyjava.judge_samples import digest, canonical

ROOT = Path(__file__).resolve().parents[1]


def authored_cases():
    rows=[]
    for split,name,tool,arguments,en,zh in HOLDOUTS:
        family=f'authored/{split}/{name}'
        for language,requests in [('en',en),('zh',zh)]:
            for request,label in zip(requests,[True,False,None]):
                row={'family':family,'language':language,'pair':f'{family}/{language}' if label is not None else None,
                     'state':{'user_request':request,'tool':tool,'arguments':arguments},'label':label,
                     'split':split,'origin':'synthetic'}
                row['sample_id']=digest(row)[:32];rows.append(row)
    return rows


def protected_inventory(design):
    protected=set();prior=defaultdict(set)
    for item in design['protected_inputs']:
        for row in read_jsonl(ROOT/item['path']):
            if item['selection']=='nontrain' and row.get('split')=='train':
                prior[protected_key(row)].add(str(row['label']))
            else: protected.add(protected_key(row))
    return protected,prior


def quarantine(cases, reviews, protected, prior):
    reasons=defaultdict(set);seen=defaultdict(list)
    for row in cases:
        family=row['family'];key=protected_key(row)
        seen[key].append(family)
        item=reviews.get(row['sample_id'])
        if item is None: reasons[family].add('missing_review')
        elif item['label'] is not row['label']: reasons[family].add('blind_label_disagreement')
        if key in protected: reasons[family].add('protected_input')
        if key in prior: reasons[family].add('prior_training_input')
    for families in seen.values():
        if len(families)>1:
            for family in families: reasons[family].add('duplicate_input')
    return [row for row in cases if row['family'] not in reasons],{k:sorted(v) for k,v in sorted(reasons.items())}


def rehearsal_rows(protected,prior,limit=384):
    buckets=defaultdict(list)
    for row in read_jsonl(ROOT/'training/datasets/intent-scope-r1/intent-v2.jsonl.gz'):
        key=protected_key(row)
        if row['split']=='train' and key not in protected and prior.get(key)=={str(row['label'])}:
            buckets[(row['group_id'],str(row['label']))].append(row)
    # Round robin families/labels; one request per old family/label, no parameter multiplication.
    for key,values in buckets.items():
        unique={}
        for row in sorted(values,key=lambda row:digest(row)):
            unique.setdefault(row['state']['user_request'],row)
        buckets[key]=list(unique.values())
    chosen=[];used=set()
    while len(chosen)<limit:
        added=False
        for key in sorted(buckets):
            while buckets[key]:
                row=buckets[key].pop(0);fingerprint=protected_key(row)
                if fingerprint in used:continue
                used.add(fingerprint);chosen.append(row);added=True;break
            if len(chosen)==limit:break
        if not added:break
    result=[]
    for row in chosen:
        family='rehearsal/'+row['group_id']
        tags={**row.get('tags',{}),'semantic_family':family,'rehearsal':True,
              'source_sample_id':row['sample_id'],'source_state_digest':protected_key(row)}
        tags.pop('contrast_pair',None)
        result.append({**row,'sample_id':digest(['semantic-r2-rehearsal',row['sample_id']])[:32],
                       'group_id':family,'tags':tags})
    return result


def convert(case):
    tags={'semantic_family':case['family'],'rehearsal':False,'language':case['language']}
    if case['pair'] is not None:tags['contrast_pair']=case['pair']
    material={'point':TOOL_INTENT.id,'version':TOOL_INTENT.version,'questions':[asdict(TOOL_INTENT)],
              'inputs':case['state'],'state':case['state']}
    return {'schema_version':2,'point':TOOL_INTENT.id,'version':TOOL_INTENT.version,
            'sample_id':case['sample_id'],'input_digest':digest(material),'group_id':case['family'],
            'split':case['split'],'origin':case['origin'],'tags':tags,'reviewer':'deepseek-flash-blind-review',
            'state':case['state'],'question':TOOL_INTENT.question,'criteria':LayaBooleanJudge.CRITERIA,'label':case['label']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','freeze'])
    parser.add_argument('--generated',type=Path,action='append',default=[])
    parser.add_argument('--cases',type=Path)
    parser.add_argument('--reviews',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='prepare':
        cases=authored_cases();families=set()
        for folder in args.generated:
            for path in sorted(folder.glob('[0-9][0-9][0-9].json')):
                body=json.loads(path.read_text());family=body['seed']['family']
                if family in families:raise ValueError('Repeated generated family')
                families.add(family)
                cases.extend({**row,'split':'train','origin':'teacher'} for row in body['cases'])
        if not families:raise ValueError('No valid teacher families')
        require_command_states(cases)
        save_new(args.output,cases,jsonl=True)
        print(json.dumps({'cases':len(cases),'teacher_families':len(families)}));return
    cases=read_jsonl(args.cases);reviews={};review_artifacts=[]
    for path in sorted(args.reviews.glob('[0-9][0-9][0-9].json')):
        body=json.loads(path.read_text());index=int(path.stem);batch=cases[index*24:index*24+24]
        if body['input_digest']!=digest([{'sample_id':row['sample_id'],'state':row['state']} for row in batch]):
            raise ValueError('Review does not match input batch')
        review_artifacts.append(body)
        for item in body['reviews']:
            if item['sample_id'] in reviews:raise ValueError('Duplicate review')
            reviews[item['sample_id']]=item
    design=load_design(ROOT/'training/designs/intent-semantic-r2.json')
    protected,prior=protected_inventory(design)
    accepted,reasons=quarantine(cases,reviews,protected,prior)
    rows=[convert(row) for row in accepted]+rehearsal_rows(protected,prior)
    groups={row['group_id']:row['split'] for row in rows};counts=dict(Counter(row['split'] for row in rows))
    manifest={'schema_version':1,'seed':'intent-semantic-r2','groups':groups,'group_components':{k:k for k in groups},
              'split_strategy':'explicit_group_map','counts':counts,'excluded':{'families':len(reasons),'rows':len(cases)-len(accepted)},
              'dataset_digest':digest(rows),'evaluation_basis':'synthetic_experiment','unknown_target':'uniform_boolean_distribution',
              'ready_for_training':all(counts.get(s,0)>0 for s in ['train','validation','calibration','test'])}
    require_command_states(rows)
    training_partitions(rows,True,True);validate_manifest(rows,manifest,True,True)
    audits={}
    for arm in ['intent-semantic-r2','intent-semantic-r2-uniform']:
        audits[arm]=audit_design(rows,load_design(ROOT/f'training/designs/{arm}.json'),ROOT)
    args.output.mkdir(parents=True,exist_ok=False)
    with open(args.output/'intent-v2.jsonl.gz','xb') as file:
        file.write(gzip.compress((''.join(canonical(row)+'\n' for row in rows)).encode(),mtime=0))
    save_new(args.output/'manifest.json',manifest)
    save_new(args.output/'fresh-cases.jsonl',cases,jsonl=True)
    save_new(args.output/'blind-reviews.json',review_artifacts)
    save_new(args.output/'quarantine.json',{'families':reasons,'accepted_fresh':len(accepted),'submitted_fresh':len(cases),
             'missing_reviews':sum(row['sample_id'] not in reviews for row in cases),'human_gold':0})
    save_new(args.output/'design-audits.json',audits)
    print(json.dumps({'counts':counts,'quarantine':reasons,'digest':manifest['dataset_digest']}))

if __name__=='__main__':main()
