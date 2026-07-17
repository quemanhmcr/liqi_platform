#!/usr/bin/env python3
"""Validate telemetry runtime policy, sink reference and bounded retention semantics."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

ROOT=Path(__file__).resolve().parents[2]
POLICY_SCHEMA=ROOT/'contracts/operations/telemetry-runtime-policy-v0.schema.json'
SINK_SCHEMA=ROOT/'contracts/operations/telemetry-sink-v0.schema.json'
DEFAULT_POLICY=ROOT/'operations/telemetry/telemetry-runtime-policy-v0.json'
DEFAULT_SINK=ROOT/'operations/telemetry/telemetry-sink-v0.example.json'
JOURNALD=ROOT/'operations/telemetry/journald-liqi-v0.conf'
TELEMETRY_CONTRACT=ROOT/'tests/contract/fixtures/operations/telemetry.valid.json'


def load(path:Path)->Any: return json.loads(path.read_text(encoding='utf-8'))
def validate(schema_path:Path, document:Any, label:str)->list[str]:
    return [f"{label}.{'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in sorted(Draft202012Validator(load(schema_path),format_checker=FormatChecker()).iter_errors(document),key=lambda x:list(x.absolute_path))]

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--policy',type=Path,default=DEFAULT_POLICY); ap.add_argument('--sink',type=Path,default=DEFAULT_SINK); args=ap.parse_args()
    policy=load(args.policy); sink=load(args.sink); failures=[]
    failures+=validate(POLICY_SCHEMA,policy,'policy'); failures+=validate(SINK_SCHEMA,sink,'sink')
    if failures:
        for f in failures: print('ERROR telemetry-policy:',f,file=sys.stderr)
        return 1
    collector=policy['collector']; memory=policy['processors']['memory_limiter']; batch=policy['processors']['batch']; exporter=policy['exporter']
    if memory['limit_mib']>collector['hard_memory_mib']: failures.append('memory limiter exceeds collector hard memory')
    if memory['spike_limit_mib']>=memory['limit_mib']: failures.append('spike limit must be below memory limit')
    if batch['send_batch_size']>batch['send_batch_max_size']: failures.append('send_batch_size exceeds send_batch_max_size')
    if exporter['retry']['max_elapsed_seconds']<=0: failures.append('retry max_elapsed_seconds must be bounded and nonzero')
    if exporter['queue']['queue_size']<=0: failures.append('sending queue must be bounded and nonzero')
    for name in ('otlp_grpc','otlp_http'):
        if not policy['receivers'][name]['endpoint'].startswith('127.0.0.1:'): failures.append(f'{name} must bind loopback')
    if policy['sampling']['tail_sampling_enabled']: failures.append('tail sampling is forbidden on V0 single node')
    if policy['journald']['system_max_use_mib']>2048 or policy['journald']['system_keep_free_mib']<10240: failures.append('journald disk guard violates V0 policy')
    contract=load(TELEMETRY_CONTRACT)
    contract_forbidden=set(contract['cardinality_policy']['forbidden_labels'])
    policy_forbidden=set(policy['cardinality']['forbidden_labels'])
    if not contract_forbidden.issubset(policy_forbidden): failures.append(f'policy misses contract forbidden labels: {sorted(contract_forbidden-policy_forbidden)}')
    deleted=set(policy['redaction']['delete_attributes'])
    required_deleted={'authorization','http.request.header.authorization','password','access_token','refresh_token','session_token'}
    if not required_deleted.issubset(deleted): failures.append(f'redaction misses required attributes: {sorted(required_deleted-deleted)}')
    if sink['cost_classification'] in {'free-trial-only','paid','unknown'} and not sink['approval_ref']: failures.append('non-free or unknown telemetry sink requires approval_ref')
    journal=JOURNALD.read_text(encoding='utf-8')
    for line in ('Storage=persistent','SystemMaxUse=2G','SystemKeepFree=10G','MaxRetentionSec=7day','ForwardToSyslog=no'):
        if line not in journal: failures.append(f'journald config missing {line}')
    if failures:
        for f in failures: print('ERROR telemetry-policy:',f,file=sys.stderr)
        return 1
    print(f"validated telemetry runtime policy: collector<={collector['hard_memory_mib']}MiB queue={exporter['queue']['queue_size']} retry={exporter['retry']['max_elapsed_seconds']}s")
    return 0
if __name__=='__main__': raise SystemExit(main())
