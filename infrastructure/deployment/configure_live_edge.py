#!/usr/bin/env python3
"""Render, validate and optionally enable the public Caddy V1 edge after health-gated activation."""
from __future__ import annotations
import argparse, base64, json, os, re, secrets, shutil, socket, ssl, subprocess, tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

ROOT=Path(__file__).resolve().parents[2]
HOST=re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL=re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def load(path:Path)->Any: return json.loads(path.read_text(encoding="utf-8"))
def run(argv:list[str],timeout:int=30)->str:
 r=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=timeout)
 if r.returncode: raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f"command failed: {argv}")
 return r.stdout.strip()
def websocket_probe(hostname:str,path:str)->None:
 key=base64.b64encode(secrets.token_bytes(16)).decode(); context=ssl.create_default_context()
 with socket.create_connection((hostname,443),timeout=10) as raw:
  with context.wrap_socket(raw,server_hostname=hostname) as stream:
   request=(f"GET {path} HTTP/1.1\r\nHost: {hostname}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n").encode()
   stream.sendall(request); response=stream.recv(4096).decode("latin1",errors="replace")
 if not response.startswith("HTTP/1.1 101") and not response.startswith("HTTP/1.0 101"): raise RuntimeError(f"WebSocket upgrade did not return 101: {response.splitlines()[0] if response else 'empty response'}")
def main()->int:
 p=argparse.ArgumentParser(); p.add_argument("--hostname",required=True); p.add_argument("--acme-email",required=True); p.add_argument("--activation-evidence",required=True,type=Path)
 p.add_argument("--template",type=Path,default=Path("/usr/local/share/liqi/Caddyfile.v1-live.tftpl")); p.add_argument("--caddyfile",type=Path,default=Path("/etc/caddy/Caddyfile")); p.add_argument("--websocket-path",default="/socket/websocket?vsn=2.0.0")
 p.add_argument("--approval-reference"); p.add_argument("--execute",action="store_true"); p.add_argument("--rendered-output",required=True,type=Path); p.add_argument("--evidence-output",required=True,type=Path)
 a=p.parse_args()
 if not HOST.fullmatch(a.hostname) or not EMAIL.fullmatch(a.acme_email): raise SystemExit("valid public hostname and ACME email are required")
 activation=load(a.activation_evidence)
 if activation.get("schema_version")!="liqi.deployment.activation/v1" or activation.get("status")!="passed" or activation.get("state")!="health-gated" or activation.get("traffic_enabled") is not False: raise SystemExit("traffic enablement requires passed health-gated activation evidence")
 if a.execute and (os.name!="posix" or os.geteuid()!=0): raise SystemExit("traffic mutation requires root on POSIX")
 if a.execute and (not a.approval_reference or len(a.approval_reference.strip())<3): raise SystemExit("traffic mutation requires cutover approval reference")
 template=a.template.read_text(encoding="utf-8"); rendered=template.replace("${hostname}",a.hostname).replace("${acme_email}",a.acme_email)
 if "${" in rendered: raise SystemExit("unresolved Caddy template variable")
 a.rendered_output.parent.mkdir(parents=True,exist_ok=True); a.rendered_output.write_text(rendered,encoding="utf-8",newline="\n")
 caddy=shutil.which("caddy")
 if caddy: run([caddy,"validate","--config",str(a.rendered_output)])
 status="engineering-complete-evidence-pending"; certificate="pending"; websocket="pending"; mutation=False
 if a.execute:
  if not caddy: raise SystemExit("caddy binary is required")
  backup=a.caddyfile.with_suffix(".pre-cutover")
  shutil.copyfile(a.caddyfile,backup)
  try:
   temporary=a.caddyfile.with_name(f".{a.caddyfile.name}.new.{os.getpid()}"); shutil.copyfile(a.rendered_output,temporary); os.chmod(temporary,0o640); os.replace(temporary,a.caddyfile)
   run([caddy,"reload","--config",str(a.caddyfile),"--force"])
   http=run(["curl","--fail","--silent","--show-error","--output","/dev/null","--write-out","%{http_code}",f"https://{a.hostname}/health/live"],60)
   if http!="200": raise RuntimeError(f"HTTPS liveness returned {http}")
   redirect=run(["curl","--silent","--show-error","--output","/dev/null","--write-out","%{http_code}",f"http://{a.hostname}/health/live"],30)
   if redirect not in {"301","302","307","308"}: raise RuntimeError(f"HTTP redirect returned {redirect}")
   websocket_probe(a.hostname,a.websocket_path); status="verified"; certificate="valid"; websocket="passed"; mutation=True
  except Exception:
   shutil.copyfile(backup,a.caddyfile); run([caddy,"reload","--config",str(a.caddyfile),"--force"]); raise
 evidence={"schema_version":"liqi.deployment.live-endpoint/v1","environment":"v1-live","classification":"production-shaped-development","git_sha":activation["git_sha"],"release_id":activation["release_id"],"hostname":a.hostname,"public_ports":[80,443],"tls":{"mode":"public-acme","issuer":"Caddy ACME issuer","certificate_status":certificate,"renewal_command":["caddy","reload","--config","/etc/caddy/Caddyfile"]},"backend":{"address":"127.0.0.1:4000","publicly_routable":False},"websocket":{"path":a.websocket_path.split('?',1)[0],"supported":websocket=="passed","resume_evidence":websocket},"health":{"public_path":"/health/live","public_detail_level":"binary-no-dependency-detail","internal_path":"/health/ready"},"security":{"admin_api_public":False,"http_redirect":True,"request_body_limit_bytes":1048576,"header_limit_bytes":32768,"security_headers":["Strict-Transport-Security","X-Content-Type-Options","Referrer-Policy","Content-Security-Policy"]},"status":status,"observed_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"evidence_class":"live-approved" if a.execute else "source-only"}
 schema_path=(ROOT/"contracts/deployment/live-endpoint-v1.schema.json") if (ROOT/"contracts/deployment/live-endpoint-v1.schema.json").is_file() else Path("/usr/local/share/liqi/contracts/deployment/live-endpoint-v1.schema.json")
 failures=list(Draft202012Validator(load(schema_path),format_checker=FormatChecker()).iter_errors(evidence))
 if failures: raise RuntimeError(f"invalid endpoint evidence: {failures[0].message}")
 a.evidence_output.parent.mkdir(parents=True,exist_ok=True); a.evidence_output.write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n",encoding="utf-8")
 print(json.dumps({"status":status,"hostname":a.hostname,"traffic_mutation_performed":mutation},sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
