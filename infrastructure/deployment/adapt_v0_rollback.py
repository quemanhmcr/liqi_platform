#!/usr/bin/env python3
"""Retain an already-installed V0 Rust release as a V1 rollback target."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

ROOT=Path(__file__).resolve().parents[2]
SEARCH_ROOTS=(ROOT/"contracts",Path("/usr/local/share/liqi/contracts"))

def contract(relative:str)->Path:
    for root in SEARCH_ROOTS:
        path=root/relative
        if path.is_file(): return path
    raise FileNotFoundError(relative)

def load(path:Path)->Any: return json.loads(path.read_text(encoding="utf-8"))
def sha(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
def errors(schema:Path,doc:Any)->list[str]:
    return [f"{'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in sorted(Draft202012Validator(load(schema),format_checker=FormatChecker()).iter_errors(doc),key=lambda i:list(i.absolute_path))]

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--release-manifest",required=True,type=Path); p.add_argument("--deployment-spec",required=True,type=Path); p.add_argument("--health-target",required=True,type=Path)
    p.add_argument("--release-path",required=True,type=Path); p.add_argument("--recovery-root",type=Path,default=Path("/var/lib/liqi/recovery"))
    p.add_argument("--approval-reference"); p.add_argument("--execute",action="store_true"); p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    manifest=load(a.release_manifest); spec=load(a.deployment_spec); health=load(a.health_target)
    failures=[]
    failures+=errors(contract("operations/release-manifest-v0.schema.json"),manifest)
    failures+=errors(contract("operations/deployment-spec-v0.schema.json"),spec)
    failures+=errors(contract("operations/health-gate-target-v0.schema.json"),health)
    release_id=manifest.get("release_id")
    if spec.get("release_id")!=release_id or health.get("release_id")!=release_id: failures.append("V0 release ID mismatch")
    if spec.get("git_sha")!=manifest.get("git_sha"): failures.append("V0 Git SHA mismatch")
    if Path(spec.get("target",{}).get("release_path","/invalid"))!=a.release_path: failures.append("V0 installed release path does not match deployment spec")
    if not a.release_path.is_dir(): failures.append("V0 installed release directory is missing")
    for artifact in spec.get("artifacts",[]):
        path=a.release_path/"bin"/artifact["name"]
        if not path.is_file(): failures.append(f"V0 artifact missing: {path}")
    if failures:
        for failure in failures: print(f"ERROR v0-retain: {failure}",file=os.sys.stderr)
        return 1
    if a.execute and (os.name!="posix" or os.geteuid()!=0): raise SystemExit("V0 retention mutation requires root on POSIX")
    if a.execute and (not a.approval_reference or len(a.approval_reference.strip())<3): raise SystemExit("V0 retention mutation requires approval reference")

    input_root=a.recovery_root/"release-inputs"
    descriptor_path=a.recovery_root/"release-targets"/f"{release_id}.json"
    retained_manifest=input_root/f"{release_id}.release-manifest-v0.json"
    retained_spec=input_root/f"{release_id}.deployment-spec-v0.json"
    retained_health=input_root/f"{release_id}.health-target-v0.json"
    health_output=a.recovery_root/"health"/f"{release_id}.json"
    migration=manifest["database_migration"]
    descriptor={
      "schema_version":"liqi.deployment.release-target/v1","release_id":release_id,"runtime_generation":"rust-v0","git_sha":manifest["git_sha"],"release_path":str(a.release_path),
      "source_manifest":{"schema_version":manifest["schema_version"],"sha256":sha(a.release_manifest),"retained_path":str(retained_manifest)},
      "services":[{"unit":item["unit"],"start_order":item["start_order"],"stop_timeout_seconds":item["stop_timeout_seconds"]} for item in spec["services"]],
      "drain":{"argv":None,"timeout_seconds":1},
      "health":{"argv":["/usr/bin/python3","/usr/local/lib/liqi-v0/scripts/release/health_gate.py","--target",str(retained_health),"--output",str(health_output)],"timeout_seconds":health["deadline_seconds"]+15},
      "database_compatibility":{"minimum_migration":int(migration["minimum"]),"maximum_migration":int(migration["maximum"]),"rollback_safe_through":int(migration["maximum"]),"database_rollback_allowed":False},
      "rollback_target_release_id":manifest["rollback"]["previous_release_id"],
      "configuration_paths":["/etc/liqi/api.json","/etc/liqi/realtime.json","/etc/liqi/worker.json","/run/liqi/secrets/liqi-api","/run/liqi/secrets/liqi-realtime","/run/liqi/secrets/liqi-worker"],
      "created_at":manifest["source_timestamp"],
    }
    descriptor_errors=errors(contract("deployment/release-target-v1.schema.json"),descriptor)
    if descriptor_errors:
        raise SystemExit("invalid V0 release descriptor: "+"; ".join(descriptor_errors))
    status="validated"
    if a.execute:
        verify_units = ["/etc/systemd/system/" + item["unit"] for item in spec["services"]]
        verify = subprocess.run(
            ["systemd-analyze", "verify", *verify_units],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        if verify.returncode:
            raise RuntimeError(verify.stderr.strip() or verify.stdout.strip() or "V0 systemd verification failed")
        for directory in (input_root,descriptor_path.parent,health_output.parent): directory.mkdir(parents=True,exist_ok=True)
        for source,target in ((a.release_manifest,retained_manifest),(a.deployment_spec,retained_spec),(a.health_target,retained_health)):
            shutil.copyfile(source,target); os.chmod(target,0o440)
        descriptor_path.write_text(json.dumps(descriptor,indent=2,sort_keys=True)+"\n",encoding="utf-8"); os.chmod(descriptor_path,0o440); status="retained"
    result={"schema_version":"liqi.deployment.v0-rollback-retention-result/v1","release_id":release_id,"git_sha":manifest["git_sha"],"status":status,"descriptor_sha256":hashlib.sha256((json.dumps(descriptor,indent=2,sort_keys=True)+"\n").encode()).hexdigest(),"approval_reference":a.approval_reference if a.execute else None,"observed_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"mutation_performed":a.execute}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
