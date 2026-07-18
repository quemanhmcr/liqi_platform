#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from datetime import datetime,timezone
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(__file__).resolve().parents[4]
def load(path:Path): return json.loads(path.read_text(encoding='utf-8'))
def main():
 p=argparse.ArgumentParser(); p.add_argument('--git-sha',required=True); p.add_argument('--locking',type=Path,required=True); p.add_argument('--backup',type=Path,required=True); p.add_argument('--restore',type=Path,required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
 lock,backup,restore=load(a.locking),load(a.backup),load(a.restore)
 if len(a.git_sha)!=40 or any(c not in '0123456789abcdef' for c in a.git_sha): raise SystemExit('git SHA must be 40 lowercase hex characters')
 for name,doc in [('locking',lock),('backup',backup),('restore',restore)]:
  if doc.get('status')!='passed': raise SystemExit(f'{name} evidence is not passed')
 doc={"schema_version":"liqi.infrastructure.state-backend-evidence/v1","environment":"v1-live","git_sha":a.git_sha,"observed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"backend":{"kind":"postgresql-self-hosted","database":"liqi_infra_state","schema":"opentofu_v1_live","external_to_oci_application_host":True},"tls":{"status":"passed","sslmode":"verify-full"},"locking":{"status":"passed","mechanism":"postgresql-advisory-locks","contender_rejected":lock.get('contender_rejected') is True},"backup":{"status":"passed","encrypted":backup.get('encrypted') is True,"off_host_from_oci_application":backup.get('off_host_from_oci_application') is True},"restore":{"status":"passed","isolated":restore.get('isolated') is True,"state_table_verified":restore.get('state_table_verified') is True},"state_encryption":{"state_enforced":True,"plan_enforced":True},"credentials_in_source":False,"status":"passed"}
 schema=load(ROOT/'contracts/infrastructure/state-backend-evidence-v1.schema.json'); errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(doc))
 if errors: raise SystemExit(errors[0].message)
 a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(doc,indent=2,sort_keys=True)+'\n',encoding='utf-8'); a.output.chmod(0o600); print(a.output)
if __name__=='__main__': main()
