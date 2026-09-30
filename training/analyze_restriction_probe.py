"""Compare frozen serving reports and isolate added-restriction decision changes."""
import argparse,gzip,json
from pathlib import Path
from compare_intent import compare
from evaluate_intent import metrics
from restriction_metrics import restriction_metrics
from judge_pipeline import save_new


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--report',action='append',required=True,help='NAME=report.json[.gz]');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();reports={}
    for entry in a.report:
        name,path=entry.split('=',1)
        if name in reports:raise ValueError('Repeated report name')
        with (gzip.open if path.endswith('.gz') else open)(path,'rt') as file:reports[name]=json.load(file)
    result=compare(a.data,'all',reports)
    for name,report in reports.items():
        model=result['models'][name];model['restriction_effect']=restriction_metrics(report['predictions'])
        model['by_condition']={}
        for role in ['plain','restricted','decline','unknown']:
            selected=[r for r in report['predictions'] if r['tags']['role']==role]
            model['by_condition'][role]=metrics(selected,[r['probability'] for r in selected])
        print(json.dumps({'model':name,'pairs':model['restriction_effect']['pairs'],
                         'lost_allow':model['restriction_effect']['lost_allow'],
                         'new_wrong_decline':model['restriction_effect']['new_wrong_decline'],
                         'correct_decisive':model['correct_decisive'],
                         'false_allow':model['metrics']['false_allow'],'false_decline':model['metrics']['false_decline'],
                         'known_abstention':model['known_abstention'],'unknown_abstention':model['unknown_abstention']}))
    save_new(a.output,result)

if __name__=='__main__':main()
