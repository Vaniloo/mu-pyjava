"""Archive reviewed runtime-captured diagnostics without declaring a training dataset ready."""
import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from capture_harness_intake import contract_findings
from judge_pipeline import save_new
from mupyjava.judge_data import load_samples, read_jsonl
from mupyjava.judge_samples import digest, canonical
from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge import LayaBooleanJudge
from build_semantic_pilot import quarantine, protected_inventory
from training_design import load_design


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['capture','controls','reviews','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();cases=read_jsonl(args.controls/'review-inputs.jsonl');original=load_samples(args.capture/'samples.jsonl')
    annotations=json.loads((args.capture/'observed-labels.json').read_text())
    raw={s['sample_id']:s for s in original};calls={};matches=[]
    for sample in original:
        task=sample['group_id'];run=json.loads((args.capture/(task+'.json')).read_text())
        siblings=[s for s in original if s['group_id']==task]
        if len(siblings)!=len(run['proposed_mutations']):raise ValueError('Original call count mismatch')
        calls[sample['sample_id']]=run['proposed_mutations'][siblings.index(sample)]
    for path in sorted(args.controls.glob('*.samples.jsonl')):
        for sample in load_samples(path):
            if sample['sample_id'] in raw:raise ValueError('Repeated raw sample ID')
            raw[sample['sample_id']]=sample
    if len({r['sample_id'] for r in cases})!=len(cases):raise ValueError('Repeated case ID')
    for row in cases:
        sample=raw[row['sample_id']];call=calls[row['source_sample_id']]
        if sample['decision']['state']!=row['state']:raise ValueError('Case differs from captured state')
        if row['role']=='observed' and annotations[row['sample_id']]['label'] is not row['label']:
            raise ValueError('Observed authored annotation changed')
        issues=contract_findings(sample,call)
        if issues:raise ValueError('Contract failure: '+str(issues))
        matches.append({'sample_id':row['sample_id'],'issues':[]})
    reviews={};review_batches=[]
    for path in sorted(args.reviews.glob('[0-9][0-9][0-9].json')):
        body=json.loads(path.read_text());index=int(path.stem);batch=cases[index*24:index*24+24]
        if (body.get('labels_hidden') is not True or body.get('prediction_blind') is not True
            or body['input_digest']!=digest([{'sample_id':r['sample_id'],'state':r['state']} for r in batch])):
            raise ValueError('Review input or blind-review provenance mismatch')
        if {r['sample_id'] for r in body['reviews']}!={r['sample_id'] for r in batch}:raise ValueError('Review batch IDs mismatch')
        for row in body['reviews']:
            if row['sample_id'] in reviews:raise ValueError('Repeated review ID')
            reviews[row['sample_id']]=row
        review_batches.append(body)
    protected,prior=protected_inventory(load_design(Path('training/designs/intent-semantic-r3.json')))
    accepted,reasons=quarantine(cases,reviews,protected,prior)
    exported=[{**r,'group_id':r['family'],'question':TOOL_INTENT.question,'criteria':LayaBooleanJudge.CRITERIA,
               'tags':{'semantic_family':r['family'],'role':r['role'],'capture_method':r['capture_method'],
                       **({'contrast_pair':r['pair']} if r['pair'] else {})}} for r in accepted]
    args.output.mkdir(parents=True,exist_ok=False)
    save_new(args.output/'cases.jsonl',exported,jsonl=True)
    save_new(args.output/'blind-reviews.json',review_batches)
    save_new(args.output/'authored-labels.json',annotations)
    save_new(args.output/'contract-audit.json',{'matches':matches,'issues':0,'mutations_executed':False})
    report={'schema_version':1,'status':'reviewed_diagnostic_intake','ready_for_training':False,
            'dataset_digest':digest(exported),'source_cases':len(cases),'accepted':len(exported),
            'families':len({r['family'] for r in exported}),'by_role':dict(Counter(r['role'] for r in exported)),
            'by_tool':dict(Counter(r['state']['tool'] for r in exported)),
            'by_label':dict(Counter(str(r['label']) for r in exported)),
            'quarantined_families':reasons,'teacher_agreement':sum(reviews.get(r['sample_id'],{}).get('label','missing') is r['label'] for r in cases),
            'reviewer':'deepseek-flash; same model family as actor, not independent human review',
            'real_user_project_traces':0,'human_gold':0,'learned_judge_inference':False,
            'limitation':'Small authored fixture cohort with derived contrasts; no train/validation/calibration/test partition.'}
    save_new(args.output/'intake-manifest.json',report)
    archive={'capture':{p.name:json.loads(p.read_text()) for p in args.capture.glob('*.json')},
             'raw_samples':list(raw.values()),'submitted_cases':cases,'capture_audit':json.loads((args.controls/'capture-audit.json').read_text())}
    (args.output/'capture-evidence.json.gz').write_bytes(gzip.compress(canonical(archive).encode(),mtime=0))
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
