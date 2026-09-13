from datetime import datetime, timezone
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from .auth import hash_password, verify_password
from .database import DB_PATH, connect, initialize_database
from .sessions import create_session, get_session, revoke_session


app = FastAPI(
    title="RAILY Dispatch Brain",
    version="73.0-local",
)

VALID_ROLES = {
    "Administrator",
    "Conductor / Reviewer",
    "Scanner Operator",
    "Viewer",
}


class LoginRequest(BaseModel):
    username: str
    password: str
    workstation_id: str = "LOCAL-DEV"
    workstation_name: str = "Local Development PC"


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str


class EnabledRequest(BaseModel):
    enabled: bool


class RoleRequest(BaseModel):
    role: str


class PasswordResetRequest(BaseModel):
    new_password: str


def audit(username, workstation, action, details=""):
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO audit_events (
                username,
                workstation,
                action,
                details
            )
            VALUES (?, ?, ?, ?)
            """,
            (username, workstation, action, details),
        )
        conn.commit()
    finally:
        conn.close()


def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization[7:].strip()
    session = get_session(token)

    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    session["_token"] = token
    return session


def administrator(user=Depends(current_user)):
    if user["role"] != "Administrator":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def require_valid_role(role: str):
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail="Invalid RAILY role",
        )


@app.on_event("startup")
def startup():
    initialize_database()


@app.get("/health")
def health():
    return {
        "status": "online",
        "service": "RAILY Dispatch Brain",
        "version": "73.0-local",
        "database": str(DB_PATH),
        "connection": "localhost",
    }


@app.post("/login")
def login(body: LoginRequest):
    conn = connect()

    try:
        user = conn.execute(
            """
            SELECT id, username, password_hash, role, enabled
            FROM users
            WHERE lower(username) = lower(?)
            """,
            (body.username.strip(),),
        ).fetchone()

        if not user or not user["enabled"]:
            audit(
                body.username,
                body.workstation_name,
                "LOGIN_FAILED",
                "Unknown or disabled account.",
            )
            raise HTTPException(status_code=401, detail="Invalid username or password")

        if not verify_password(user["password_hash"], body.password):
            audit(
                user["username"],
                body.workstation_name,
                "LOGIN_FAILED",
                "Incorrect password.",
            )
            raise HTTPException(status_code=401, detail="Invalid username or password")

        conn.execute(
            """
            INSERT INTO workstations (
                id,
                friendly_name,
                last_seen
            )
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                friendly_name = excluded.friendly_name,
                last_seen = excluded.last_seen
            """,
            (
                body.workstation_id,
                body.workstation_name,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    finally:
        conn.close()

    token, expires = create_session(user["id"], body.workstation_id)

    audit(
        user["username"],
        body.workstation_name,
        "LOGIN_SUCCESS",
        "Authenticated to RAILY Dispatch Brain.",
    )

    return {
        "token": token,
        "expires_at": expires.isoformat(),
        "username": user["username"],
        "role": user["role"],
    }


@app.get("/me")
def me(user=Depends(current_user)):
    return {
        "username": user["username"],
        "role": user["role"],
        "workstation_id": user["workstation_id"],
        "authenticated": True,
    }


@app.post("/logout")
def logout(user=Depends(current_user)):
    revoke_session(user["_token"])

    audit(
        user["username"],
        user["workstation_id"],
        "LOGOUT",
        "RAILY session ended.",
    )

    return {"status": "logged_out"}


@app.get("/admin/users")
def list_users(admin=Depends(administrator)):
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                username,
                role,
                enabled,
                created_at
            FROM users
            ORDER BY lower(username)
            """
        ).fetchall()

        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.post("/admin/users")
def create_user(body: CreateUserRequest, admin=Depends(administrator)):
    username = body.username.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be blank")

    if len(body.password) < 12:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 12 characters",
        )

    require_valid_role(body.role)

    conn = connect()
    try:
        try:
            cursor = conn.execute(
                """
                INSERT INTO users (
                    username,
                    password_hash,
                    role,
                    enabled
                )
                VALUES (?, ?, ?, 1)
                """,
                (
                    username,
                    hash_password(body.password),
                    body.role,
                ),
            )
            conn.commit()
            user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Username already exists")
    finally:
        conn.close()

    audit(
        admin["username"],
        admin["workstation_id"],
        "USER_CREATED",
        f"Created user {username} with role {body.role}.",
    )

    return {
        "id": user_id,
        "username": username,
        "role": body.role,
        "enabled": True,
    }


