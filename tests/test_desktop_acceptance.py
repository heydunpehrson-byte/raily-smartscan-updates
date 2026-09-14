"""Behavioral acceptance checks. Every file and database is temporary."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from raily.brain import database, worker


class DesktopAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target, name, value in [(database, 'DB_PATH', self.root/'Data'/'raily.db'), (worker, 'BRAIN_ROOT', self.root)]:
            patcher = patch.object(target, name, value); patcher.start(); self.addCleanup(patcher.stop)
        database.initialize_database()

    def queue(self, name, content):
        folder = self.root/'incoming'; folder.mkdir(exist_ok=True)
        path = folder/name; path.write_bytes(content)
        conn = database.connect()
        conn.execute("INSERT INTO processing_jobs(document_name,status,stored_path,sha256) VALUES(?,'QUEUED',?,?)", (name, str(path), hashlib.sha256(content).hexdigest()))
        conn.commit(); conn.close()

    def test_no_filename_overwrite(self):
        self.queue('same.png', b'new contents')
        destination = self.root/'Documents'/'Railroads'/'Unassigned Railroad'/'General'
        destination.mkdir(parents=True)
        existing = destination/'same.png'; existing.write_bytes(b'original contents')
        with patch.object(worker, 'process_document', return_value={'review_required':False, 'railroad':'', 'location':''}):
            worker.process_one()
        self.assertEqual(existing.read_bytes(), b'original contents')

    def test_learned_rule_recognition(self):
        conn = database.connect()
        conn.execute("INSERT INTO learned_rules(rule_type,pattern,correction_json) VALUES('document','stable inspection header',?)", (json.dumps({'document_type':'Inspection'}),))
        conn.commit(); conn.close()
        self.queue('new.png', b'new contents')
        with patch.object(worker, 'process_document', return_value={'review_required':True, 'raw_text':'stable inspection header', 'ocr_confidence':98, 'railroad':'', 'location':''}):
            self.assertEqual(worker.process_one(), 'FILED')

if __name__ == '__main__': unittest.main()
