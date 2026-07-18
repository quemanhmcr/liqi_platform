#!/usr/bin/env python3
"""Validate provider-owned isolated restore/PITR evidence for exact release use."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'operations/bin'))
from readiness_v1_common import load_json, validate_document  # noqa: E402
SCHEMA=ROOT/'contracts/readiness/recovery-result-v1.schema.json'
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--result',type=Path,required=True);p.add_argument('--git-sha',required=True);p.add_argument('--release-id',required=True);p.add_argument('--environment',choices=('staging','production'),required=True);a=p.parse_args()
 try:doc=load_json(a.result.resolve())
 except Exception as exc:print(f'ERROR restore-result: {exc}',file=sys.stderr);return 65
 errors=validate_document(SCHEMA,doc,'recovery')
 for key,expected in [('git_sha',a.git_sha),('release_id',a.release_id),('environment',a.environment)]:
  if doc.get(key)!=expected:errors.append(f'{key} mismatch')
 if doc.get('status')!='passed' or doc.get('evidence_mode')!='live':errors.append('restore evidence must be passed and live')
 if doc.get('mutations',{}).get('source_database_mutated') is not False:errors.append('source database must not be mutated')
 if doc.get('cleanup',{}).get('status')!='passed':errors.append('cleanup must pass')
 if errors:
  for error in errors:print(f'ERROR restore-result: {error}',file=sys.stderr)
  return 1
 print('validated exact-release isolated restore/PITR evidence')
 return 0
if __name__=='__main__':raise SystemExit(main())
