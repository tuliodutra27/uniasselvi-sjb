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

            CREATE TABLE IF NOT EXISTS enrollment_uploads (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                filename      TEXT,
                uploaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_records INTEGER
            );

            CREATE TABLE IF NOT EXISTS enrolled_students (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                student_code          TEXT,
                student_name          TEXT,
                email                 TEXT,
                polo                  TEXT,
                course                TEXT,
                modulo                TEXT,
                tipo_aluno            TEXT,
                situacao_aluno        TEXT,
                situacao_matricula    TEXT,
                ativo                 TEXT,
                semestre              TEXT,
                turno                 TEXT,
                turma_dia             TEXT,
                inadimplente          TEXT,
                ultimo_acesso         TEXT,
                forma_ingresso        TEXT,
                enrollment_upload_id  INTEGER
            );

            CREATE TABLE IF NOT EXISTS payment_uploads (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                filename      TEXT,
                uploaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_records INTEGER
            );

            CREATE TABLE IF NOT EXISTS payments (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                student_code      TEXT,
                student_name      TEXT,
                course            TEXT,
                polo              TEXT,
                turno             TEXT,
                semestre          TEXT,
                status            TEXT,
                situacao          TEXT,
                ultimo_pagamento  TEXT,
                qtd_pago          TEXT,
                payment_upload_id INTEGER
            );
        """)
        _migrate(conn)
        _migrate_payments(conn)


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


def _migrate_payments(conn):
    """Migrate payments table: recreate if old CPF-based schema, add new columns otherwise."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(payments)")}
    if "cpf" in cols:
        conn.execute("DROP TABLE payments")
        conn.execute("""
            CREATE TABLE payments (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                student_code      TEXT,
                student_name      TEXT,
                course            TEXT,
                polo              TEXT,
                turno             TEXT,
                semestre          TEXT,
                status            TEXT,
                situacao          TEXT,
                ultimo_pagamento  TEXT,
                qtd_pago          TEXT,
                payment_upload_id INTEGER
            )
        """)
    else:
        if "situacao" not in cols:
            conn.execute("ALTER TABLE payments ADD COLUMN situacao TEXT")


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


# ── PAGAMENTOS ────────────────────────────────────────────────────────────────

def record_payment_upload(filename: str, total: int) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO payment_uploads (filename, total_records) VALUES (?,?)",
            (filename, total),
        )
        return cur.lastrowid


def insert_payments(payments: list[dict], upload_id: int):
    if not payments:
        return
    with get_connection() as conn:
        conn.executemany(
            """INSERT INTO payments
               (student_code, student_name, course, polo, turno,
                semestre, status, situacao, ultimo_pagamento, qtd_pago, payment_upload_id)
               VALUES
               (:student_code,:student_name,:course,:polo,:turno,
                :semestre,:status,:situacao,:ultimo_pagamento,:qtd_pago,:payment_upload_id)""",
            [{**p, "payment_upload_id": upload_id} for p in payments],
        )


def get_payment_filter_options():
    with get_connection() as conn:
        def distinct(col):
            return [
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT {col} FROM payments "
                    f"WHERE {col} IS NOT NULL AND {col} != '' "
                    f"ORDER BY {col}"
                )
            ]
        return {
            "courses":   distinct("course"),
            "polos":     distinct("polo"),
            "turnos":    distinct("turno"),
            "semestres": distinct("semestre"),
            "situacoes": distinct("situacao"),
            "uploads":   conn.execute(
                "SELECT id, filename, uploaded_at FROM payment_uploads ORDER BY uploaded_at DESC"
            ).fetchall(),
        }


def get_payments_filtered(nome=None, course=None, polo=None, turno=None,
                          status=None, situacao=None, semestre=None,
                          ult_pag_ini=None, ult_pag_fim=None, upload_id=None):
    conditions, params = [], []

    if nome:
        conditions.append("student_name LIKE ?")
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
    if status:
        conditions.append("status = ?")
        params.append(status)
    if situacao:
        conditions.append("situacao = ?")
        params.append(situacao)
    if semestre:
        conditions.append("semestre = ?")
        params.append(semestre)
    if ult_pag_ini:
        conditions.append("ultimo_pagamento >= ?")
        params.append(ult_pag_ini)
    if ult_pag_fim:
        conditions.append("ultimo_pagamento <= ?")
        params.append(ult_pag_fim)
    if upload_id:
        conditions.append("payment_upload_id = ?")
        params.append(int(upload_id))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    status_where = (where + " AND status=?" if conditions else "WHERE status=?")

    with get_connection() as conn:
        payments = conn.execute(
            f"SELECT * FROM payments {where} ORDER BY student_name",
            params,
        ).fetchall()
        total_db      = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        adimplentes   = conn.execute(f"SELECT COUNT(*) FROM payments {status_where}", params + ["Adimplente"]).fetchone()[0]
        inadimplentes = conn.execute(f"SELECT COUNT(*) FROM payments {status_where}", params + ["Inadimplente"]).fetchone()[0]

    return payments, total_db, adimplentes, inadimplentes


