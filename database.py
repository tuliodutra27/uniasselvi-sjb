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
                cpf          TEXT PRIMARY KEY,
                name         TEXT,
                student_id   TEXT,
                course       TEXT,
                enrollment_date  TEXT,
                inscription_date TEXT,
                polo         TEXT,
                turno        TEXT,
                matriculou   TEXT,
                tipo_inscricao TEXT,
                first_seen_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                first_upload_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS uploads (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                filename       TEXT,
                uploaded_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_students INTEGER,
                new_students   INTEGER
            );
        """)
        _migrate(conn)


def _migrate(conn):
    """Add columns introduced after the initial schema without dropping data."""
    new_cols = [
        ("inscription_date", "TEXT"),
        ("polo",             "TEXT"),
        ("turno",            "TEXT"),
        ("matriculou",       "TEXT"),
        ("tipo_inscricao",   "TEXT"),
        ("first_upload_id",  "INTEGER"),
    ]
    existing = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
    for col, typ in new_cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE students ADD COLUMN {col} {typ}")


def get_known_cpfs():
    with get_connection() as conn:
        return {r["cpf"] for r in conn.execute("SELECT cpf FROM students")}


def record_upload(filename: str, total: int, new: int) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO uploads (filename, total_students, new_students) VALUES (?,?,?)",
            (filename, total, new),
        )
        return cur.lastrowid


def insert_new_students(students: list[dict], upload_id: int):
    if not students:
        return
    with get_connection() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO students
               (cpf, name, student_id, course, enrollment_date,
                inscription_date, polo, turno, matriculou, tipo_inscricao,
                first_upload_id)
               VALUES
               (:cpf,:name,:student_id,:course,:enrollment_date,
                :inscription_date,:polo,:turno,:matriculou,:tipo_inscricao,
                :first_upload_id)""",
            [{**s, "first_upload_id": upload_id} for s in students],
        )


def get_upload(upload_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()


def get_new_students_for_upload(upload_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM students WHERE first_upload_id=?", (upload_id,)
        ).fetchall()


def get_recent_uploads(limit: int = 10):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM uploads ORDER BY uploaded_at DESC LIMIT ?", (limit,)
        ).fetchall()


def get_filter_options():
    with get_connection() as conn:
        def distinct(col):
            return [
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT {col} FROM students "
                    f"WHERE {col} IS NOT NULL AND {col} != '' AND {col} != 'nan' "
                    f"ORDER BY {col}"
                )
            ]
        return {
            "courses":  distinct("course"),
            "polos":    distinct("polo"),
            "turnos":   distinct("turno"),
            "tipos":    distinct("tipo_inscricao"),
            "uploads":  conn.execute(
                "SELECT id, filename, uploaded_at FROM uploads ORDER BY uploaded_at DESC"
            ).fetchall(),
        }


def get_students_filtered(nome=None, course=None, polo=None, turno=None,
                          matriculou=None, tipo=None,
                          data_ini=None, data_fim=None, upload_id=None):
    conditions, params = [], []

    if nome:
        conditions.append("name LIKE ?")
        params.append(f"%{nome}%")
    if course:
        conditions.append("course = ?")
        params.append(course)
    if polo:
        conditions.append("polo = ?")
        params.append(polo)
    if turno:
        conditions.append("turno = ?")
        params.append(turno)
    if matriculou:
        conditions.append("matriculou = ?")
        params.append(matriculou)
    if tipo:
        conditions.append("tipo_inscricao = ?")
        params.append(tipo)
    if data_ini:
        conditions.append("inscription_date >= ?")
        params.append(data_ini)
    if data_fim:
        conditions.append("inscription_date <= ?")
        params.append(data_fim)
    if upload_id:
        conditions.append("first_upload_id = ?")
        params.append(int(upload_id))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with get_connection() as conn:
        students = conn.execute(
            f"SELECT * FROM students {where} ORDER BY name", params
        ).fetchall()
        total_db = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        matriculados = conn.execute(
            f"SELECT COUNT(*) FROM students {where} AND matriculou='S'"
            if conditions else "SELECT COUNT(*) FROM students WHERE matriculou='S'",
            params if conditions else [],
        ).fetchone()[0]
    return students, total_db, matriculados
