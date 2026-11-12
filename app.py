import urllib.parse
import bleach
import markdown
from flask import Flask, request
from markdownify import markdownify
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql import text

from config import Config
from extensions import db, login_manager, migrate
from models import User

from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.billing import billing_bp
from routes.clients import clients_bp
from routes.portal import portal_bp
from routes.projects import projects_bp
from routes.tickets import tickets_bp

app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
db.init_app(app)
migrate.init_app(app, db)
login_manager.init_app(app)
login_manager.login_view = "auth.login"

# Register modular blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(tickets_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(clients_bp)
app.register_blueprint(billing_bp)
app.register_blueprint(portal_bp)
app.register_blueprint(admin_bp)

# Configure markdown & bleach sanitization
md = markdown.Markdown(
    extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"]
)

allowed_tags = [
    "p", "br", "strong", "em", "u",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code",
    "a", "img", "table", "thead", "tbody", "tr", "th", "td",
    "hr", "div", "span",
]
allowed_attributes = {
    "*": ["class", "id"],
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "code": ["class"],
    "pre": ["class"],
}


@app.context_processor
def inject_branding():
    return {
        "BRAND_NAME": app.config.get("BRAND_NAME"),
        "BRAND_LOGO_PATH": app.config.get("BRAND_LOGO_PATH"),
        "COMPANY_NAME": app.config.get("COMPANY_NAME"),
    }


@app.context_processor
def inject_request():
    return dict(request=request)


@app.template_filter("ymd")
def ymd(value):
    if not value:
        return ""
    try:
        return value.strftime("%Y-%m-%d")
    except Exception:
        s = str(value)
        return s[:10] if len(s) >= 10 else s


@app.template_filter("sanitize_html")
def sanitize_html_filter(s):
    ALLOWED_TAGS = [
        "b", "i", "u", "em", "strong", "a", "p", "br",
        "ul", "ol", "li", "pre", "code", "blockquote",
    ]
    ALLOWED_ATTRIBUTES = {"a": ["href", "title", "target"]}
    return bleach.clean(s, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)


@app.template_filter("markdown")
def markdown_filter(text_val):
    if text_val is None:
        return ""
    html = md.convert(str(text_val))
    return bleach.clean(html, tags=allowed_tags, attributes=allowed_attributes)


@app.template_filter("urlencode")
def urlencode_filter(s):
    if s is None:
        return ""
    return urllib.parse.quote(s)


@app.template_filter("clean_email_to_markdown")
def clean_email_to_markdown_filter(email_text):
    if email_text is None:
        return ""
    markdown_text = markdownify(email_text, heading_style="ATX")
    lines = markdown_text.split("\n")
    cleaned_lines = []
    for line in lines:
        if any(
            pattern in line.lower()
            for pattern in [
                "from:", "sent:", "to:", "subject:", "cc:", "bcc:",
                "best regards", "sincerely", "thank you", "thanks",
                "--", "---", "sent from my", "get outlook",
            ]
        ):
            continue
        if not cleaned_lines and line.strip() == "":
            continue
        line = line.strip()
        if line:
            cleaned_lines.append(line)
    return "\n\n".join(cleaned_lines)


@app.before_request
def before_request():
    """Ensure MySQL connection is active before processing requests."""
    try:
        db.session.execute(text("SELECT 1"))
    except OperationalError:
        db.session.rollback()


@app.teardown_appcontext
def shutdown_session(exception=None):
    """Ensure database session is properly closed after each request."""
    db.session.remove()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)