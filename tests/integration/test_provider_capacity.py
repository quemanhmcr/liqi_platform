from __future__ import annotations
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];SCRIPT=ROOT/'scripts/operations/collect_provider_capacity.py';FIX=ROOT/'tests/contract/fixtures/capacity-registry'
class ProviderCapacityTests(unittest.TestCase):
 def invoke(self,registry:Path,allow=False):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'capacity.json';cmd=[sys.executable,str(SCRIPT),'--registry',str(registry),'--output',str(out)]+(['--allow-blocked'] if allow else []);r=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,check=False);return r,json.loads(out.read_text())
 def test_all_provider_budgets_aggregate(self):
  r,p=self.invoke(FIX/'available.json');self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(p['status'],'passed');self.assertLessEqual(p['totals']['ocpu'],3);self.assertLessEqual(p['totals']['memory_mib'],20480)
 def test_pending_provider_is_blocked(self):
  r,p=self.invoke(FIX/'blocked.json',True);self.assertEqual(r.returncode,0);self.assertEqual(p['status'],'blocked');self.assertTrue(any('Senior 1' in f for f in p['failures']))
 def test_strict_pending_provider_fails(self):
  r,p=self.invoke(FIX/'blocked.json');self.assertEqual(r.returncode,2);self.assertEqual(p['status'],'blocked')
if __name__=='__main__':unittest.main()
