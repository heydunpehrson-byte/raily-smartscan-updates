import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient
from raily.brain import database, intake, worker


class ReviewFilingTests(unittest.TestCase):
    def test_blank_optional_fields_and_slash_category(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(database, 'DB_PATH', root/'Data'/'raily.db'), patch.object(intake, 'BRAIN_ROOT', root), patch.object(intake, 'router', APIRouter()):
                database.initialize_database()
                intake.ensure_intake_schema()
                source = root/'sample.png'
                source.write_bytes(b'preserved submitted document')
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                conn = database.connect()
                conn.execute("INSERT INTO processing_jobs(id,document_name,original_name,status,stored_path,sha256) VALUES(1,'sample.png','sample.png','CONDUCTOR REVIEW',?,?)", (str(source), digest))
                conn.commit(); conn.close()
                app = FastAPI()
                app.include_router(intake.build_router(lambda: {'role':'Administrator','username':'test','workstation_id':'test'}, lambda *args: None))
                with TestClient(app) as client:
                    self.assertEqual(client.post('/review/1', json={}).status_code, 400)
                    response = client.post('/review/1', json={'document_type':'Work Log / Start Count Log','railroad':'','location':'','date':'','name':'','teach':False})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['status'], 'FILED')
                    self.assertEqual(client.post('/review/1', json={'document_type':'Work Log'}).status_code, 404)
                conn = database.connect()
                job = conn.execute('SELECT * FROM processing_jobs').fetchone()
                target = Path(job['stored_path'])
                self.assertEqual(target.parent, root/'Documents'/'Railroads'/'Unassigned Railroad'/'General')
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), digest)
                self.assertEqual(json.loads(job['metadata_json'])['railroad'], '')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM processing_jobs').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM learned_rules').fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events WHERE action='JOB_REVIEW_APPROVED'").fetchone()[0], 1)
                conn.close()
                # A normal queued job (also the retry/autoclassification path)
                # must land beside the reviewed file, not in legacy folders.
                automatic = root/'automatic.png'
                automatic.write_bytes(b'automatic document')
                conn = database.connect()
                conn.execute("INSERT INTO processing_jobs(document_name,status,stored_path,sha256) VALUES('automatic.png','QUEUED',?,?)", (str(automatic), hashlib.sha256(automatic.read_bytes()).hexdigest()))
                conn.commit(); conn.close()
                with patch.object(worker, 'BRAIN_ROOT', root), patch.object(worker, 'process_document', return_value={'railroad':'', 'location':'', 'review_required':False}):
                    self.assertEqual(worker.process_one(), 'FILED')
                conn = database.connect()
                auto_job = conn.execute('SELECT stored_path FROM processing_jobs WHERE id=2').fetchone()
                self.assertEqual(Path(auto_job['stored_path']).parent, target.parent)
                self.assertTrue(Path(auto_job['stored_path']).is_file())
                self.assertFalse((root/'Documents'/'Railroads'/'Unknown Railroad').exists())
                conn.close()

if __name__ == '__main__': unittest.main()
