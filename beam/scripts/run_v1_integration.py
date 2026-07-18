#!/usr/bin/env python3
"""Compose provider-owned PostgreSQL migrations with the root Ecto/Oban consumer."""
from __future__ import annotations
import argparse,json,os,secrets,shutil,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from beam.scripts.prepare_disposable_database import parse_admin_url,write_inputs
RESULT_SCHEMA=ROOT/'contracts/runtime/runtime-integration-result-v1.schema.json'
BOUNDED_RUNNER=ROOT/'beam/scripts/run_bounded.py'

def utc_now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def git_sha(): return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,timeout=10).strip()
def redact(value,secrets_to_redact):
    result=value
    for secret in secrets_to_redact:
        if secret: result=result.replace(secret,'<redacted>')
    return result[-8000:]
def bounded(command,env,timeout,secrets_to_redact):
    try:
        completed=subprocess.run([sys.executable,str(BOUNDED_RUNNER),'--timeout',str(timeout),'--',*command],cwd=ROOT,env=env,capture_output=True,text=True,timeout=timeout+30,check=False)
    except (subprocess.TimeoutExpired,OSError) as error:
        return 124,redact(f"{type(error).__name__}: bounded command did not complete",secrets_to_redact)
    return completed.returncode,redact(completed.stdout+completed.stderr,secrets_to_redact)
def psql_drop(env,database,secrets_to_redact):
    sql="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :'database_name' AND pid <> pg_backend_pid();\nSELECT format('DROP DATABASE IF EXISTS %I', :'database_name') \\gexec\n"
    try:
        completed=subprocess.run([shutil.which('psql') or 'psql','--no-psqlrc','--quiet','--set=ON_ERROR_STOP=1','--dbname=postgres',f'--set=database_name={database}'],cwd=ROOT,env=env,input=sql,capture_output=True,text=True,timeout=60,check=False)
    except (subprocess.TimeoutExpired,OSError) as error:
        return 124,redact(f"{type(error).__name__}: disposable cleanup did not complete",secrets_to_redact)
    return completed.returncode,redact(completed.stdout+completed.stderr,secrets_to_redact)
def result_document(status,checks,blockers):
    return {'schema_version':'runtime-integration-result-v1','git_sha':git_sha(),'observed_at':utc_now(),'status':status,'database_target':'disposable-redacted','checks':checks,'blockers':blockers}
