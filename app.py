import os
import re
import json
import secrets
import hashlib
import mimetypes
from datetime import datetime, date
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    send_from_directory, abort, session, jsonify, make_response
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required,
    current_user
)
from sqlalchemy import inspect, text, or_, func
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SQLALCHEMY_DATABASE_URI="sqlite:///" + str(INSTANCE_DIR / "drone_portal.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_CONTENT_LENGTH", 8 * 1024 * 1024 * 1024)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
)

# Change these in production through environment variables.
app.config["MAX_TAGS"] = 30
app.config["MAX_TAG_LENGTH"] = 40
app.config["MAX_SEARCH_LENGTH"] = 120

DB = SQLAlchemy(app)
db = DB  # backwards-compatible alias
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."

ALLOWED_VIDEO = {"mp4", "mov", "m4v", "webm", "avi"}
ALLOWED_DOCS = {"pdf", "jpg", "jpeg", "png", "csv", "xlsx", "docx", "txt", "zip"}
DIRECTIONS = ["W-E", "S-N", "SW-N", "SW-E", "E-SW"]
MAP_DIRECTIONS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def now_utc():
    return datetime.utcnow()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ProjectAccess(db.Model):
    """Four-letter bearer access code for a read-only project viewer."""
    id = db.Column(db.Integer, primary_key=True)
    project = db.Column(db.String(160), unique=True, nullable=False, index=True)
    code = db.Column(db.String(4), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False)


class ProjectSiteMap(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project = db.Column(db.String(160), unique=True, nullable=False, index=True)
    filename = db.Column(db.String(500), unique=True, nullable=False)
    original_filename = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=now_utc, nullable=False)


class ProjectMapPin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project = db.Column(db.String(160), nullable=False, index=True)
    label = db.Column(db.String(120), nullable=False)
    direction = db.Column(db.String(8), nullable=False)
    x_percent = db.Column(db.Float, nullable=False)
    y_percent = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False)


