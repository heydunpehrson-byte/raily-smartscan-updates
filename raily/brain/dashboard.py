import platform
import shutil
from datetime import datetime, timezone

import psutil

from .database import BRAIN_ROOT, connect


def ensure_dispatch_tables():
    conn = connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_name TEXT,
                status TEXT NOT NULL DEFAULT 'QUEUED',
                source_workstation TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        conn.close()


def get_dashboard_data(user):
    ensure_dispatch_tables()

    conn = connect()

    try:
        def job_count(status):
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM processing_jobs WHERE status = ?",
                (status,),
            ).fetchone()
            return int(row["total"])

        yard = {
            "incoming": job_count("ARRIVING"),
            "queued": job_count("QUEUED"),
            "processing": job_count("PROCESSING"),
            "review": job_count("CONDUCTOR REVIEW"),
            "duplicates": job_count("DUPLICATE SIDING"),
        }

        users = conn.execute(
            "SELECT COUNT(*) AS total FROM users WHERE enabled = 1"
        ).fetchone()["total"]

        sessions = conn.execute("""
            SELECT
                s.id,
                u.username,
                u.role,
                s.workstation_id,
                COALESCE(w.friendly_name, s.workstation_id) AS workstation_name,
                s.last_seen,
                s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN workstations w ON w.id = s.workstation_id
            WHERE s.revoked_at IS NULL
              AND u.enabled = 1
              AND s.expires_at > ?
            ORDER BY s.last_seen DESC
        """, (datetime.now(timezone.utc).isoformat(),)).fetchall()

        workstations = conn.execute("""
            SELECT
                id,
                friendly_name,
                last_seen
            FROM workstations
            ORDER BY last_seen DESC
        """).fetchall()

        recent_audit = []

        if user["role"] == "Administrator":
            recent_audit = conn.execute("""
                SELECT
                    timestamp,
                    username,
                    workstation,
                    action,
                    details
                FROM audit_events
                ORDER BY id DESC
                LIMIT 15
            """).fetchall()

        today = datetime.now(timezone.utc).date().isoformat()

        filed_today = conn.execute("""
            SELECT COUNT(*) AS total
            FROM processing_jobs
            WHERE status = 'FILED'
              AND substr(updated_at, 1, 10) = ?
        """, (today,)).fetchone()["total"]

        yard["filed_today"] = int(filed_today)

    finally:
        conn.close()

    disk = shutil.disk_usage(BRAIN_ROOT)

    process = psutil.Process()
    uptime_seconds = max(
        0,
        datetime.now(timezone.utc).timestamp() - process.create_time()
    )

    return {
        "signal": "ALL CLEAR",
        "brain": {
            "name": platform.node(),
            "status": "ONLINE",
            "version": "73.0-local",
            "uptime_seconds": int(uptime_seconds),
            "storage_free_gb": round(disk.free / (1024 ** 3), 1),
        },
        "user": {
            "username": user["username"],
            "role": user["role"],
            "workstation_id": user["workstation_id"],
        },
        "yard": yard,
        "users_enabled": int(users),
        "active_sessions": [dict(x) for x in sessions],
        "workstations": [dict(x) for x in workstations],
        "recent_audit": [dict(x) for x in recent_audit],
    }
