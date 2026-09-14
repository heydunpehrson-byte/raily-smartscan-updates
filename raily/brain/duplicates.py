"""Exact content matching using durable intake hashes."""
import hashlib
import json
import shutil
import uuid
from pathlib import Path


def route_duplicate(conn, root, job, source, digest):
    match = conn.execute("SELECT id, stored_path FROM processing_jobs WHERE sha256=? AND id<>? AND status IN ('FILED','CONDUCTOR REVIEW') ORDER BY CASE status WHEN 'FILED' THEN 0 ELSE 1 END, id LIMIT 1", (digest, job['id'])).fetchone()
    if not match:
        return None
    destination = Path(root)/'Documents'/'Duplicates'
    destination.mkdir(parents=True, exist_ok=True)
    target = destination/f"{job['id']}__{uuid.uuid4().hex}__{source.name}"
    with source.open('rb') as input_file, target.open('xb') as output:
        shutil.copyfileobj(input_file, output)
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError('Duplicate destination verification failed; source preserved')
    details = {'matched_job_id': match['id'], 'matched_path': match['stored_path'], 'sha256': digest}
    conn.execute("UPDATE processing_jobs SET status='DUPLICATE SIDING', stored_path=?, duplicate_of_job_id=?, error_message=?, updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE id=?", (str(target), match['id'], json.dumps(details), job['id']))
    conn.execute("INSERT INTO audit_events(username,workstation,action,details) VALUES(?,?,?,?)", (job.get('submitted_by'), job.get('source_workstation'), 'EXACT_DUPLICATE_DETECTED', json.dumps({'job_id':job['id'], **details})))
    # Keep the source intake copy as recovery evidence; duplicate storage is
    # the authoritative location. Never delete the original accepted document.
    return match['id']
