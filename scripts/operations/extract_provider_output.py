#!/usr/bin/env python3
"""Copy one passed machine-readable provider result by stable gate ID."""
from __future__ import annotations
import argparse,json,shutil,sys
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker
ROOT=Path(__file__).resolve().parents[2]
RESULT_SCHEMA=ROOT/'contracts/operations/integration-result-v0.schema.json'
def load(p:Path)->Any:return json.loads(p.read_text(encoding='utf-8'))
def main()->int:
 ap=argparse.ArgumentParser();ap.add_argument('--integration-result',type=Path,required=True);ap.add_argument('--gate-id',required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
 result=load(args.integration_result);schema_errors=list(Draft202012Validator(load(RESULT_SCHEMA),format_checker=FormatChecker()).iter_errors(result))
 if schema_errors:
  print(f'ERROR provider-output: invalid integration result: {schema_errors[0].message}',file=sys.stderr);return 65
 matches=[item for item in result.get('provider_results',[]) if item.get('gate_id')==args.gate_id]
 if len(matches)!=1:
  print(f'ERROR provider-output: expected one result for {args.gate_id}, got {len(matches)}',file=sys.stderr);return 1
 item=matches[0]
 if item.get('status')!='passed' or not item.get('output_ref'):
  print(f"ERROR provider-output: gate {args.gate_id} is not passed with output",file=sys.stderr);return 1
 source=Path(item['output_ref']);source=(ROOT/source).resolve() if not source.is_absolute() else source.resolve()
 try:source.relative_to((ROOT/'.artifacts').resolve())
 except ValueError:
  print('ERROR provider-output: provider output must remain under .artifacts evidence directory',file=sys.stderr);return 1
 if source.suffix.lower()!='.json' or not source.is_file():
  print('ERROR provider-output: provider output is not a JSON file',file=sys.stderr);return 1
 load(source)
 args.output.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,args.output)
 print(f'extracted provider output {args.gate_id}: {args.output}')
 return 0
if __name__=='__main__':raise SystemExit(main())
