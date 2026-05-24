import csv
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from functools import wraps
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
import database

try:
    from zoneinfo import ZoneInfo
    _BR_TZ = ZoneInfo("America/Sao_Paulo")
    def _to_br(dt):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_BR_TZ)
except ImportError:
    import pytz
    _BR_TZ = pytz.timezone("America/Sao_Paulo")
    def _to_br(dt):
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(_BR_TZ)


def _br_dt(value):
    """Datetime string UTC → 'DD/MM/YYYY HH:MM' no fuso São Paulo."""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value)[:19])
        dt = _to_br(dt)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(value)


def _br_d(value):
    """Date string YYYY-MM-DD (ou datetime) → 'DD/MM/YYYY'."""
    if not value:
        return "—"
    try:
        s = str(value)[:10]
        if len(s) == 10 and "-" in s:
            y, m, d = s.split("-")
            return f"{d}/{m}/{y}"
        return s
    except Exception:
        return str(value)


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
def _wa_number(value):
    """Phone string → digits only with BR country code (55), for wa.me links."""
    if not value:
        return ""
    digits = "".join(c for c in str(value) if c.isdigit())
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def _mask_phone(value):
    """Phone → masked display keeping DDD and last 4 digits: (22) ****-3429."""
    if not value:
        return ""
    digits = "".join(c for c in str(value) if c.isdigit())
    if len(digits) >= 10:
        return f"({digits[:2]}) ****-{digits[-4:]}"
    if digits:
        return f"****-{digits[-4:]}" if len(digits) >= 4 else "****"
    return value


def _mask_email(value):
    """Email → masked display: use***@domain.com."""
    if not value or "@" not in str(value):
        return value or ""
    local, domain = str(value).split("@", 1)
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


app.jinja_env.filters["br_dt"]      = _br_dt
app.jinja_env.filters["br_d"]       = _br_d
app.jinja_env.filters["wa_number"]  = _wa_number
app.jinja_env.filters["mask_phone"] = _mask_phone
app.jinja_env.filters["mask_email"] = _mask_email

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
    "phone":            ["telefone", "fone", "tel", "phone", "telefone_fixo", "tel_fixo"],
    "cellphone":        ["celular", "cell", "cel", "mobile", "telefone_celular", "tel_celular"],
}


# ── AUTH HELPERS ──────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Acesso restrito a administradores.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_user():
    return {
        "current_user": {
            "username": session.get("username", ""),
            "role": session.get("role", ""),
            "is_admin": session.get("role") == "admin",
            "is_authenticated": "username" in session,
        }
    }


@app.before_request
def check_auth():
    exempt = {"login", "logout", "setup", "static"}
    if request.endpoint in exempt or request.endpoint is None:
        return None
    if not database.has_any_user():
        return redirect(url_for("setup"))
    if "username" not in session:
        return redirect(url_for("login", next=request.path))


# ── CSV PARSING HELPERS ───────────────────────────────────────────────────────

def _parse_aluno_col(val: str) -> tuple[str, str]:
    s = _clean(val).strip('"')
    m = re.search(r'\((\d+)\)\s*$', s)
    if m:
        return s[:m.start()].strip(), m.group(1)
    return s, ""


def _find_col_partial(normalized_cols: dict, *substrings) -> str | None:
    for norm, orig in normalized_cols.items():
        for sub in substrings:
            if sub in norm:
                return orig
    return None


def _map_payment_status(val: str) -> str:
    v = normalize(str(val)) if val else ""
    if v in ("sim", "s", "y", "yes"):
        return "Inadimplente"
    return "Adimplente"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def detect_columns(df) -> dict:
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
    s = _clean(val)
    return s[:10] if len(s) >= 10 else s


def _normalize_br_date(val: str) -> str:
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
        df = pd.read_csv(filepath, dtype=str, encoding=encoding, sep=sep, index_col=False)
    except Exception as e:
        return [], f"Erro ao ler o arquivo: {e}"

    df.columns = [c.strip() for c in df.columns]
    nc = {normalize(c): c for c in df.columns}

    col_cod   = nc.get("cod_aluno")
    col_aluno = nc.get("aluno")
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
        df = pd.read_csv(filepath, dtype=str, encoding=encoding, sep=sep, index_col=False)
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
    col_phone    = (nc.get("telefone") or nc.get("fone") or nc.get("tel") or
                    nc.get("telefone_fixo") or nc.get("tel_fixo"))
    col_cell     = (nc.get("celular") or nc.get("cell") or nc.get("cel") or
                    nc.get("mobile") or nc.get("telefone_celular") or nc.get("tel_celular"))

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
            "phone":              _clean(row[col_phone])    if col_phone    else "",
            "cellphone":          _clean(row[col_cell])     if col_cell     else "",
            "raw_data":           json.dumps({c: _clean(str(row[c])) for c in df.columns}, ensure_ascii=False),
        })
    return students, None


