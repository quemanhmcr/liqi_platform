#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,re,stat
from pathlib import Path
from urllib.parse import quote,unquote,urlsplit
ADMIN_DATABASE=re.compile(r'^liqi_v1_(?:ci|test(?:_[a-z0-9_]+)?)$')
TARGET_DATABASE=re.compile(r'^liqi_v1_test(?:_[a-z0-9_]+)?$')
ROLE_USERS={'command':'liqi_api','realtime':'liqi_realtime','worker':'liqi_worker'}

def parse_admin_url(value):
    parsed=urlsplit(value); database=unquote(parsed.path.removeprefix('/'))
    if parsed.scheme not in {'postgres','postgresql'}: raise ValueError('database URL must use postgres or postgresql')
    if parsed.hostname not in {'127.0.0.1','localhost'}: raise ValueError('database URL must target loopback')
    if not parsed.username: raise ValueError('database URL must include an administrative username')
    if parsed.password is not None: raise ValueError('password-bearing DSNs are not accepted')
    if parsed.query or parsed.fragment: raise ValueError('database URL query and fragment are forbidden')
    if not ADMIN_DATABASE.fullmatch(database): raise ValueError('administrative database is not disposable')
    return {'host':parsed.hostname,'port':parsed.port or 5432,'admin_user':unquote(parsed.username),'admin_database':database}

def write_inputs(database_url,directory,target_database):
    if not TARGET_DATABASE.fullmatch(target_database): raise ValueError('target database is not disposable')
    target=parse_admin_url(database_url); target['target_database']=target_database; directory.mkdir(parents=True,exist_ok=True)
    bundle_path=directory/'database-role-urls.json'; runtime_path=directory/'runtime-config.json'; target_path=directory/'target.json'
    bundle={role:f"postgresql://{quote(user,safe='')}@{target['host']}:{target['port']}/{quote(target_database,safe='')}" for role,user in ROLE_USERS.items()}
    bundle_path.write_text(json.dumps(bundle,separators=(',',':'))+'\n',encoding='utf-8'); os.chmod(bundle_path,stat.S_IRUSR|stat.S_IWUSR)
    root=Path(__file__).resolve().parents[2]
    runtime=json.loads((root/'contracts/runtime/examples/runtime-config-v1.json').read_text(encoding='utf-8'))
    runtime['environment']='test'; runtime['releaseId']='liqi-v1-disposable-integration'
    runtime['database']['secretRef']='file://'+bundle_path.resolve().as_posix(); runtime['database']['credentialFormat']='role-url-bundle-v1'
    runtime['http']['secretRef']='env://LIQI_TEST_ENDPOINT_SECRET'; runtime['security']['probeTokenRef']='env://LIQI_TEST_PROBE_TOKEN'; runtime['shutdown']['drainTokenRef']='env://LIQI_TEST_DRAIN_TOKEN'
    runtime['oban']['enabled']=False; runtime['features']={'persistence':False,'realtimeDispatcher':False,'outboxWorker':False}
    runtime_path.write_text(json.dumps(runtime,indent=2)+'\n',encoding='utf-8',newline='\n'); os.chmod(runtime_path,stat.S_IRUSR|stat.S_IWUSR)
    public={**target,'bundle_path':str(bundle_path.resolve()),'runtime_config_path':str(runtime_path.resolve())}
    target_path.write_text(json.dumps(public,indent=2)+'\n',encoding='utf-8',newline='\n'); os.chmod(target_path,stat.S_IRUSR|stat.S_IWUSR)
    return public

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--database-url',required=True); parser.add_argument('--directory',required=True,type=Path); parser.add_argument('--target-database',default=os.environ.get('LIQI_RUNTIME_TEST_DATABASE','liqi_v1_test_runtime')); args=parser.parse_args()
    try: result=write_inputs(args.database_url,args.directory,args.target_database)
    except ValueError as error: parser.error(str(error))
    print(json.dumps(result,separators=(',',':'))); return 0
if __name__=='__main__': raise SystemExit(main())
