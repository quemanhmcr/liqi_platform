#!/usr/bin/env python3
"""Dry-run, activate, or rollback a retained LIQI release using host-local descriptors."""
from __future__ import annotations
import argparse, json, os, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

ROOT=Path(__file__).resolve().parents[2]
CONTRACT_ROOTS = {
    "deployment": (ROOT / "contracts/deployment", Path("/usr/local/share/liqi/contracts/deployment")),
    "infrastructure": (ROOT / "contracts/infrastructure", Path("/usr/local/share/liqi/contracts/infrastructure")),
}

def utc()->str: return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def load(path:Path)->Any: return json.loads(path.read_text(encoding="utf-8"))
def contract(name: str, namespace: str = "deployment") -> Path:
    for root in CONTRACT_ROOTS[namespace]:
        path = root / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"{namespace}/{name}")

def validate(name: str, doc: Any, namespace: str = "deployment") -> list[str]:
    validator = Draft202012Validator(load(contract(name, namespace)), format_checker=FormatChecker())
    return [
        f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(doc), key=lambda item: list(item.absolute_path))
    ]
def check(name:str,status:str,detail:str,duration_ms:int=0)->dict[str,Any]: return {"name":name,"status":status,"duration_ms":duration_ms,"detail":detail[:2048]}
def rollback_check(name:str,status:str,detail:str)->dict[str,str]: return {"name":name,"status":status,"detail":detail[:2048]}

def run(argv:list[str],timeout:int)->tuple[bool,str,int]:
    started=time.monotonic()
    try:
        result=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=timeout)
        detail=(result.stderr or result.stdout or "")[-2048:].strip()
        return result.returncode==0,detail,int((time.monotonic()-started)*1000)
    except subprocess.TimeoutExpired:
        return False,"command timed out",int((time.monotonic()-started)*1000)

def atomic_symlink(target:Path,link:Path)->None:
    link.parent.mkdir(parents=True,exist_ok=True)
    temporary=link.parent/f".{link.name}.new.{os.getpid()}"
    temporary.unlink(missing_ok=True); temporary.symlink_to(target); os.replace(temporary,link)

def descriptor(path:Path)->dict[str,Any]:
    doc=load(path); failures=validate("release-target-v1.schema.json",doc)
    if failures: raise RuntimeError(f"invalid descriptor {path}: {'; '.join(failures)}")
    return doc

def current_release_id(link:Path)->str:
    if not link.is_symlink(): raise RuntimeError(f"current link is not a symlink: {link}")
    resolved=link.resolve(strict=True)
    if resolved.parent!=Path("/opt/liqi/releases"): raise RuntimeError("current release escapes /opt/liqi/releases")
    return resolved.name

def database_version(path:Path)->int:
    doc=load(path)
    if doc.get("schemaVersion") != "database-readiness-v0" or doc.get("ready") is not True or doc.get("reason") != "ready":
        raise RuntimeError("database readiness provider is not ready/compatible")
    return int(doc["currentVersion"])
def compatibility(target:dict[str,Any],rollback:dict[str,Any],version:int)->None:
    target_db=target["database_compatibility"]; rollback_db=rollback["database_compatibility"]
    if not target_db["minimum_migration"]<=version<=target_db["maximum_migration"]: raise RuntimeError("database version is outside target release range")
    if not rollback_db["minimum_migration"]<=version<=rollback_db["rollback_safe_through"]: raise RuntimeError("database version is not safe for retained rollback target")
    if target_db["database_rollback_allowed"] or rollback_db["database_rollback_allowed"]: raise RuntimeError("database down migration is forbidden")
def configs_ready(doc:dict[str,Any])->None:
    missing=[path for path in doc["configuration_paths"] if not Path(path).exists()]
    if missing: raise RuntimeError(f"required configuration paths missing: {missing}")