def write_result(path,document):
    schema=json.loads(RESULT_SCHEMA.read_text(encoding='utf-8')); errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(document))
    if errors: raise RuntimeError('invalid runtime integration result: '+'; '.join(error.message for error in errors))
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(document,indent=2)+'\n',encoding='utf-8',newline='\n')
def run_step(name,command,env,timeout,secrets_to_redact,checks,blockers):
    rc,log=bounded(command,env,timeout,secrets_to_redact); checks.append({'name':name,'status':'passed' if rc==0 else 'failed'})
    if rc!=0:
        blockers.append(f'provider or consumer command failed: {name}'); print(log,file=sys.stderr)
    return rc==0

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True,type=Path); args=parser.parse_args(argv)
    database_url=os.environ.get('LIQI_TEST_DATABASE_URL')
    if not database_url:
        write_result(args.output,result_document('blocked',[{'name':'disposable-database-input','status':'blocked'}],['LIQI_TEST_DATABASE_URL is required'])); return 2
    try:
        parse_admin_url(database_url)
    except ValueError as error:
        write_result(args.output,result_document('failed',[{'name':'disposable-database-safety','status':'failed'}],[str(error)])); return 1
    missing=[name for name in ('bash','psql','mix') if not shutil.which(name)]
    if missing:
        write_result(args.output,result_document('blocked',[{'name':'disposable-database-tooling','status':'blocked'}],[f"required disposable database commands are missing: {', '.join(missing)}"])); return 2

    checks=[]; blockers=[]; suffix=secrets.token_hex(4); target_database=os.environ.get('LIQI_RUNTIME_TEST_DATABASE',f'liqi_v1_test_runtime_{suffix}')
    provider_ok=consumer_ok=cleanup_ok=False
    with tempfile.TemporaryDirectory(prefix='liqi-runtime-db-') as directory:
        try:
            target=write_inputs(database_url,Path(directory),target_database)
        except ValueError as error:
            write_result(args.output,result_document('failed',[{'name':'disposable-database-safety','status':'failed'}],[str(error)])); return 1
        bundle=json.loads(Path(target['bundle_path']).read_text(encoding='utf-8')); secrets_to_redact=[database_url,*bundle.values()]
        env=os.environ.copy(); env.update({'PGHOST':str(target['host']),'PGPORT':str(target['port']),'PGUSER':str(target['admin_user']),'PGDATABASE':'postgres','PGSSLMODE':'disable','LIQI_TEST_DATABASE':target_database})
        for key in ('PGPASSWORD','PGSERVICE','PGSERVICEFILE','LIQI_API_DATABASE_SECRET_REF','LIQI_REALTIME_DATABASE_SECRET_REF','LIQI_WORKER_DATABASE_SECRET_REF'): env.pop(key,None)
        empty_pgpass=Path(directory)/'empty-pgpass'; empty_pgpass.write_text('',encoding='utf-8'); empty_pgpass.chmod(0o600); env['PGPASSFILE']=str(empty_pgpass)
        preclean_rc,preclean_log=psql_drop(env,target_database,secrets_to_redact)
        checks.append({'name':'disposable-database-preclean','status':'passed' if preclean_rc==0 else 'failed'})
        if preclean_rc!=0:
            blockers.append('failed to prepare the protected disposable database name'); print(preclean_log,file=sys.stderr)
        else:
            commands=[
                ('provider-cluster-roles',[shutil.which('psql') or 'psql','--no-psqlrc','--set=ON_ERROR_STOP=1','--dbname=postgres','--file=database/bootstrap/00_cluster_roles.sql'],120),
                ('provider-test-database',[shutil.which('psql') or 'psql','--no-psqlrc','--set=ON_ERROR_STOP=1','--dbname=postgres',f'--set=database_name={target_database}','--file=database/bootstrap/10_create_database.sql'],120),
                ('provider-role-settings',[shutil.which('psql') or 'psql','--no-psqlrc','--set=ON_ERROR_STOP=1','--dbname=postgres',f'--set=database_name={target_database}','--file=database/bootstrap/20_role_settings.sql'],120),
            ]
            provider_ok=all(run_step(name,command,env,timeout,secrets_to_redact,checks,blockers) for name,command,timeout in commands)
            if provider_ok:
                migration_env=env.copy(); migration_env['PGDATABASE']=target_database
                provider_ok=run_step('provider-migrations',[shutil.which('bash') or 'bash','database/bin/migrate.sh'],migration_env,600,secrets_to_redact,checks,blockers)
            if provider_ok:
                consumer_env=env.copy(); consumer_env.update({'LIQI_RUNTIME_CONFIG_PATH':str(target['runtime_config_path']),'LIQI_DATABASE_INTEGRATION':'1','LIQI_TEST_ENDPOINT_SECRET':'integration-endpoint-secret','LIQI_TEST_PROBE_TOKEN':'integration-probe-token','LIQI_TEST_DRAIN_TOKEN':'integration-drain-token','MIX_ENV':'test'})
                consumer_ok=run_step('root-consumer-integration',[shutil.which('mix') or 'mix','test','beam/test/liqi/persistence/database_provider_integration_test.exs','--seed','0'],consumer_env,600,secrets_to_redact,checks,blockers)
        cleanup_rc,cleanup_log=psql_drop(env,target_database,secrets_to_redact); cleanup_ok=cleanup_rc==0; checks.append({'name':'disposable-database-cleanup','status':'passed' if cleanup_ok else 'failed'})
        if not cleanup_ok: blockers.append('disposable database cleanup failed'); print(cleanup_log,file=sys.stderr)
    status='passed' if provider_ok and consumer_ok and cleanup_ok else 'failed'; write_result(args.output,result_document(status,checks,blockers)); return 0 if status=='passed' else 1
if __name__=='__main__': raise SystemExit(main())
