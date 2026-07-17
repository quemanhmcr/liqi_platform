#!/usr/bin/env python3
"""Collect provider-owned budgets and invoke the canonical capacity aggregator."""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parents[2]
REGISTRY=ROOT/'operations/capacity/provider-capacity-registry-v0.json'
REGISTRY_SCHEMA=ROOT/'contracts/operations/provider-capacity-registry-v0.schema.json'
RESULT_SCHEMA=ROOT/'contracts/operations/capacity-result-v0.schema.json'
ENVELOPE=ROOT/'operations/capacity/capacity-envelope-v0.json'
AGGREGATOR=ROOT/'scripts/operations/check_capacity.py'
def load(p:Path)->Any:return json.loads(p.read_text(encoding='utf-8'))
def validate(schema:Path,doc:Any)->list[str]:return [f"{'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in sorted(Draft202012Validator(load(schema)).iter_errors(doc),key=lambda x:list(x.absolute_path))]
def main()->int:
 ap=argparse.ArgumentParser();ap.add_argument('--registry',type=Path,default=REGISTRY);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--allow-blocked',action='store_true');args=ap.parse_args()
 registry=load(args.registry);failures=validate(REGISTRY_SCHEMA,registry);providers=registry.get('providers',[]);names=[p.get('provider') for p in providers]
 if set(names)!={'infrastructure','database','runtime','operations'} or len(names)!=4:failures.append('capacity registry must contain each provider exactly once')
 budgets=[];blocked=[]
 for record in providers:
  path=(ROOT/record['path']).resolve()
  try:path.relative_to(ROOT.resolve())
  except ValueError:failures.append(f"{record['owner']} capacity path escapes repository");continue
  if record['state']!='available':blocked.append(f"{record['owner']} {record['provider']}: provider state is {record['state']}; {record['action_required']}")
  elif not path.is_file():blocked.append(f"{record['owner']} {record['provider']}: budget missing at {record['path']}; {record['action_required']}")
  else:budgets.append(path)
 if failures:
  status='failed';messages=failures+blocked
 elif blocked:
  status='blocked';messages=blocked
 else:
  command=[sys.executable,str(AGGREGATOR),*map(str,budgets),'--output',str(args.output)]
  completed=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,check=False)
  if completed.stdout:print(completed.stdout,end='')
  if completed.stderr:print(completed.stderr,end='',file=sys.stderr)
  return completed.returncode
 result={'schema_version':'capacity-result-v0','status':status,'envelope':load(ENVELOPE),'totals':{'ocpu':0.0,'memory_mib':0,'disk_gib':0.0,'postgres_connections':0},'components':[],'failures':messages}
 schema_errors=validate(RESULT_SCHEMA,result)
 if schema_errors:
  for message in schema_errors:print(f'ERROR capacity-result: {message}',file=sys.stderr)
  return 65
 args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
 print(f'provider capacity {status}: {args.output}')
 if status=='failed':return 1
 if status=='blocked' and not args.allow_blocked:return 2
 return 0
if __name__=='__main__':raise SystemExit(main())
