import csv
import os
import unicodedata
from datetime import date
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
import database

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
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

PAYMENT_COLUMN_ALIASES = {
    "cpf":             ["cpf", "documento", "doc", "cpf/cnpj"],
    "student_name":    ["nome", "name", "aluno", "nome_aluno", "nome do aluno"],
    "course":          ["nome_do_curso", "curso", "course", "disciplina"],
    "polo":            ["polo", "nome_do_polo"],
    "reference_month": ["referencia", "mes_referencia", "competencia", "periodo", "mes", "mes/ano"],
    "due_date":        ["data_vencimento", "vencimento", "dt_vencimento", "data de vencimento",
                        "data_boleto", "vencto", "dt_vencto"],
    "payment_date":    ["data_pagamento", "dt_pagamento", "data de pagamento", "pago_em",
                        "data_baixa", "dt_baixa", "data_recebimento"],
    "amount":          ["valor", "valor_parcela", "mensalidade", "valor_mensalidade",
                        "valor da parcela", "vl_parcela", "vl_mensalidade", "valor_cobrado"],
    "status":          ["status", "situacao", "situacao_financeira", "situacao financeira",
                        "status_financeiro"],
}

STATUS_MAP = {
    "pago": "Pago",
    "paga": "Pago",
    "quitado": "Pago",
    "quitada": "Pago",
    "recebido": "Pago",
    "liquidado": "Pago",
    "baixado": "Pago",
    "em aberto": "Em aberto",
    "aberto": "Em aberto",
    "pendente": "Em aberto",
    "aguardando": "Em aberto",
    "a vencer": "Em aberto",
    "vencido": "Atrasado",
    "atrasado": "Atrasado",
    "inadimplente": "Atrasado",
    "em atraso": "Atrasado",
    "cancelado": "Cancelado",
    "cancelada": "Cancelado",
}


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


def _compute_payment_status(status_raw: str, payment_date: str, due_date: str) -> str:
    if payment_date:
        return "Pago"
    if status_raw:
        key = normalize(status_raw)
        return STATUS_MAP.get(key, status_raw.strip().title())
    if due_date and due_date < date.today().isoformat():
        return "Atrasado"
    return "Em aberto"


def detect_payment_columns(df: pd.DataFrame) -> dict:
    normalized_cols = {normalize(c): c for c in df.columns}
    return {
        field: next((normalized_cols[a] for a in aliases if a in normalized_cols), None)
        for field, aliases in PAYMENT_COLUMN_ALIASES.items()
    }


def parse_payment_csv(filepath: str) -> tuple[list[dict], str | None]:
    encoding, sep = _detect_encoding_and_sep(filepath)
    try:
        df = pd.read_csv(filepath, dtype=str, encoding=encoding, sep=sep,
                         index_col=False)
    except Exception as e:
        return [], f"Erro ao ler o arquivo: {e}"

    df.columns = [c.strip() for c in df.columns]
    col = detect_payment_columns(df)

    if not col["cpf"]:
        return [], "Coluna de CPF não encontrada. O arquivo deve ter uma coluna 'CPF', 'Documento' ou similar."

    payments = []
    for _, row in df.iterrows():
        cpf = "".join(filter(str.isdigit, _clean(row[col["cpf"]])))
        if not cpf:
            continue
        payment_date = _normalize_date(row[col["payment_date"]]) if col["payment_date"] else ""
        due_date     = _normalize_date(row[col["due_date"]])     if col["due_date"]     else ""
        status_raw   = _clean(row[col["status"]]) if col["status"] else ""
        payments.append({
            "cpf":             cpf,
            "student_name":    _clean(row[col["student_name"]]) if col["student_name"] else "",
            "course":          _clean(row[col["course"]])        if col["course"]        else "",
            "polo":            _clean(row[col["polo"]])          if col["polo"]          else "",
            "reference_month": _clean(row[col["reference_month"]]) if col["reference_month"] else "",
            "due_date":        due_date,
            "payment_date":    payment_date,
            "amount":          _clean(row[col["amount"]]) if col["amount"] else "",
            "status":          _compute_payment_status(status_raw, payment_date, due_date),
        })
    return payments, None


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

    students_display = [
        {**dict(s), "cpf_masked": mask_cpf(s["cpf"])}
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


@app.route("/payments")
def payments():
    filters = {
        "nome":            request.args.get("nome", "").strip(),
        "course":          request.args.get("course", ""),
        "polo":            request.args.get("polo", ""),
        "status":          request.args.get("status", ""),
        "reference_month": request.args.get("reference_month", ""),
        "due_ini":         request.args.get("due_ini", ""),
        "due_fim":         request.args.get("due_fim", ""),
        "upload_id":       request.args.get("upload_id", ""),
    }
    active = {k: v for k, v in filters.items() if v}

    rows, total_db, pagos, em_aberto, atrasados = database.get_payments_filtered(**filters)
    options = database.get_payment_filter_options()

    payments_display = [
        {**dict(p), "cpf_masked": mask_cpf(p["cpf"])}
        for p in rows
    ]
    return render_template(
        "payments.html",
        payments=payments_display,
        filters=filters,
        active_count=len(active),
        total_filtered=len(rows),
        total_db=total_db,
        pagos=pagos,
        em_aberto=em_aberto,
        atrasados=atrasados,
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


if __name__ == "__main__":
    database.init_db()
    app.run(debug=True)
