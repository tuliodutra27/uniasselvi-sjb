import sqlite3
from datetime import datetime

DB_PATH = "student_tracker.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS students (
                cpf TEXT PRIMARY KEY,
                name TEXT,
                student_id TEXT,
                course TEXT,
                enrollment_date TEXT,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_students INTEGER,
                new_students INTEGER
            );
        """)


def get_known_cpfs():
    with get_connection() as conn:
        rows = conn.execute("SELECT cpf FROM students").fetchall()
    return {row["cpf"] for row in rows}


def insert_new_students(students: list[dict]):
    if not students:
        return
    with get_connection() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO students (cpf, name, student_id, course, enrollment_date)
               VALUES (:cpf, :name, :student_id, :course, :enrollment_date)""",
            students,
        )


def record_upload(filename: str, total: int, new: int) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO uploads (filename, total_students, new_students) VALUES (?, ?, ?)",
            (filename, total, new),
        )
        return cursor.lastrowid


def get_upload(upload_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM uploads WHERE id = ?", (upload_id,)
        ).fetchone()


def get_new_students_for_upload(upload_id: int):
    """Returns students whose first_seen_at matches the upload timestamp."""
    with get_connection() as conn:
        upload = conn.execute(
            "SELECT uploaded_at FROM uploads WHERE id = ?", (upload_id,)
        ).fetchone()
        if not upload:
            return []
        return conn.execute(
            """SELECT * FROM students
               WHERE strftime('%Y-%m-%d %H:%M', first_seen_at) =
                     strftime('%Y-%m-%d %H:%M', ?)""",
            (upload["uploaded_at"],),
        ).fetchall()


def get_recent_uploads(limit: int = 10):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM uploads ORDER BY uploaded_at DESC LIMIT ?", (limit,)
        ).fetchall()