def parse_csv(filepath: str) -> tuple[list[dict], str | None]:
    encoding, sep = _detect_encoding_and_sep(filepath)
    try:
        df = pd.read_csv(filepath, dtype=str, encoding=encoding, sep=sep, index_col=False)
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
        raw_row = {c: _clean(str(row[c])) for c in df.columns}
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
            "phone":            _clean(row[col["phone"]]) if col["phone"] else "",
            "cellphone":        _clean(row[col["cellphone"]]) if col["cellphone"] else "",
            "raw_data":         json.dumps(raw_row, ensure_ascii=False),
        })
    return students, None


# ── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if not database.has_any_user():
        return redirect(url_for("setup"))
    if "username" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = database.get_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            session["username"] = user["username"]
            session["role"] = user["role"]
            database.add_audit_log(username, request.remote_addr, "login", "Login bem-sucedido")
            return redirect(request.args.get("next") or url_for("index"))
        flash("Usuário ou senha inválidos.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    username = session.get("username", "")
    if username:
        database.add_audit_log(username, request.remote_addr, "logout", "")
    session.clear()
    return redirect(url_for("login"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if database.has_any_user():
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm  = request.form.get("confirm", "").strip()
        if not username or not password:
            flash("Preencha todos os campos.", "danger")
        elif password != confirm:
            flash("As senhas não coincidem.", "danger")
        elif len(password) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
        else:
            database.create_user(username, password, "admin")
            session["username"] = username
            session["role"] = "admin"
            database.add_audit_log(username, request.remote_addr, "setup",
                                   "Conta de administrador criada na primeira execução")
            flash("Conta de administrador criada com sucesso!", "success")
            return redirect(url_for("index"))
    return render_template("setup.html")


# ── USER MANAGEMENT ROUTES ────────────────────────────────────────────────────

@app.route("/users")
@admin_required
def users():
    all_users = database.get_all_users()
    return render_template("users.html", users=all_users)


@app.route("/users/add", methods=["POST"])
@admin_required
def add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "user")
    if not username or not password:
        flash("Preencha todos os campos.", "danger")
    elif len(password) < 6:
        flash("A senha deve ter pelo menos 6 caracteres.", "danger")
    elif role not in ("admin", "user"):
        flash("Perfil inválido.", "danger")
    elif database.get_user_by_username(username):
        flash(f"Usuário '{username}' já existe.", "danger")
    else:
        database.create_user(username, password, role, created_by=session["username"])
        database.add_audit_log(session["username"], request.remote_addr, "create_user",
                               f"Criou usuário '{username}' com perfil '{role}'")
        flash(f"Usuário '{username}' criado com sucesso.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    all_users = database.get_all_users()
    target = next((u for u in all_users if u["id"] == user_id), None)
    if not target:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("users"))
    if target["username"] == session["username"]:
        flash("Você não pode excluir sua própria conta.", "danger")
        return redirect(url_for("users"))
    if target["role"] == "admin" and database.count_admins() <= 1:
        flash("Não é possível excluir o único administrador.", "danger")
        return redirect(url_for("users"))
    database.delete_user(user_id)
    database.add_audit_log(session["username"], request.remote_addr, "delete_user",
                           f"Excluiu usuário '{target['username']}'")
    flash(f"Usuário '{target['username']}' excluído.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/role", methods=["POST"])
@admin_required
def change_user_role(user_id):
    new_role = request.form.get("role", "user")
    if new_role not in ("admin", "user"):
        flash("Perfil inválido.", "danger")
        return redirect(url_for("users"))
    all_users = database.get_all_users()
    target = next((u for u in all_users if u["id"] == user_id), None)
    if not target:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("users"))
    if target["username"] == session["username"] and new_role != "admin":
        flash("Você não pode remover seu próprio acesso de administrador.", "danger")
        return redirect(url_for("users"))
    if target["role"] == "admin" and new_role != "admin" and database.count_admins() <= 1:
        flash("Não é possível rebaixar o único administrador.", "danger")
        return redirect(url_for("users"))
    database.update_user_role(user_id, new_role)
    database.add_audit_log(session["username"], request.remote_addr, "change_role",
                           f"Alterou perfil de '{target['username']}' para '{new_role}'")
    flash(f"Perfil de '{target['username']}' alterado para '{new_role}'.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/password", methods=["POST"])
@admin_required
def change_user_password(user_id):
    password = request.form.get("password", "").strip()
    if len(password) < 6:
        flash("A senha deve ter pelo menos 6 caracteres.", "danger")
        return redirect(url_for("users"))
    all_users = database.get_all_users()
    target = next((u for u in all_users if u["id"] == user_id), None)
    if not target:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("users"))
    database.update_user_password(user_id, password)
    database.add_audit_log(session["username"], request.remote_addr, "change_password",
                           f"Alterou senha de '{target['username']}'")
    flash(f"Senha de '{target['username']}' atualizada.", "success")
    return redirect(url_for("users"))


