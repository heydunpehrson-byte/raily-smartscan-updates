from __future__ import annotations
import hashlib
import shutil
import threading
import logging
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .database import BRAIN_ROOT, connect
from raily.engine.adapter import process_document
from raily.filing import resolve_destination
from .duplicates import route_duplicate
from .learning import apply_rules

REVIEW = "CONDUCTOR REVIEW"
LOG = logging.getLogger("raily.worker")
if not LOG.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s worker: %(message)s")

def claim_job():
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM processing_jobs WHERE status='QUEUED' ORDER BY id LIMIT 1").fetchone()
        if not row: conn.rollback(); return None
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE processing_jobs SET status='PROCESSING', updated_at=? WHERE id=? AND status='QUEUED'", (now, row['id']))
        if conn.total_changes != 1: conn.rollback(); return None
        conn.commit(); LOG.info("claimed job %s (%s)", row['id'], row['document_name']); return dict(row)
    finally: conn.close()

def _finish(job, status, conn=None, **fields):
    owned = conn is None
    conn = conn or connect()
    try:
        sets = ["status=?", "updated_at=?"] + [f"{k}=?" for k in fields]
        conn.execute(f"UPDATE processing_jobs SET {', '.join(sets)} WHERE id=?", (status, datetime.now(timezone.utc).isoformat(), *fields.values(), job['id']))
        if owned: conn.commit()
    finally:
        if owned: conn.close()

def process_one(job=None):
    job = job or claim_job()
    if not job: return None
    source = Path(job.get('stored_path') or '')
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        if not source.exists(): raise FileNotFoundError(str(source))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if job.get('sha256') and digest != job['sha256']: raise ValueError('SHA-256 verification failed')
        if route_duplicate(conn, BRAIN_ROOT, job, source, digest):
            conn.commit()
            LOG.info('job %s -> DUPLICATE SIDING', job['id'])
            return 'DUPLICATE SIDING'
        conn.commit()
        result = process_document(source)
        result = apply_rules(conn, result, job.get('original_name') or job['document_name'])
        # OCR holds no database lock. Serialize the final check and transition
        # so concurrent workers cannot both accept identical queued uploads.
        conn.execute('BEGIN IMMEDIATE')
        if route_duplicate(conn, BRAIN_ROOT, job, source, digest):
            conn.commit()
            return 'DUPLICATE SIDING'
        if result['review_required']:
            _finish(job, REVIEW, conn=conn, error_message='Required filing metadata needs conductor review', raw_ocr_context=result.get('raw_text',''), cleaned_ocr_context=result.get('cleaned_text',''), ocr_confidence=result.get('ocr_confidence',0), metadata_json=json.dumps(result))
            conn.commit()
            LOG.info("job %s -> %s", job['id'], REVIEW); return REVIEW
        destination = resolve_destination(BRAIN_ROOT, result.get('railroad'), result.get('location'))
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        while True:
            try:
                with target.open('xb') as output, source.open('rb') as input_file:
                    shutil.copyfileobj(input_file, output)
                break
            except FileExistsError:
                target = destination / f'{job["id"]}__{uuid.uuid4().hex}__{source.name}'
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise IOError('Destination hash verification failed; source preserved')
        if not target.exists(): raise IOError('Destination verification failed')
        _finish(job, 'FILED', conn=conn, stored_path=str(target), error_message=None, metadata_json=json.dumps(result), raw_ocr_context=result.get('raw_text',''), cleaned_ocr_context=result.get('cleaned_text',''), ocr_confidence=result.get('ocr_confidence'))
        conn.execute('INSERT INTO audit_events(username,workstation,action,details) VALUES(?,?,?,?)', (job.get('submitted_by'), job.get('source_workstation'), 'JOB_FILED', json.dumps({'job_id':job['id'], 'destination':str(target), 'learned_rule_ids':result.get('learned_rule_ids', [])})))
        conn.commit()
        LOG.info("job %s -> FILED (%s)", job['id'], target); return 'FILED'
    except Exception as exc:
        conn.rollback()
        LOG.exception("job %s -> ERROR: %s", job['id'], exc); _finish(job, 'ERROR', error_message=str(exc)); return 'ERROR'
    finally:
        conn.close()

def worker_loop(stop_event: threading.Event):
    LOG.info("worker started; database=%s", BRAIN_ROOT / 'Data' / 'raily.db')
    while not stop_event.is_set():
        try:
            process_one()
        except Exception:
            LOG.exception('Worker poll failed; will retry after a short pause')
            stop_event.wait(2)
        stop_event.wait(0.25)

if __name__ == "__main__":
    stop = threading.Event()
    try:
        worker_loop(stop)
    except KeyboardInterrupt:
        stop.set()
