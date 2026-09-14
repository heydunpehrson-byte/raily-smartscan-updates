import hashlib
import os
import re
import sqlite3
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .database import BRAIN_ROOT, connect


router = APIRouter()

UPLOAD_ROOT = BRAIN_ROOT / "Uploads"
INCOMING_ROOT = BRAIN_ROOT / "Documents" / "Incoming"

UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
INCOMING_ROOT.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
}

MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def safe_filename(name: str) -> str:
    name = Path(name or "document").name
    name = re.sub(r"[^A-Za-z0-9._() -]+", "_", name)
    name = name.strip(" .")

    if not name:
        name = "document"

    return name[:180]


def ensure_intake_schema():
    conn = connect()

    try:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(processing_jobs)"
            ).fetchall()
        }

        additions = {
            "job_uuid": "TEXT",
            "original_name": "TEXT",
            "stored_path": "TEXT",
            "sha256": "TEXT",
            "size_bytes": "INTEGER",
            "submitted_by": "TEXT",
            "error_message": "TEXT",
            "metadata_json": "TEXT",
            "review_reason": "TEXT",
            "raw_ocr_context": "TEXT", "cleaned_ocr_context": "TEXT", "ocr_confidence": "REAL",
        }

        for column, definition in additions.items():
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE processing_jobs "
                    f"ADD COLUMN {column} {definition}"
                )

        conn.commit()

    finally:
        conn.close()


