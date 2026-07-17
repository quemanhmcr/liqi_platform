from __future__ import annotations
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];RUNNER=ROOT/'scripts/operations/run_provider_gates.py';EXTRACT=ROOT/'scripts/operations/extract_provider_output.py';REG=ROOT/'tests/contract/fixtures/provider-gates/registry.json'
class ExtractProviderOutputTests(unittest.TestCase):
 def test_extracts_passed_json_gate_by_id(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'.artifacts') as tmp:
   base=Path(tmp);integration=base/'integration.json';evidence=base/'evidence';out=base/'selected.json'
   env=dict(__import__('os').environ);env['LIQI_TEST_PLAN_PATH']='C:/fixture/plan.json'
   r=subprocess.run([sys.executable,str(RUNNER),'--registry',str(REG),'--stage','source','--output',str(integration),'--evidence-dir',str(evidence),'--allow-blocked'],cwd=ROOT,text=True,capture_output=True,env=env,check=False);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([sys.executable,str(EXTRACT),'--integration-result',str(integration),'--gate-id','senior2-json-output','--output',str(out)],cwd=ROOT,text=True,capture_output=True,check=False);self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(json.loads(out.read_text())['passed'])
 def test_rejects_unknown_gate(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);p=base/'integration.json';p.write_text(json.dumps({'provider_results':[]}));r=subprocess.run([sys.executable,str(EXTRACT),'--integration-result',str(p),'--gate-id','missing-gate','--output',str(base/'out.json')],cwd=ROOT,text=True,capture_output=True,check=False);self.assertNotEqual(r.returncode,0)
if __name__=='__main__':unittest.main()
