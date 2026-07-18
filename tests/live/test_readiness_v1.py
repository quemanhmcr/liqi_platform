from __future__ import annotations
import json
import os
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
   'runtime-artifact':'15e2dd5a263decb91308a0d1783c4610bd7dc62d',
   'database-source':'168f6b3be66ff36eac4b4944f8d6940b6d2026ce',
   'database-integration':'168f6b3be66ff36eac4b4944f8d6940b6d2026ce',
   'native-source':'c73a4d15f366bc6675c36458642d3911a58a42fb',
   'native-safety':'c73a4d15f366bc6675c36458642d3911a58a42fb',
   'native-artifact':'c73a4d15f366bc6675c36458642d3911a58a42fb',
   'deployment-artifact':'ca99b7d14816cd051fce15a54accdeb17276096d',
   'infrastructure-source':'ca99b7d14816cd051fce15a54accdeb17276096d',
   'infrastructure-plan':'ca99b7d14816cd051fce15a54accdeb17276096d',
   'database-recovery':'2fd2db659335f740ac4d95a9065bd09be0f3f6ec',
  }
  for ident,commit in integrated.items():
   self.assertEqual('available',gates[ident]['provider_state'])
   self.assertEqual(commit,gates[ident]['provider_commit'])
  for ident in ('runtime-live-probe','host-readiness','rollback-evidence'):
   self.assertEqual('pending-live-evidence',gates[ident]['provider_state'])
   self.assertIsNotNone(gates[ident]['provider_commit'])

 def test_available_database_recovery_stays_blocked_without_protected_live_inputs(self):
  registry=json.loads((ROOT/'operations/readiness/provider-gates-v1.json').read_text(encoding='utf-8'))
  gate=next(item for item in registry['gates'] if item['id']=='database-recovery')
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);registry_path=root/'registry.json';out=root/'checkpoint.json'
   registry_path.write_text(json.dumps({'schema_version':'provider-gates-v1','registry_version':'test','gates':[gate]}),encoding='utf-8')
   result=subprocess.run([PYTHON,'operations/bin/run_provider_gates_v1.py','--registry',str(registry_path),'--stage','promotion','--output',str(out),'--evidence-dir',str(root/'evidence'),'--allow-blocked'],cwd=ROOT,text=True,capture_output=True,check=False,env={key:value for key,value in os.environ.items() if not key.startswith('LIQI_')})
   self.assertEqual(0,result.returncode,result.stderr)
   doc=json.loads(out.read_text());self.assertEqual('blocked',doc['status'])
   self.assertEqual('PROVIDER_INPUT_MISSING',doc['blockers'][0]['code'])
   self.assertFalse((root/'evidence/database-recovery.json').exists())

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
 def test_schema_valid_provider_blocked_status_is_not_promoted(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);script=root/'provider.py';registry_path=root/'registry.json';out=root/'checkpoint.json'
   sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
   document={
    'schema_version':'native-safety-result-v1','git_sha':sha,'status':'blocked',
    'started_at':'2026-07-18T00:00:00Z','completed_at':'2026-07-18T00:00:01Z',
    'toolchain':{'rust':'unavailable','cargo':'unavailable','elixir':'unavailable','otp':'unavailable','rustler':'0.38.0','nif_abi':'2.15'},
    'checks':[{'id':'environment','status':'blocked','command':['provider-test'],'duration_ms':0,'log_ref':'provider-test.log'}],
    'fuzz_seconds':1,'property_cases':2000,'failures':['Linux is required'],
   }
   script.write_text("import json,sys\njson.dump("+repr(document)+",open(sys.argv[1],'w'))\nraise SystemExit(69)\n",encoding='utf-8')
   source=json.loads((ROOT/'operations/readiness/provider-gates-v1.json').read_text(encoding='utf-8'))['gates'][0]
   gate=dict(source);gate.update({'id':'provider-status-test','owner':'Senior 3','seam':'schema-valid blocked result','stages':['source'],'provider_state':'available','provider_commit':'ca71a1be6914a33db22544802f704084f3346af5','mutation_class':'read-only','required_paths':['contracts/native/native-safety-result-v1.schema.json'],'argv':[PYTHON,str(script),'{output}'],'required_environment':[],'timeout_seconds':30,'result_schema':'contracts/native/native-safety-result-v1.schema.json','action_required':'Run on the required platform.'})
   registry_path.write_text(json.dumps({'schema_version':'provider-gates-v1','registry_version':'test','gates':[gate]}),encoding='utf-8')
   self.invoke(PYTHON,'operations/bin/run_provider_gates_v1.py','--registry',str(registry_path),'--stage','source','--output',str(out),'--evidence-dir',str(root/'evidence'),'--allow-blocked')
   doc=json.loads(out.read_text());self.assertEqual('blocked',doc['status']);self.assertEqual('blocked',doc['provider_results'][0]['status']);self.assertEqual('PROVIDER_GATE_BLOCKED',doc['blockers'][0]['code'])

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
