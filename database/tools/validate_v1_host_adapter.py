#!/usr/bin/env python3
"""Validate V1 database recovery assumptions against the integrated OCI output contract."""
from __future__ import annotations
import json,sys
from pathlib import Path

def fail(message:str)->None:
 print(f"ERROR: {message}",file=sys.stderr); raise SystemExit(1)
def main()->int:
 if len(sys.argv)!=2: print('usage: validate_v1_host_adapter.py <oci-live-v1.example.json>',file=sys.stderr); return 64
 path=Path(sys.argv[1]); host=json.loads(path.read_text(encoding='utf-8'))
 if host.get('schema_version')!='liqi.infrastructure.oci-live/v1': fail('unsupported OCI live contract')
 ingress={(x.get('protocol'),x.get('port')) for x in host.get('network',{}).get('public_ingress',[])}
 if ingress!={('tcp',80),('tcp',443)}: fail(f'public ingress changed: {sorted(ingress)}')
 tunnel=host.get('network',{}).get('management_tunnel',{})
 if tunnel.get('mode')!='outbound-only' or tunnel.get('protocol')!='wireguard-udp' or tunnel.get('public_ingress_required') is not False: fail('management tunnel is not outbound-only WireGuard')
 storage=host.get('storage',{})
 if storage.get('application_backup_authority')!='independent-management-storage' or storage.get('artifact_archive_authority')!='independent-management-storage': fail('independent recovery authority missing')
 if any('bucket' in key.lower() or 'object' in key.lower() for key in storage): fail('V1 storage contract retains Object Storage')
 capabilities=host.get('identity',{}).get('capabilities',[])
 if capabilities!=['vault-secret-bundle-read']: fail(f'host identity is broader than Vault read: {capabilities}')
 state=host.get('state_backend',{})
 if state.get('kind')!='postgresql-self-hosted' or state.get('locking')!='postgresql-advisory-locks': fail('state backend is not independent PostgreSQL')
 print(json.dumps({'validation':'database-oci-live-adapter-v1','contract':str(path),'backupAuthority':'independent-management-storage','passed':True},separators=(',',':'))); return 0
if __name__=='__main__': raise SystemExit(main())