def services(doc:dict[str,Any],reverse:bool=False)->list[dict[str,Any]]: return sorted(doc["services"],key=lambda i:i["start_order"],reverse=reverse)
def stop_release(doc:dict[str,Any],systemctl:str)->list[dict[str,Any]]:
    results=[]
    drain=doc["drain"]
    if drain["argv"]:
        ok,detail,duration=run(drain["argv"],drain["timeout_seconds"]); results.append(check("drain-current","passed" if ok else "failed",detail or "drain completed",duration))
        if not ok: raise RuntimeError(f"drain failed: {detail}")
    for item in services(doc,True):
        ok,detail,duration=run([systemctl,"stop",item["unit"]],item["stop_timeout_seconds"]+5); results.append(check("stop-service","passed" if ok else "failed",f"{item['unit']}: {detail or 'stopped'}",duration))
        if not ok: raise RuntimeError(f"stop failed for {item['unit']}: {detail}")
    return results
def start_release(doc:dict[str,Any],systemctl:str)->list[dict[str,Any]]:
    results=[]
    for item in services(doc):
        ok,detail,duration=run([systemctl,"start",item["unit"]],60); results.append(check("start-service","passed" if ok else "failed",f"{item['unit']}: {detail or 'started'}",duration))
        if not ok: raise RuntimeError(f"start failed for {item['unit']}: {detail}")
    ok,detail,duration=run(doc["health"]["argv"],doc["health"]["timeout_seconds"]); results.append(check("health-gate","passed" if ok else "failed",detail or "health passed",duration))
    if not ok: raise RuntimeError(f"health gate failed: {detail}")
    return results

def parse()->argparse.Namespace:
    p=argparse.ArgumentParser(); p.add_argument("mode",choices=("activate","rollback")); p.add_argument("--target-release-id")
    p.add_argument("--deployment-id",required=True); p.add_argument("--descriptor-dir",type=Path,default=Path("/var/lib/liqi/recovery/release-targets")); p.add_argument("--current-link",type=Path,default=Path("/opt/liqi/current"))
    p.add_argument("--host-readiness",required=True,type=Path); p.add_argument("--database-readiness",required=True,type=Path); p.add_argument("--systemctl",default="systemctl"); p.add_argument("--maximum-duration-seconds",type=int,default=300)
    p.add_argument("--approval-reference"); p.add_argument("--execute",action="store_true"); p.add_argument("--activation-output",type=Path); p.add_argument("--rollback-output",type=Path,required=True)
    return p.parse_args()