def create_arriving_job(
    job_uuid,
    original_name,
    user,
):
    conn = connect()

    try:
        cursor = conn.execute(
            """
            INSERT INTO processing_jobs (
                job_uuid,
                document_name,
                original_name,
                status,
                source_workstation,
                submitted_by,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 'ARRIVING', ?, ?, ?, ?)
            """,
            (
                job_uuid,
                original_name,
                original_name,
                user["workstation_id"],
                user["username"],
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        conn.commit()
        return cursor.lastrowid

    finally:
        conn.close()


def mark_job(
    job_id,
    status,
    stored_path=None,
    sha256=None,
    size_bytes=None,
    error_message=None,
):
    conn = connect()

    try:
        conn.execute(
            """
            UPDATE processing_jobs
            SET
                status = ?,
                stored_path = COALESCE(?, stored_path),
                sha256 = COALESCE(?, sha256),
                size_bytes = COALESCE(?, size_bytes),
                error_message = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                stored_path,
                sha256,
                size_bytes,
                error_message,
                datetime.now(timezone.utc).isoformat(),
                job_id,
            ),
        )

        conn.commit()

    finally:
        conn.close()


def build_router(current_user, audit):
    @router.post("/documents/upload")
    async def upload_document(
        file: UploadFile = File(...),
        user=Depends(current_user),
    ):
        ensure_intake_schema()

        original_name = safe_filename(file.filename or "document")
        extension = Path(original_name).suffix.lower()

        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail="Unsupported document type",
            )

        job_uuid = str(uuid.uuid4())

        job_id = create_arriving_job(
            job_uuid,
            original_name,
            user,
        )

        temporary_path = UPLOAD_ROOT / f"{job_uuid}.part"

        hasher = hashlib.sha256()
        total = 0

        try:
            with temporary_path.open("wb") as output:
                while True:
                    chunk = await file.read(1024 * 1024)

                    if not chunk:
                        break

                    total += len(chunk)

                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="Document exceeds upload size limit",
                        )

                    hasher.update(chunk)
                    output.write(chunk)

            if total <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Uploaded document is empty",
                )

            digest = hasher.hexdigest()

            final_name = f"{job_uuid}__{original_name}"
            final_path = INCOMING_ROOT / final_name

            os.replace(
                temporary_path,
                final_path,
            )

            mark_job(
                job_id,
                "QUEUED",
                stored_path=str(final_path),
                sha256=digest,
                size_bytes=total,
            )

            audit(
                user["username"],
                user["workstation_id"],
                "DOCUMENT_SUBMITTED",
                (
                    f"Queued {original_name}; "
                    f"job={job_id}; "
                    f"sha256={digest}"
                ),
            )

            return {
                "job_id": job_id,
                "job_uuid": job_uuid,
                "filename": original_name,
                "status": "QUEUED",
                "sha256": digest,
                "size_bytes": total,
            }

        except HTTPException as exc:
            try:
                if temporary_path.exists():
                    temporary_path.unlink()
            except Exception:
                pass

            mark_job(
                job_id,
                "ERROR",
                error_message=str(exc.detail),
            )

            raise

        except Exception as exc:
            try:
                if temporary_path.exists():
                    temporary_path.unlink()
            except Exception:
                pass

            mark_job(
                job_id,
                "ERROR",
                error_message=str(exc),
            )

            raise HTTPException(
                status_code=500,
                detail="Document intake failed",
            )

        finally:
            await file.close()

    @router.get("/jobs")
    def list_jobs(
        limit: int = 100,
        user=Depends(current_user),
    ):
        ensure_intake_schema()

        limit = max(1, min(limit, 500))

        conn = connect()

        try:
            rows = conn.execute(
                """
                SELECT
                    id,
                    job_uuid,
                    document_name,
                    original_name,
                    status,
                    source_workstation,
                    submitted_by,
                    sha256,
                    size_bytes,
                    created_at,
                    updated_at,
                    error_message
                FROM processing_jobs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            return [dict(row) for row in rows]

        finally:
            conn.close()

    @router.get("/jobs/{job_id}")
    def get_job(
        job_id: int,
        user=Depends(current_user),
    ):
        ensure_intake_schema()

        conn = connect()

        try:
            row = conn.execute(
                """
                SELECT *
                FROM processing_jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()

            if not row:
                raise HTTPException(
                    status_code=404,
                    detail="Job not found",
                )

            return dict(row)

        finally:
            conn.close()

    @router.post("/jobs/{job_id}/retry")
    def retry_job(job_id: int, user=Depends(current_user)):
        if user["role"] not in {"Administrator", "Conductor / Reviewer"}:
            raise HTTPException(status_code=403, detail="Conductor or Administrator access required")
        ensure_intake_schema()
        conn = connect()
        try:
            row = conn.execute("SELECT status, document_name FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")
            if row["status"] not in {"ERROR", "CONDUCTOR REVIEW"}:
                raise HTTPException(status_code=409, detail="Only failed or review jobs can be retried")
            conn.execute("UPDATE processing_jobs SET status='QUEUED', error_message=NULL, updated_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), job_id))
            conn.commit()
        finally:
            conn.close()
        audit(user["username"], user["workstation_id"], "JOB_REQUEUED", f"Requeued job {job_id} ({row['document_name']})")
        return {"job_id": job_id, "status": "QUEUED"}

    @router.get("/review")
    def review_queue(user=Depends(current_user)):
        if user["role"] not in {"Administrator", "Conductor / Reviewer"}:
            raise HTTPException(status_code=403, detail="Conductor or Administrator access required")
        ensure_intake_schema(); conn = connect()
        try:
            rows = conn.execute("SELECT * FROM processing_jobs WHERE status='CONDUCTOR REVIEW' ORDER BY updated_at DESC").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                # Review must be a fast read of persisted job metadata. OCR is
                # performed by the worker; never rerun it synchronously here.
                item["ocr"] = {}
                item["raw_ocr_context"] = item.get("raw_ocr_context", "")
                item["cleaned_ocr_context"] = item.get("cleaned_ocr_context", "")
                item["proposed_filename"] = row["original_name"] or row["document_name"]
                stored = json.loads(item.get("metadata_json") or "{}")
                item["ocr"] = {"railroad": stored.get("railroad"), "location": stored.get("location"), "category": stored.get("document_type"), "date": stored.get("date"), "name": stored.get("name"), "ocr_confidence": item.get("ocr_confidence", 0)}
                if not item["ocr"]["category"] and (item.get("raw_ocr_context") or item.get("cleaned_ocr_context")):
                    text = (item.get("cleaned_ocr_context") or item.get("raw_ocr_context") or "").casefold()
                    if "irail services group llc" in text and sum(x in text for x in ("start count", "on duty", "total starts")) >= 2:
                        item["ocr"]["category"] = "Work Log / Start Count Log"; item["ocr"]["ocr_confidence"] = max(item["ocr"]["ocr_confidence"], 90)
                item["proposed_destination"] = str(BRAIN_ROOT / "Documents" / "Railroads" / (stored.get("railroad") or "Unassigned Railroad") / (stored.get("location") or "General"))
                result.append(item)
            return result
        finally: conn.close()

    @router.post("/review/{job_id}")
    def complete_review(job_id: int, body: dict, user=Depends(current_user)):
        if user["role"] not in {"Administrator", "Conductor / Reviewer"}:
            raise HTTPException(status_code=403, detail="Conductor or Administrator access required")
        ensure_intake_schema(); conn = connect()
        try:
            row = conn.execute("SELECT * FROM processing_jobs WHERE id=? AND status='CONDUCTOR REVIEW'", (job_id,)).fetchone()
            if not row: raise HTTPException(status_code=404, detail="Review job not found")
            metadata = {k: str(body.get(k, "")).strip() for k in ("railroad", "location", "document_type", "date", "name")}
            if not metadata["document_type"]:
                raise HTTPException(status_code=400, detail="Document Type/Category is required")
            conn.execute("UPDATE processing_jobs SET status='FILED', metadata_json=?, review_reason=NULL, error_message=NULL, updated_at=? WHERE id=?", (json.dumps(metadata), datetime.now(timezone.utc).isoformat(), job_id)); conn.commit()
            if body.get("teach"):
                conn.execute("INSERT INTO learned_rules(rule_type, pattern, correction_json, created_by) VALUES(?,?,?,?)", (body.get("rule_type", "document"), body.get("pattern", ""), json.dumps(metadata), user["username"])); conn.commit()
        finally: conn.close()
        audit(user["username"], user["workstation_id"], "JOB_REVIEW_APPROVED", f"Approved job {job_id} with corrected metadata")
        return {"job_id": job_id, "status": "FILED", "metadata": metadata}

    @router.get("/admin/learned-rules")
    def learned_rules(user=Depends(current_user)):
        if user["role"] not in {"Administrator", "Conductor / Reviewer"}: raise HTTPException(status_code=403, detail="Conductor or Administrator access required")
        conn=connect()
        try: return [dict(x) for x in conn.execute("SELECT * FROM learned_rules ORDER BY id DESC").fetchall()]
        finally: conn.close()

    @router.patch("/admin/learned-rules/{rule_id}")
    def edit_learned_rule(rule_id: int, body: dict, user=Depends(current_user)):
        if user["role"] != "Administrator": raise HTTPException(status_code=403, detail="Administrator access required")
        conn=connect()
        try: conn.execute("UPDATE learned_rules SET enabled=COALESCE(?,enabled), pattern=COALESCE(?,pattern), updated_at=? WHERE id=?", (body.get("enabled"), body.get("pattern"), datetime.now(timezone.utc).isoformat(), rule_id)); conn.commit()
        finally: conn.close()
        audit(user["username"], user["workstation_id"], "LEARNED_RULE_EDITED", f"Edited learned rule {rule_id}")
        return {"id": rule_id, "status": "updated"}

    @router.delete("/admin/learned-rules/{rule_id}")
    def delete_learned_rule(rule_id: int, user=Depends(current_user)):
        if user["role"] != "Administrator": raise HTTPException(status_code=403, detail="Administrator access required")
        conn=connect()
        try: conn.execute("DELETE FROM learned_rules WHERE id=?", (rule_id,)); conn.commit()
        finally: conn.close()
        audit(user["username"], user["workstation_id"], "LEARNED_RULE_DELETED", f"Deleted learned rule {rule_id}")
        return {"id": rule_id, "status": "deleted"}

    return router
