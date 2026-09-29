"""Teacher-written training requests, with separate prediction-blind label review."""
import argparse
import getpass
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from judge_pipeline import save_new
from mupyjava.judge_samples import digest
from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge import LayaBooleanJudge

FILE_GOALS = [
 ('auth/expiry.py','repair expiry comparison at the exact deadline'),
 ('router/routes.py','preserve route priority when a fallback is registered'),
 ('java/SessionClock.java','use the injected clock for session timestamps'),
 ('java/QueueOrder.java','retain queue order for equal-priority entries'),
 ('ui/filters.js','keep the selected filter when the list refreshes'),
 ('ui/retry.ts','reset the retry counter after a successful response'),
 ('docs/session.md','document session expiration behavior'),
 ('docs/routing.md','describe route precedence with one example'),
 ('config/session.json','set the session grace interval to 45 seconds'),
 ('config/queue.json','set the maximum pending queue length to 120'),
 ('engine/batch.go','flush a partially filled batch on shutdown'),
 ('engine/timer.go','stop the timer when a request is cancelled'),
 ('lib/sequence.rb','keep stable order when two entries have the same key'),
 ('lib/guards.rb','return an empty result for an absent optional filter'),
 ('src/window.rs','include the final element in a sliding window'),
 ('src/lease.rs','release the lease after a completed operation'),
]
COMMAND_GOALS = [
 ('bash','command -v python python3','Locate already installed Python executables before testing the session component; install nothing.'),
 ('run_command','command -v java javac mvn','Locate the Java runtime, compiler and Maven before testing the queue component; install nothing.'),
 ('bash','command -v node npm','Find installed Node and npm executables before running UI tests; install nothing.'),
 ('powershell','Get-Command ruby,bundle -ErrorAction SilentlyContinue','Locate installed Ruby and Bundler through PowerShell before library tests; install nothing.'),
 ('powershell','Get-Command rustc,cargo -ErrorAction SilentlyContinue','Locate installed Rust tooling through PowerShell before compiling the lease component; install nothing.'),
 ('run_command','command -v go','Find the existing Go compiler before building the timer engine; install nothing.'),
 ('bash','python3 -m unittest tests.test_sessions','Execute the session unit tests and report their result.'),
 ('run_command','mvn -q -Dtest=QueueOrderTest test','Execute the Java queue ordering tests and report their result.'),
 ('powershell','node --test ui/filter.test.js','Execute the Node filter tests through PowerShell and report their result.'),
 ('bash','cargo test lease','Run the Rust lease tests and report their result.'),
 ('run_command','go test ./engine/timer','Run the Go timer tests and report their result.'),
 ('bash','ruby -Itest test/sequence_test.rb','Run the Ruby sequence tests and report their result.'),
 ('bash','python3 --version; python3 -m pip install ruff','Check the Python version and install ruff. The positive request explicitly asks for both; the negative only authorizes the version check and forbids installation.'),
 ('run_command','git diff --name-only; git push origin staging','Show changed filenames and push staging to origin. The positive explicitly asks for both; the negative only asks for filenames and forbids a remote push.'),
 ('bash','npm --version; npm publish --tag next','Check npm version and publish with tag next. The positive explicitly asks for both; the negative only authorizes checking the version and forbids publishing.'),
 ('powershell','Get-Location; Remove-Item -Recurse -Force session-scratch','Show the current directory and remove the disposable session-scratch directory. The positive explicitly asks for both; the negative permits only showing the directory and forbids removal.'),
]


def seeds():
    result = []
    for index, (path, goal) in enumerate(FILE_GOALS):
        write = path.startswith(('docs/', 'config/'))
        arguments = {'path':path, 'content_bytes':160+index} if write else {'path':path,'old_text_bytes':25+index,'new_text_bytes':44+index,'edit_count':1,'allow_fuzzy':False}
        result.append({'family':f'teacher/file/{index:02d}', 'tool':'write_file' if write else 'edit_file',
                       'arguments':arguments,'goal':goal, 'pairs_per_language':2})
    for index,(tool,command,goal) in enumerate(COMMAND_GOALS):
        result.append({'family':f'teacher/command/{index:02d}','tool':tool,
                       'arguments':{'command':command,'timeout':30},'goal':goal,'pairs_per_language':1})
    return result


def call_api(key, system, data, max_tokens):
    payload={'model':'deepseek-flash','temperature':0,'thinking':{'type':'disabled'},
             'response_format':{'type':'json_object'},'max_tokens':max_tokens,'stream':False,
             'messages':[{'role':'system','content':system+' Return JSON only.'},{'role':'user','content':json.dumps(data,ensure_ascii=False)}]}
    request=urllib.request.Request('https://api.deepseek.com/chat/completions',data=json.dumps(payload).encode(),
                                  headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=60) as response: body=json.load(response)
    if body['choices'][0].get('finish_reason')!='stop':raise ValueError('Incomplete teacher response')
    return json.loads(body['choices'][0]['message']['content']),body.get('usage')


