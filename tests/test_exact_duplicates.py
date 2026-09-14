import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from raily.brain import database, worker


class DuplicateTests(unittest.TestCase):
    def test_identical_bytes_different_names_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(database, 'DB_PATH', root/'Data'/'raily.db'), patch.object(worker, 'BRAIN_ROOT', root):
                database.initialize_database()
                digest = hashlib.sha256(b'identical contents').hexdigest()
                def submit(name):
                    source = root/name; source.write_bytes(b'identical contents')
                    conn = database.connect()
                    conn.execute("INSERT INTO processing_jobs(document_name,status,stored_path,sha256) VALUES(?,'QUEUED',?,?)", (name,str(source),digest))
                    conn.commit(); conn.close()
                submit('first.png')
                with patch.object(worker, 'process_document', return_value={'review_required':False,'railroad':'','location':''}):
                    self.assertEqual(worker.process_one(), 'FILED')
                # Reinitialize schema and reopen connections: no in-memory index.
                database.initialize_database()
                submit('completely-different-name.png')
                with patch.object(worker, 'process_document', side_effect=AssertionError('Duplicate must skip OCR')):
                    self.assertEqual(worker.process_one(), 'DUPLICATE SIDING')
                conn = database.connect()
                original, duplicate = conn.execute('SELECT * FROM processing_jobs ORDER BY id').fetchall()
                self.assertEqual(duplicate['duplicate_of_job_id'], original['id'])
                self.assertEqual(Path(original['stored_path']).read_bytes(), b'identical contents')
                self.assertEqual(Path(duplicate['stored_path']).read_bytes(), b'identical contents')
                self.assertEqual(Path(duplicate['stored_path']).parent, root/'Documents'/'Duplicates')
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM processing_jobs WHERE status='FILED'").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events WHERE action='EXACT_DUPLICATE_DETECTED'").fetchone()[0], 1)
                conn.close()
