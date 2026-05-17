import sqlite3

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
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                first_upload_id INTEGER
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


def record_upload(filename: str, total: int, new: int) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO uploads (filename, total_students, new_students) VALUES (?, ?, ?)",
            (filename, total, new),
        )
        return cursor.lastrowid


def insert_new_students(students: list[dict], upload_id: int):
    if not students:
        return
    with get_connection() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO students
               (cpf, name, student_id, course, enrollment_date, first_upload_id)
               VALUES (:cpf, :name, :student_id, :course, :enrollment_date, :first_upload_id)""",
            [{**s, "first_upload_id": upload_id} for s in students],
        )


def get_upload(upload_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM uploads WHERE id = ?", (upload_id,)
        ).fetchone()


def get_new_students_for_upload(upload_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM students WHERE first_upload_id = ?", (upload_id,)
        ).fetchall()


def get_recent_uploads(limit: int = 10):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM uploads ORDER BY uploaded_at DESC LIMIT ?", (limit,)
        ).fetchall()
