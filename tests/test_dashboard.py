import csv
import io
import json
import tempfile
import os
import subprocess
import sys
from unittest.mock import patch
import unittest
from pathlib import Path
from openpyxl import load_workbook
from dashboard.app import create_app, connect, process_one, safe_public_url

ROW={'place_id':'P1','cid':'C1','title':'قطعات تراکتور بغداد','phone':'+964 12345','emails':'hello@example.org','website':'https://example.org','address':'بغداد، عراق','link':'https://www.google.com/maps?cid=C1','category':'Tractor parts store'}
class FakeEngine:
    def __init__(self): self.created=[]; self.state='ok'; self.rows=[ROW]; self.fail=False
    def geocode(self,place): return '33.3152','44.3661'
    def call(self,method,path,body=None):
        if self.fail: raise ConnectionError('network denied')
        if method=='POST': self.created.append(body); return b'{"id":"test-id"}'
        if path.endswith('/download'):
            out=io.StringIO();w=csv.DictWriter(out,fieldnames=ROW.keys());w.writeheader();w.writerows(self.rows);return out.getvalue().encode()
        if path=='/api/v1/jobs':return b'[]'
        return json.dumps({'Status':self.state}).encode()

class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=str(Path(self.temp.name)/'leads.sqlite3');self.engine=FakeEngine()
        self.app=create_app({'TESTING':True,'DB':self.path,'ENGINE':self.engine});self.client=self.app.test_client();self.client.get('/')
        with self.client.session_transaction() as s:self.token=s['csrf']
        self.headers={'X-CSRF-Token':self.token}
    def tearDown(self):self.temp.cleanup()
    def get(self,path):return self.client.get('/api/'+path,headers=self.headers)
    def project(self,**kwargs):
        body={'name':'بازار عراق','country':'Iraq','category':'قطعات تراکتور','cities':['Baghdad'],'keywords':['قطع غيار جرارات'],'depth':5,'consent':True};body.update(kwargs)
        return self.client.post('/api/projects',json=body,headers=self.headers)
    def test_local_security(self):
        self.assertEqual(self.client.get('/api/leads').status_code,403)
        self.assertEqual(self.client.get('/',headers={'Host':'evil.test'}).status_code,403)
        self.assertEqual(self.client.post('/api/projects',headers={**self.headers,'Origin':'https://evil.test'},json={}).status_code,403)
        self.assertEqual(self.client.get('/health').status_code,200)
    def test_multilingual_queue_and_limits(self):
        self.assertEqual(self.project(cities=['Baghdad','Basra','Baghdad'],keywords=['قطع غيار','tractor parts','traktör','ٹریکٹر','تراکتور']).status_code,201)
        self.assertEqual(len(self.get('jobs').json),10)
        self.assertEqual(self.project(depth=50).status_code,400)
        self.assertEqual(self.project(consent=False).status_code,400)
        self.assertEqual(self.project(keywords=['x'+str(i) for i in range(21)]).status_code,400)
    def test_worker_persistence_and_manual_fields(self):
        self.project();process_one(self.app)
        self.assertEqual(self.get('jobs').json[0]['state'],'done');self.assertEqual(self.engine.created[0]['depth'],5)
        self.assertIn('Baghdad, Iraq',self.engine.created[0]['keywords'][0]);lead=self.get('leads').json[0]
        response=self.client.patch('/api/leads/'+str(lead['id']),headers=self.headers,json={'notes':'پیگیری شنبه','tags':'مهم','status':'followup','name':'نام اصلاح‌شده'})
        self.assertEqual(response.status_code,200);self.project();process_one(self.app)
        rows=self.get('leads').json;self.assertEqual(len(rows),1);self.assertEqual(rows[0]['notes'],'پیگیری شنبه');self.assertEqual(rows[0]['name'],'نام اصلاح‌شده')
        self.assertEqual(len(self.get('leads?q=مهم&status=followup&project=2').json),1)
        restored=create_app({'TESTING':True,'DB':self.path,'ENGINE':self.engine})
        with connect(restored.config['DB']) as db:self.assertEqual(db.execute('SELECT count(*) FROM leads').fetchone()[0],1)
    def test_resume_existing_engine_job(self):
        self.project();self.engine.state='working';process_one(self.app);self.assertEqual(len(self.engine.created),1)
        self.engine.state='ok';process_one(self.app);self.assertEqual(len(self.engine.created),1);self.assertEqual(self.get('jobs').json[0]['state'],'done')
    def test_failed_and_empty_are_not_success(self):
        self.project();self.engine.fail=True;process_one(self.app);self.assertEqual(self.get('jobs').json[0]['state'],'failed')
        self.engine.fail=False;self.engine.rows=[];self.project();process_one(self.app)
        job=self.get('jobs').json[0];self.assertEqual(job['state'],'empty');self.assertTrue(job['error'])
    def test_engine_failed(self):
        self.project();self.engine.state='failed';process_one(self.app);self.assertEqual(self.get('jobs').json[0]['state'],'failed')
    def test_reliable_identifier_alias(self):
        self.project();process_one(self.app);self.engine.rows=[{**ROW,'place_id':''}];self.project();process_one(self.app)
        self.assertEqual(len(self.get('leads').json),1)
    def test_exports_unicode_and_formula_safety(self):
        self.engine.rows=[{**ROW,'title':'=1+1'}];self.project();process_one(self.app)
        response=self.get('export/csv');self.assertTrue(response.data.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))));self.assertEqual(rows[0]['name'],"'=1+1")
        wb=load_workbook(io.BytesIO(self.get('export/xlsx').data));ws=wb.active
        self.assertEqual(ws.max_row,2);self.assertEqual(ws['A2'].value,'=1+1');self.assertEqual(ws['A2'].data_type,'s');self.assertEqual(ws['E2'].value,ROW['address'])
    def test_edit_validation(self):
        self.project();process_one(self.app)
        self.assertEqual(self.client.patch('/api/leads/1',json={'status':'bad'},headers=self.headers).status_code,400)
        self.assertEqual(self.client.patch('/api/leads/1',json={'id':'2'},headers=self.headers).status_code,400)
    def test_private_social_url_rejected(self):
        with self.assertRaises(ValueError):safe_public_url('file:///etc/passwd')
        with self.assertRaises(ValueError):safe_public_url('http://127.0.0.1/')
    def test_health_engine_unavailable(self):
        self.engine.fail=True;self.assertEqual(self.get('health').status_code,503)

    def test_stalled_job_stops_without_new_scrape(self):
        self.project()
        with connect(self.path) as db:
            db.execute("UPDATE jobs SET state='working',engine_id='old-job',updated='2020-01-01T00:00:00+00:00'")
        process_one(self.app)
        self.assertEqual(self.get('jobs').json[0]['state'],'failed')
        self.assertEqual(len(self.engine.created),0)
    def test_backup_restore(self):
        from dashboard.restore import restore
        self.project();process_one(self.app);self.project()
        data=subprocess.check_output([sys.executable,'-m','dashboard.backup'],env={**os.environ,'DASHBOARD_DB':self.path})
        backup=Path(self.temp.name)/'backup.sqlite3';backup.write_bytes(data)
        target=Path(self.temp.name)/'restored.sqlite3'
        restore(backup,target)
        with connect(target) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM leads').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0],0)
        invalid=Path(self.temp.name)/'invalid.sqlite3';invalid.write_bytes(b'bad')
        with self.assertRaises(Exception):restore(invalid,target)
        with connect(target) as db:self.assertEqual(db.execute('SELECT count(*) FROM leads').fetchone()[0],1)
    def test_social_failure_preserves_results(self):
        self.project(socials=True)
        with patch('dashboard.app.social_profiles',side_effect=ConnectionError('blocked')):process_one(self.app)
        self.assertEqual(self.get('jobs').json[0]['state'],'done')
        self.assertTrue(self.get('jobs').json[0]['error'])
        self.assertEqual(len(self.get('leads').json),1)
    def test_invalid_lists(self):
        self.assertEqual(self.project(cities='Baghdad').status_code,400)

if __name__=='__main__':unittest.main()
