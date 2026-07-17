#!/usr/bin/env python3
"""Generate deterministic OpenTelemetry Collector config from V0 policy and sink reference."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from typing import Any
import yaml
from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[2]
POLICY_SCHEMA=ROOT/'contracts/operations/telemetry-runtime-policy-v0.schema.json'
SINK_SCHEMA=ROOT/'contracts/operations/telemetry-sink-v0.schema.json'

def load(path:Path)->Any:return json.loads(path.read_text(encoding='utf-8'))
def validate(schema:Path,doc:Any)->None:
    errors=sorted(Draft202012Validator(load(schema)).iter_errors(doc),key=lambda x:list(x.absolute_path))
    if errors: raise ValueError('; '.join(f"{'.'.join(map(str,e.absolute_path))}: {e.message}" for e in errors))

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--policy',type=Path,required=True); ap.add_argument('--sink',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    p=load(args.policy); s=load(args.sink); validate(POLICY_SCHEMA,p); validate(SINK_SCHEMA,s)
    if s['cost_classification'] in {'free-trial-only','paid','unknown'} and not s['approval_ref']: raise ValueError('telemetry sink requires explicit approval_ref')
    ml=p['processors']['memory_limiter']; batch=p['processors']['batch']; exp=p['exporter']; recv=p['receivers']
    delete_actions=[{'key':key,'action':'delete'} for key in sorted(p['redaction']['delete_attributes'])]
    config={
      'receivers':{
        'otlp':{'protocols':{
          'grpc':{'endpoint':recv['otlp_grpc']['endpoint'],'max_recv_msg_size_mib':recv['otlp_grpc']['max_receive_message_mib']},
          'http':{'endpoint':recv['otlp_http']['endpoint']}
        }},
        'host_metrics':{'collection_interval':f"{recv['host_metrics']['collection_interval_seconds']}s",'scrapers':{name:{} for name in sorted(recv['host_metrics']['scrapers'])}}
      },
      'processors':{
        'memory_limiter':{'check_interval':f"{ml['check_interval_seconds']}s",'limit_mib':ml['limit_mib'],'spike_limit_mib':ml['spike_limit_mib']},
        'attributes/redact':{'actions':delete_actions},
        'batch':{'timeout':f"{batch['timeout_ms']}ms",'send_batch_size':batch['send_batch_size'],'send_batch_max_size':batch['send_batch_max_size']}
      },
      'exporters':{
        'otlphttp/upstream':{
          'endpoint':f"${{env:{s['endpoint_environment_variable']}}}",
          'headers':{'Authorization':f"${{env:{s['authorization_environment_variable']}}}"},
          'tls':{'insecure_skip_verify':False},
          'timeout':f"{exp['timeout_seconds']}s",
          'sending_queue':{'enabled':True,'num_consumers':exp['queue']['num_consumers'],'queue_size':exp['queue']['queue_size']},
          'retry_on_failure':{'enabled':True,'initial_interval':f"{exp['retry']['initial_interval_seconds']}s",'max_interval':f"{exp['retry']['max_interval_seconds']}s",'max_elapsed_time':f"{exp['retry']['max_elapsed_seconds']}s"}
        }
      },
      'extensions':{'health_check':{'endpoint':'127.0.0.1:13133'}},
      'service':{
        'extensions':['health_check'],
        'pipelines':{
          'traces':{'receivers':['otlp'],'processors':['memory_limiter','attributes/redact','batch'],'exporters':['otlphttp/upstream']},
          'metrics':{'receivers':['otlp','host_metrics'],'processors':['memory_limiter','attributes/redact','batch'],'exporters':['otlphttp/upstream']},
          'logs':{'receivers':['otlp'],'processors':['memory_limiter','attributes/redact','batch'],'exporters':['otlphttp/upstream']}
        }
      }
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(yaml.safe_dump(config,sort_keys=True,default_flow_style=False),encoding='utf-8',newline='\n')
    print(f'generated collector config: {args.output}')
    return 0
if __name__=='__main__': raise SystemExit(main())
