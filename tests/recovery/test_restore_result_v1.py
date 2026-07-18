from __future__ import annotations
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];PYTHON=sys.executable;sys.path.insert(0,str(ROOT/'tests/live'))
import evidence_factory as factory  # noqa: E402
class RestoreResultTests(unittest.TestCase):
 def invoke(self,doc,expected):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'recovery.json';path.write_text(json.dumps(doc),encoding='utf-8')
   result=subprocess.run([PYTHON,'tests/recovery/validate_restore_result_v1.py','--result',str(path),'--git-sha',factory.SHA,'--release-id',factory.RELEASE_ID,'--environment',factory.ENVIRONMENT],cwd=ROOT,text=True,capture_output=True,check=False)
   self.assertEqual(expected,result.returncode,result.stderr)
 def test_passed_isolated_restore_validates(self):self.invoke(factory.recovery(),0)
 def test_source_database_mutation_fails(self):
  doc=factory.recovery();doc['source']['mutated']=True;self.invoke(doc,1)
if __name__=='__main__':unittest.main()
