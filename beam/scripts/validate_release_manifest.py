#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys, tarfile
from datetime import datetime, timezone
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT=Path(__file__).resolve().parents[2]
SCHEMA=ROOT/'contracts/deployment/mix-release-v1.schema.json'
RESULT_SCHEMA=ROOT/'contracts/runtime/runtime-artifact-result-v1.schema.json'
REQUIRED={'bin/liqi_platform','bin/liqi-health','bin/liqi-drain','releases/start_erl.data'}

def digest(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def check(name:str, ok:bool, checks:list, blockers:list, detail:str):
    checks.append({'name':name,'status':'passed' if ok else 'failed'})
    if not ok: blockers.append(detail)

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--manifest',required=True,type=Path); ap.add_argument('--output',required=True,type=Path); a=ap.parse_args()
    checks=[]; blockers=[]; manifest={}; artifact_sha=None
    try:
        manifest=json.loads(a.manifest.read_text(encoding='utf-8'))
        errors=sorted(Draft202012Validator(json.loads(SCHEMA.read_text()),format_checker=FormatChecker()).iter_errors(manifest),key=lambda e:list(e.path))
        check('manifest-schema',not errors,checks,blockers,'; '.join(e.message for e in errors[:8]) or 'manifest schema invalid')
        artifact=a.manifest.parent/manifest.get('artifact',{}).get('filename','')
        check('artifact-present',artifact.is_file(),checks,blockers,f'artifact missing: {artifact.name}')
        if artifact.is_file():
            artifact_sha=digest(artifact)
            check('artifact-sha256',artifact_sha==manifest['artifact']['sha256'],checks,blockers,'artifact sha256 mismatch')
            with tarfile.open(artifact,'r:gz') as tf:
                members=tf.getmembers()
                names={n.name.lstrip('./') for n in members}
                check('release-layout',all(any(x==r or x.endswith('/'+r) for x in names) for r in REQUIRED),checks,blockers,'release missing provider command or start_erl.data')
                check('erts-included',any('/erts-' in '/'+x or x.startswith('erts-') for x in names),checks,blockers,'release does not include ERTS')
                beam_member=next((m for m in members if m.name.replace('\\','/').endswith('/bin/beam.smp') or m.name.replace('\\','/')=='bin/beam.smp'),None)
                arm64=False
                if beam_member is not None:
                    stream=tf.extractfile(beam_member)
                    header=stream.read(20) if stream else b''
                    if len(header)>=20 and header[:4]==b'\x7fELF':
                        byteorder='little' if header[5]==1 else 'big'
                        arm64=int.from_bytes(header[18:20],byteorder)==183
                check('aarch64-elf',arm64,checks,blockers,'ERTS beam.smp is not an AArch64 ELF binary')
                check('elixir-runtime',any('/lib/elixir-1.20.2/' in '/'+x for x in names),checks,blockers,'release does not contain Elixir 1.20.2')
                check('application-version',any('/lib/liqi_platform-1.0.0-dev/' in '/'+x for x in names),checks,blockers,'release does not contain liqi_platform 1.0.0-dev')
        sig=a.manifest.parent/manifest.get('artifact',{}).get('signature',{}).get('signature_filename','')
        check('signature-file-present',sig.is_file(),checks,blockers,f'signature file missing: {sig.name}')
        if sig.is_file(): check('signature-sha256',digest(sig)==manifest['artifact']['signature']['signature_sha256'],checks,blockers,'signature checksum mismatch')
        rt=manifest.get('runtime',{})
        check('runtime-version',rt.get('otp_release','').startswith('28') and rt.get('elixir_version')=='1.20.2',checks,blockers,'runtime version differs from Senior 1 toolchain')
        commands=rt.get('commands',{})
        check('loopback-control-commands',commands.get('health')==['bin/liqi-health'] and commands.get('drain')==['bin/liqi-drain'],checks,blockers,'health/drain must use release overlay commands')
        db=manifest.get('database_compatibility',{})
        check('database-compatibility',db.get('minimum_migration')==8 and db.get('maximum_migration')==8 and db.get('rollback_safe_through')==4,checks,blockers,'database compatibility must be 8/8 with V0 rollback safe through 4')
    except Exception as exc:
        blockers.append(f'{type(exc).__name__}: {exc}'); checks.append({'name':'verification-exception','status':'failed'})
    status='passed' if checks and not blockers else 'failed'
    result={'schema_version':'runtime-artifact-result-v1','git_sha':manifest.get('git_sha') or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'release_id':manifest.get('release_id','unknown'),'observed_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'status':status,'artifact_sha256':artifact_sha,'checks':checks,'blockers':blockers}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
    result_errors=list(Draft202012Validator(json.loads(RESULT_SCHEMA.read_text()),format_checker=FormatChecker()).iter_errors(result))
    if result_errors:
        print('; '.join(e.message for e in result_errors),file=sys.stderr); return 65
    return 0 if status=='passed' else 1
if __name__=='__main__': raise SystemExit(main())
