from __future__ import annotations
import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];SCRIPT=ROOT/'scripts/operations/assemble_source_readiness.py'
PROVIDER=ROOT/'tests/contract/fixtures/operations/integration-result.valid.json';COMPAT=ROOT/'tests/contract/fixtures/operations/provider-compatibility-result.valid.json';CAPACITY=ROOT/'tests/contract/fixtures/operations/capacity-result.valid.json';AVAILABLE=ROOT/'tests/contract/fixtures/capacity-registry/available.json';BLOCKED=ROOT/'tests/contract/fixtures/capacity-registry/blocked.json'
class SourceReadinessTests(unittest.TestCase):
 def write(self,root:Path,name:str,value:dict)->Path:
  path=root/name;path.write_text(json.dumps(value),encoding='utf-8');return path
 def invoke(self,provider:Path,compat:Path,capacity:Path,registry:Path,allow=False):
  out=provider.parent/'readiness.json';cmd=[sys.executable,str(SCRIPT),'--provider-result',str(provider),'--compatibility-result',str(compat),'--capacity-result',str(capacity),'--capacity-registry',str(registry),'--output',str(out)]+(['--allow-blocked'] if allow else []);r=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,check=False);return r,json.loads(out.read_text())
 def passed_provider(self)->dict:
  p=copy.deepcopy(json.loads(PROVIDER.read_text()));p['mode']='provider';p['overall_status']='passed';p['violations']=[]
  for item in p['provider_results']:
   item['command']='python provider-validator.py';item['status']='passed';item['exit_code']=0;item['failure_class']=None
  return p
 def test_all_checkpoints_pass(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);provider=self.write(root,'provider.json',self.passed_provider());r,p=self.invoke(provider,COMPAT,CAPACITY,AVAILABLE);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(p['status'],'passed');self.assertEqual(p['blockers'],[])
 def test_pending_capacity_and_provider_are_blocked_with_owners(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);provider_doc=self.passed_provider();provider_doc['overall_status']='blocked';provider_doc['provider_results'][0]['status']='blocked';provider_doc['provider_results'][0]['exit_code']=None;provider_doc['violations']=[{'owner':'Senior 3','seam':'runtime source','code':'PROVIDER_SEAM_MISSING','message':'runtime seam absent','action_required':'Senior 3 publish runtime'}];provider=self.write(root,'provider.json',provider_doc);capacity_doc=json.loads(CAPACITY.read_text());capacity_doc['status']='blocked';capacity_doc['totals']={'ocpu':0,'memory_mib':0,'disk_gib':0,'postgres_connections':0};capacity_doc['steady_state_totals']=dict(capacity_doc['totals']);capacity_doc['hard_limit_totals']=dict(capacity_doc['totals']);capacity_doc['postgres_connection_accounting']={'server_reservation':0,'pooled_server_capacity':0,'pooled_runtime_demand':0,'direct_reserved_capacity':0};capacity_doc['components']=[];capacity_doc['failures']=['Senior 1 infrastructure pending'];capacity=self.write(root,'capacity.json',capacity_doc);r,p=self.invoke(provider,COMPAT,capacity,BLOCKED,True);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(p['status'],'blocked');owners={x['owner'] for x in p['blockers']};self.assertIn('Senior 1',owners);self.assertIn('Senior 3',owners)
 def test_present_compatibility_failure_fails(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);provider=self.write(root,'provider.json',self.passed_provider());compat_doc=json.loads(COMPAT.read_text());compat_doc['overall_status']='failed';compat_doc['checks'][0].update({'status':'failed','code':'HOST_POLICY_INVALID','message':'host policy mismatch','action_required':'Senior 1 repair host policy'});compat=self.write(root,'compat.json',compat_doc);r,p=self.invoke(provider,compat,CAPACITY,AVAILABLE,True);self.assertEqual(r.returncode,1);self.assertEqual(p['status'],'failed');self.assertTrue(any(x['owner']=='Senior 1' and x['severity']=='failed' for x in p['blockers']))
if __name__=='__main__':unittest.main()
