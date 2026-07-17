from __future__ import annotations
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'scripts/operations/run_recovery_exercise.py'
MOCK=ROOT/'tests/contract/fixtures/recovery-exercise/plan.mock.json'
PROVIDER=ROOT/'operations/disaster-recovery/recovery-exercise-plan-v0.example.json'
class RecoveryExerciseTests(unittest.TestCase):
    def test_provider_plan_is_blocked_until_senior2_commands_exist(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); out=root/'result.json'
            r=subprocess.run([sys.executable,str(RUN),'--plan',str(PROVIDER),'--output',str(out),'--evidence-dir',str(root/'evidence')],cwd=ROOT,text=True,capture_output=True,check=False)
            self.assertEqual(r.returncode,2)
            payload=json.loads(out.read_text()); self.assertEqual(payload['status'],'blocked'); self.assertTrue(any('provider command missing' in f for f in payload['failures']))
    def test_mock_execution_proves_verify_freshness_and_cleanup(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); out=root/'result.json'; target=root/'isolated'
            r=subprocess.run([sys.executable,str(RUN),'--plan',str(MOCK),'--output',str(out),'--evidence-dir',str(root/'evidence'),'--target-root-override',str(target),'--allow-mock','--execute','--approval-ref','approval://test/recovery'],cwd=ROOT,text=True,capture_output=True,check=False)
            self.assertEqual(r.returncode,0,r.stderr)
            payload=json.loads(out.read_text()); self.assertEqual(payload['status'],'passed'); self.assertEqual(payload['verification']['status'],'passed'); self.assertEqual(payload['cleanup']['status'],'passed'); self.assertFalse(target.exists()); self.assertFalse(payload['mutation']['source_database_mutated'])
    def test_execute_requires_exact_approval(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); out=root/'result.json'
            r=subprocess.run([sys.executable,str(RUN),'--plan',str(MOCK),'--output',str(out),'--evidence-dir',str(root/'evidence'),'--target-root-override',str(root/'isolated'),'--allow-mock','--execute','--approval-ref','approval://wrong'],cwd=ROOT,text=True,capture_output=True,check=False)
            self.assertEqual(r.returncode,2); payload=json.loads(out.read_text()); self.assertFalse(payload['mutation']['isolated_target_mutated'])
if __name__=='__main__':unittest.main()
