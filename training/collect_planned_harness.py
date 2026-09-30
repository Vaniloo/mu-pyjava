"""Collect a prespecified workflow plan through Agent, with per-task evidence files."""
import argparse
import getpass
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from capture_harness_intake import collect
from judge_pipeline import save_new
from mupyjava.model import ChatCompletionsModel
from mupyjava.judge_samples import digest
from mupyjava.judge_data import load_samples


def validate_plan(plan):
    seen=set();splits=set()
    for task in plan['tasks']:
        name=task['id']
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in name) or name in seen:
            raise ValueError('Invalid or duplicate task ID')
        seen.add(name);splits.add(task['split'])
        if task['split'] not in {'train','validation','calibration','test'}:raise ValueError('Invalid split')
        for field in ['prompt','decline','unknown']:
            if not isinstance(task[field],str) or not task[field].strip() or len(task[field])>1000:raise ValueError('Invalid request')
        for name,content in task['initial'].items():
            path=PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or not isinstance(content,str):raise ValueError('Unsafe fixture path/content')
    if splits!={'train','validation','calibration','test'}:raise ValueError('Missing planned partition')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=3);args=parser.parse_args()
    plan=json.loads(args.plan.read_text());validate_plan(plan)
    if not 1<=args.workers<=4:raise ValueError('Workers must be 1..4')
    args.output.mkdir(parents=True,exist_ok=False)
    save_new(args.output/'plan.json',plan)
    model=ChatCompletionsModel('https://api.deepseek.com',getpass.getpass('DeepSeek API key: '),'deepseek-flash',max_output_tokens=1200)
    with tempfile.TemporaryDirectory(prefix='mu-planned-') as directory:
        def run(task):
            root=Path(directory)/task['id'];root.mkdir()
            for name,content in task['initial'].items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
            try:result=collect(model,task,root,args.output/(task['id']+'.samples.jsonl'))
            except Exception as error:result={'task':task['id'],'failure':type(error).__name__}
            save_new(args.output/(task['id']+'.json'),result)
            summary={'task':task['id'],'split':task['split'],'calls':len(result.get('proposed_mutations',[])),'failure':result.get('failure')}
            print(json.dumps(summary),flush=True);return summary
        with ThreadPoolExecutor(max_workers=args.workers) as executor:outcomes=list(executor.map(run,plan['tasks']))
    samples=[]
    for task in plan['tasks']:
        file=args.output/(task['id']+'.samples.jsonl')
        if file.exists():samples.extend(load_samples(file))
    save_new(args.output/'samples.jsonl',samples,jsonl=True)
    save_new(args.output/'outcomes.json',{'plan_digest':digest(plan),'tasks':outcomes,'samples':len(samples),'ready_for_training':False})

if __name__=='__main__':main()
