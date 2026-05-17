import csv
import os
import unicodedata
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
import database

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

ALLOWED_EXTENSIONS = {"csv"}

# Mapeamento flexível de nomes de coluna para campos internos
COLUMN_ALIASES = {
    "cpf": ["cpf", "documento", "doc", "cpf/cnpj"],
    "name": ["nome", "name", "aluno", "nome_aluno", "nome do aluno"],
    "student_id": ["codigo_aluno", "id", "matricula", "matricula", "codigo", "codigo", "ra", "registro"],
    "course": ["nome_do_curso", "curso", "course", "turma", "disciplina", "polo"],
    "enrollment_date": ["data_matricula", "data", "dt_matricula", "data de matricula",
                        "data matricula", "data_inicio", "inicio"],
}


def normalize(text: str) -> str:
    """Remove accents and lowercase for flexible column matching."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def detect_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Returns mapping of internal field → actual CSV column name."""
    normalized_cols = {normalize(c): c for c in df.columns}
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        matched = next((normalized_cols[a] for a in aliases if a in normalized_cols), None)
        mapping[field] = matched
    return mapping


def mask_cpf(cpf: str) -> str:
    digits = "".join(filter(str.isdigit, str(cpf)))
    if len(digits) == 11:
        return f"{digits[:3]}.***.***-{digits[9:11]}"
    return cpf[:3] + "***" + cpf[-2:] if len(cpf) > 5 else cpf


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _detect_encoding_and_sep(filepath: str) -> tuple[str, str]:
    """Sniff file encoding and CSV separator."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(filepath, "r", encoding=encoding) as f:
                sample = f.read(4096)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return encoding, dialect.delimiter
        except (UnicodeDecodeError, csv.Error):
            continue
    return "latin-1", ";"


def parse_csv(filepath: str) -> tuple[list[dict], str | None]:
    """Parse CSV and return (list of student dicts, error message or None)."""
    encoding, sep = _detect_encoding_and_sep(filepath)
    try:
        df = pd.read_csv(filepath, dtype=str, encoding=encoding, sep=sep)
    except Exception as e:
        return [], f"Erro ao ler o arquivo: {e}"

    df.columns = [c.strip() for c in df.columns]
    col_map = detect_columns(df)

    if not col_map["cpf"]:
        return [], (
            "Coluna de CPF não encontrada. O arquivo deve ter uma coluna chamada "
            "'CPF', 'Documento' ou similar."
        )
    if not col_map["name"]:
        return [], (
            "Coluna de Nome não encontrada. O arquivo deve ter uma coluna chamada "
            "'Nome', 'Aluno' ou similar."
        )

    students = []
    for _, row in df.iterrows():
        cpf_raw = str(row[col_map["cpf"]]).strip()
        cpf = "".join(filter(str.isdigit, cpf_raw))
        if not cpf:
            continue
        students.append({
            "cpf": cpf,
            "name": str(row[col_map["name"]]).strip() if col_map["name"] else "",
            "student_id": str(row[col_map["student_id"]]).strip() if col_map["student_id"] else "",
            "course": str(row[col_map["course"]]).strip() if col_map["course"] else "",
            "enrollment_date": str(row[col_map["enrollment_date"]]).strip() if col_map["enrollment_date"] else "",
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


@app.route("/history")
def history():
    uploads = database.get_recent_uploads(50)
    return render_template("history.html", uploads=uploads)


if __name__ == "__main__":
    database.init_db()
    app.run(debug=True)
