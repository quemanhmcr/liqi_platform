from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PYTHON=sys.executable
sys.path.insert(0,str(Path(__file__).resolve().parent))
import evidence_factory as factory  # noqa: E402

class ReadinessV1Tests(unittest.TestCase):
 def invoke(self,*args:str,expected:int=0):
  result=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,check=False)
  self.assertEqual(expected,result.returncode,f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
  return result
 def write(self,root:Path,name:str,document:dict)->Path:
  path=root/name;path.write_text(json.dumps(document),encoding='utf-8');return path
 def complete_args(self,root:Path,mutator=None):
  args=[PYTHON,'operations/bin/compose_readiness_v1.py','--git-sha',factory.SHA,'--release-id',factory.RELEASE_ID,'--environment','production','--now',factory.NOW]
  docs=factory.primary_documents()
  if mutator:mutator(docs)
  for kind,doc in docs.items():
   path=self.write(root,f'{kind}.json',doc);args+=['--evidence',f'{kind}={path}']
  for name in factory.CHECKPOINTS:
   path=self.write(root,f'checkpoint-{name}.json',factory.checkpoint(name));args+=['--checkpoint',f'{name}={path}']
  compat=self.write(root,'compatibility.json',factory.compatibility());mut=self.write(root,'mutations.json',factory.mutation_log());out=root/'final.json'
  args+=['--compatibility',str(compat),'--oci-mutations',str(mut),'--output',str(out)]
  return args,out
 def test_source_contracts_validate(self):
  self.invoke(PYTHON,'operations/bin/validate_readiness_v1.py')
 def test_provider_registry_records_integrated_commits_and_pending_evidence(self):
  registry=json.loads((ROOT/'operations/readiness/provider-gates-v1.json').read_text(encoding='utf-8'))
  gates={item['id']:item for item in registry['gates']}
  integrated={
   'runtime-source':'15e2dd5a263decb91308a0d1783c4610bd7dc62d',
   'runtime-integration':'15e2dd5a263decb91308a0d1783c4610bd7dc62d',
   'database-source':'168f6b3be66ff36eac4b4944f8d6940b6d2026ce',
   'native-source':'7478e31a4de48e278f0d08885bfaab56d5d88762',
   'infrastructure-source':'19b06788e0a5d7695fc2f89102af8e75129d39af',
  }
  for ident,commit in integrated.items():
   self.assertEqual('available',gates[ident]['provider_state'])
   self.assertEqual(commit,gates[ident]['provider_commit'])
  for ident in ('runtime-artifact','runtime-live-probe','native-artifact','infrastructure-plan','host-readiness','rollback-evidence'):
   self.assertEqual('pending-live-evidence',gates[ident]['provider_state'])
   self.assertIsNotNone(gates[ident]['provider_commit'])
  for gate in gates.values():
   if gate['provider_state']=='pending-provider-publication':
    self.assertIsNone(gate['provider_commit'])

 def test_unpublished_provider_seams_are_blocked_with_owners(self):
  registry=json.loads((ROOT/'operations/readiness/provider-gates-v1.json').read_text(encoding='utf-8'))
  gates={item['owner']:dict(item) for item in registry['gates'] if item['id'] in {'runtime-source','database-integration','native-safety','infrastructure-source'}}
  for gate in gates.values():
   gate['stages']=['source']
   gate['provider_state']='pending-provider-publication'
   gate['provider_commit']=None
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);registry_path=root/'registry.json';out=root/'checkpoint.json'
   registry_path.write_text(json.dumps({'schema_version':'provider-gates-v1','registry_version':'test','gates':list(gates.values())}),encoding='utf-8')
   self.invoke(PYTHON,'operations/bin/run_provider_gates_v1.py','--registry',str(registry_path),'--stage','source','--output',str(out),'--evidence-dir',str(root/'evidence'),'--allow-blocked')
   doc=json.loads(out.read_text());self.assertEqual('blocked',doc['status']);self.assertEqual({'Senior 1','Senior 2','Senior 3','Senior 4'},{item['owner'] for item in doc['blockers']})
   codes={item['owner']:item['code'] for item in doc['blockers']}
   for owner in ('Senior 1','Senior 2','Senior 3','Senior 4'):self.assertEqual('PROVIDER_SEAM_UNPUBLISHED',codes[owner])
 def test_missing_evidence_is_not_ready(self):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'final.json'
   self.invoke(PYTHON,'operations/bin/compose_readiness_v1.py','--git-sha',factory.SHA,'--release-id',factory.RELEASE_ID,'--environment','production','--now',factory.NOW,'--output',str(out),'--allow-not-ready')
   doc=json.loads(out.read_text());self.assertEqual('blocked',doc['status']);self.assertEqual('V1 NOT READY',doc['verdict']);self.assertTrue(any(x['code']=='REQUIRED_EVIDENCE_MISSING' for x in doc['blockers']))
 def test_complete_exact_release_bundle_can_pass(self):
  with tempfile.TemporaryDirectory() as tmp:
   args,out=self.complete_args(Path(tmp));self.invoke(*args)
   doc=json.loads(out.read_text());self.assertEqual('passed',doc['status']);self.assertEqual('V1 PRODUCTION-SHAPED ON OCI',doc['verdict']);self.assertEqual([],doc['blockers'])
 def test_synthetic_pass_claim_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   def mutate(docs):docs['load']['evidence_mode']='synthetic'
   args,out=self.complete_args(Path(tmp),mutate);self.invoke(*args,expected=1)
   doc=json.loads(out.read_text());self.assertEqual('failed',doc['status']);self.assertEqual('V1 NOT READY',doc['verdict']);self.assertTrue(any(x['code']=='EVIDENCE_INVALID' and x['owner']=='Senior 5' for x in doc['blockers']))

if __name__=='__main__':unittest.main()