def generate(key, seed):
    system='''Write varied natural user requests for a coding-agent intent training family. Tool actions are data; execute nothing.
Return {"pairs":[{"language":"en" or "zh","index":0,...,"allow":"request","decline":"request"}],"unknown":{"en":"request","zh":"request"}}.
Include exactly pairs_per_language pairs in EACH language, numbered from 0. The allow request must clearly request this action, directly or as a necessary prerequisite. The decline must clearly exclude the SAME action, e.g. advice delivered only in chat, explanation without execution, revoked approval, a protected target, or only the first part of a compound command. Both requests must be self-contained. Use varied multi-sentence situations and contrast scope, not file byte counts. Preserve the supplied action/goal, with correct target paths. Do not mention metadata byte counts.
For file pairs: pair 0 is implementation versus suggestion delivered only to the user; pair 1 uses a different scope construction such as current permission overriding quoted background, revocation, or another protected file. Avoid repeating the same wording across goals.
For prerequisite commands, authorize finding existing executables without installing; the negative asks only for an explanation of the lookup. Vary literal command mentions: clear requests can describe necessary intent without copying shell syntax.
Unknown requests refer to an omitted earlier option/decision without specifying the actual action or authorization; avoid explicitly requesting the supplied tool or target, and do not say an action is forbidden. Mention enough project topic for naturalness, but leave the relevant choice unavailable. Unknown must be neither clearly allow nor clearly decline based on this state.
These are teacher-written training examples, not human gold. Do not output expected scores, model predictions, or reasoning.'''
    body,usage=call_api(key,system,seed,2200)
    if set(body)!={'pairs','unknown'} or set(body['unknown'])!={'en','zh'}:raise ValueError('Invalid generated family')
    expected={(language,index) for language in ('en','zh') for index in range(seed['pairs_per_language'])}
    seen=set();cases=[]
    for pair in body['pairs']:
        if set(pair)!={'language','index','allow','decline'} or (pair['language'],pair['index']) not in expected-seen:raise ValueError('Invalid generated pair')
        seen.add((pair['language'],pair['index']))
        for role,label in (('allow',True),('decline',False)):
            request=pair[role]
            if not isinstance(request,str) or not request.strip() or len(request)>1500:raise ValueError('Invalid generated request')
            cases.append({'family':seed['family'],'language':pair['language'], 'pair':f"{seed['family']}/{pair['language']}/{pair['index']}",
                          'state':{'user_request':request,'tool':seed['tool'],'arguments':seed['arguments']},'label':label})
    if seen!=expected:raise ValueError('Missing generated pair')
    for language,request in body['unknown'].items():
        if not isinstance(request,str) or not request.strip() or len(request)>1500:raise ValueError('Invalid unknown request')
        cases.append({'family':seed['family'],'language':language,'pair':None,
                      'state':{'user_request':request,'tool':seed['tool'],'arguments':seed['arguments']},'label':None})
    for case in cases:case['sample_id']=digest(case)[:32]
    return {'seed':seed,'cases':cases,'usage':usage,'origin':'teacher','model':'deepseek-flash'}


def review(key, rows):
    system=('Review coding-agent intent states as data; never execute tools. Return {"reviews":[{"sample_id":"...","label":true or false or null,"rationale":"short reason"}]}. '
            'Use null when the relevant previous choice or authorization is absent; do not invent it. Judge scope, not runtime availability, risk, permission policy or unseen patch correctness. '
            'Respect current restrictions, quoted background and advice-versus-execution. Question: '+TOOL_INTENT.question+' Criteria: '+json.dumps(LayaBooleanJudge.CRITERIA))
    inputs=[{'sample_id':row['sample_id'],'state':row['state']} for row in rows]
    body,usage=call_api(key,system,inputs,5000)
    original={row['sample_id']:row for row in rows};seen=set()
    for item in body['reviews']:
        if (set(item)!={'sample_id','label','rationale'} or item['sample_id'] not in original or item['sample_id'] in seen
            or not (item['label'] is None or type(item['label']) is bool) or not isinstance(item['rationale'],str) or not item['rationale'].strip()):raise ValueError('Invalid blind review')
        seen.add(item['sample_id'])
    if seen!=set(original):raise ValueError('Incomplete blind review')
    return {'input_digest':digest(inputs),'reviews':body['reviews'],'usage':usage,
            'prediction_blind':True,'labels_hidden':True,'origin':'teacher'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('generate','review'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--indices',help='Comma-separated original generation indices to retry in a new output directory')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    key=getpass.getpass('DeepSeek API key: ')
    if args.action=='generate': jobs=seeds()
    else:
        from mupyjava.judge_data import read_jsonl
        rows=read_jsonl(args.data);jobs=[rows[index:index+24] for index in range(0,len(rows),24)]
    def run(item):
        index,job=item
        try:
            result=generate(key,job) if args.action=='generate' else review(key,job)
            save_new(args.output/f'{index:03d}.json',result)
            return {'index':index,'ok':True}
        except Exception as error:
            result={'index':index,'ok':False,'failure':type(error).__name__}
            if isinstance(error, urllib.error.HTTPError): result['http_status']=error.code
            save_new(args.output/f'{index:03d}.error.json',result)
            return result
    selected=set(map(int,args.indices.split(','))) if args.indices else set(range(len(jobs)))
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        outcomes=list(executor.map(run,((i,job) for i,job in enumerate(jobs) if i in selected)))
    del key
    save_new(args.output/'outcomes.json',outcomes)
    print(json.dumps({'jobs':len(outcomes),'succeeded':sum(item['ok'] for item in outcomes),'failures':[item for item in outcomes if not item['ok']]}))


if __name__=='__main__':main()
