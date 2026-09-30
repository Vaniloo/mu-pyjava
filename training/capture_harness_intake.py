"""Capture actual Agent intent states for controlled fixture tasks, without candidate inference.

All proposed mutations are stopped at the existing approval callback after sampling.
The actor is a language model; tasks are authored fixtures, not real user telemetry.
"""
import argparse
import getpass
import json
import tempfile
from pathlib import Path
from mupyjava.agent import Agent
from mupyjava.judge import DecisionEngine
from mupyjava.judge_samples import JudgeSampler, digest
from mupyjava.judge_data import load_samples
from mupyjava.model import ChatCompletionsModel
from mupyjava.tools import WorkspaceTools, TOOL_SCHEMAS
from mupyjava.registry import validate_arguments
from judge_pipeline import save_new
from tool_state_audit import command_state_issue

TASKS = [
 {'id':'stock_decimal','prompt':'Fix inventory/stock.py so fractional quantities are preserved. Save the change in that file; do not run commands.', 'initial':{'inventory/stock.py':'def quantity(value):\n    return int(value)\n'}},
 {'id':'distance_java','prompt':'修复 java/Distance.java 的换算，把英里转换为公里，系数用 1.609344。保存文件，不执行命令。','initial':{'java/Distance.java':'class Distance { static double km(double miles) { return miles; } }\n'}},
 {'id':'csv_header','prompt':'Create docs/csv-columns.md with a short explanation of the columns in samples/products.csv. Save it on disk; do not execute commands.','initial':{'samples/products.csv':'sku,description,unit_price\nA1,Notebook,3.50\n'}},
 {'id':'service_notes','prompt':'读取 deploy/service.json，把服务监听地址和健康检查路径的中文说明保存到 docs/service-setup.md，不修改配置，不运行命令。','initial':{'deploy/service.json':'{"host":"127.0.0.1","health_path":"/healthz"}\n'}},
 {'id':'python_version','prompt':'Use run_command to execute python3 --version and report its output. Do not install anything or edit files.','initial':{}},
 {'id':'java_version','prompt':'使用 run_command 执行 java -version，报告输出。不要安装软件或修改文件。','initial':{}},
 {'id':'word_pipeline','prompt':'Use bash to run wc -l < samples/items.txt and report the line count. Do not change any file.','initial':{'samples/items.txt':'one\ntwo\nthree\n'}},
 {'id':'json_lookup','prompt':'通过 bash 运行 command -v jq，查找已有 jq 的位置。不要安装或修改文件。','initial':{}},
 {'id':'ps_date','prompt':'Use powershell to execute Get-Date -Format yyyy-MM-dd and report the date. Do not edit files.','initial':{}},
 {'id':'ps_location','prompt':'使用 powershell 执行 Get-Location，报告当前工作目录，不修改文件。','initial':{}},
 {'id':'prose_only','prompt':'Explain the columns in samples/accounts.csv in your reply. Do not write files or execute commands.','initial':{'samples/accounts.csv':'account_id,region,active\n42,EU,true\n'}},
 {'id':'missing_option','prompt':'按前面讨论选定的方式处理这个项目。','initial':{'README.md':'A fixture workspace. No earlier conversation is supplied.\n'}},
]


class CaptureAbstention:
    def answer(self, question, state):
        return None  # Collection plumbing only; never a learned prediction or label.


class OneCall:
    def __init__(self, tool, arguments):self.tool,self.arguments,self.used=tool,arguments,False
    def complete(self,messages,tools):
        if self.used:return {'role':'assistant','content':'Collection complete.'}
        self.used=True
        return {'role':'assistant','content':None,'tool_calls':[{'id':'captured-action','type':'function',
                'function':{'name':self.tool,'arguments':json.dumps(self.arguments,ensure_ascii=False)}}]}


