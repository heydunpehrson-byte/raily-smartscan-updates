import getpass
import sqlite3

from .auth import hash_password
from .database import initialize_database, DB_PATH


def main():
    initialize_database()

    print()
    print("RAILY DISPATCH BRAIN - FIRST ADMINISTRATOR")
    print("------------------------------------------")

    username = input("Administrator username: ").strip()

    if not username:
        print("Username cannot be blank.")
        return

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords do not match.")
        return

    if len(password) < 12:
        print("Password must be at least 12 characters.")
        return

    conn = sqlite3.connect(DB_PATH)

    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if existing:
            print("That username already exists.")
            return

        conn.execute(
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
                hash_password(password),
                "Administrator",
            )
        )

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
            (
                username,
                "RAILY-BRAIN",
                "ADMIN_ACCOUNT_CREATED",
                "Initial Dispatch Brain administrator created locally.",
            )
        )

        conn.commit()

        print()
        print("RAILY Administrator created successfully.")
        print("Role: Administrator")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