# ── MATRICULADOS ──────────────────────────────────────────────────────────────

def record_enrollment_upload(filename: str, total: int) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO enrollment_uploads (filename, total_records) VALUES (?,?)",
            (filename, total),
        )
        return cur.lastrowid


def insert_enrolled_students(students: list[dict], upload_id: int):
    if not students:
        return
    with get_connection() as conn:
        conn.executemany(
            """INSERT INTO enrolled_students
               (student_code, student_name, email, polo, course, modulo,
                tipo_aluno, situacao_aluno, situacao_matricula, ativo,
                semestre, turno, turma_dia, inadimplente, ultimo_acesso,
                forma_ingresso, enrollment_upload_id)
               VALUES
               (:student_code,:student_name,:email,:polo,:course,:modulo,
                :tipo_aluno,:situacao_aluno,:situacao_matricula,:ativo,
                :semestre,:turno,:turma_dia,:inadimplente,:ultimo_acesso,
                :forma_ingresso,:enrollment_upload_id)""",
            [{**s, "enrollment_upload_id": upload_id} for s in students],
        )


def get_enrolled_filter_options():
    with get_connection() as conn:
        def distinct(col):
            return [
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT {col} FROM enrolled_students "
                    f"WHERE {col} IS NOT NULL AND {col} != '' "
                    f"ORDER BY {col}"
                )
            ]
        return {
            "courses":          distinct("course"),
            "polos":            distinct("polo"),
            "turnos":           distinct("turno"),
            "semestres":        distinct("semestre"),
            "tipos_aluno":      distinct("tipo_aluno"),
            "situacoes_aluno":  distinct("situacao_aluno"),
            "situacoes_mat":    distinct("situacao_matricula"),
            "formas_ingresso":  distinct("forma_ingresso"),
            "uploads":          conn.execute(
                "SELECT id, filename, uploaded_at FROM enrollment_uploads ORDER BY uploaded_at DESC"
            ).fetchall(),
        }


def get_enrolled_filtered(nome=None, course=None, polo=None, turno=None,
                          semestre=None, ativo=None, inadimplente=None,
                          tipo_aluno=None, situacao_aluno=None,
                          situacao_matricula=None, upload_id=None):
    conditions, params = [], []

    if nome:
        conditions.append("student_name LIKE ?")
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
    if semestre:
        conditions.append("semestre = ?")
        params.append(semestre)
    if ativo:
        conditions.append("ativo = ?")
        params.append(ativo)
    if inadimplente:
        conditions.append("inadimplente = ?")
        params.append(inadimplente)
    if tipo_aluno:
        conditions.append("tipo_aluno = ?")
        params.append(tipo_aluno)
    if situacao_aluno:
        conditions.append("situacao_aluno = ?")
        params.append(situacao_aluno)
    if situacao_matricula:
        conditions.append("situacao_matricula = ?")
        params.append(situacao_matricula)
    if upload_id:
        conditions.append("enrollment_upload_id = ?")
        params.append(int(upload_id))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    ativo_where = (where + " AND ativo='S'" if conditions else "WHERE ativo='S'")
    inad_where  = (where + " AND inadimplente='Sim'" if conditions else "WHERE inadimplente='Sim'")

    with get_connection() as conn:
        students = conn.execute(
            f"SELECT * FROM enrolled_students {where} ORDER BY student_name",
            params,
        ).fetchall()
        total_db    = conn.execute("SELECT COUNT(*) FROM enrolled_students").fetchone()[0]
        ativos      = conn.execute(f"SELECT COUNT(*) FROM enrolled_students {ativo_where}", params).fetchone()[0]
        inadimplentes_count = conn.execute(f"SELECT COUNT(*) FROM enrolled_students {inad_where}", params).fetchone()[0]

    return students, total_db, ativos, inadimplentes_count