# ── AUDIT LOG ROUTE ───────────────────────────────────────────────────────────

@app.route("/audit")
@admin_required
def audit_log():
    logs = database.get_audit_logs(500)
    return render_template("audit.html", logs=logs)


def _extract_contacts_from_raw(raw_json: str) -> tuple[str, str]:
    """Re-extrai phone e cellphone de um JSON de linha bruta do CSV."""
    try:
        raw = json.loads(raw_json)
    except Exception:
        return "", ""
    nc = {normalize(col): val for col, val in raw.items()}
    phone, cellphone = "", ""
    for alias in COLUMN_ALIASES.get("phone", []):
        if alias in nc and nc[alias]:
            phone = nc[alias]
            break
    for alias in COLUMN_ALIASES.get("cellphone", []):
        if alias in nc and nc[alias]:
            cellphone = nc[alias]
            break
    return phone, cellphone


@app.route("/admin/reprocess-leads", methods=["POST"])
@admin_required
def reprocess_leads():
    updated_raw = 0
    updated_csv = 0

    # 1. Re-extrair de raw_data armazenado
    raw_rows = database.get_all_students_raw()
    updates_from_raw = []
    for row in raw_rows:
        phone, cellphone = _extract_contacts_from_raw(row["raw_data"])
        if phone or cellphone:
            updates_from_raw.append({"cpf": row["cpf"], "phone": phone, "cellphone": cellphone})
    if updates_from_raw:
        database.bulk_update_contacts(updates_from_raw)
        updated_raw = len(updates_from_raw)

    # 2. Re-ler CSVs originais para alunos sem raw_data
    cpfs_with_raw = {row["cpf"] for row in raw_rows}
    for upload in database.get_recent_uploads(1000):
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], upload["filename"])
        if not os.path.exists(filepath):
            continue
        students, error = parse_csv(filepath)
        if error:
            continue
        updates_from_file = [
            {"cpf": s["cpf"], "phone": s["phone"], "cellphone": s["cellphone"]}
            for s in students
            if s["cpf"] not in cpfs_with_raw and (s["phone"] or s["cellphone"])
        ]
        if updates_from_file:
            database.bulk_update_contacts(updates_from_file)
            updated_csv += len(updates_from_file)
            # Salvar raw_data nos registros que ainda não tinham
            database.bulk_update_raw_data([
                {"cpf": s["cpf"], "raw_data": s["raw_data"]}
                for s in students if s["cpf"] not in cpfs_with_raw
            ])

    total = updated_raw + updated_csv
    database.add_audit_log(session["username"], request.remote_addr, "reprocess_leads",
                           f"{total} alunos com contato atualizado ({updated_raw} via raw_data, {updated_csv} via CSV)")
    if total:
        flash(f"{total} aluno(s) tiveram contato atualizado com sucesso.", "success")
    else:
        flash("Nenhum contato novo encontrado. Os arquivos CSV originais podem não estar mais disponíveis.", "warning")
    return redirect(url_for("history"))


@app.route("/admin/reprocess-enrolled", methods=["POST"])
@admin_required
def reprocess_enrolled():
    raw_rows = database.get_all_enrolled_raw()
    updates = []
    for row in raw_rows:
        phone, cellphone = _extract_contacts_from_raw(row["raw_data"])
        if phone or cellphone:
            updates.append({"id": row["id"], "phone": phone, "cellphone": cellphone})
    if updates:
        database.bulk_update_enrolled_contacts(updates)
    database.add_audit_log(session["username"], request.remote_addr, "reprocess_enrolled",
                           f"{len(updates)} alunos matriculados com contato atualizado")
    if updates:
        flash(f"{len(updates)} aluno(s) matriculado(s) tiveram contato atualizado.", "success")
    else:
        flash("Nenhum contato encontrado nos dados brutos. Reimporte os arquivos para armazenar o raw_data.", "warning")
    return redirect(url_for("enrolled"))


