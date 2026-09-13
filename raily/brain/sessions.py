import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .database import connect


SESSION_HOURS = 12


def utcnow():
    return datetime.now(timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int, workstation_id: str):
    token = secrets.token_urlsafe(48)
    now = utcnow()
    expires = now + timedelta(hours=SESSION_HOURS)

    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO sessions (
                user_id,
                workstation_id,
                token_hash,
                created_at,
                expires_at,
                last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                workstation_id,
                token_hash(token),
                now.isoformat(),
                expires.isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return token, expires


def get_session(token: str):
    now = utcnow()
    digest = token_hash(token)

    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT
                s.id AS session_id,
                s.expires_at,
                s.revoked_at,
                u.id AS user_id,
                u.username,
                u.role,
                u.enabled,
                s.workstation_id
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (digest,),
        ).fetchone()

        if not row:
            return None

        if row["revoked_at"] is not None:
            return None

        if not row["enabled"]:
            return None

        expires = datetime.fromisoformat(row["expires_at"])
        if expires <= now:
            return None

        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE id = ?",
            (now.isoformat(), row["session_id"]),
        )
        conn.commit()

        return dict(row)
    finally:
        conn.close()


def revoke_session(token: str):
    digest = token_hash(token)

    conn = connect()
    try:
        conn.execute(
            """
            UPDATE sessions
            SET revoked_at = ?
            WHERE token_hash = ?
            """,
            (utcnow().isoformat(), digest),
        )
        conn.commit()
    finally:
        conn.close()
