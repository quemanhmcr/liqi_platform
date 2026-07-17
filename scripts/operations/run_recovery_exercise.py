#!/usr/bin/env python3
"""Plan or execute a bounded provider-owned recovery exercise."""
from __future__ import annotations
import argparse,json,os,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator,FormatChecker

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.operations.redaction import redact
PLAN_SCHEMA=ROOT/'contracts/operations/recovery-exercise-plan-v0.schema.json'
RESULT_SCHEMA=ROOT/'contracts/operations/recovery-exercise-result-v0.schema.json'
RECOVERY_SCHEMA=ROOT/'contracts/operations/recovery-status-v0.schema.json'
FRESHNESS=ROOT/'scripts/operations/check_recovery_freshness.py'

def now()->str:return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def load(p:Path)->Any:return json.loads(p.read_text(encoding='utf-8'))
def errors(schema:Path,doc:Any,label:str)->list[str]:return [f"{label}.{'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in sorted(Draft202012Validator(load(schema),format_checker=FormatChecker()).iter_errors(doc),key=lambda x:list(x.absolute_path))]
def safe_repo_path(value:str)->Path:
    p=(ROOT/value).resolve(); p.relative_to(ROOT.resolve()); return p

def expand(argv:list[str],values:dict[str,str])->list[str]:
    out=[]
    for part in argv:
        for key,value in values.items(): part=part.replace('{'+key+'}',value)
        if '{' in part or '}' in part: raise ValueError(f'unresolved command placeholder: {part}')
        out.append(part)
    return out

def run(argv:list[str],timeout:int,log:Path)->tuple[int,int,str|None]:
    started=time.monotonic()
    try:
        c=subprocess.run(argv,cwd=ROOT,text=True,capture_output=True,timeout=timeout,check=False)
        rendered=redact((c.stdout or '')+'\n'+(c.stderr or ''))[-16384:]
        log.write_text(rendered,encoding='utf-8',newline='\n')
        err=None if c.returncode==0 else (redact(c.stderr or c.stdout or 'provider command failed')[-1000:])
        return c.returncode,int((time.monotonic()-started)*1000),err
    except subprocess.TimeoutExpired as exc:
        log.write_text(redact(((exc.stdout or '') if isinstance(exc.stdout,str) else '')+'\ncommand timed out')[-16384:],encoding='utf-8',newline='\n')
        return 124,int((time.monotonic()-started)*1000),'provider command timed out'

def write(path:Path,result:dict[str,Any])->None:
    result['completed_at']=now(); failures=errors(RESULT_SCHEMA,result,'result')
    if failures: raise RuntimeError('; '.join(failures))
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--plan',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--evidence-dir',type=Path,required=True); ap.add_argument('--approval-ref'); ap.add_argument('--execute',action='store_true'); ap.add_argument('--allow-mock',action='store_true'); ap.add_argument('--target-root-override',type=Path); args=ap.parse_args()
    plan=load(args.plan); started=now(); plan_errors=errors(PLAN_SCHEMA,plan,'plan')
    mode=plan.get('mode','provider'); target=plan.get('target',{}); target_root=str(args.target_root_override.resolve()) if args.target_root_override else target.get('root','')
    step_names=[step.get('name') for step in plan.get('steps',[])]
    if step_names!=['prepare','restore','verify','cleanup']: plan_errors.append(f'recovery steps must be exactly prepare, restore, verify, cleanup; got {step_names}')
    result={
      'schema_version':'recovery-exercise-result-v0','exercise_id':plan.get('exercise_id','rx-invalid'),'mode':mode,'source_environment':plan.get('source_environment','development'),'target_database':target.get('database','liqi_restore_invalid'),'started_at':started,'completed_at':started,'status':'blocked','approval_ref':args.approval_ref,
      'steps':[],'verification':{'status':'not-run','provider_result_ref':None,'freshness_result_ref':None},'cleanup':{'required':True,'status':'not-run'},
      'mutation':{'isolated_target_mutated':False,'source_database_mutated':False,'production_traffic_changed':False,'oci_mutated':False},'failures':[]}
    values={'python':sys.executable,'exercise_id':plan.get('exercise_id',''),'source_environment':plan.get('source_environment',''),'target_root':target_root,'target_database':target.get('database',''),'backup_ref':plan.get('backup_ref',''),'required_database_migration':plan.get('required_database_migration',''),'verification_output':str((args.evidence_dir/'recovery-status.json').resolve())}
    resolved=[]
    for step in plan.get('steps',[]):
        try: argv=expand(step['argv'],values)
        except Exception as exc: plan_errors.append(str(exc)); argv=step.get('argv',[])
        command=' '.join(argv)
        state='planned'; error=None
        if argv:
            command_path=argv[0]
            if Path(command_path).name.lower() in {'python','python.exe','python3','bash','bash.exe','sh','sh.exe'} and len(argv)>1:
                command_path=argv[1]
            allowed_prefix='tests/contract/fixtures/recovery-exercise/' if mode=='mock' else 'database/'
            if not command_path.replace('\\','/').startswith(allowed_prefix): error=f'{mode} provider command must be under {allowed_prefix}'; state='blocked'; plan_errors.append(error)
            else:
                try:
                    if not safe_repo_path(command_path).is_file(): error=f'provider command missing: {command_path}'; state='blocked'; plan_errors.append(error)
                except Exception: error=f'provider command escapes repository: {command_path}'; state='blocked'; plan_errors.append(error)
        resolved.append((step,argv))
        result['steps'].append({'name':step.get('name','prepare'),'owner':'Senior 2','command':command,'status':state,'exit_code':None,'duration_ms':0,'log_ref':None,'error':error})
    if mode=='mock' and not args.allow_mock: plan_errors.append('mock recovery commands require --allow-mock')
    if args.target_root_override and not (mode=='mock' and args.allow_mock): plan_errors.append('target root override is test-only')
    if plan_errors:
        result['failures']=plan_errors[:]; write(args.output,result)
        for f in plan_errors: print('ERROR recovery-exercise:',f,file=sys.stderr)
        return 2
    if not args.execute:
        result['status']='planned'; write(args.output,result); print(f'recovery exercise dry-run passed: {args.output}'); return 0
    if not args.approval_ref or args.approval_ref!=plan.get('approval_ref'):
        result['failures']=['execution requires the exact owner approval_ref in the plan']; write(args.output,result); return 2
    args.evidence_dir.mkdir(parents=True,exist_ok=True)
    provider_result=Path(values['verification_output']); freshness=args.evidence_dir/'recovery-freshness.json'; failed=False
    for index,(step,argv) in enumerate(resolved):
        if step['name']=='cleanup': continue
        log=args.evidence_dir/f"{index+1:02d}-{step['name']}.log"; code,duration,error=run(argv,step['timeout_seconds'],log)
        row=result['steps'][index]; row.update({'status':'passed' if code==0 else 'failed','exit_code':code,'duration_ms':duration,'log_ref':str(log),'error':error})
        if step['mutation']=='isolated-target': result['mutation']['isolated_target_mutated']=True
        if code!=0: result['failures'].append(f"{step['name']} failed: {error}"); failed=True; break
    if not failed:
        verification_errors=[]
        try: verification=load(provider_result); verification_errors=errors(RECOVERY_SCHEMA,verification,'recovery_status')
        except Exception as exc: verification_errors=[f'cannot read provider recovery result: {exc}']
        if verification_errors:
            result['verification']['status']='failed'; result['failures']+=verification_errors; failed=True
        else:
            code,duration,error=run([sys.executable,str(FRESHNESS),'--status',str(provider_result),'--output',str(freshness)],120,args.evidence_dir/'freshness.log')
            result['verification']={'status':'passed' if code==0 else 'failed','provider_result_ref':str(provider_result),'freshness_result_ref':str(freshness)}
            if code!=0: result['failures'].append(f'recovery freshness failed: {error}'); failed=True
    cleanup_index=next(i for i,(step,_) in enumerate(resolved) if step['name']=='cleanup'); cleanup_step,cleanup_argv=resolved[cleanup_index]; cleanup_log=args.evidence_dir/'04-cleanup.log'
    code,duration,error=run(cleanup_argv,cleanup_step['timeout_seconds'],cleanup_log)
    result['steps'][cleanup_index].update({'status':'passed' if code==0 else 'failed','exit_code':code,'duration_ms':duration,'log_ref':str(cleanup_log),'error':error})
    result['cleanup']['status']='passed' if code==0 else 'failed'
    if code!=0: result['failures'].append(f'cleanup failed: {error}'); result['status']='incident'
    elif failed: result['status']='failed'
    else: result['status']='passed'
    write(args.output,result); print(f"recovery exercise {result['status']}: {args.output}")
    return 0 if result['status']=='passed' else 4 if result['status']=='incident' else 1
if __name__=='__main__': raise SystemExit(main())