def main()->int:
    a=parse(); started=utc(); checks=[]; rb_checks=[]
    if not (1<=a.maximum_duration_seconds<=900): raise SystemExit("maximum duration must be 1..900 seconds")
    current_id=current_release_id(a.current_link); current=descriptor(a.descriptor_dir/f"{current_id}.json")
    target_id=a.target_release_id or current.get("rollback_target_release_id")
    if not target_id: raise SystemExit("no rollback target is declared")
    target=descriptor(a.descriptor_dir/f"{target_id}.json")
    if a.mode=="activate" and target["rollback_target_release_id"]!=current_id: raise SystemExit("activation target does not declare the current release as rollback target")
    if a.mode=="rollback" and current["rollback_target_release_id"]!=target_id: raise SystemExit("requested rollback target is not predeclared")
    host = load(a.host_readiness)
    host_failures = validate("host-runtime-v1.schema.json", host, "infrastructure")
    if host_failures:
        raise SystemExit("invalid host readiness evidence: " + "; ".join(host_failures))
    if host.get("status") != "ready":
        raise SystemExit("host readiness is not ready")
    version=database_version(a.database_readiness); compatibility(target,current if a.mode=="activate" else target,version)
    if not Path(target["release_path"]).is_dir() or not Path(current["release_path"]).is_dir(): raise SystemExit("release directory missing")
    configs_ready(target); configs_ready(current)
    checks.extend([check("host-readiness","passed","host readiness passed"),check("database-compatibility","passed",f"migration {version} compatible"),check("rollback-target","passed",f"retained target {target_id}"),check("configuration","passed","target/current configuration paths present")])

    evidence_class="live-approved" if a.execute else "dry-run"
    approval=a.approval_reference if a.execute else None
    if a.execute and (os.name!="posix" or os.geteuid()!=0): raise SystemExit("release mutation requires root on POSIX")
    if a.execute and (not approval or len(approval.strip())<3): raise SystemExit("release mutation requires approval reference")

    rollback_doc={"schema_version":"liqi.deployment.rollback/v1","rollback_id":f"{a.deployment_id}-rollback","from_release_id":current_id,"target_release_id":target_id,"target_git_sha":target["git_sha"],"target_manifest_sha256":target["source_manifest"]["sha256"],"database_compatibility":"compatible","config_compatibility":"compatible","maximum_duration_seconds":a.maximum_duration_seconds,"status":"engineering-complete-evidence-pending","started_at":started,"completed_at":utc(),"approval_reference":approval,"checks":[rollback_check("preflight","passed","rollback target, config and database compatibility validated")],"evidence_class":evidence_class}
    activation_doc=None
    if a.mode=="activate":
        if not a.activation_output: raise SystemExit("activate requires --activation-output")
        activation_doc={"schema_version":"liqi.deployment.activation/v1","deployment_id":a.deployment_id,"release_id":target_id,"git_sha":target["git_sha"],"manifest_sha256":target["source_manifest"]["sha256"],"previous_release_id":current_id,"rollback_target_release_id":current_id,"state":"preflight-passed","status":"engineering-complete-evidence-pending","started_at":started,"completed_at":utc(),"approval_reference":approval,"checks":checks,"traffic_enabled":False,"evidence_class":evidence_class}
    if not a.execute:
        a.rollback_output.parent.mkdir(parents=True,exist_ok=True); a.rollback_output.write_text(json.dumps(rollback_doc,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        if activation_doc:
            a.activation_output.parent.mkdir(parents=True,exist_ok=True); a.activation_output.write_text(json.dumps(activation_doc,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        print("release control dry-run passed; no mutation performed"); return 0

    original=current; selected=target; operation_started=time.monotonic()
    try:
        checks.extend(stop_release(original,a.systemctl)); atomic_symlink(Path(selected["release_path"]),a.current_link); checks.append(check("select-release","passed",str(selected["release_path"])))
        checks.extend(start_release(selected,a.systemctl))
        elapsed=int(time.monotonic()-operation_started)
        if elapsed>a.maximum_duration_seconds: raise RuntimeError("release operation exceeded maximum duration")
        if a.mode=="activate":
            activation_doc.update({"state":"health-gated","status":"passed","completed_at":utc(),"checks":checks})
            rollback_doc["checks"].append(rollback_check("not-executed","not-run","rollback was not required"))
        else:
            rollback_doc.update({"status":"passed","completed_at":utc(),"checks":[rollback_check("drain-stop-switch-start-health","passed",f"rollback completed in {elapsed}s")]})
    except Exception as error:
        rb_checks.append(rollback_check("operation-failure","failed",str(error)))
        try:
            for item in services(selected,True): run([a.systemctl,"stop",item["unit"]],item["stop_timeout_seconds"]+5)
            atomic_symlink(Path(original["release_path"]),a.current_link); start_release(original,a.systemctl)
            rb_checks.append(rollback_check("automatic-recovery","passed",f"restored {current_id}"))
            rollback_doc.update({"from_release_id":target_id,"target_release_id":current_id,"target_git_sha":original["git_sha"],"target_manifest_sha256":original["source_manifest"]["sha256"],"status":"passed","completed_at":utc(),"checks":rb_checks})
            if activation_doc: activation_doc.update({"state":"rolled-back","status":"failed","completed_at":utc(),"checks":checks+[check("automatic-rollback","passed",str(error))]})
        except Exception as recovery_error:
            rb_checks.append(rollback_check("automatic-recovery","failed",str(recovery_error))); rollback_doc.update({"status":"failed","completed_at":utc(),"checks":rb_checks})
            if activation_doc: activation_doc.update({"state":"activation-failed","status":"failed","completed_at":utc(),"checks":checks+[check("automatic-rollback","failed",str(recovery_error))]})
    for name,doc,path in (("rollback-v1.schema.json",rollback_doc,a.rollback_output),("activation-v1.schema.json",activation_doc,a.activation_output)):
        if doc is None or path is None: continue
        failures=validate(name,doc)
        if failures: raise RuntimeError(f"invalid evidence {name}: {'; '.join(failures)}")
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(doc,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"mode":a.mode,"activation_status":activation_doc and activation_doc["status"],"rollback_status":rollback_doc["status"]},sort_keys=True))
    if activation_doc and activation_doc["status"]=="failed": return 3
    return 0 if rollback_doc["status"] in {"passed","engineering-complete-evidence-pending"} else 4
if __name__=="__main__": raise SystemExit(main())
