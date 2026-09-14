from pathlib import Path
import sqlite3
import os

BRAIN_ROOT = Path(os.environ.get('RAILY_BRAIN_ROOT', Path.home() / 'Documents' / 'RAILY-Brain'))
DB_PATH = BRAIN_ROOT / "Data" / "raily.db"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def initialize_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL;")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_info (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS workstations (
                id TEXT PRIMARY KEY,
                friendly_name TEXT NOT NULL,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                workstation_id TEXT,
                token_hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                revoked_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(workstation_id) REFERENCES workstations(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                username TEXT,
                workstation TEXT,
                action TEXT NOT NULL,
                details TEXT
            )
        """)
        conn.execute("""CREATE TABLE IF NOT EXISTS processing_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, document_name TEXT,
            status TEXT NOT NULL DEFAULT 'QUEUED', source_workstation TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS learned_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, rule_type TEXT NOT NULL,
            pattern TEXT NOT NULL, correction_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, created_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(processing_jobs)")}
        for name, definition in {
            "job_uuid":"TEXT", "original_name":"TEXT", "stored_path":"TEXT",
            "sha256":"TEXT", "size_bytes":"INTEGER", "submitted_by":"TEXT",
            "error_message":"TEXT", "duplicate_of_job_id":"INTEGER", "metadata_json":"TEXT", "review_reason":"TEXT"
            ,"raw_ocr_context":"TEXT", "cleaned_ocr_context":"TEXT", "ocr_confidence":"REAL"
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE processing_jobs ADD COLUMN {name} {definition}")

        conn.execute('CREATE INDEX IF NOT EXISTS jobs_content_hash ON processing_jobs(sha256, status)')
        conn.commit()
    finally:
        conn.close()

    return DB_PATH
