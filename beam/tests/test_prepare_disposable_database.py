import json,os,stat,tempfile,unittest
from pathlib import Path
from beam.scripts.prepare_disposable_database import parse_admin_url,write_inputs
class PrepareDisposableDatabaseTest(unittest.TestCase):
    def test_writes_role_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            result=write_inputs('postgres://postgres@127.0.0.1:5432/liqi_v1_ci',Path(directory),'liqi_v1_test_runtime')
            bundle_path=Path(result['bundle_path']); runtime_path=Path(result['runtime_config_path'])
            bundle=json.loads(bundle_path.read_text()); runtime=json.loads(runtime_path.read_text())
            self.assertEqual(set(bundle),{'command','realtime','worker'}); self.assertIn('liqi_api@127.0.0.1:5432/liqi_v1_test_runtime',bundle['command']); self.assertEqual(runtime['database']['credentialFormat'],'role-url-bundle-v1')
            if os.name!='nt': self.assertEqual(stat.S_IMODE(bundle_path.stat().st_mode),0o600)
    def test_rejects_unsafe_admin_urls(self):
        for value in ['postgres://postgres@db.internal/liqi_v1_ci','postgres://postgres:secret@127.0.0.1/liqi_v1_ci','postgres://postgres@127.0.0.1/liqi_v1_ci?x=1','postgres://postgres@127.0.0.1/liqi']:
            with self.subTest(value=value),self.assertRaises(ValueError): parse_admin_url(value)
    def test_rejects_unsafe_target(self):
        with tempfile.TemporaryDirectory() as directory,self.assertRaises(ValueError): write_inputs('postgres://postgres@127.0.0.1/liqi_v1_ci',Path(directory),'liqi')
if __name__=='__main__': unittest.main()
