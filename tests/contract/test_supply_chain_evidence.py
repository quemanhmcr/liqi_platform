from __future__ import annotations
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];SCRIPT=ROOT/'scripts/release/validate_supply_chain_evidence.py';MANIFEST=ROOT/'tests/contract/fixtures/operations/release-manifest.valid.json';SBOM=ROOT/'tests/contract/fixtures/release-input/release.spdx.json';PROV=ROOT/'tests/contract/fixtures/release-input/release.intoto.jsonl'
class SupplyChainEvidenceTests(unittest.TestCase):
 def invoke(self,manifest:Path,sbom:Path,provenance:Path,out:Path):return subprocess.run([sys.executable,str(SCRIPT),'--manifest',str(manifest),'--sbom',str(sbom),'--provenance',str(provenance),'--output',str(out)],cwd=ROOT,text=True,capture_output=True,check=False)
 def test_valid_spdx_and_slsa_bind_all_artifacts(self):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'result.json';r=self.invoke(MANIFEST,SBOM,PROV,out);self.assertEqual(r.returncode,0,r.stderr);p=json.loads(out.read_text());self.assertEqual(p['status'],'passed');self.assertEqual(p['provenance']['subject_count'],3)
 def test_tampered_provenance_subject_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);statement=json.loads(PROV.read_text());statement['subject'][0]['digest']['sha256']='0'*64;bad=root/'bad.jsonl';bad.write_text(json.dumps(statement)+'\n');manifest=json.loads(MANIFEST.read_text());import hashlib;manifest['supply_chain']['provenance']['sha256']=hashlib.sha256(bad.read_bytes()).hexdigest();mp=root/'manifest.json';mp.write_text(json.dumps(manifest));out=root/'result.json';r=self.invoke(mp,SBOM,bad,out);self.assertEqual(r.returncode,1);self.assertIn('subjects do not match',r.stderr)
 def test_wrong_source_commit_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);statement=json.loads(PROV.read_text());statement['predicate']['buildDefinition']['resolvedDependencies'][0]['digest']['gitCommit']='0'*40;bad=root/'bad.jsonl';bad.write_text(json.dumps(statement)+'\n');manifest=json.loads(MANIFEST.read_text());import hashlib;manifest['supply_chain']['provenance']['sha256']=hashlib.sha256(bad.read_bytes()).hexdigest();mp=root/'manifest.json';mp.write_text(json.dumps(manifest));out=root/'result.json';r=self.invoke(mp,SBOM,bad,out);self.assertEqual(r.returncode,1);self.assertIn('gitCommit',r.stderr)
if __name__=='__main__':unittest.main()