# ── MAIN ROUTES ───────────────────────────────────────────────────────────────

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
    database.add_audit_log(session.get("username", ""), request.remote_addr,
                           "upload_inscricoes",
                           f"Arquivo: {filename} | Total: {len(students)} | Novos: {len(new_students)}")

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
        "nome":        request.args.get("nome", "").strip(),
        "cpf":         request.args.get("cpf", "").strip(),
        "course":      request.args.get("course", ""),
        "polo":        request.args.get("polo", ""),
        "turno":       request.args.get("turno", ""),
        "matriculou":  request.args.get("matriculou", ""),
        "tipo":        request.args.get("tipo", ""),
        "data_ini":    request.args.get("data_ini", ""),
        "data_fim":    request.args.get("data_fim", ""),
        "upload_id":   request.args.get("upload_id", ""),
        "has_contact": request.args.get("has_contact", ""),
        "trabalhado":  request.args.get("trabalhado", ""),
    }
    active = {k: v for k, v in filters.items() if v}

    rows, total_db, matriculados = database.get_students_filtered(
        nome=filters["nome"], cpf=filters["cpf"], course=filters["course"],
        polo=filters["polo"], turno=filters["turno"], matriculou=filters["matriculou"],
        tipo=filters["tipo"], data_ini=filters["data_ini"], data_fim=filters["data_fim"],
        upload_id=filters["upload_id"], has_contact=filters["has_contact"],
        trabalhado=filters["trabalhado"],
    )
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
    uploads = database.get_all_uploads_unified(100)
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
        "has_contact":        request.args.get("has_contact", ""),
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

    # Automatically populate payments from the same file
    pay_records, pay_error = parse_payment_csv(filepath)
    if not pay_error and pay_records:
        pay_upload_id = database.record_payment_upload(filename, len(pay_records))
        database.insert_payments(pay_records, pay_upload_id)

    database.add_audit_log(session.get("username", ""), request.remote_addr,
                           "upload_matriculados",
                           f"Arquivo: {filename} | Registros: {len(records)}")

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
    database.add_audit_log(session.get("username", ""), request.remote_addr,
                           "upload_mensalidades",
                           f"Arquivo: {filename} | Registros: {len(records)}")

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
    username = session.get("username", "")
    if note:
        database.add_note("student", cpf, note, created_by=username)
        database.add_audit_log(username, request.remote_addr,
                               "add_nota", f"Anotação adicionada ao aluno CPF: {cpf[:3]}***")
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
    username = session.get("username", "")
    if note:
        database.add_note("enrolled", code, note, created_by=username)
        database.add_audit_log(username, request.remote_addr,
                               "add_nota", f"Anotação adicionada ao matriculado código: {code}")
    return redirect(url_for("enrolled_student_detail", code=code))


@app.route("/student/<cpf>/toggle-contacted", methods=["POST"])
def toggle_contacted(cpf):
    from flask import jsonify
    student = database.get_student(cpf)
    if not student:
        return jsonify({"ok": False}), 404
    username = session.get("username", "")
    if student["contacted_at"]:
        database.set_student_uncontacted(cpf)
        database.add_audit_log(username, request.remote_addr,
                               "uncontacted_lead", f"CPF: {cpf[:3]}***")
        return jsonify({"ok": True, "contacted": False})
    else:
        database.set_student_contacted(cpf, username)
        updated = database.get_student(cpf)
        at_br = _br_d(updated["contacted_at"])
        database.add_audit_log(username, request.remote_addr,
                               "contacted_lead", f"CPF: {cpf[:3]}***")
        return jsonify({
            "ok": True,
            "contacted": True,
            "contacted_at_br": at_br,
            "contacted_by": username,
        })


@app.route("/note/<int:note_id>/delete", methods=["POST"])
def delete_note(note_id):
    back = request.form.get("back", "/")
    note = database.get_note(note_id)
    database.delete_note(note_id)
    if note:
        database.add_audit_log(session.get("username", ""), request.remote_addr,
                               "delete_nota",
                               f"Excluiu nota #{note_id} ({note['entity_type']}:{note['entity_id']})")
    return redirect(back)


if __name__ == "__main__":
    app.run(debug=True)
