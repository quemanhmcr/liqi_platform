from __future__ import annotations
import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[2]
VALIDATE=ROOT/'scripts/operations/validate_telemetry_runtime.py'
GENERATE=ROOT/'scripts/operations/generate_otel_collector_config.py'
POLICY=ROOT/'operations/telemetry/telemetry-runtime-policy-v0.json'
SINK=ROOT/'operations/telemetry/telemetry-sink-v0.example.json'

class TelemetryRuntimeTests(unittest.TestCase):
    def test_policy_and_generated_config_are_bounded(self)->None:
        result=subprocess.run([sys.executable,str(VALIDATE)],cwd=ROOT,text=True,capture_output=True,check=False)
        self.assertEqual(result.returncode,0,result.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            one=Path(tmp)/'one.yaml'; two=Path(tmp)/'two.yaml'
            for out in (one,two):
                r=subprocess.run([sys.executable,str(GENERATE),'--policy',str(POLICY),'--sink',str(SINK),'--output',str(out)],cwd=ROOT,text=True,capture_output=True,check=False)
                self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(one.read_bytes(),two.read_bytes())
            config=yaml.safe_load(one.read_text(encoding='utf-8'))
            self.assertEqual(config['service']['pipelines']['traces']['processors'][0],'memory_limiter')
            self.assertNotIn('tail_sampling',config['processors'])
            self.assertEqual(config['exporters']['otlphttp/upstream']['retry_on_failure']['max_elapsed_time'],'300s')
            self.assertEqual(config['receivers']['otlp']['protocols']['grpc']['endpoint'],'127.0.0.1:4317')

    def test_unbounded_retry_is_rejected(self)->None:
        with tempfile.TemporaryDirectory() as tmp:
            policy=json.loads(POLICY.read_text(encoding='utf-8')); policy['exporter']['retry']['max_elapsed_seconds']=0
            path=Path(tmp)/'policy.json'; path.write_text(json.dumps(policy),encoding='utf-8')
            r=subprocess.run([sys.executable,str(VALIDATE),'--policy',str(path)],cwd=ROOT,text=True,capture_output=True,check=False)
            self.assertNotEqual(r.returncode,0)

    def test_public_receiver_and_unapproved_sink_are_rejected(self)->None:
        with tempfile.TemporaryDirectory() as tmp:
            policy=json.loads(POLICY.read_text(encoding='utf-8')); policy['receivers']['otlp_http']['endpoint']='0.0.0.0:4318'
            sink=json.loads(SINK.read_text(encoding='utf-8')); sink['cost_classification']='unknown'
            pp=Path(tmp)/'policy.json'; sp=Path(tmp)/'sink.json'; pp.write_text(json.dumps(policy),encoding='utf-8'); sp.write_text(json.dumps(sink),encoding='utf-8')
            r=subprocess.run([sys.executable,str(VALIDATE),'--policy',str(pp),'--sink',str(sp)],cwd=ROOT,text=True,capture_output=True,check=False)
            self.assertNotEqual(r.returncode,0)

if __name__=='__main__': unittest.main()
