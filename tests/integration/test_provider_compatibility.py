from __future__ import annotations
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'scripts/operations/validate_provider_compatibility.py'
FIX=ROOT/'tests/contract/fixtures/provider-compatibility'
class ProviderCompatibilityTests(unittest.TestCase):
    def invoke(self,provider:Path,allow:bool=False):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'result.json'; cmd=[sys.executable,str(SCRIPT),'--provider-root',str(provider),'--output',str(out)]
            if allow:cmd.append('--allow-missing')
            completed=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,check=False)
            return completed,json.loads(out.read_text(encoding='utf-8'))
    def test_compatible_provider_seams_pass(self):
        result,payload=self.invoke(FIX/'compatible');self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(payload['overall_status'],'passed')
    def test_current_known_provider_mismatches_are_owner_attributed(self):
        result,payload=self.invoke(FIX/'incompatible');self.assertEqual(result.returncode,1);self.assertEqual(payload['overall_status'],'failed');owners={c['owner'] for c in payload['checks'] if c['status']=='failed'};self.assertEqual(owners,{'Senior 1','Senior 2','Senior 3'})
    def test_missing_provider_is_blocked_not_emulated(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,payload=self.invoke(Path(tmp),allow=True);self.assertEqual(result.returncode,0);self.assertEqual(payload['overall_status'],'blocked')
if __name__=='__main__':unittest.main()
