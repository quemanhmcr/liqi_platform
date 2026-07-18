#!/usr/bin/env python3
"""Assemble 15 already-executed resilience scenario results into suite evidence."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'operations/bin'))
from readiness_v1_common import load_json, relative_ref, sha256_file, utc_now, validate_document, write_json  # noqa: E402
SCHEMA=ROOT/'contracts/readiness/resilience-result-v1.schema.json'
SUITE_SCHEMA=ROOT/'contracts/readiness/resilience-suite-result-v1.schema.json'
CATALOG={item['id']:item for item in load_json(ROOT/'operations/resilience/scenario-catalog-v1.json')['scenarios']}
EXPECTED={"postgresql-restart","pgbouncer-unavailable","outbox-backlog","oban-backlog","realtime-slow-consumers","reconnect-storm-25pct","native-artifact-disabled","native-kernel-panic","telemetry-sink-unavailable","disk-pressure","beam-process-crash","actor-supervisor-restart","release-activation-failure","v1-rollback-to-v0","host-reboot"}

def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--git-sha',required=True);p.add_argument('--release-id',required=True);p.add_argument('--environment',choices=('staging','production'),required=True);p.add_argument('--scenario',action='append',default=[],help='id=path');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 paths={}
 for value in a.scenario:
  if '=' not in value: print(f'ERROR scenario must be id=path: {value}',file=sys.stderr);return 64
  ident,raw=value.split('=',1)
  if ident in paths: print(f'ERROR duplicate scenario: {ident}',file=sys.stderr);return 64
  paths[ident]=Path(raw).resolve()
 if set(paths)!=EXPECTED:
  print(f'ERROR scenario set mismatch; missing={sorted(EXPECTED-set(paths))} extra={sorted(set(paths)-EXPECTED)}',file=sys.stderr);return 2
 entries=[];counts={n:0 for n in ['authorization_bypass','secret_exposure','duplicate_durable_identity','event_before_commit','durable_event_loss']};failures=[];started=[];completed=[]
 for ident,path in sorted(paths.items()):
  try: doc=load_json(path)
  except Exception as exc: failures.append(f'{ident}: {exc}');continue
  errors=validate_document(SCHEMA,doc,ident)
  if doc.get('scenario_id')!=ident: errors.append(f'scenario_id must equal {ident}')
  if doc.get('git_sha')!=a.git_sha: errors.append('git_sha mismatch')
  if doc.get('release_id')!=a.release_id: errors.append('release_id mismatch')
  if doc.get('environment')!=a.environment: errors.append('environment mismatch')
  if doc.get('status')!='passed' or doc.get('evidence_mode')!='live': errors.append('scenario must be passed live evidence')
  if errors: failures.extend(f'{ident}: {e}' for e in errors)
  for key,value in doc.get('data_safety',{}).items():
   if key in counts and isinstance(value,int): counts[key]+=value
  started.append(doc.get('started_at'));completed.append(doc.get('completed_at'))
  entries.append({'id':ident,'owner':CATALOG[ident]['injection_owner'],'status':'failed' if errors else 'passed','sha256':sha256_file(path),'evidence_ref':relative_ref(path)})
 result={'schema_version':'resilience-suite-result-v1','evidence_mode':'live','git_sha':a.git_sha,'release_id':a.release_id,'environment':a.environment,'started_at':min(x for x in started if x) if started else utc_now(),'completed_at':max(x for x in completed if x) if completed else utc_now(),'status':'passed' if not failures and all(v==0 for v in counts.values()) else 'failed','scenarios':entries,'correctness_events':counts,'failures':failures}
 errors=validate_document(SUITE_SCHEMA,result,'suite')
 if errors:
  for error in errors: print(f'ERROR suite: {error}',file=sys.stderr)
  return 65
 write_json(a.output.resolve(),result)
 return 0 if result['status']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())
