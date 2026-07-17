#!/usr/bin/env python3
from __future__ import annotations
import json,shutil,sys
from datetime import datetime,timezone
from pathlib import Path

def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
action=sys.argv[1]; root=Path(sys.argv[2]); database=sys.argv[3]
if action=='prepare': root.mkdir(parents=True,exist_ok=True); (root/'prepared').write_text(database)
elif action=='restore': (root/'restored').write_text(sys.argv[4])
elif action=='verify':
    output=Path(sys.argv[5]); output.parent.mkdir(parents=True,exist_ok=True)
    t=now(); output.write_text(json.dumps({
      'schema_version':'recovery-status-v0','owner':'Senior 2','environment':'development',
      'database':{'authority_version':'database-v0','migration_version':sys.argv[4]},
      'backup':{'completed_at':t,'off_host':True,'encrypted':True,'evidence_ref':'evidence://mock/backup'},
      'wal_archive':{'last_archived_at':t,'lag_seconds':0,'evidence_ref':'evidence://mock/wal'},
      'restore_verification':{'status':'passed','verified_at':t,'rpo_observed_seconds':0,'rto_observed_seconds':1,'source_backup_ref':'oci://mock/backup','evidence_ref':'evidence://mock/restore'}
    }),encoding='utf-8')
elif action=='cleanup': shutil.rmtree(root,ignore_errors=True)
else: raise SystemExit(64)
