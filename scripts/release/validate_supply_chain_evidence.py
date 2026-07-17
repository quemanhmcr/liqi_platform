#!/usr/bin/env python3
"""Validate SPDX/SLSA content and bind it to release-manifest-v0 subjects."""
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA=ROOT/'contracts/operations/release-manifest-v0.schema.json'
RESULT_SCHEMA=ROOT/'contracts/operations/supply-chain-result-v0.schema.json'
def load(p:Path)->Any:return json.loads(p.read_text(encoding='utf-8'))
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def validate(schema:Path,doc:Any,label:str)->list[str]:return [f"{label}.{'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in sorted(Draft202012Validator(load(schema),format_checker=FormatChecker()).iter_errors(doc),key=lambda x:list(x.absolute_path))]
def checksum_map(items:list[dict[str,Any]],name_field:str)->dict[str,str]:
 result={}
 for item in items:
  name=Path(str(item.get(name_field,''))).name
  checks=[c for c in item.get('checksums',[]) if c.get('algorithm')=='SHA256']
  if len(checks)==1:result[name]=str(checks[0].get('checksumValue','')).lower()
 return result
def main()->int:
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--sbom',type=Path,required=True);ap.add_argument('--provenance',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
 manifest=load(args.manifest);failures=validate(MANIFEST_SCHEMA,manifest,'manifest');subjects={a['name']:a['sha256'] for a in manifest.get('artifacts',[])};expected={'liqi-api','liqi-realtime','liqi-worker'}
 if set(subjects)!=expected:failures.append('manifest must contain exactly liqi-api, liqi-realtime and liqi-worker')
 sbom=load(args.sbom);sbom_digest=sha(args.sbom)
 if sbom.get('spdxVersion')!='SPDX-2.3':failures.append('SBOM spdxVersion must be SPDX-2.3')
 if sbom.get('SPDXID')!='SPDXRef-DOCUMENT':failures.append('SBOM SPDXID must be SPDXRef-DOCUMENT')
 if sbom.get('dataLicense')!='CC0-1.0':failures.append('SBOM dataLicense must be CC0-1.0')
 namespace=str(sbom.get('documentNamespace',''))
 if manifest.get('release_id') not in namespace:failures.append('SBOM documentNamespace must include release_id')
 creation=sbom.get('creationInfo',{})
 if not creation.get('created') or not creation.get('creators'):failures.append('SBOM creationInfo.created and creators are required')
 sbom_subjects=checksum_map(sbom.get('files',[]),'fileName')
 if sbom_subjects!=subjects:failures.append(f'SBOM binary subjects do not match manifest: {sbom_subjects}')
 lines=[line for line in args.provenance.read_text(encoding='utf-8').splitlines() if line.strip()]
 if len(lines)!=1:failures.append('provenance JSONL must contain exactly one statement');statement={}
 else:
  try:statement=json.loads(lines[0])
  except json.JSONDecodeError as exc:failures.append(f'invalid provenance JSON: {exc}');statement={}
 provenance_digest=sha(args.provenance)
 if statement.get('_type')!='https://in-toto.io/Statement/v1':failures.append('provenance _type must be in-toto Statement/v1')
 if statement.get('predicateType')!='https://slsa.dev/provenance/v1':failures.append('provenance predicateType must be SLSA provenance/v1')
 provenance_subjects={Path(str(x.get('name',''))).name:str(x.get('digest',{}).get('sha256','')).lower() for x in statement.get('subject',[])}
 if provenance_subjects!=subjects:failures.append(f'provenance subjects do not match manifest: {provenance_subjects}')
 predicate=statement.get('predicate',{});definition=predicate.get('buildDefinition',{});details=predicate.get('runDetails',{})
 builder=str(details.get('builder',{}).get('id',''));build_type=str(definition.get('buildType',''))
 dependencies=definition.get('resolvedDependencies',[]);source_sha=None
 for dependency in dependencies:
  digest=dependency.get('digest',{})
  if digest.get('gitCommit'):source_sha=str(digest['gitCommit']).lower();break
 if not builder:failures.append('provenance builder.id is required')
 if not build_type:failures.append('provenance buildDefinition.buildType is required')
 if source_sha!=manifest.get('git_sha'):failures.append(f'provenance source gitCommit {source_sha!r} does not match manifest git_sha')
 if manifest.get('supply_chain',{}).get('sbom',{}).get('sha256')!=sbom_digest:failures.append('manifest SBOM digest does not match supplied SBOM')
 if manifest.get('supply_chain',{}).get('provenance',{}).get('sha256')!=provenance_digest:failures.append('manifest provenance digest does not match supplied statement')
 result={'schema_version':'supply-chain-result-v0','release_id':manifest.get('release_id','liqi-invalid'),'git_sha':manifest.get('git_sha','0'*40),'status':'failed' if failures else 'passed','artifact_subjects':[{'name':n,'sha256':subjects.get(n,'0'*64)} for n in sorted(expected)],'sbom':{'spdx_version':sbom.get('spdxVersion','SPDX-2.3'),'document_namespace':namespace or 'https://invalid.example/','sha256':sbom_digest,'artifact_count':len(sbom_subjects)},'provenance':{'statement_type':statement.get('_type','https://in-toto.io/Statement/v1'),'predicate_type':statement.get('predicateType','https://slsa.dev/provenance/v1'),'builder_id':builder or 'https://invalid.example/','build_type':build_type or 'https://invalid.example/','sha256':provenance_digest,'subject_count':len(provenance_subjects),'source_git_sha':source_sha or '0'*40},'failures':failures}
 result_errors=validate(RESULT_SCHEMA,result,'result')
 if result_errors:
  for error in result_errors:print('ERROR supply-chain-result:',error,file=sys.stderr)
  return 65
 args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
 print(f"supply-chain evidence {result['status']}: {args.output}")
 if failures:
  for failure in failures:print('ERROR supply-chain:',failure,file=sys.stderr)
  return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