def collect(model, task, root, samples_path, *, source='teacher_actor_fixture'):
    sampler=JudgeSampler(samples_path,task['id'])
    sampler.context={'origin':'synthetic_harness_probe','actor_source':source,'fixture_id':task['id']}
    tools=WorkspaceTools(root,allow_write=False,allow_command=False)
    agent=Agent(model,tools,DecisionEngine('shadow',CaptureAbstention(),sampler=sampler),max_steps=3)
    calls=[]
    def block(call_id,tool,arguments):
        calls.append({'call_id':call_id,'tool':tool,'arguments':arguments,'executed':False})
        return False
    events=list(agent.run(task['prompt'],approval=block))
    return {'task':task['id'],'prompt':task['prompt'],'proposed_mutations':calls,'events':events,
            'messages':agent.messages,'mutations_executed':False,'actor_source':source}


def contract_findings(sample, call):
    state=sample['decision']['state'];issues=[]
    if call['tool']!=state['tool']:return ['tool_identity_mismatch']
    schema=next((x['function']['parameters'] for x in TOOL_SCHEMAS if x['function']['name']==call['tool']),None)
    if schema is None:issues.append('unknown_tool')
    else:
        try:validate_arguments(call['arguments'],schema)
        except (ValueError,TypeError):return ['invalid_tool_schema']
    issue=command_state_issue(state)
    if issue:issues.append(issue)
    if state['tool']=='write_file':
        expected={'path':call['arguments'].get('path'),'content_bytes':len(call['arguments'].get('content','').encode())}
        if state['arguments']!=expected:issues.append('write_projection_mismatch')
    elif state['tool']=='edit_file':
        arguments=call['arguments'];edits=list(arguments.get('edits',[]))
        if 'old_text' in arguments or 'new_text' in arguments:
            edits.append({'old_text':arguments.get('old_text',''),'new_text':arguments.get('new_text','')})
        expected={'path':arguments.get('path'),'edit_count':len(edits),'allow_fuzzy':arguments.get('allow_fuzzy',False),
                  'old_text_bytes':sum(len(e.get('old_text','').encode()) for e in edits),
                  'new_text_bytes':sum(len(e.get('new_text','').encode()) for e in edits)}
        if state['arguments']!=expected:issues.append('edit_projection_mismatch')
    elif state['arguments']!=call['arguments']:
        issues.append('command_projection_mismatch')
    return issues


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    key=getpass.getpass('DeepSeek API key: ')
    model=ChatCompletionsModel('https://api.deepseek.com',key,'deepseek-flash',max_output_tokens=1200)
    del key
    runs=[]
    with tempfile.TemporaryDirectory(prefix='mu-intake-') as directory:
        for task in TASKS:
            root=Path(directory)/task['id'];root.mkdir()
            for name,content in task['initial'].items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
            try:run=collect(model,task,root,args.output/'samples.jsonl')
            except Exception as error:run={'task':task['id'],'failure':type(error).__name__}
            save_new(args.output/(task['id']+'.json'),run);runs.append(run)
            print(json.dumps({'task':task['id'],'calls':len(run.get('proposed_mutations',[])),'failure':run.get('failure')}),flush=True)
    samples=load_samples(args.output/'samples.jsonl') if (args.output/'samples.jsonl').exists() else []
    matched=[]
    for run in runs:
        selected=[s for s in samples if s['group_id']==run['task']]
        calls=run.get('proposed_mutations',[])
        if len(selected)!=len(calls):
            matched.append({'task':run['task'],'issues':['sample_call_count_mismatch']});continue
        for sample,call in zip(selected,calls):
            matched.append({'task':run['task'],'sample_id':sample['sample_id'],
                            'state_digest':digest(sample['decision']['state']),'issues':contract_findings(sample,call)})
    save_new(args.output/'intake-audit.json',{'tasks':len(TASKS),'samples':len(samples),'matches':matched,
             'failures':[r for r in runs if 'failure' in r],'source':'teacher_actor_on_authored_fixtures',
             'real_user_project_traces':0,'human_gold':0,'candidate_inference':False,'ready_for_training':False})
    save_new(args.output/'fixtures.json',TASKS)

if __name__=='__main__':main()
