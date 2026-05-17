import csv
import os
import re
import unicodedata
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
import database

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

with app.app_context():
    database.init_db()
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

ALLOWED_EXTENSIONS = {"csv"}

COLUMN_ALIASES = {
    "cpf":              ["cpf", "documento", "doc", "cpf/cnpj"],
    "name":             ["nome", "name", "aluno", "nome_aluno", "nome do aluno"],
    "student_id":       ["codigo_aluno", "id", "matricula", "codigo", "ra", "registro"],
    "course":           ["nome_do_curso", "curso", "course", "disciplina"],
    "enrollment_date":  ["data_matricula", "dt_matricula", "data de matricula", "data_inicio"],
    "inscription_date": ["data_da_inscricao", "data_inscricao", "data da inscricao"],
    "polo":             ["nome_do_polo", "polo", "local_de_inscricao"],
    "turno":            ["turno"],
    "matriculou":       ["matriculou"],
    "tipo_inscricao":   ["tipo_da_inscricao", "tipo_inscricao"],
}

def _parse_aluno_col(val: str) -> tuple[str, str]:
    """Extract (name, code) from 'Nome Completo (12345)' format."""
    s = _clean(val).strip('"')
    m = re.search(r'\((\d+)\)\s*$', s)
    if m:
        return s[:m.start()].strip(), m.group(1)
    return s, ""


def _find_col_partial(normalized_cols: dict, *substrings) -> str | None:
    """Return the first original column name whose normalized form contains any substring."""
    for norm, orig in normalized_cols.items():
        for sub in substrings:
            if sub in norm:
                return orig
    return None


def _map_payment_status(val: str) -> str:
    """Map INADIMPLENTE (Sim/Não) or NEGATIVADO (S/N) to Adimplente/Inadimplente."""
    v = normalize(str(val)) if val else ""
    if v in ("sim", "s", "y", "yes"):
        return "Inadimplente"
    return "Adimplente"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def detect_columns(df: pd.DataFrame) -> dict:
    normalized_cols = {normalize(c): c for c in df.columns}
    return {
        field: next((normalized_cols[a] for a in aliases if a in normalized_cols), None)
        for field, aliases in COLUMN_ALIASES.items()
    }


def mask_cpf(cpf: str) -> str:
    digits = "".join(filter(str.isdigit, str(cpf)))
    if len(digits) == 11:
        return f"{digits[:3]}.***.***-{digits[9:11]}"
    return cpf[:3] + "***" + cpf[-2:] if len(cpf) > 5 else cpf


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _detect_encoding_and_sep(filepath: str) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(filepath, "r", encoding=encoding) as f:
                sample = f.read(4096)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return encoding, dialect.delimiter
        except (UnicodeDecodeError, csv.Error):
            continue
    return "latin-1", ";"


def _clean(val) -> str:
    s = str(val).strip()
    return "" if s in ("nan", "None", "NaT") else s


def _normalize_date(val: str) -> str:
    """Keep only the YYYY-MM-DD part."""
    s = _clean(val)
    return s[:10] if len(s) >= 10 else s


