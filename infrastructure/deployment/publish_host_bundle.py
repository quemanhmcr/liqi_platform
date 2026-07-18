#!/usr/bin/env python3
"""Validate and optionally publish an immutable host bundle to OCI Object Storage."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/infrastructure/host-bundle-v1.schema.json"
IDENT = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(argv: list[str]) -> None:
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "OCI CLI command failed")

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--bundle-dir", required=True, type=Path); p.add_argument("--bundle-id", required=True)
    p.add_argument("--public-key", required=True, type=Path); p.add_argument("--namespace", required=True); p.add_argument("--bucket", required=True)
    p.add_argument("--approval-reference"); p.add_argument("--oci-profile"); p.add_argument("--execute", action="store_true")
    p.add_argument("--output", required=True, type=Path)
    a=p.parse_args()
    if not IDENT.fullmatch(a.bundle_id): raise SystemExit("invalid bundle ID")
    manifest=a.bundle_dir/f"liqi-host-bundle-{a.bundle_id}.json"
    signature=a.bundle_dir/f"liqi-host-bundle-{a.bundle_id}.json.sig"
    archive=a.bundle_dir/f"liqi-host-bundle-{a.bundle_id}.tar.gz"
    for path in (manifest,signature,archive,a.public_key):
        if not path.is_file(): raise SystemExit(f"missing input: {path}")
    doc=json.loads(manifest.read_text(encoding="utf-8"))
    errors=list(Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")),format_checker=FormatChecker()).iter_errors(doc))
    if errors: raise SystemExit(errors[0].message)
    if doc["bundle_id"] != a.bundle_id or doc["artifact"]["sha256"] != sha(archive) or doc["artifact"]["size_bytes"] != archive.stat().st_size:
        raise SystemExit("bundle identity mismatch")
    verify=subprocess.run(["openssl","pkeyutl","-verify","-rawin","-pubin","-inkey",str(a.public_key),"-in",str(manifest),"-sigfile",str(signature)],capture_output=True,text=True,check=False)
    if verify.returncode: raise SystemExit(verify.stderr.strip() or "manifest signature verification failed")
    prefix=doc["installation"]["object_prefix"]
    objects=[(manifest,prefix+manifest.name),(signature,prefix+signature.name),(archive,prefix+archive.name)]
    status="validated"
    if a.execute:
        if not a.approval_reference or len(a.approval_reference.strip()) < 3: raise SystemExit("--execute requires approval reference")
        for local, name in objects:
            command=["oci","os","object","put","--namespace-name",a.namespace,"--bucket-name",a.bucket,"--name",name,"--file",str(local),"--if-none-match","*","--no-multipart"]
            if a.oci_profile: command.extend(["--profile",a.oci_profile])
            run(command)
        status="published"
    result={"schema_version":"liqi.infrastructure.host-bundle-publish-result/v1","bundle_id":a.bundle_id,"git_sha":doc["git_sha"],"status":status,"approval_reference":a.approval_reference if a.execute else None,"objects":[{"name":name,"sha256":sha(local),"size_bytes":local.stat().st_size} for local,name in objects],"observed_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"oci_mutation_performed":a.execute}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2,sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
