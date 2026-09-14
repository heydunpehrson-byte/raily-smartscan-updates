import threading
from pathlib import Path
from unittest.mock import patch
import raily.brain.database as database
import raily.brain.worker as worker

def test_claim_only_queued_and_error_is_explicitly_terminal(tmp_path):
    with patch.object(database, "BRAIN_ROOT", tmp_path), patch.object(database, "DB_PATH", tmp_path / "Data" / "raily.db"), patch.object(worker, "connect", database.connect):
        database.initialize_database()
        c = database.connect()
        c.execute("INSERT INTO processing_jobs(document_name,status) VALUES('failed.png','ERROR')")
        c.commit(); c.close()
        assert worker.claim_job() is None
