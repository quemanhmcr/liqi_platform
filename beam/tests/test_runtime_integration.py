import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlunsplit
from jsonschema import Draft202012Validator,FormatChecker
from beam.scripts import run_v1_integration as integration

def admin_dsn(host='127.0.0.1',database='liqi_v1_ci'):
    return urlunsplit(('postgres','postgres@'+host+':5432','/'+database,'',''))

class RuntimeIntegrationTest(unittest.TestCase):
    def test_missing_database_url_emits_blocked_evidence(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{},clear=False):
            previous=os.environ.pop('LIQI_TEST_DATABASE_URL',None); output=Path(directory)/'result.json'
            try: rc=integration.main(['--output',str(output)])
            finally:
                if previous is not None: os.environ['LIQI_TEST_DATABASE_URL']=previous
            result=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(rc,2); self.assertEqual(result['status'],'blocked')

    def test_unsafe_remote_target_fails_before_tooling_lookup(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LIQI_TEST_DATABASE_URL':admin_dsn(host='database.internal')},clear=False),patch.object(integration.shutil,'which',return_value=None):
            output=Path(directory)/'result.json'; rc=integration.main(['--output',str(output)]); result=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(rc,1); self.assertEqual(result['checks'][0]['name'],'disposable-database-safety')

    def test_missing_tooling_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'LIQI_TEST_DATABASE_URL':admin_dsn()},clear=False),patch.object(integration.shutil,'which',return_value=None):
            output=Path(directory)/'result.json'; rc=integration.main(['--output',str(output)]); result=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(rc,2); self.assertEqual(result['status'],'blocked')

    def test_redaction_removes_target_material(self):
        value=integration.redact('failed role-url-value and admin-url-value',['role-url-value','admin-url-value'])
        self.assertNotIn('url-value',value); self.assertIn('<redacted>',value)

    def test_result_document_matches_schema(self):
        with patch.object(integration,'git_sha',return_value='a'*40): document=integration.result_document('blocked',[{'name':'tooling','status':'blocked'}],['missing'])
        schema=json.loads(integration.RESULT_SCHEMA.read_text(encoding='utf-8'))
        self.assertEqual(list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(document)),[])
if __name__=='__main__': unittest.main()
