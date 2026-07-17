#!/usr/bin/env python3
"""Validate cross-provider seams owned by Senior 4 without reproducing provider logic."""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[2]
SCHEMA=ROOT/'contracts/operations/provider-compatibility-result-v0.schema.json'
POLICY=ROOT/'operations/telemetry/telemetry-runtime-policy-v0.json'

def load(path:Path)->Any:return json.loads(path.read_text(encoding='utf-8'))
def check(owner:str,seam:str,status:str,code:str,message:str,action:str)->dict[str,str]:return {'owner':owner,'seam':seam,'status':status,'code':code,'message':message,'action_required':action}
def journal_value(text:str,key:str)->str|None:
    match=re.search(rf'(?m)^\s*{re.escape(key)}\s*=\s*([^\s#]+)',text); return match.group(1) if match else None

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--provider-root',type=Path,default=ROOT); ap.add_argument('--policy',type=Path,default=POLICY); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--allow-missing',action='store_true'); args=ap.parse_args()
    provider=args.provider_root.resolve(); policy=load(args.policy); checks=[]
    cloud=provider/'infrastructure/cloud-init/host-bootstrap.yaml.tftpl'; host=provider/'contracts/platform/oci-host-v0.example.json'
    infra_missing=[p.relative_to(provider).as_posix() for p in (cloud,host) if not p.is_file()]
    if infra_missing:
        checks.append(check('Senior 1','OCI host output and host logging policy','blocked','INFRASTRUCTURE_SEAM_MISSING',f'missing provider paths: {infra_missing}','Senior 1 must merge the published OCI host output and cloud-init source.'))
    else:
        host_doc=load(host); failures=[]
        if host_doc.get('infrastructure_output_version')!='0.2.0':failures.append('infrastructure output version must be 0.2.0')
        release=host_doc.get('release_target',{})
        expected={'staging_path':'/var/tmp/liqi/releases','deployment_path':'/opt/liqi/releases','current_symlink':'/opt/liqi/current','installation_semantics':'upload-to-staging-then-root-owned-atomic-install'}
        for key,value in expected.items():
            if release.get(key)!=value:failures.append(f'release_target.{key} must be {value!r}')
        text=cloud.read_text(encoding='utf-8')
        expected_journal={
          'SystemMaxUse':f"{policy['journald']['system_max_use_mib']//1024}G",
          'SystemKeepFree':f"{policy['journald']['system_keep_free_mib']//1024}G",
          'MaxRetentionSec':f"{policy['journald']['max_retention_seconds']//86400}day",
          'RateLimitIntervalSec':f"{policy['journald']['rate_limit_interval_seconds']}s",
          'RateLimitBurst':str(policy['journald']['rate_limit_burst']),
          'ForwardToSyslog':'no'
        }
        for key,value in expected_journal.items():
            actual=journal_value(text,key)
            if actual!=value:failures.append(f'journald {key} expected {value}, got {actual}')
        checks.append(check('Senior 1','OCI host output and host logging policy','failed' if failures else 'passed','HOST_OPERABILITY_INCOMPATIBLE' if failures else 'HOST_OPERABILITY_COMPATIBLE','; '.join(failures) if failures else 'host output 0.2.0, release target and journald policy are compatible','Senior 1 must align cloud-init journald and host output with contracts/operations and ADR 0408.' if failures else 'none'))
    database=provider/'contracts/platform/database-v0.example.json'
    if not database.is_file():
        checks.append(check('Senior 2','database recovery command ownership','blocked','DATABASE_SEAM_MISSING','database provider contract is not merged','Senior 2 must merge database-v0 and provider-owned recovery commands.'))
    else:
        doc=load(database); restore=doc.get('restore',{}); failures=[]
        for field in ('command','verificationCommand'):
            value=str(restore.get(field,''))
            if not value.startswith('database/'):
                failures.append(f'restore.{field} must be provider-owned under database/**, got {value!r}')
        result_schema=str(restore.get('resultSchema',''))
        if not result_schema.startswith('contracts/platform/database-'):
            failures.append('restore.resultSchema must remain a Senior 2 platform contract')
        checks.append(check('Senior 2','database recovery command ownership','failed' if failures else 'passed','DATABASE_RECOVERY_OWNERSHIP_INVALID' if failures else 'DATABASE_RECOVERY_OWNERSHIP_VALID','; '.join(failures) if failures else 'database recovery commands remain provider-owned','Senior 2 must move or version restore commands under database/**; Senior 4 will not create an operations wrapper.' if failures else 'none'))
    statuses=[item['status'] for item in checks]; overall='failed' if 'failed' in statuses else 'blocked' if 'blocked' in statuses else 'passed'
    result={'schema_version':'provider-compatibility-result-v0','overall_status':overall,'checks':checks}
    errors=list(Draft202012Validator(load(SCHEMA)).iter_errors(result))
    if errors:
        for error in errors:print(f"ERROR provider-compatibility-result: {error.message}",file=sys.stderr)
        return 65
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
    print(f'provider compatibility {overall}: {args.output}')
    if overall=='failed':return 1
    if overall=='blocked' and not args.allow_missing:return 2
    return 0
if __name__=='__main__':raise SystemExit(main())
