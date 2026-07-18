#!/usr/bin/env python3
"""Create or version OCI Vault secrets without putting secret bytes in argv/state/source."""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, subprocess, tempfile
from datetime import datetime, timezone
from pathlib import Path

OCIDS = {
    "compartment": re.compile(r"^ocid1\.compartment\."),
    "key": re.compile(r"^ocid1\.key\."),
    "vault": re.compile(r"^ocid1\.vault\."),
    "secret": re.compile(r"^ocid1\.vaultsecret\."),
}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
MAX_SECRET_BYTES = 25 * 1024

def parse() -> argparse.Namespace:
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest="action",required=True)
    create=sub.add_parser("create")
    create.add_argument("--compartment-id",required=True); create.add_argument("--vault-id",required=True); create.add_argument("--key-id",required=True); create.add_argument("--secret-name",required=True)
    update=sub.add_parser("update")
    update.add_argument("--secret-id",required=True)
    for command in (create,update):
        command.add_argument("--input-file",required=True,type=Path)
        command.add_argument("--content-name",required=True)
        command.add_argument("--stage",choices=("CURRENT","PENDING"),default="CURRENT")
        command.add_argument("--approval-reference")
        command.add_argument("--oci-profile")
        command.add_argument("--execute",action="store_true")
        command.add_argument("--output",required=True,type=Path)
    return parser.parse_args()

def validate(a: argparse.Namespace, content: bytes) -> None:
    if not content or len(content)>MAX_SECRET_BYTES: raise SystemExit("secret must contain 1..25600 bytes")
    if not NAME.fullmatch(a.content_name): raise SystemExit("invalid content name")
    if a.action=="create":
        if a.stage!="CURRENT": raise SystemExit("new secrets must start at CURRENT")
        if not NAME.fullmatch(a.secret_name): raise SystemExit("invalid secret name")
        for kind,value in (("compartment",a.compartment_id),("vault",a.vault_id),("key",a.key_id)):
            if not OCIDS[kind].match(value): raise SystemExit(f"invalid {kind} OCID")
    elif not OCIDS["secret"].match(a.secret_id): raise SystemExit("invalid secret OCID")
    mode=a.input_file.stat().st_mode & 0o777
    if os.name=="posix" and mode & 0o077: raise SystemExit("secret input file must not be group/world accessible")
    if a.execute and (not a.approval_reference or len(a.approval_reference.strip())<3): raise SystemExit("--execute requires approval reference")

def main() -> int:
    a=parse(); content=a.input_file.read_bytes(); validate(a,content)
    content_digest=hashlib.sha256(content).hexdigest()
    status="validated"; secret_id=getattr(a,"secret_id",None); version=None
    if a.execute:
        encoded=base64.b64encode(content).decode("ascii")
        request={"secret_content_content":encoded,"secret_content_name":a.content_name,"secret_content_stage":a.stage,"force":True}
        if a.action=="create": request.update({"compartment_id":a.compartment_id,"vault_id":a.vault_id,"key_id":a.key_id,"secret_name":a.secret_name})
        else: request["secret_id"]=a.secret_id
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",prefix="liqi-vault-request-",suffix=".json",delete=False) as handle:
            json.dump(request,handle,separators=(",",":")); handle.write("\n"); request_path=Path(handle.name)
        try:
            os.chmod(request_path,0o600)
            command=["oci","vault","secret",f"{a.action}-base64","--from-json",f"file://{request_path}"]
            if a.oci_profile: command.extend(["--profile",a.oci_profile])
            result=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=120)
            if result.returncode: raise RuntimeError(result.stderr.strip() or "OCI secret mutation failed")
            response=json.loads(result.stdout); data=response.get("data",{})
            secret_id=data.get("id",secret_id); version=data.get("current-version-number")
            status="created" if a.action=="create" else "version-created"
        finally:
            try:
                request_path.write_bytes(b"\x00"*request_path.stat().st_size)
            except OSError: pass
            request_path.unlink(missing_ok=True)
    evidence={"schema_version":"liqi.infrastructure.vault-secret-result/v1","action":a.action,"status":status,"secret_id":secret_id,"secret_name":getattr(a,"secret_name",None),"content_name":a.content_name,"stage":a.stage,"content_sha256":content_digest,"content_size_bytes":len(content),"current_version_number":version,"approval_reference":a.approval_reference if a.execute else None,"observed_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"oci_mutation_performed":a.execute}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n",encoding="utf-8"); os.chmod(a.output,0o600)
    print(json.dumps({k:v for k,v in evidence.items() if k!="content_sha256"},indent=2,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
