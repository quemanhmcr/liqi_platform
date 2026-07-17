#!/usr/bin/env python3
"""Validate cross-provider seams owned by Senior 4 without reproducing provider logic."""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[2]
SCHEMA=ROOT/'contracts/operations/provider-compatibility-result-v0.schema.json'
POLICY=ROOT/'operations/telemetry/telemetry-runtime-policy-v0.json'

def load(path:Path)->Any:return json.loads(path.read_text(encoding='utf-8'))
def check(owner:str,seam:str,status:str,code:str,message:str,action:str)->dict[str,str]:return {'owner':owner,'seam':seam,'status':status,'code':code,'message':message,'action_required':action}
def journal_value(text:str,key:str)->str|None:
    match=re.search(rf'(?m)^\s*{re.escape(key)}\s*=\s*([^\s#]+)',text); return match.group(1) if match else None

def failure_summary(failures:list[str])->str:
    full='; '.join(failures)
    if len(full)<=900:return full
    prefix=f'{len(failures)} incompatibilities: '
    selected='; '.join(failures[:5])
    return prefix+selected+'; additional failures are reported on stderr'

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--provider-root',type=Path,default=ROOT); ap.add_argument('--policy',type=Path,default=POLICY); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--allow-missing',action='store_true'); args=ap.parse_args()
    provider=args.provider_root.resolve(); policy=load(args.policy); checks=[]
    cloud=provider/'infrastructure/cloud-init/host-bootstrap.yaml.tftpl'; host=provider/'contracts/platform/oci-host-v0.example.json'
    edge=provider/'infrastructure/edge/nginx.conf'; edge_hardening=provider/'infrastructure/edge/nginx-liqi-hardening.conf'
    infra_missing=[p.relative_to(provider).as_posix() for p in (cloud,host,edge,edge_hardening) if not p.is_file()]
    if infra_missing:
        checks.append(check('Senior 1','OCI host output and host logging policy','blocked','INFRASTRUCTURE_SEAM_MISSING',f'missing provider paths: {infra_missing}','Senior 1 must merge the published OCI host output and cloud-init source.'))
    else:
        host_doc=load(host); failures=[]
        if host_doc.get('infrastructure_output_version')!='0.3.0':failures.append('infrastructure output version must be 0.3.0')
        if host_doc.get('bootstrap_version')!='0.3.0':failures.append('bootstrap version must be 0.3.0')
        release=host_doc.get('release_target',{})
        expected={'staging_path':'/var/tmp/liqi/releases','deployment_path':'/opt/liqi/releases','current_symlink':'/opt/liqi/current','installation_semantics':'upload-to-staging-then-root-owned-atomic-install'}
        for key,value in expected.items():
            if release.get(key)!=value:failures.append(f'release_target.{key} must be {value!r}')
        text=cloud.read_text(encoding='utf-8')
        expected_journal={
          'SystemMaxUse':f"{policy['journald']['system_max_use_mib']//1024}G",
          'SystemKeepFree':f"{policy['journald']['system_keep_free_mib']//1024}G",
          'MaxRetentionSec':f"{policy['journald']['max_retention_seconds']//86400}day",
          'RateLimitIntervalSec':f"{policy['journald']['rate_limit_interval_seconds']}s",
          'RateLimitBurst':str(policy['journald']['rate_limit_burst']),
          'ForwardToSyslog':'no'
        }
        for key,value in expected_journal.items():
            actual=journal_value(text,key)
            if actual!=value:failures.append(f'journald {key} expected {value}, got {actual}')
        edge_text=edge.read_text(encoding='utf-8')
        for token in ('return 444;','ssl_reject_handshake on;','server 127.0.0.1:8080','server 127.0.0.1:8081','client_max_body_size 1m;'):
            if token not in edge_text:failures.append(f'fail-closed edge missing {token}')
        hardening_text=edge_hardening.read_text(encoding='utf-8')
        for token in ('Slice=liqi-platform-edge.slice','CPUQuota=10%','MemoryMax=256M','MemorySwapMax=0','NoNewPrivileges=yes'):
            if token not in hardening_text:failures.append(f'edge systemd hardening missing {token}')
        checks.append(check('Senior 1','OCI host output and host logging policy','failed' if failures else 'passed','HOST_OPERABILITY_INCOMPATIBLE' if failures else 'HOST_OPERABILITY_COMPATIBLE',failure_summary(failures) if failures else 'host output/bootstrap 0.3.0, release target and journald policy are compatible','Senior 1 must align cloud-init journald and host output with contracts/operations and ADR 0408.' if failures else 'none'))
    database=provider/'contracts/platform/database-v0.example.json'
    if not database.is_file():
        checks.append(check('Senior 2','database recovery command ownership','blocked','DATABASE_SEAM_MISSING','database provider contract is not merged','Senior 2 must merge database-v0 and provider-owned recovery commands.'))
    else:
        doc=load(database); restore=doc.get('restore',{}); failures=[]
        for field in ('command','verificationCommand'):
            value=str(restore.get(field,''))
            if not value.startswith('database/'):
                failures.append(f'restore.{field} must be provider-owned under database/**, got {value!r}')
        result_schema=str(restore.get('resultSchema',''))
        if not result_schema.startswith('contracts/platform/database-'):
            failures.append('restore.resultSchema must remain a Senior 2 platform contract')
        checks.append(check('Senior 2','database recovery command ownership','failed' if failures else 'passed','DATABASE_RECOVERY_OWNERSHIP_INVALID' if failures else 'DATABASE_RECOVERY_OWNERSHIP_VALID',failure_summary(failures) if failures else 'database recovery commands remain provider-owned','Senior 2 must move or version restore commands under database/**; Senior 4 will not create an operations wrapper.' if failures else 'none'))
    runtime_paths={
      'workspace':provider/'Cargo.toml',
      'toolchain':provider/'rust-toolchain.toml',
      'config':provider/'contracts/platform/runtime-config-v0.schema.json',
      'openapi':provider/'contracts/openapi/platform-v0.yaml',
      'tool':provider/'services/admin-cli/src/main.rs',
      'persistence':provider/'crates/persistence-postgres/src/lib.rs',
      'realtime':provider/'services/realtime/src/main.rs',
      'probe':provider/'services/admin-cli/src/platform_probe.rs',
      'api_config':provider/'contracts/platform/runtime-config-api.local.example.json',
      'realtime_config':provider/'contracts/platform/runtime-config-realtime.local.example.json',
      'worker_config':provider/'contracts/platform/runtime-config-worker.local.example.json',
      'api_unit':provider/'services/systemd/liqi-api.service',
      'realtime_unit':provider/'services/systemd/liqi-realtime.service',
      'worker_unit':provider/'services/systemd/liqi-worker.service',
    }
    runtime_missing=[path.relative_to(provider).as_posix() for path in runtime_paths.values() if not path.is_file()]
    if runtime_missing:
        checks.append(check('Senior 3','runtime validation, health identity and promotion probe','blocked','RUNTIME_SEAM_MISSING',f'missing provider paths: {runtime_missing}','Senior 3 must merge the pinned Cargo workspace, runtime config/OpenAPI contracts and admin validation tool.'))
    else:
        failures=[]
        toolchain_text=runtime_paths['toolchain'].read_text(encoding='utf-8')
        if not re.search(r'(?m)^channel\s*=\s*["\']1\.97\.1["\']',toolchain_text):
            failures.append('rust-toolchain channel must be 1.97.1')
        if 'aarch64-unknown-linux-gnu' not in toolchain_text:
            failures.append('rust-toolchain must publish aarch64-unknown-linux-gnu')
        config=load(runtime_paths['config'])
        service=config.get('properties',{}).get('service',{}).get('properties',{})
        if service.get('version',{}).get('maxLength')!=64:
            failures.append('runtime service.version must remain bounded to 64 characters')
        listen=service.get('listen',{}).get('properties',{})
        if listen.get('host',{}).get('const')!='127.0.0.1':
            failures.append('runtime listeners must remain loopback-only')
        database_props=config.get('properties',{}).get('database',{}).get('properties',{})
        if database_props.get('requiredMigrationVersion',{}).get('minimum')!=4:
            failures.append('runtime requiredMigrationVersion minimum must be 4')
        for config_key in ('api_config','realtime_config','worker_config'):
            runtime_example=load(runtime_paths[config_key])
            if runtime_example.get('database',{}).get('requiredMigrationVersion')!=4:
                failures.append(f"{runtime_paths[config_key].name} must require database migration version 4")
        unit_expectations={
          'api_unit':('User=liqi-api','ExecStart=/opt/liqi/current/bin/liqi-api --config /etc/liqi/api.json','/run/liqi/secrets/liqi-api/database-password'),
          'realtime_unit':('User=liqi-realtime','ExecStart=/opt/liqi/current/bin/liqi-realtime --config /etc/liqi/realtime.json','/run/liqi/secrets/liqi-realtime/database-password'),
          'worker_unit':('User=liqi-worker','ExecStart=/opt/liqi/current/bin/liqi-worker --config /etc/liqi/worker.json','/run/liqi/secrets/liqi-worker/database-password'),
        }
        for unit_key,required in unit_expectations.items():
            unit_text=runtime_paths[unit_key].read_text(encoding='utf-8')
            for token in (*required,'NoNewPrivileges=yes','ProtectSystem=strict','MemoryDenyWriteExecute=yes'):
                if token not in unit_text:failures.append(f'{runtime_paths[unit_key].name} missing {token}')
            if 'User=root' in unit_text:failures.append(f'{runtime_paths[unit_key].name} must remain non-root')
        endpoint=database_props.get('endpoint',{}).get('properties',{})
        if endpoint.get('host',{}).get('const')!='127.0.0.1' or endpoint.get('port',{}).get('const')!=6432 or endpoint.get('poolingMode',{}).get('const')!='transaction':
            failures.append('runtime database endpoint must use loopback PgBouncer transaction pooling')
        openapi=runtime_paths['openapi'].read_text(encoding='utf-8')
        for path in ('/health/live:','/health/ready:','/metrics:','/platform/v0/metadata:','/platform/v0/probes:'):
            if path not in openapi: failures.append(f'OpenAPI is missing {path[:-1]}')
        persistence=runtime_paths['persistence'].read_text(encoding='utf-8')
        for token in ('platform.read_realtime_handoff_v0($1, $2)','schema_version','producer','correlation_id','causation_id','metadata','map_event_row'):
            if token not in persistence:
                failures.append(f'runtime PostgreSQL adapter missing committed handoff mapping token {token}')
        realtime=runtime_paths['realtime'].read_text(encoding='utf-8')
        if 'refresh_handoff_readiness' not in realtime or 'committed-handoff-provider-missing' in realtime:
            failures.append('realtime readiness must prove the committed handoff provider dynamically')
        probe=runtime_paths['probe'].read_text(encoding='utf-8')
        if 'platform.observe_probe_v0($1, $2)' not in probe:
            failures.append('platform probe must consume platform.observe_probe_v0(uuid,uuid)')
        for forbidden in ('FROM platform.probe_state_v0','JOIN platform.outbox_events','JOIN platform.probe_effects_v0'):
            if forbidden in probe:
                failures.append(f'platform probe must not read authority tables directly: {forbidden}')
        tool=runtime_paths['tool'].read_text(encoding='utf-8')
        if 'PrintValidationManifest' not in tool:
            failures.append('liqi-platform-tool must publish PrintValidationManifest')
        if 'PlatformProbe' not in tool or 'platform-probe-result-v0' not in tool:
            failures.append('provider-owned platform probe runner/result is missing; POST probe commit alone is not promotion proof')
        telemetry_schema=load(ROOT/'contracts/operations/telemetry-v0.schema.json')
        telemetry_expected={
          'liqi-api':provider/'contracts/platform/runtime-telemetry-api-v0.json',
          'liqi-realtime':provider/'contracts/platform/runtime-telemetry-realtime-v0.json',
          'liqi-worker':provider/'contracts/platform/runtime-telemetry-worker-v0.json',
        }
        for service_name,path in telemetry_expected.items():
            if not path.is_file():
                failures.append(f'missing telemetry capability declaration {path.relative_to(provider).as_posix()}')
                continue
            document=load(path)
            schema_errors=list(Draft202012Validator(telemetry_schema).iter_errors(document))
            if schema_errors:
                failures.append(f'{path.name} does not satisfy telemetry-v0')
            if document.get('service',{}).get('name')!=service_name or document.get('service',{}).get('owner')!='Senior 3':
                failures.append(f'{path.name} must declare {service_name} owned by Senior 3')
        if failures:
            for failure in failures:print(f'ERROR Senior 3 compatibility: {failure}',file=sys.stderr)
        checks.append(check('Senior 3','runtime validation, health identity and promotion probe','failed' if failures else 'passed','RUNTIME_OPERABILITY_INCOMPATIBLE' if failures else 'RUNTIME_OPERABILITY_COMPATIBLE',failure_summary(failures) if failures else 'runtime toolchain, loopback contracts, telemetry declarations and platform probe result seam are compatible','Senior 3 owns repair of runtime operability contract or platform-probe seam failures; Senior 4 will not synthesize runtime evidence.' if failures else 'none'))
    statuses=[item['status'] for item in checks]; overall='failed' if 'failed' in statuses else 'blocked' if 'blocked' in statuses else 'passed'
    result={'schema_version':'provider-compatibility-result-v0','overall_status':overall,'checks':checks}
    errors=list(Draft202012Validator(load(SCHEMA)).iter_errors(result))
    if errors:
        for error in errors:print(f"ERROR provider-compatibility-result: {error.message}",file=sys.stderr)
        return 65
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
    print(f'provider compatibility {overall}: {args.output}')
    if overall=='failed':return 1
    if overall=='blocked' and not args.allow_missing:return 2
    return 0
if __name__=='__main__':raise SystemExit(main())