def _normalize_br_date(val: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD. Returns '' for empty/dash values."""
    s = _clean(val)
    if not s or s in ("-", " - ", "–", "—"):
        return ""
    parts = s.split("/")
    if len(parts) == 3 and len(parts[2]) == 4:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return s


def parse_payment_csv(filepath: str) -> tuple[list[dict], str | None]:
    encoding, sep = _detect_encoding_and_sep(filepath)
    try:
        df = pd.read_csv(filepath, dtype=str, encoding=encoding, sep=sep,
                         index_col=False)
    except Exception as e:
        return [], f"Erro ao ler o arquivo: {e}"

    df.columns = [c.strip() for c in df.columns]
    nc = {normalize(c): c for c in df.columns}  # normalized → original

    # Detect which format we're dealing with
    col_cod   = nc.get("cod_aluno")        # File 2: COD_ALUNO column
    col_aluno = nc.get("aluno")            # File 1: "Nome (código)" column
    col_nome  = nc.get("nome_aluno")

    if not col_cod and not col_aluno:
        return [], ("Coluna de identificação não encontrada. "
                    "O arquivo deve ter 'COD_ALUNO' ou 'ALUNO'.")

    col_polo     = nc.get("nome_polo") or nc.get("polo")
    col_course   = nc.get("nome_curso") or nc.get("nome_do_curso") or nc.get("curso")
    col_turno    = nc.get("turma_turno") or nc.get("turno")
    col_semestre = nc.get("semestre")
    col_inad     = nc.get("inadimplente")
    col_neg      = nc.get("negativado")
    col_qtd      = _find_col_partial(nc, "qtd pago", "qtd. pago")
    col_ult      = _find_col_partial(nc, "ultimo pagamento", "ult. pagamento", "ultimo pag")
    col_situacao = _find_col_partial(nc, "situacao do aluno semestre", "situacao do aluno")

    payments = []
    for _, row in df.iterrows():
        if col_cod:
            student_code = _clean(row[col_cod])
            student_name = _clean(row[col_nome]) if col_nome else ""
        else:
            student_name, student_code = _parse_aluno_col(_clean(row[col_aluno]))

        if not student_code and not student_name:
            continue

        # Determine status from INADIMPLENTE or NEGATIVADO
        status = "Adimplente"
        if col_inad:
            status = _map_payment_status(_clean(row[col_inad]))
        elif col_neg:
            status = _map_payment_status(_clean(row[col_neg]))

        payments.append({
            "student_code":     student_code,
            "student_name":     student_name,
            "course":           _clean(row[col_course])   if col_course   else "",
            "polo":             _clean(row[col_polo])     if col_polo     else "",
            "turno":            _clean(row[col_turno])    if col_turno    else "",
            "semestre":         _clean(row[col_semestre]) if col_semestre else "",
            "status":           status,
            "situacao":         _clean(row[col_situacao]) if col_situacao else "",
            "ultimo_pagamento": _normalize_br_date(_clean(row[col_ult])) if col_ult else "",
            "qtd_pago":         _clean(row[col_qtd])     if col_qtd      else "",
        })
    return payments, None


def parse_enrolled_csv(filepath: str) -> tuple[list[dict], str | None]:
    encoding, sep = _detect_encoding_and_sep(filepath)
    try:
        df = pd.read_csv(filepath, dtype=str, encoding=encoding, sep=sep,
                         index_col=False)
    except Exception as e:
        return [], f"Erro ao ler o arquivo: {e}"

    df.columns = [c.strip() for c in df.columns]
    nc = {normalize(c): c for c in df.columns}

    col_code = nc.get("cod_aluno") or nc.get("codigo_aluno") or nc.get("id_aluno")
    col_name = nc.get("nome_aluno") or nc.get("nome") or nc.get("aluno")

    if not col_code and not col_name:
        return [], ("Coluna de identificação não encontrada. "
                    "O arquivo deve ter 'COD_ALUNO' ou 'NOME_ALUNO'.")

    col_email    = nc.get("email_aluno") or nc.get("email")
    col_polo     = nc.get("nome_polo") or nc.get("polo")
    col_course   = nc.get("nome_curso") or nc.get("nome_do_curso") or nc.get("curso")
    col_modulo   = nc.get("modulo")
    col_tipo     = nc.get("tipo_aluno")
    col_sit_al   = nc.get("situacao_do_aluno") or _find_col_partial(nc, "situacao_do_aluno", "situacao do aluno")
    col_sit_mat  = (nc.get("situacao_matricula_aluno_semestre")
                    or _find_col_partial(nc, "situacao_matricula", "situacao matricula"))
    col_ativo    = nc.get("aluno_eh_ativo") or _find_col_partial(nc, "aluno_eh_ativo", "eh_ativo")
    col_semestre = nc.get("semestre")
    col_turno    = nc.get("turma_turno") or nc.get("turno")
    col_dia      = nc.get("turma_dia")
    col_inad     = nc.get("inadimplente")
    col_acesso   = nc.get("ultimo_acesso") or _find_col_partial(nc, "ultimo_acesso", "ultimo acesso")
    col_ingresso = nc.get("forma_ingresso")

    students = []
    for _, row in df.iterrows():
        code = _clean(row[col_code]) if col_code else ""
        name = _clean(row[col_name]) if col_name else ""
        if not code and not name:
            continue
        students.append({
            "student_code":       code,
            "student_name":       name,
            "email":              _clean(row[col_email])    if col_email    else "",
            "polo":               _clean(row[col_polo])     if col_polo     else "",
            "course":             _clean(row[col_course])   if col_course   else "",
            "modulo":             _clean(row[col_modulo])   if col_modulo   else "",
            "tipo_aluno":         _clean(row[col_tipo])     if col_tipo     else "",
            "situacao_aluno":     _clean(row[col_sit_al])   if col_sit_al   else "",
            "situacao_matricula": _clean(row[col_sit_mat])  if col_sit_mat  else "",
            "ativo":              _clean(row[col_ativo])    if col_ativo    else "",
            "semestre":           _clean(row[col_semestre]) if col_semestre else "",
            "turno":              _clean(row[col_turno])    if col_turno    else "",
            "turma_dia":          _clean(row[col_dia])      if col_dia      else "",
            "inadimplente":       _clean(row[col_inad])     if col_inad     else "",
            "ultimo_acesso":      _normalize_date(_clean(row[col_acesso]))   if col_acesso   else "",
            "forma_ingresso":     _clean(row[col_ingresso]) if col_ingresso else "",
        })
    return students, None


def parse_csv(filepath: str) -> tuple[list[dict], str | None]:
    encoding, sep = _detect_encoding_and_sep(filepath)
    try:
        df = pd.read_csv(filepath, dtype=str, encoding=encoding, sep=sep,
                         index_col=False)
    except Exception as e:
        return [], f"Erro ao ler o arquivo: {e}"

    df.columns = [c.strip() for c in df.columns]
    col = detect_columns(df)

    if not col["cpf"]:
        return [], "Coluna de CPF não encontrada. O arquivo deve ter uma coluna 'CPF', 'Documento' ou similar."
    if not col["name"]:
        return [], "Coluna de Nome não encontrada. O arquivo deve ter uma coluna 'Nome', 'Aluno' ou similar."

    students = []
    for _, row in df.iterrows():
        cpf = "".join(filter(str.isdigit, _clean(row[col["cpf"]])))
        if not cpf:
            continue
        students.append({
            "cpf":              cpf,
            "name":             _clean(row[col["name"]]) if col["name"] else "",
            "student_id":       _clean(row[col["student_id"]]) if col["student_id"] else "",
            "course":           _clean(row[col["course"]]) if col["course"] else "",
            "enrollment_date":  _normalize_date(row[col["enrollment_date"]]) if col["enrollment_date"] else "",
            "inscription_date": _normalize_date(row[col["inscription_date"]]) if col["inscription_date"] else "",
            "polo":             _clean(row[col["polo"]]) if col["polo"] else "",
            "turno":            _clean(row[col["turno"]]) if col["turno"] else "",
            "matriculou":       _clean(row[col["matriculou"]]) if col["matriculou"] else "",
            "tipo_inscricao":   _clean(row[col["tipo_inscricao"]]) if col["tipo_inscricao"] else "",
        })
    return students, None


@app.route("/")
def index():
    recent = database.get_recent_uploads(5)
    return render_template("index.html", recent=recent)


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        flash("Nenhum arquivo enviado.", "danger")
        return redirect(url_for("index"))

    file = request.files["file"]
    if file.filename == "" or not allowed_file(file.filename):
        flash("Envie um arquivo .csv válido.", "danger")
        return redirect(url_for("index"))

    filename = secure_filename(file.filename)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    students, error = parse_csv(filepath)
    if error:
        flash(error, "danger")
        return redirect(url_for("index"))

    known_cpfs = database.get_known_cpfs()
    new_students = [s for s in students if s["cpf"] not in known_cpfs]

    upload_id = database.record_upload(filename, len(students), len(new_students))
    database.insert_new_students(new_students, upload_id)

    return redirect(url_for("report", upload_id=upload_id))


@app.route("/report/<int:upload_id>")
def report(upload_id):
    upload = database.get_upload(upload_id)
    if not upload:
        flash("Upload não encontrado.", "danger")
        return redirect(url_for("index"))

    new_students = database.get_new_students_for_upload(upload_id)
    students_display = [
        {**dict(s), "cpf_masked": mask_cpf(s["cpf"])}
        for s in new_students
    ]
    return render_template("report.html", upload=upload, students=students_display)


@app.route("/students")
def students():
    filters = {
        "nome":       request.args.get("nome", "").strip(),
        "course":     request.args.get("course", ""),
        "polo":       request.args.get("polo", ""),
        "turno":      request.args.get("turno", ""),
        "matriculou": request.args.get("matriculou", ""),
        "tipo":       request.args.get("tipo", ""),
        "data_ini":   request.args.get("data_ini", ""),
        "data_fim":   request.args.get("data_fim", ""),
        "upload_id":  request.args.get("upload_id", ""),
    }
    active = {k: v for k, v in filters.items() if v}

    rows, total_db, matriculados = database.get_students_filtered(**filters)
    options = database.get_filter_options()

    cpfs = [s["cpf"] for s in rows]
    note_counts = database.get_note_counts("student", cpfs)
    students_display = [
        {**dict(s), "cpf_masked": mask_cpf(s["cpf"]), "note_count": note_counts.get(s["cpf"], 0)}
        for s in rows
    ]
    return render_template(
        "students.html",
        students=students_display,
        filters=filters,
        active_count=len(active),
        total_filtered=len(rows),
        total_db=total_db,
        matriculados=matriculados,
        options=options,
    )


@app.route("/history")
def history():
    uploads = database.get_recent_uploads(50)
    return render_template("history.html", uploads=uploads)


@app.route("/enrolled")
def enrolled():
    filters = {
        "nome":               request.args.get("nome", "").strip(),
        "course":             request.args.get("course", ""),
        "polo":               request.args.get("polo", ""),
        "turno":              request.args.get("turno", ""),
        "semestre":           request.args.get("semestre", ""),
        "ativo":              request.args.get("ativo", ""),
        "inadimplente":       request.args.get("inadimplente", ""),
        "tipo_aluno":         request.args.get("tipo_aluno", ""),
        "situacao_aluno":     request.args.get("situacao_aluno", ""),
        "situacao_matricula": request.args.get("situacao_matricula", ""),
        "upload_id":          request.args.get("upload_id", ""),
    }
    active = {k: v for k, v in filters.items() if v}

    rows, total_db, _, _ = database.get_enrolled_filtered(**filters)
    options = database.get_enrolled_filter_options()

    ativos        = sum(1 for r in rows if r["ativo"] == "S")
    inativos      = sum(1 for r in rows if r["ativo"] == "N")
    inadimplentes = sum(1 for r in rows if r["inadimplente"] == "Sim")
    desistentes   = sum(1 for r in rows if "desistente" in (r["situacao_aluno"] or "").lower())

    codes = [r["student_code"] for r in rows]
    note_counts = database.get_note_counts("enrolled", codes)
    rows = [dict(r) | {"note_count": note_counts.get(r["student_code"], 0)} for r in rows]

    quick_filter = None
    if active_count := len(active):
        if active_count == 1:
            if filters["ativo"] == "S":
                quick_filter = "ativos"
            elif filters["ativo"] == "N":
                quick_filter = "inativos"
            elif filters["situacao_aluno"] and "desistente" in filters["situacao_aluno"].lower():
                quick_filter = "desistentes"
            elif filters["situacao_matricula"] and "confirmada" in filters["situacao_matricula"].lower():
                quick_filter = "confirmados"
            elif filters["situacao_matricula"] and "aguardando" in filters["situacao_matricula"].lower():
                quick_filter = "aguardando"
            elif filters["inadimplente"] == "Sim":
                quick_filter = "inadimplentes"

    return render_template(
        "enrolled.html",
        students=rows,
        filters=filters,
        active_count=len(active),
        total_filtered=len(rows),
        total_db=total_db,
        ativos=ativos,
        inativos=inativos,
        inadimplentes=inadimplentes,
        desistentes=desistentes,
        quick_filter=quick_filter,
        options=options,
    )


@app.route("/upload/enrolled", methods=["POST"])
def upload_enrolled():
    if "file" not in request.files:
        flash("Nenhum arquivo enviado.", "danger")
        return redirect(url_for("enrolled"))

    file = request.files["file"]
    if file.filename == "" or not allowed_file(file.filename):
        flash("Envie um arquivo .csv válido.", "danger")
        return redirect(url_for("enrolled"))

    filename = secure_filename(file.filename)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    records, error = parse_enrolled_csv(filepath)
    if error:
        flash(error, "danger")
        return redirect(url_for("enrolled"))

    upload_id = database.record_enrollment_upload(filename, len(records))
    database.insert_enrolled_students(records, upload_id)

    flash(f"{len(records)} alunos matriculados importados com sucesso.", "success")
    return redirect(url_for("enrolled"))


@app.route("/payments")
def payments():
    filters = {
        "nome":        request.args.get("nome", "").strip(),
        "course":      request.args.get("course", ""),
        "polo":        request.args.get("polo", ""),
        "turno":       request.args.get("turno", ""),
        "status":      request.args.get("status", ""),
        "situacao":    request.args.get("situacao", ""),
        "semestre":    request.args.get("semestre", ""),
        "ult_pag_ini": request.args.get("ult_pag_ini", ""),
        "ult_pag_fim": request.args.get("ult_pag_fim", ""),
        "upload_id":   request.args.get("upload_id", ""),
    }
    active = {k: v for k, v in filters.items() if v}

    rows, total_db, adimplentes, inadimplentes = database.get_payments_filtered(**filters)
    options = database.get_payment_filter_options()

    return render_template(
        "payments.html",
        payments=rows,
        filters=filters,
        active_count=len(active),
        total_filtered=len(rows),
        total_db=total_db,
        adimplentes=adimplentes,
        inadimplentes=inadimplentes,
        options=options,
    )


@app.route("/upload/payments", methods=["POST"])
def upload_payments():
    if "file" not in request.files:
        flash("Nenhum arquivo enviado.", "danger")
        return redirect(url_for("payments"))

    file = request.files["file"]
    if file.filename == "" or not allowed_file(file.filename):
        flash("Envie um arquivo .csv válido.", "danger")
        return redirect(url_for("payments"))

    filename = secure_filename(file.filename)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    records, error = parse_payment_csv(filepath)
    if error:
        flash(error, "danger")
        return redirect(url_for("payments"))

    upload_id = database.record_payment_upload(filename, len(records))
    database.insert_payments(records, upload_id)

    flash(f"{len(records)} registros de pagamento importados com sucesso.", "success")
    return redirect(url_for("payments"))


@app.route("/student/<cpf>")
def student_detail(cpf):
    student = database.get_student(cpf)
    if not student:
        flash("Aluno não encontrado.", "danger")
        return redirect(url_for("students"))
    notes = database.get_notes("student", cpf)
    return render_template(
        "student_detail.html",
        student={**dict(student), "cpf_masked": mask_cpf(student["cpf"])},
        notes=notes,
    )


@app.route("/student/<cpf>/note", methods=["POST"])
def add_student_note(cpf):
    note = request.form.get("note", "").strip()
    if note:
        database.add_note("student", cpf, note)
    return redirect(url_for("student_detail", cpf=cpf))


@app.route("/enrolled/student/<code>")
def enrolled_student_detail(code):
    enrollments = database.get_enrolled_by_code(code)
    if not enrollments:
        flash("Aluno não encontrado.", "danger")
        return redirect(url_for("enrolled"))
    notes = database.get_notes("enrolled", code)
    return render_template(
        "enrolled_detail.html",
        student_name=enrollments[0]["student_name"],
        student_code=code,
        enrollments=enrollments,
        notes=notes,
    )


@app.route("/enrolled/student/<code>/note", methods=["POST"])
def add_enrolled_note(code):
    note = request.form.get("note", "").strip()
    if note:
        database.add_note("enrolled", code, note)
    return redirect(url_for("enrolled_student_detail", code=code))


@app.route("/note/<int:note_id>/delete", methods=["POST"])
def delete_note(note_id):
    back = request.form.get("back", "/")
    database.delete_note(note_id)
    return redirect(back)


if __name__ == "__main__":
    app.run(debug=True)
