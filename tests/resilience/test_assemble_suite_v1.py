from __future__ import annotations
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];PYTHON=sys.executable;sys.path.insert(0,str(ROOT/'tests/live'))
import evidence_factory as factory  # noqa: E402
class ResilienceSuiteTests(unittest.TestCase):
 def test_exact_scenario_set_assembles(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);args=[PYTHON,'tests/resilience/assemble_suite_v1.py','--git-sha',factory.SHA,'--release-id',factory.RELEASE_ID,'--environment',factory.ENVIRONMENT]
   for ident in factory.SCENARIOS:
    path=root/f'{ident}.json';path.write_text(json.dumps(factory.scenario_result(ident)),encoding='utf-8');args+=['--scenario',f'{ident}={path}']
   out=root/'suite.json';args+=['--output',str(out)]
   result=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,check=False);self.assertEqual(0,result.returncode,result.stderr);self.assertEqual('passed',json.loads(out.read_text())['status'])
 def test_missing_scenario_fails_closed(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);args=[PYTHON,'tests/resilience/assemble_suite_v1.py','--git-sha',factory.SHA,'--release-id',factory.RELEASE_ID,'--environment',factory.ENVIRONMENT]
   for ident in factory.SCENARIOS[:-1]:
    path=root/f'{ident}.json';path.write_text(json.dumps(factory.scenario_result(ident)),encoding='utf-8');args+=['--scenario',f'{ident}={path}']
   args+=['--output',str(root/'suite.json')]
   result=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,check=False);self.assertEqual(2,result.returncode)
if __name__=='__main__':unittest.main()