@app.patch("/admin/users/{user_id}/enabled")
def set_user_enabled(
    user_id: int,
    body: EnabledRequest,
    admin=Depends(administrator),
):
    if user_id == admin["user_id"] and not body.enabled:
        raise HTTPException(
            status_code=400,
            detail="You cannot disable your own active administrator account",
        )

    conn = connect()
    try:
        row = conn.execute(
            "SELECT username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        conn.execute(
            "UPDATE users SET enabled = ? WHERE id = ?",
            (1 if body.enabled else 0, user_id),
        )

        if not body.enabled:
            conn.execute(
                """
                UPDATE sessions
                SET revoked_at = ?
                WHERE user_id = ?
                  AND revoked_at IS NULL
                """,
                (datetime.now(timezone.utc).isoformat(), user_id),
            )

        conn.commit()
    finally:
        conn.close()

    audit(
        admin["username"],
        admin["workstation_id"],
        "USER_ENABLED_CHANGED",
        f"{row['username']} enabled={body.enabled}.",
    )

    return {
        "username": row["username"],
        "enabled": body.enabled,
    }


@app.patch("/admin/users/{user_id}/role")
def set_user_role(
    user_id: int,
    body: RoleRequest,
    admin=Depends(administrator),
):
    require_valid_role(body.role)

    if user_id == admin["user_id"] and body.role != "Administrator":
        raise HTTPException(
            status_code=400,
            detail="You cannot remove your own Administrator role",
        )

    conn = connect()
    try:
        row = conn.execute(
            "SELECT username, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (body.role, user_id),
        )
        conn.commit()
    finally:
        conn.close()

    audit(
        admin["username"],
        admin["workstation_id"],
        "USER_ROLE_CHANGED",
        f"{row['username']}: {row['role']} -> {body.role}.",
    )

    return {
        "username": row["username"],
        "role": body.role,
    }


@app.post("/admin/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    body: PasswordResetRequest,
    admin=Depends(administrator),
):
    if len(body.new_password) < 12:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 12 characters",
        )

    conn = connect()
    try:
        row = conn.execute(
            "SELECT username FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(body.new_password), user_id),
        )

        conn.execute(
            """
            UPDATE sessions
            SET revoked_at = ?
            WHERE user_id = ?
              AND revoked_at IS NULL
            """,
            (datetime.now(timezone.utc).isoformat(), user_id),
        )

        conn.commit()
    finally:
        conn.close()

    audit(
        admin["username"],
        admin["workstation_id"],
        "PASSWORD_RESET",
        f"Password reset for {row['username']}; existing sessions revoked.",
    )

    return {
        "status": "password_reset",
        "username": row["username"],
    }


@app.get("/admin/sessions")
def list_active_sessions(admin=Depends(administrator)):
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                s.id,
                u.username,
                u.role,
                s.workstation_id,
                w.friendly_name AS workstation_name,
                s.created_at,
                s.last_seen,
                s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN workstations w ON w.id = s.workstation_id
            WHERE s.revoked_at IS NULL
              AND u.enabled = 1
            ORDER BY s.last_seen DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.get("/admin/audit")
def list_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    admin=Depends(administrator),
):
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                timestamp,
                username,
                workstation,
                action,
                details
            FROM audit_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [dict(row) for row in rows]
    finally:
        conn.close()

from .dashboard import get_dashboard_data

@app.get("/dashboard")
def dispatch_dashboard(user=Depends(current_user)):
    return get_dashboard_data(user)

from .intake import build_router

app.include_router(
    build_router(
        current_user=current_user,
        audit=audit,
    )
)

from .intake import build_router

app.include_router(
    build_router(
        current_user=current_user,
        audit=audit,
    )
)
