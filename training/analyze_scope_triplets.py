"""Compare serving reports on one frozen scope-triplet partition."""
import argparse
import gzip
import json
from pathlib import Path

from compare_intent import compare
from evaluate_intent import metrics
from judge_pipeline import save_new
from scope_triplet_metrics import triplet_metrics


def analyze(data,split,reports):
    result=compare(data,split,reports)
    for name,report in reports.items():
        model=result['models'][name]
        model['triplets']=triplet_metrics(report['predictions'])
        model['by_role']={}
        for role in ('plain','restricted','forbidden','unknown'):
            selected=[r for r in report['predictions'] if r['tags']['role']==role]
            model['by_role'][role]=metrics(selected,[r['probability'] for r in selected])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--split',default='test')
    parser.add_argument('--report',action='append',required=True,help='NAME=report.json[.gz]')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();reports={}
    for item in args.report:
        name,path=item.split('=',1)
        if name in reports:raise ValueError('Repeated model name')
        with (gzip.open if path.endswith('.gz') else open)(path,'rt',encoding='utf-8') as file:
            reports[name]=json.load(file)
    result=analyze(args.data,args.split,reports)
    save_new(args.output,result)
    for name,model in result['models'].items():
        print(json.dumps({'model':name,'correct_decisive':model['correct_decisive'],
              'false_allow':model['metrics']['false_allow'],'false_decline':model['metrics']['false_decline'],
              'known_abstention':model['known_abstention'],'unknown_abstention':model['unknown_abstention'],
              'triplets':model['triplets']['overall']}))


if __name__=='__main__':main()
