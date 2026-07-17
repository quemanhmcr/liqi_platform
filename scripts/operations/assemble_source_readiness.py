#!/usr/bin/env python3
"""Compose source provider, compatibility and capacity evidence into one readiness result."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator,FormatChecker

ROOT=Path(__file__).resolve().parents[2]
PROVIDER_SCHEMA=ROOT/'contracts/operations/integration-result-v0.schema.json'
COMPAT_SCHEMA=ROOT/'contracts/operations/provider-compatibility-result-v0.schema.json'
CAPACITY_SCHEMA=ROOT/'contracts/operations/capacity-result-v0.schema.json'
RESULT_SCHEMA=ROOT/'contracts/operations/integration-readiness-result-v0.schema.json'
CAPACITY_REGISTRY=ROOT/'operations/capacity/provider-capacity-registry-v0.json'

def load(path:Path)->Any:return json.loads(path.read_text(encoding='utf-8'))
def errors(schema:Path,document:Any,label:str)->list[str]:
 return [f"{label}.{'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in sorted(Draft202012Validator(load(schema),format_checker=FormatChecker()).iter_errors(document),key=lambda x:list(x.absolute_path))]
def ref(path:Path)->str:
 resolved=path.resolve()
 try:return resolved.relative_to(ROOT.resolve()).as_posix()
 except ValueError:return resolved.as_posix()
def blocker(owner:str,seam:str,code:str,severity:str,message:str,action:str)->dict[str,str]:
 return {'owner':owner,'seam':seam,'code':code,'severity':severity,'message':message[:1000],'action_required':action[:1000]}
def overall(statuses:list[str])->str:return 'failed' if 'failed' in statuses else 'blocked' if 'blocked' in statuses else 'passed'

def main()->int:
 ap=argparse.ArgumentParser();ap.add_argument('--provider-result',type=Path,required=True);ap.add_argument('--compatibility-result',type=Path,required=True);ap.add_argument('--capacity-result',type=Path,required=True);ap.add_argument('--capacity-registry',type=Path,default=CAPACITY_REGISTRY);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--allow-blocked',action='store_true');args=ap.parse_args()
 documents={
  'provider-source':(args.provider_result,PROVIDER_SCHEMA),
  'provider-compatibility':(args.compatibility_result,COMPAT_SCHEMA),
  'provider-capacity':(args.capacity_result,CAPACITY_SCHEMA),
 }
 loaded={};schema_failures=[]
 for name,(path,schema) in documents.items():
  try:loaded[name]=load(path);schema_failures.extend(errors(schema,loaded[name],name))
  except (OSError,json.JSONDecodeError) as exc:schema_failures.append(f'{name}: cannot read evidence: {exc}')
 if schema_failures:
  git_sha='0'*40
  try:git_sha=str(loaded.get('provider-source',{}).get('git_sha',git_sha))
  except Exception:pass
  result={'schema_version':'integration-readiness-result-v0','git_sha':git_sha,'status':'failed','checkpoints':[{'name':name,'status':'failed','evidence_ref':ref(path)} for name,(path,_) in documents.items()],'blockers':[blocker('Senior 4','source readiness evidence validation','READINESS_INPUT_INVALID','failed','; '.join(schema_failures),'Repair the owning evidence producer or schema; do not bypass readiness composition.')]}
 else:
  provider=loaded['provider-source'];compat=loaded['provider-compatibility'];capacity=loaded['provider-capacity']
  statuses=[provider['overall_status'],compat['overall_status'],capacity['status']]
  blocks=[]
  for item in provider.get('violations',[]):
   severity='failed' if item.get('code') in {'PROVIDER_GATE_FAILED','PROVIDER_GATE_TIMEOUT'} else 'blocked'
   blocks.append(blocker(item['owner'],item['seam'],item['code'],severity,item['message'],item['action_required']))
  for item in compat.get('checks',[]):
   if item['status']!='passed':blocks.append(blocker(item['owner'],item['seam'],item['code'],item['status'],item['message'],item['action_required']))
  registry=load(args.capacity_registry)
  records={record['provider']:record for record in registry.get('providers',[])}
  if capacity['status']=='blocked':
   for record in records.values():
    path=(ROOT/record['path']).resolve()
    if record['state']!='available' or not path.is_file():
     blocks.append(blocker(record['owner'],f"{record['provider']} capacity budget",'PROVIDER_CAPACITY_UNAVAILABLE','blocked',f"capacity budget unavailable at {record['path']} (state={record['state']})",record['action_required']))
  if capacity['status']=='failed':
   for message in capacity.get('failures',[]):
    matched=None
    for provider_name,record in records.items():
     if provider_name in message or record['path'] in message:
      matched=record;break
    if matched:
     blocks.append(blocker(matched['owner'],f"{matched['provider']} capacity budget",'PROVIDER_CAPACITY_INVALID','failed',message,matched['action_required']))
    else:
     blocks.append(blocker('Senior 4','capacity envelope aggregation','CAPACITY_ENVELOPE_FAILED','failed',message,'Coordinate the provider budget that consumes shared headroom; do not increase the envelope silently.'))
  status=overall(statuses)
  # Deduplicate while preserving deterministic order.
  unique={}
  for item in blocks:unique[(item['owner'],item['seam'],item['code'],item['message'])]=item
  blocks=sorted(unique.values(),key=lambda x:(x['severity'],x['owner'],x['code'],x['seam'],x['message']))
  if status=='passed' and blocks:
   status='failed';blocks.append(blocker('Senior 4','source readiness semantics','READINESS_STATUS_INCONSISTENT','failed','all checkpoints passed but blockers remain','Repair readiness composition invariants.'))
  result={'schema_version':'integration-readiness-result-v0','git_sha':provider['git_sha'],'status':status,'checkpoints':[
   {'name':'provider-source','status':provider['overall_status'],'evidence_ref':ref(args.provider_result)},
   {'name':'provider-compatibility','status':compat['overall_status'],'evidence_ref':ref(args.compatibility_result)},
   {'name':'provider-capacity','status':capacity['status'],'evidence_ref':ref(args.capacity_result)},
  ],'blockers':blocks}
 output_errors=errors(RESULT_SCHEMA,result,'readiness_result')
 if result['status']=='passed' and result['blockers']:output_errors.append('passed readiness cannot contain blockers')
 if result['status']!='passed' and not result['blockers']:output_errors.append('blocked/failed readiness must contain blockers')
 if output_errors:
  for message in output_errors:print('ERROR source-readiness:',message,file=sys.stderr)
  return 65
 args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
 print(f"source integration readiness {result['status']}: {args.output}")
 if result['status']=='failed':return 1
 if result['status']=='blocked' and not args.allow_blocked:return 2
 return 0
if __name__=='__main__':raise SystemExit(main())