class Flight(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.String(80), unique=True, nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    direction = db.Column(db.String(30), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    filename = db.Column(db.String(500), nullable=False, unique=True)
    original_filename = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.BigInteger, default=0)
    duration = db.Column(db.String(30), default="")
    uploaded_at = db.Column(db.DateTime, default=now_utc, nullable=False, index=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Construction-specific metadata.
    project = db.Column(db.String(160), default="Main Project", index=True)
    area = db.Column(db.String(160), default="")
    tags = db.Column(db.Text, default="[]")  # JSON list; keeps SQLite setup simple.
    notes = db.Column(db.Text, default="")
    drone = db.Column(db.String(120), default="")
    pilot = db.Column(db.String(120), default="")
    client = db.Column(db.String(160), default="")
    captured_at = db.Column(db.DateTime, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    checksum_sha256 = db.Column(db.String(64), default="", index=True)
    status = db.Column(db.String(30), default="ready", nullable=False)
    view_count = db.Column(db.Integer, default=0, nullable=False)
    download_count = db.Column(db.Integer, default=0, nullable=False)

    @property
    def tag_list(self):
        return parse_tags(self.tags)


class ProjectFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    filename = db.Column(db.String(500), nullable=False, unique=True)
    category = db.Column(db.String(50), default="Document")
    uploaded_at = db.Column(db.DateTime, default=now_utc, nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    project = db.Column(db.String(160), default="Main Project", index=True)
    tags = db.Column(db.Text, default="[]")
    notes = db.Column(db.Text, default="")
    original_filename = db.Column(db.String(500), default="")
    file_size = db.Column(db.BigInteger, default=0)
    checksum_sha256 = db.Column(db.String(64), default="", index=True)
    download_count = db.Column(db.Integer, default=0, nullable=False)

    @property
    def tag_list(self):
        return parse_tags(self.tags)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    resource_type = db.Column(db.String(50), default="")
    resource_id = db.Column(db.String(80), default="")
    detail = db.Column(db.Text, default="")
    ip_address = db.Column(db.String(80), default="")
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False, index=True)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin or not current_user.active:
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def protect_post():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("_csrf")
        supplied = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            abort(400, "Invalid CSRF token")


def ext_allowed(filename, allowed):
    return bool(filename and "." in filename and filename.rsplit(".", 1)[1].lower() in allowed)


def extension(filename):
    return Path(filename).suffix.lower().lstrip(".")


def human_size(n):
    n = n or 0
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    x = float(n)
    while x >= 1024 and i < len(units) - 1:
        x /= 1024
        i += 1
    return f"{x:.1f} {units[i]}"


app.jinja_env.filters["filesize"] = human_size
app.jinja_env.filters["tags"] = lambda value: ", ".join(parse_tags(value))


def parse_tags(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        try:
            raw = json.loads(value)
            if not isinstance(raw, list):
                raw = str(value).split(",")
        except (json.JSONDecodeError, TypeError):
            raw = str(value).split(",")
    cleaned = []
    seen = set()
    for tag in raw:
        tag = re.sub(r"\s+", " ", str(tag).strip())
        if not tag:
            continue
        tag = tag[: app.config["MAX_TAG_LENGTH"]]
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            cleaned.append(tag)
        if len(cleaned) >= app.config["MAX_TAGS"]:
            break
    return cleaned


def tags_from_request():
    values = request.form.getlist("tags")
    if len(values) == 1 and "," in values[0]:
        values = values[0].split(",")
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        values = payload.get("tags", values)
    return parse_tags(values)


def safe_name(filename):
    name = secure_filename(filename or "")
    if not name:
        name = "upload"
    return name[:240]


def unique_filename(original):
    return f"{secrets.token_hex(12)}_{safe_name(original)}"


def sha256_file(path, chunk_size=8 * 1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def save_upload(file_storage):
    """Save an uploaded file safely and return (filename, size, sha256)."""
    original = safe_name(file_storage.filename)
    filename = unique_filename(original)
    destination = UPLOAD_DIR / filename
    file_storage.save(destination)
    size = destination.stat().st_size
    checksum = sha256_file(destination)
    return filename, size, checksum, original


def delete_stored_file(filename):
    if not filename:
        return
    path = UPLOAD_DIR / Path(filename).name
    try:
        path.resolve().relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return
    if path.exists() and path.is_file():
        path.unlink()


def parse_date(value, fallback=None):
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


def parse_datetime(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def audit(action, resource_type="", resource_id="", detail=""):
    try:
        entry = AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            detail=detail[:4000],
            ip_address=(request.headers.get("X-Forwarded-For", request.remote_addr) or "")[:80],
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()


def migrate_sqlite():
    """Add new columns to the original MVP database without destroying existing data."""
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    if not existing_tables:
        db.create_all()
        return

    expected = {
        "user": {
            "last_login_at": "DATETIME",
        },
        "flight": {
            "project": "VARCHAR(160)", "area": "VARCHAR(160)", "tags": "TEXT",
            "notes": "TEXT", "drone": "VARCHAR(120)", "pilot": "VARCHAR(120)",
            "client": "VARCHAR(160)", "captured_at": "DATETIME", "latitude": "FLOAT",
            "longitude": "FLOAT", "checksum_sha256": "VARCHAR(64)",
            "status": "VARCHAR(30)", "view_count": "INTEGER", "download_count": "INTEGER",
        },
        "project_file": {
            "project": "VARCHAR(160)", "tags": "TEXT", "notes": "TEXT",
            "original_filename": "VARCHAR(500)", "file_size": "BIGINT",
            "checksum_sha256": "VARCHAR(64)", "download_count": "INTEGER",
        },
    }
    for table, columns in expected.items():
        if table not in existing_tables:
            continue
        current = {c["name"] for c in inspect(db.engine).get_columns(table)}
        for column, sql_type in columns.items():
            if column not in current:
                db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'))
    db.session.commit()
    db.create_all()

    # Normalize NULLs introduced by migrations.
    db.session.execute(text("UPDATE flight SET tags='[]' WHERE tags IS NULL"))
    db.session.execute(text("UPDATE flight SET project='Main Project' WHERE project IS NULL"))
    db.session.execute(text("UPDATE flight SET status='ready' WHERE status IS NULL"))
    db.session.execute(text("UPDATE flight SET view_count=0 WHERE view_count IS NULL"))
    db.session.execute(text("UPDATE flight SET download_count=0 WHERE download_count IS NULL"))
    db.session.execute(text("UPDATE project_file SET tags='[]' WHERE tags IS NULL"))
    db.session.execute(text("UPDATE project_file SET project='Main Project' WHERE project IS NULL"))
    db.session.execute(text("UPDATE project_file SET download_count=0 WHERE download_count IS NULL"))
    db.session.commit()


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        code = re.sub(r"[^A-Z]", "", request.form.get("code", "").upper())[:4]
        access = ProjectAccess.query.filter_by(code=code).first() if len(code) == 4 else None
        if access:
            session["viewer_code"] = access.code
            return redirect(url_for("project_view", code=access.code))
        flash("That project code was not recognized. Check the four letters and try again.", "error")
    return render_template("access.html")


@app.get("/project/<code>")
def project_view(code):
    access = ProjectAccess.query.filter_by(code=code.upper()).first_or_404()
    # The share URL is itself a bearer link; entering the same four-letter code
    # at the access screen establishes the same read-only viewer session.
    session["viewer_code"] = access.code
    flights = Flight.query.filter_by(project=access.project).order_by(Flight.date.desc(), Flight.uploaded_at.desc()).all()
    docs = ProjectFile.query.filter_by(project=access.project).order_by(ProjectFile.date.desc(), ProjectFile.uploaded_at.desc()).all()
    dates = sorted({f.date for f in flights}, reverse=True)
    selected_date = parse_date(request.args.get("date"))
    if selected_date not in dates:
        selected_date = dates[0] if dates else now_utc().date()
    day_flights = [f for f in flights if f.date == selected_date]
    site_map = ProjectSiteMap.query.filter_by(project=access.project).first()
    map_pins = ProjectMapPin.query.filter_by(project=access.project).order_by(ProjectMapPin.id).all()
    selected_id = request.args.get("flight", type=int)
    selected_flight = next((f for f in day_flights if f.id == selected_id), None) or (day_flights[0] if day_flights else None)
    tab = request.args.get("tab", "map")
    if tab not in {"overview", "map", "timeline", "media", "documents"}:
        tab = "map"
    return render_template("project_view.html", project=access.project, code=access.code,
                           flights=flights, day_flights=day_flights, dates=dates, docs=docs,
                           selected_date=selected_date, selected_flight=selected_flight, tab=tab,
                           site_map=site_map, map_pins=map_pins)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.active and user.check_password(password):
            login_user(user, remember=False)
            user.last_login_at = now_utc()
            db.session.commit()
            audit("login", "user", user.id, "Successful login")
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    audit("logout", "user", current_user.id, "User signed out")
    logout_user()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    selected_date = parse_date(request.args.get("date"))
    project = request.args.get("project", "").strip()[:160]
    direction = request.args.get("direction", "").strip()
    tag = request.args.get("tag", "").strip()[:40]
    q = request.args.get("q", "").strip()[: app.config["MAX_SEARCH_LENGTH"]]

    available_dates = [r[0] for r in Flight.query.with_entities(Flight.date).distinct().order_by(Flight.date.desc()).all()]
    if not selected_date:
        selected_date = available_dates[0] if available_dates else now_utc().date()

    query = Flight.query.filter(Flight.date == selected_date)
    if project:
        query = query.filter(Flight.project == project)
    if direction in DIRECTIONS:
        query = query.filter(Flight.direction == direction)
    if tag:
        query = query.filter(Flight.tags.ilike(f'%"{tag}"%'))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Flight.flight_id.ilike(like), Flight.title.ilike(like), Flight.description.ilike(like), Flight.area.ilike(like), Flight.notes.ilike(like), Flight.tags.ilike(like)))
    flights = query.order_by(Flight.direction, Flight.uploaded_at.desc()).all()

    docs_query = ProjectFile.query.filter(ProjectFile.date == selected_date)
    if project:
        docs_query = docs_query.filter(ProjectFile.project == project)
    if q:
        like = f"%{q}%"
        docs_query = docs_query.filter(or_(ProjectFile.name.ilike(like), ProjectFile.notes.ilike(like), ProjectFile.tags.ilike(like)))
    docs = docs_query.order_by(ProjectFile.uploaded_at.desc()).all()

    projects = [r[0] for r in Flight.query.with_entities(Flight.project).distinct().order_by(Flight.project).all() if r[0]]
    return render_template(
        "dashboard.html", flights=flights, docs=docs, dates=available_dates,
        selected_date=selected_date, directions=DIRECTIONS, projects=projects,
        selected_project=project, selected_direction=direction, selected_tag=tag, search_query=q,
    )


@app.get("/flight/<int:flight_id>")
@login_required
def flight_view(flight_id):
    flight = db.get_or_404(Flight, flight_id)
    flight.view_count = (flight.view_count or 0) + 1
    db.session.commit()
    audit("view", "flight", flight.id, f"Viewed {flight.flight_id}")
    return render_template("flight.html", flight=flight)


@app.get("/files/<path:filename>")
def protected_file(filename):
    # Never allow traversal; only serve files in our upload directory. Public
    # viewers may access files only from the project that their code unlocks.
    safe = Path(filename).name
    flight_record = Flight.query.filter_by(filename=safe).first()
    document_record = ProjectFile.query.filter_by(filename=safe).first()
    map_record = ProjectSiteMap.query.filter_by(filename=safe).first()
    if not current_user.is_authenticated:
        viewer_code = session.get("viewer_code")
        access = ProjectAccess.query.filter_by(code=viewer_code).first() if viewer_code else None
        record_project = (flight_record.project if flight_record else
                          document_record.project if document_record else
                          map_record.project if map_record else None)
        if not access or access.project != record_project:
            abort(403)
    path = UPLOAD_DIR / safe
    if not path.exists() or not path.is_file():
        abort(404)
    download = request.args.get("download") == "1"
    flight = flight_record
    doc = document_record
    if download:
        if flight:
            flight.download_count = (flight.download_count or 0) + 1
            resource = ("flight", flight.id, flight.flight_id)
        elif doc:
            doc.download_count = (doc.download_count or 0) + 1
            resource = ("document", doc.id, doc.name)
        else:
            resource = ("file", safe, safe)
        db.session.commit()
        audit("download", resource[0], resource[1], resource[2])
    else:
        audit("stream", "file", safe, "Authenticated file access")
    return send_from_directory(str(UPLOAD_DIR), safe, as_attachment=download, conditional=True, mimetype=mimetypes.guess_type(safe)[0])


@app.route("/admin")
@admin_required
def admin():
    users = User.query.order_by(User.created_at.desc()).all()
    flights = Flight.query.order_by(Flight.date.desc(), Flight.uploaded_at.desc()).limit(250).all()
    docs = ProjectFile.query.order_by(ProjectFile.date.desc(), ProjectFile.uploaded_at.desc()).limit(250).all()
    audit_logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(100).all()
    project_names = {r[0] for r in db.session.query(Flight.project).distinct().all() if r[0]}
    project_names.update(r[0] for r in db.session.query(ProjectFile.project).distinct().all() if r[0])
    access_rows = {row.project: row for row in ProjectAccess.query.all()}
    project_names.update(access_rows)
    selected_map_project = request.args.get("map_project", "").strip()
    if selected_map_project not in project_names:
        selected_map_project = sorted(project_names, key=str.casefold)[0] if project_names else ""
    selected_site_map = ProjectSiteMap.query.filter_by(project=selected_map_project).first() if selected_map_project else None
    selected_map_pins = ProjectMapPin.query.filter_by(project=selected_map_project).order_by(ProjectMapPin.id).all() if selected_map_project else []
    return render_template("admin.html", users=users, flights=flights, docs=docs, audit_logs=audit_logs,
                           project_names=sorted(project_names, key=str.casefold), access_rows=access_rows,
                           selected_map_project=selected_map_project, selected_site_map=selected_site_map,
                           selected_map_pins=selected_map_pins, map_directions=MAP_DIRECTIONS)


@app.post("/admin/projects/access")
@admin_required
def create_project_access():
    project = request.form.get("project", "").strip()[:160]
    if not project:
        abort(400, "Project is required")
    access = ProjectAccess.query.filter_by(project=project).first()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    for _ in range(100):
        code = "".join(secrets.choice(alphabet) for _ in range(4))
        if not ProjectAccess.query.filter_by(code=code).first():
            break
    else:
        abort(503, "Could not allocate a unique code")
    if access:
        access.code = code
        access.created_at = now_utc()
    else:
        access = ProjectAccess(project=project, code=code)
        db.session.add(access)
    db.session.commit()
    audit("project_access", "project", project, f"Generated viewer code {code}")
    flash(f"Viewer access updated for {project}.", "success")
    return redirect(url_for("admin") + "#project-access")


@app.route("/admin/upload", methods=["GET", "POST"])
@admin_required
def upload():
    if request.method == "POST":
        date_value = request.form.get("date", "")
        flight_id = request.form.get("flight_id", "").strip()[:80]
        direction = request.form.get("direction", "").strip()
        title = request.form.get("title", "").strip()[:200]
        description = request.form.get("description", "").strip()
        duration = request.form.get("duration", "").strip()[:30]
        project = request.form.get("project", "Main Project").strip()[:160] or "Main Project"
        area = request.form.get("area", "").strip()[:160]
        notes = request.form.get("notes", "").strip()
        drone = request.form.get("drone", "").strip()[:120]
        pilot = request.form.get("pilot", "").strip()[:120]
        client = request.form.get("client", "").strip()[:160]
        captured_at = parse_datetime(request.form.get("captured_at", ""))
        tags = tags_from_request()
        video = request.files.get("video")

        date_value = parse_date(date_value)
        if not date_value:
            flash("Choose a valid date.", "error")
            return redirect(url_for("upload"))
        if not flight_id or not title or direction not in DIRECTIONS:
            flash("Flight ID, title, and direction are required.", "error")
            return redirect(url_for("upload"))
        if not video or not video.filename:
            flash("Select a video file.", "error")
            return redirect(url_for("upload"))
        if not ext_allowed(video.filename, ALLOWED_VIDEO):
            flash("Allowed video types: MP4, MOV, M4V, WEBM, AVI.", "error")
            return redirect(url_for("upload"))
        if Flight.query.filter_by(flight_id=flight_id).first():
            flash("That Flight ID already exists.", "error")
            return redirect(url_for("upload"))

        stored = None
        try:
            stored = save_upload(video)
            filename, size, checksum, original = stored
            # Avoid duplicate physical uploads even if the flight ID differs.
            duplicate = Flight.query.filter_by(checksum_sha256=checksum).first()
            if duplicate:
                delete_stored_file(filename)
                flash(f"This video already exists as Flight {duplicate.flight_id}.", "error")
                return redirect(url_for("upload"))

            flight = Flight(
                flight_id=flight_id, date=date_value, direction=direction, title=title,
                description=description, duration=duration, filename=filename,
                original_filename=original, file_size=size, uploaded_by=current_user.id,
                project=project, area=area, tags=json.dumps(tags), notes=notes,
                drone=drone, pilot=pilot, client=client, captured_at=captured_at,
                checksum_sha256=checksum, status="ready",
            )
            db.session.add(flight)
            db.session.commit()
            audit("upload", "flight", flight.id, f"Uploaded {original}; tags={tags}")
            flash(f"Flight {flight_id} uploaded successfully.", "success")
            return redirect(url_for("admin"))
        except Exception as exc:
            db.session.rollback()
            if stored:
                delete_stored_file(stored[0])
            app.logger.exception("Flight upload failed")
            flash(f"Upload failed: {exc}", "error")
            return redirect(url_for("upload"))

    project_names = {r[0] for r in db.session.query(Flight.project).distinct().all() if r[0]}
    project_names.update(r[0] for r in db.session.query(ProjectFile.project).distinct().all() if r[0])
    project_names.update(r[0] for r in db.session.query(ProjectAccess.project).distinct().all())
    return render_template("upload.html", directions=DIRECTIONS,
                           projects=sorted(project_names, key=str.casefold))


@app.post("/admin/upload-batch")
@admin_required
def upload_batch():
    """Upload multiple flights for one project/date with a direction per video."""
    date_value = parse_date(request.form.get("date"))
    project = request.form.get("project", "Main Project").strip()[:160] or "Main Project"
    tags = tags_from_request()
    area = request.form.get("area", "").strip()[:160]
    videos = request.files.getlist("videos")
    directions = request.form.getlist("directions")
    titles = request.form.getlist("titles")
    if not date_value or not videos:
        flash("Choose a date and add at least one video.", "error")
        return redirect(url_for("upload"))
    if len(directions) != len(videos) or any(d not in DIRECTIONS for d in directions):
        flash("Choose a valid direction for every selected video.", "error")
        return redirect(url_for("upload"))

    created = 0
    failed = 0
    for index, (video, direction) in enumerate(zip(videos, directions)):
        if not video or not video.filename or not ext_allowed(video.filename, ALLOWED_VIDEO):
            failed += 1
            continue
        stored = None
        try:
            stored = save_upload(video)
            filename, size, checksum, original = stored
            if Flight.query.filter_by(checksum_sha256=checksum).first():
                delete_stored_file(filename)
                failed += 1
                continue
            stem = Path(original).stem
            base_id = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:50] or "FLIGHT"
            flight_id = f"{date_value:%Y%m%d}-{direction.replace('-', '')}-{base_id}"
            suffix = 1
            candidate = flight_id
            while Flight.query.filter_by(flight_id=candidate).first():
                suffix += 1
                candidate = f"{flight_id}-{suffix}"
            supplied_title = titles[index].strip()[:200] if index < len(titles) else ""
            flight = Flight(
                flight_id=candidate, date=date_value, direction=direction,
                title=supplied_title or stem[:200], description="", filename=filename,
                original_filename=original, file_size=size, uploaded_by=current_user.id,
                project=project, area=area, tags=json.dumps(tags), checksum_sha256=checksum,
                status="ready",
            )
            db.session.add(flight)
            db.session.commit()
            audit("upload_batch", "flight", flight.id, f"Uploaded {original}; direction={direction}; date={date_value}")
            created += 1
        except Exception:
            db.session.rollback()
            if stored:
                delete_stored_file(stored[0])
            failed += 1
    flash(f"Batch upload complete: {created} uploaded, {failed} skipped/failed.", "success" if created else "error")
    return redirect(url_for("admin") + "#recent-flights")


@app.post("/admin/projects/map")
@admin_required
def upload_project_map():
    project = request.form.get("project", "").strip()[:160]
    map_file = request.files.get("map_file")
    if not project or not map_file or not map_file.filename or extension(map_file.filename) not in {"jpg", "jpeg", "png", "webp"}:
        flash("Choose a project and a JPG, PNG, or WEBP site map.", "error")
        return redirect(url_for("admin", map_project=project) + "#map-settings")
    stored = None
    try:
        stored = save_upload(map_file)
        filename, _size, _checksum, original = stored
        site_map = ProjectSiteMap.query.filter_by(project=project).first()
        old_filename = site_map.filename if site_map else None
        if site_map:
            site_map.filename = filename
            site_map.original_filename = original
            site_map.uploaded_at = now_utc()
        else:
            site_map = ProjectSiteMap(project=project, filename=filename, original_filename=original)
            db.session.add(site_map)
        db.session.commit()
        if old_filename:
            delete_stored_file(old_filename)
        audit("upload_map", "project", project, f"Uploaded site overview map {original}")
        flash("Site overview map uploaded. Click the map to place direction pins.", "success")
    except Exception:
        db.session.rollback()
        if stored:
            delete_stored_file(stored[0])
        app.logger.exception("Project map upload failed")
        flash("Map upload failed.", "error")
    return redirect(url_for("admin", map_project=project) + "#map-settings")


@app.post("/admin/projects/pins")
@admin_required
def create_project_pin():
    project = request.form.get("project", "").strip()[:160]
    label = request.form.get("label", "").strip()[:120]
    direction = request.form.get("direction", "").strip().upper()
    try:
        x = float(request.form.get("x_percent", ""))
        y = float(request.form.get("y_percent", ""))
    except (TypeError, ValueError):
        abort(400, "Click a location on the map first")
    if not ProjectSiteMap.query.filter_by(project=project).first():
        abort(400, "Upload a site map before adding pins")
    if direction not in MAP_DIRECTIONS or not (0 <= x <= 100 and 0 <= y <= 100):
        abort(400, "Pin direction or map coordinates are invalid")
    db.session.add(ProjectMapPin(project=project, label=label or direction, direction=direction,
                                 x_percent=x, y_percent=y))
    db.session.commit()
    audit("add_map_pin", "project", project, f"Added {direction} pin at {x:.1f}%, {y:.1f}%")
    return redirect(url_for("admin", map_project=project) + "#map-settings")


@app.post("/admin/projects/pins/<int:pin_id>/delete")
@admin_required
def delete_project_pin(pin_id):
    pin = db.get_or_404(ProjectMapPin, pin_id)
    project = pin.project
    db.session.delete(pin)
    db.session.commit()
    audit("delete_map_pin", "project", project, f"Removed {pin.direction} pin")
    return redirect(url_for("admin", map_project=project) + "#map-settings")


@app.post("/admin/document")
@admin_required
def upload_document():
    date_value = parse_date(request.form.get("date"))
    name = request.form.get("name", "").strip()[:255]
    project = request.form.get("project", "Main Project").strip()[:160] or "Main Project"
    notes = request.form.get("notes", "").strip()
    tags = tags_from_request()
    file = request.files.get("file")
    if not date_value:
        flash("Choose a valid date.", "error")
        return redirect(url_for("admin"))
    if not name or not file or not file.filename:
        flash("Document name and file are required.", "error")
        return redirect(url_for("admin"))
    if not ext_allowed(file.filename, ALLOWED_DOCS):
        flash("That document type is not allowed.", "error")
        return redirect(url_for("admin"))
    stored = None
    try:
        stored = save_upload(file)
        filename, size, checksum, original = stored
        if ProjectFile.query.filter_by(checksum_sha256=checksum).first():
            delete_stored_file(filename)
            flash("That exact document already exists.", "error")
            return redirect(url_for("admin"))
        p = ProjectFile(
            date=date_value, name=name, filename=filename, category=extension(original).upper() or "Document",
            uploaded_by=current_user.id, project=project, tags=json.dumps(tags), notes=notes,
            original_filename=original, file_size=size, checksum_sha256=checksum,
        )
        db.session.add(p)
        db.session.commit()
        audit("upload", "document", p.id, f"Uploaded {original}; tags={tags}")
        flash("Document uploaded.", "success")
    except Exception as exc:
        db.session.rollback()
        if stored:
            delete_stored_file(stored[0])
        app.logger.exception("Document upload failed")
        flash(f"Document upload failed: {exc}", "error")
    return redirect(url_for("admin"))


@app.post("/admin/flight/<int:flight_id>/edit")
@admin_required
def edit_flight(flight_id):
    flight = db.get_or_404(Flight, flight_id)
    flight.title = request.form.get("title", flight.title).strip()[:200] or flight.title
    flight.description = request.form.get("description", flight.description).strip()
    flight.project = request.form.get("project", flight.project).strip()[:160] or "Main Project"
    flight.area = request.form.get("area", flight.area).strip()[:160]
    flight.direction = request.form.get("direction", flight.direction).strip()
    if flight.direction not in DIRECTIONS:
        abort(400, "Invalid direction")
    flight.tags = json.dumps(tags_from_request())
    flight.notes = request.form.get("notes", flight.notes).strip()
    flight.drone = request.form.get("drone", flight.drone).strip()[:120]
    flight.pilot = request.form.get("pilot", flight.pilot).strip()[:120]
    flight.client = request.form.get("client", flight.client).strip()[:160]
    captured = parse_datetime(request.form.get("captured_at", ""))
    if captured:
        flight.captured_at = captured
    db.session.commit()
    audit("edit", "flight", flight.id, f"Updated metadata; tags={flight.tag_list}")
    flash("Flight metadata updated.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/document/<int:doc_id>/edit")
@admin_required
def edit_document(doc_id):
    doc = db.get_or_404(ProjectFile, doc_id)
    doc.name = request.form.get("name", doc.name).strip()[:255] or doc.name
    doc.project = request.form.get("project", doc.project).strip()[:160] or "Main Project"
    doc.tags = json.dumps(tags_from_request())
    doc.notes = request.form.get("notes", doc.notes).strip()
    db.session.commit()
    audit("edit", "document", doc.id, f"Updated metadata; tags={doc.tag_list}")
    flash("Document metadata updated.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/users/create")
@admin_required
def create_user():
    username = request.form.get("username", "").strip().lower()
    name = request.form.get("name", "").strip()
    password = request.form.get("password", "")
    is_admin = request.form.get("is_admin") == "on"
    if not username or not re.fullmatch(r"[a-z0-9._-]{3,80}", username) or not name or len(password) < 10:
        flash("Use a valid username, name, and password of at least 10 characters.", "error")
        return redirect(url_for("admin"))
    if User.query.filter_by(username=username).first():
        flash("Username already exists.", "error")
        return redirect(url_for("admin"))
    u = User(username=username, name=name, is_admin=is_admin)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    audit("create_user", "user", u.id, f"Created {username}; admin={is_admin}")
    flash("User created.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/users/<int:user_id>/toggle")
@admin_required
def toggle_user(user_id):
    u = db.get_or_404(User, user_id)
    if u.id == current_user.id:
        flash("You cannot disable your own account.", "error")
        return redirect(url_for("admin"))
    u.active = not u.active
    db.session.commit()
    audit("toggle_user", "user", u.id, f"active={u.active}")
    flash("User status updated.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/users/<int:user_id>/reset")
@admin_required
def reset_password(user_id):
    u = db.get_or_404(User, user_id)
    new_password = request.form.get("new_password", "")
    if len(new_password) < 10:
        flash("New password must be at least 10 characters.", "error")
        return redirect(url_for("admin"))
    u.set_password(new_password)
    db.session.commit()
    audit("reset_password", "user", u.id, "Password reset by administrator")
    flash("Password reset.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/flight/<int:flight_id>/delete")
@admin_required
def delete_flight(flight_id):
    f = db.get_or_404(Flight, flight_id)
    filename = f.filename
    flight_id_text = f.flight_id
    db.session.delete(f)
    db.session.commit()
    delete_stored_file(filename)
    audit("delete", "flight", flight_id, f"Deleted {flight_id_text}")
    flash("Flight deleted.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/document/<int:doc_id>/delete")
@admin_required
def delete_document(doc_id):
    d = db.get_or_404(ProjectFile, doc_id)
    filename = d.filename
    db.session.delete(d)
    db.session.commit()
    delete_stored_file(filename)
    audit("delete", "document", doc_id, f"Deleted {d.name}")
    flash("Document deleted.", "success")
    return redirect(url_for("admin"))


# --------------------------- JSON API ---------------------------
# These endpoints let a future React/mobile frontend use the same backend.

@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok", "service": "construction-drone-portal", "time_utc": now_utc().isoformat()})


@app.get("/api/flights")
@login_required
def api_flights():
    query = Flight.query
    date_value = parse_date(request.args.get("date"))
    project = request.args.get("project", "").strip()[:160]
    direction = request.args.get("direction", "").strip()
    tag = request.args.get("tag", "").strip()[:40]
    q = request.args.get("q", "").strip()[: app.config["MAX_SEARCH_LENGTH"]]
    if date_value:
        query = query.filter(Flight.date == date_value)
    if project:
        query = query.filter(Flight.project == project)
    if direction in DIRECTIONS:
        query = query.filter(Flight.direction == direction)
    if tag:
        query = query.filter(Flight.tags.ilike(f'%"{tag}"%'))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Flight.flight_id.ilike(like), Flight.title.ilike(like), Flight.description.ilike(like), Flight.area.ilike(like), Flight.tags.ilike(like)))
    rows = query.order_by(Flight.date.desc(), Flight.uploaded_at.desc()).limit(500).all()
    return jsonify({"count": len(rows), "flights": [flight_json(f) for f in rows]})


def flight_json(f):
    return {
        "id": f.id, "flight_id": f.flight_id, "date": f.date.isoformat(), "direction": f.direction,
        "title": f.title, "description": f.description, "project": f.project, "area": f.area,
        "tags": f.tag_list, "notes": f.notes, "drone": f.drone, "pilot": f.pilot, "client": f.client,
        "captured_at": f.captured_at.isoformat() if f.captured_at else None,
        "file_size": f.file_size, "file_size_human": human_size(f.file_size),
        "duration": f.duration, "status": f.status, "view_count": f.view_count,
        "download_count": f.download_count,
        "stream_url": url_for("protected_file", filename=f.filename),
        "download_url": url_for("protected_file", filename=f.filename, download=1),
    }


@app.get("/api/tags")
@login_required
def api_tags():
    tags = {}
    for raw in db.session.query(Flight.tags).all():
        for tag in parse_tags(raw[0]):
            tags[tag.casefold()] = tag
    for raw in db.session.query(ProjectFile.tags).all():
        for tag in parse_tags(raw[0]):
            tags[tag.casefold()] = tag
    return jsonify(sorted(tags.values(), key=str.casefold))


@app.get("/api/projects")
@login_required
def api_projects():
    values = {r[0] for r in Flight.query.with_entities(Flight.project).distinct().all() if r[0]}
    values.update({r[0] for r in ProjectFile.query.with_entities(ProjectFile.project).distinct().all() if r[0]})
    return jsonify(sorted(values, key=str.casefold))


@app.get("/api/stats")
@login_required
def api_stats():
    total_flights = Flight.query.count()
    total_docs = ProjectFile.query.count()
    total_bytes = (db.session.query(func.coalesce(func.sum(Flight.file_size), 0)).scalar() or 0) + (db.session.query(func.coalesce(func.sum(ProjectFile.file_size), 0)).scalar() or 0)
    projects = db.session.query(func.count(func.distinct(Flight.project))).scalar() or 0
    return jsonify({"flights": total_flights, "documents": total_docs, "storage_bytes": total_bytes, "storage_human": human_size(total_bytes), "projects": projects})


@app.get("/api/audit")
@admin_required
def api_audit():
    limit = min(max(int(request.args.get("limit", 100)), 1), 500)
    rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return jsonify([{
        "id": r.id, "user_id": r.user_id, "action": r.action,
        "resource_type": r.resource_type, "resource_id": r.resource_id,
        "detail": r.detail, "ip_address": r.ip_address, "created_at": r.created_at.isoformat()
    } for r in rows])


@app.errorhandler(413)
def too_large(_):
    return "Upload is too large. The server limit is 8 GB.", 413


@app.errorhandler(403)
def forbidden(_):
    return render_template("login.html", error="You do not have permission to access that page."), 403


def init_db():
    with app.app_context():
        migrate_sqlite()
        admin_username = os.environ.get("ADMIN_USERNAME", "admin").strip().lower()
        admin_password = os.environ.get("ADMIN_PASSWORD")
        if not User.query.filter_by(username=admin_username).first():
            if not admin_password:
                raise RuntimeError("Set ADMIN_PASSWORD before initializing the administrator account.")
            u = User(username=admin_username, name="Project Administrator", is_admin=True)
            u.set_password(admin_password)
            db.session.add(u)
            db.session.commit()
            print(f"Created administrator account: {admin_username}")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
