import pickle
import random
import urllib.parse
from datetime import datetime, timedelta, timezone
from io import BytesIO

import bleach
import markdown
import pandas as pd
import pymysql
from dateutil import parser
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from google_auth_oauthlib.flow import InstalledAppFlow
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from markdownify import markdownify
from pytz import UTC, timezone
from quickbooks import QuickBooks
from quickbooks.objects import Invoice, SalesItemLine, SalesItemLineDetail
from quickbooks.objects.base import Ref
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql import func, text
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from forms import (
    AddBulkInventoryForm,
    AddDeployedAssetForm,
    AddNoteForm,
    ChangeRoleForm,
    ClientForm,
    CompanyForm,
    EditClientForm,
    EditCompanyForm,
    EditNoteForm,
    LoginForm,
    PhaseForm,
    ProjectForm,
    RandomNumberForm,
    RegistrationForm,
    SelectPhaseForm,
    SelectProjectForm,
    TicketForm,
    UpdateProjectForm,
    UpdateTicketForm,
)
from gmail_send import send_gmail_message
from google_calendar import create_event
from models import (
    BulkInventory,
    Client,
    Company,
    DeployedAsset,
    Phase,
    Project,
    Ticket,
    TicketNote,
    User,
    db,
)

# db = SQLAlchemy()

app = Flask(__name__)


# configurations
app.config.from_object(Config)


@app.context_processor
def inject_branding():
    return {
        "BRAND_NAME": app.config.get("BRAND_NAME"),
        "BRAND_LOGO_PATH": app.config.get("BRAND_LOGO_PATH"),
        "COMPANY_NAME": app.config.get("COMPANY_NAME"),  # Add this line
    }


@app.context_processor
def inject_request():
    return dict(request=request)


@app.template_filter("ymd")
def ymd(value):
    """Return YYYY-MM-DD for datetime/date; pass through strings; empty for falsy."""
    if not value:
        return ""
    try:
        return value.strftime("%Y-%m-%d")
    except Exception:
        # If it's already a string (or something else), return a safe slice
        s = str(value)
        return s[:10] if len(s) >= 10 else s


# #initialize the database
db.init_app(app)

# Initialize Flask-Migrate
migrate = Migrate(app, db)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Configure markdown
md = markdown.Markdown(
    extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"]
)

# Configure bleach for HTML sanitization
allowed_tags = [
    "p",
    "br",
    "strong",
    "em",
    "u",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "hr",
    "div",
    "span",
]
allowed_attributes = {
    "*": ["class", "id"],
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "code": ["class"],
    "pre": ["class"],
}


SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CLIENT_SECRET_PATH = app.config["CLIENT_SECRET_PATH"]
TOKEN_PATH = app.config["TOKEN_PATH"]

def send_assignment_email(ticket):
    """Helper function to send email notification to an assigned technician."""
    if not ticket.assigned_tech or not ticket.assigned_tech.email:
        return

    subject = f"[Assigned] Ticket #{ticket.id} - {ticket.subject}"
    logo_url = Config.LOGO_URL

    client_info = (
        f"{ticket.client.first_name} {ticket.client.last_name}"
        if ticket.client
        else "N/A"
    )

    body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #e0e6ed; color: #2a3f54; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 20px; border-radius: 10px; }}
            h2 {{ color: #1b3a57; }}
            .ticket-details {{ background-color: #cbd5e0; padding: 15px; border-radius: 5px; margin: 15px 0; }}
            .footer {{ text-align: center; font-size: 0.9em; color: #2a3f54; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="text-align: center;">
                <img src="{logo_url}" alt="Logo" style="max-width: 200px; margin-bottom: 20px;">
            </div>
            <h2>New Ticket Assignment</h2>
            <p>Hello <strong>{ticket.assigned_tech.username}</strong>,</p>
            <p>You have been assigned to Ticket <strong>#{ticket.id}</strong>:</p>
            <div class="ticket-details">
                <p><strong>Subject:</strong> {ticket.subject}</p>
                <p><strong>Priority:</strong> {ticket.priority}</p>
                <p><strong>Status:</strong> {ticket.status}</p>
                <p><strong>Client:</strong> {client_info}</p>
                <p><strong>Description:</strong></p>
                <div style="background-color: #ffffff; padding: 10px; border-left: 4px solid #007bff; margin-top: 5px;">
                    {md.convert(ticket.description or '')}
                </div>
            </div>
            <p>Please log in to review and respond to this ticket.</p>
            <div class="footer">
                <p>{Config.COMPANY_NAME} | Contact Us: {Config.COMPANY_SUPPORT_PHONE} | {Config.COMPANY_SUPPORT_EMAIL}</p>
            </div>
        </div>
    </body>
    </html>
    """
    try:
        send_gmail_message(to=ticket.assigned_tech.email, subject=subject, html_body=body)
    except Exception as e:
        print(f"Error sending assignment email: {e}")

@app.before_request
def before_request():
    """Ensure MySQL connection is active before processing requests."""
    try:
        with app.app_context():
            db.session.execute(text("SELECT 1"))
    except OperationalError:
        db.session.rollback()


@app.teardown_appcontext
def shutdown_session(exception=None):
    """Ensure databse session is properly closed after each request."""
    db.session.remove()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.route("/register", methods=["GET", "POST"])
@login_required
def register():
    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = generate_password_hash(
            form.password.data, method="pbkdf2:sha256"
        )
        user = User(
            username=form.username.data, email=form.email.data, password=hashed_password
        )
        db.session.add(user)
        db.session.commit()
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user)
            flash("Login successful!", "success")

            # Send clients to the portal, everyone else to the dashboard
            if user.role == "client":
                return redirect(url_for("client_portal"))
            else:
                next_page = request.args.get("next")
                return (
                    redirect(next_page) if next_page else redirect(url_for("dashboard"))
                )
        else:
            flash(
                "Login unsuccessful. Please check your username and password.", "danger"
            )
    return render_template("login.html", form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))

@app.route("/")
@app.route("/dashboard")
@login_required
def dashboard():
    # Security Guard: Block clients from the staff dashboard
    if current_user.role == "client":
        return redirect(url_for("client_portal"))

    # Custom ordering for status: Open > In Progress > Closed
    tickets = (
        Ticket.query.filter(
            ~Ticket.status.in_(["Closed"])  # ✅ Corrected
        )
        .order_by(
            db.case(
                (Ticket.status.in_(["Open", "In Progress"]), 1),
                (Ticket.status == "Touched", 2),
                (Ticket.status == "On Hold", 3),
                (Ticket.status == "Closed", 4),
            ).asc(),
            db.case(
                (Ticket.priority == "Critical", 1),
                (Ticket.priority == "High", 2),
                (Ticket.priority == "Medium", 3),
                (Ticket.priority == "Low", 4),
            ).asc(),
        )
        .all()
    )

    eastern = timezone("US/Eastern")
    current_time = datetime.now(UTC)

    # ✅ Fix: Handle NULL/Invalid timestamps
    for ticket in tickets:
        if ticket.created_at:
            try:
                # ✅ Convert string timestamps safely
                if isinstance(ticket.created_at, str):
                    if ticket.created_at not in [None, "0000-00-00 00:00:00"]:
                        ticket.created_at = datetime.strptime(
                            ticket.created_at, "%Y-%m-%d %H:%M:%S"
                        )
                    else:
                        ticket.created_at = None  # Handle invalid timestamps

                # ✅ Ensure proper timezone conversion
                if ticket.created_at:
                    ticket.created_at = ticket.created_at.replace(
                        tzinfo=UTC
                    ).astimezone(eastern)

                    # ✅ Calculate ticket age
                    age = current_time - ticket.created_at.astimezone(UTC)
                    days = age.days
                    hours = age.seconds // 3600  # Get hours from seconds
                    ticket.age = f"{days}d:{hours}h"
                else:
                    ticket.age = "N/A"  # Handle cases with missing timestamps

            except ValueError:
                ticket.created_at = None  # Handle any parsing errors
                ticket.age = "N/A"

    session.pop("start_time_utc", None)
    # Add this line to fetch tech and admin users
    technicians = User.query.filter(User.role.in_(['admin', 'technician'])).all()

    session.pop("start_time_utc", None)
    return render_template("dashboard.html", tickets=tickets, technicians=technicians)


@app.route("/download_tickets_excel")
@login_required
def download_tickets_excel():
    # Retrieve all the tickets from the database (replace with your actual query)
    # Create a list of dictionaries to store ticket data
    tickets = (
        Ticket.query.filter(Ticket.status != "Closed")
        .order_by(
            db.case(
                (Ticket.status in ["Open", "In Progress"], 1),
                (Ticket.status == "Touched", 2),
                (Ticket.status == "On Hold", 3),
                (Ticket.status == "Closed", 4),
            ).asc(),
            db.case(
                (Ticket.priority == "Important-Urgent", 1),
                (Ticket.priority == "Important-NotUrgent", 2),
                (Ticket.priority == "NotImportant-Urgent", 3),
                (Ticket.priority == "NotImportant-NotUrgent", 4),
            ).asc(),
        )
        .all()
    )
    ticket_data = []
    for ticket in tickets:
        ticket_data.append(
            {
                "Ticket ID": ticket.id,
                "Subject": ticket.subject,
                "Status": ticket.status,
                "Priority": ticket.priority,
                "Due Date": ticket.due_date.strftime("%Y-%m-%d")
                if ticket.due_date
                else None,
                "Estimated Hours": ticket.estimated_hours,
                "Project": ticket.project.name if ticket.project else "No Project",
                "Phase": ticket.phase.name if ticket.phase else "No Phase",
                "Assigned Tech": ticket.assigned_tech.username if ticket.assigned_tech else "Unassigned",
            }
        )

    # Create a DataFrame from the ticket data
    df = pd.DataFrame(ticket_data)

    # Create an in-memory output file
    output = BytesIO()

    # Write the DataFrame to an Excel file
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Tickets")

    # Set the output file's position to the beginning
    output.seek(0)

    # Send the file as an attachment to be downloaded by the user
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="tickets.xlsx",
    )



@app.route("/project_or_regular")
@login_required
def project_or_regular():
    return render_template("project_or_regular.html")


@app.route("/new_ticket", methods=["GET", "POST"])
@login_required
def new_ticket():
    project_id = request.args.get("project_id", type=int)  # Get project_id if passed
    phase_id = request.args.get("phase_id", type=int)  # Get phase_id if passed
    from_dashboard = request.args.get("from_dashboard")  # Track the origin

    form = TicketForm()

    # Populate the client (employee) choices for the requestor
    clients = Client.query.all()  # Retrieve all clients (employees)
    form.client_id.choices = [
        (client.id, f"{client.first_name} {client.last_name} ({client.company.name})")
        for client in clients
    ]

    users = User.query.filter(User.role.in_(['admin', 'technician'])).all()
    if not users:
        users = User.query.all()
    form.assigned_tech_id.choices = [(0, "Unassigned")] + [
        (u.id, f"{u.username} ({u.email})") for u in users
    ]

    # Populate phase choices if a project is selected
    if project_id:
        project = Project.query.get_or_404(project_id)
        phases = [(p.id, p.name) for p in project.phases]
        form.phase_id.choices = phases if phases else [(0, "No Phases Available")]
        if phase_id:
            form.phase_id.data = phase_id
    else:
        form.phase_id.choices = [(0, "No Project Assigned")]

    # Validate and handle the form submission
    if form.validate_on_submit():
        assigned_tech_id = (
            form.assigned_tech_id.data
            if form.assigned_tech_id.data and form.assigned_tech_id.data != 0
            else None
        )
        # Create the ticket
        ticket = Ticket(
            subject=form.subject.data,
            description=form.description.data,
            priority=form.priority.data,
            status=form.status.data,
            user_id=current_user.id,  # User who created the ticket
            assigned_tech_id=assigned_tech_id, # ADDED
            phase_id=form.phase_id.data if form.phase_id.data != 0 else None,
            project_id=project_id,
            client_id=form.client_id.data,  # Link the requestor (client)
            requestor_email=form.requestor_email.data,  # Add requestor email
            cc_emails=form.cc_emails.data,  # Add CC emails
            due_date=form.due_date.data,
            estimated_hours=form.estimated_hours.data if form.estimated_hours.data is not None else 0.5,
        )
        db.session.add(ticket)
        db.session.commit()
        if ticket.assigned_tech_id:
            send_assignment_email(ticket)
            
        flash("Ticket created successfully!", "success")
        # Redirect based on whether it’s linked to a project
        if phase_id:
            return redirect(url_for("project_dashboard", project_id=project_id))
        elif from_dashboard:
            return redirect(url_for("dashboard"))
        else:
            return redirect(url_for("dashboard"))

    return render_template(
        "new_ticket.html", form=form, project_id=project_id, phase_id=phase_id
    )


@app.route("/view_ticket/<int:id>", methods=["GET", "POST"])
@login_required
def view_ticket(id):
    ticket = Ticket.query.get_or_404(id)
    note_form = AddNoteForm()
    eastern = timezone("US/Eastern")  # Define the Eastern timezone

    # Safely access the project and company (if they exist)
    project = ticket.project
    company = project.company if project else None

    # Store the start time in UTC in the session when user views the ticket
    if "start_time_utc" not in session:
        session["start_time_utc"] = datetime.now(
            UTC
        ).isoformat()  # Store as ISO format string

    start_time_utc = datetime.fromisoformat(session["start_time_utc"])

    # Handle form submissions
    if request.method == "POST":
        # Handle Add Note form submission
        if (
            request.form.get("form_name") == "note_form"
            and note_form.validate_on_submit()
        ):
            # Convert form data from Eastern time to UTC
            if note_form.note_start_time.data:
                note_start_time = eastern.localize(
                    note_form.note_start_time.data
                ).astimezone(UTC)
            else:
                note_start_time = start_time_utc

            if note_form.note_finish_time.data:
                note_finish_time = eastern.localize(
                    note_form.note_finish_time.data
                ).astimezone(UTC)
            else:
                note_finish_time = datetime.now(
                    UTC
                )  # Set the finish time to now in UTC if not provided

            # Create and store the new note
            note = TicketNote(
                content=note_form.content.data,
                ticket_id=ticket.id,
                user_id=current_user.id,
                note_start_time=note_start_time,
                note_finish_time=note_finish_time,
            )
            db.session.add(note)
            db.session.commit()

            start_time_google = note_start_time.isoformat()
            end_time_google = note_finish_time.isoformat()

            create_event(
                summary=ticket.subject,
                start_time=start_time_google,
                end_time=end_time_google,
                description=note_form.content.data,
                location="",
            )

            # Clear the start time after the note is added
            session.pop("start_time_utc", None)

            if request.form.get("send_email") == "true":
                send_note_email(ticket.requestor_email, ticket, note)

            flash("Note added successfully!", "success")
            return redirect(url_for("view_ticket", id=ticket.id))

    # Convert completed_at and note times to Eastern Time for display
    if ticket.completed_at:
        ticket.completed_at = ticket.completed_at.replace(tzinfo=UTC).astimezone(
            eastern
        )

    # Convert note times from UTC to Eastern Time for display
    for note in ticket.notes:
        if note.created_at:
            note.created_at = note.created_at.replace(tzinfo=UTC).astimezone(eastern)
        if note.note_start_time:
            note.note_start_time = note.note_start_time.replace(tzinfo=UTC).astimezone(
                eastern
            )
        if note.note_finish_time:
            note.note_finish_time = note.note_finish_time.replace(
                tzinfo=UTC
            ).astimezone(eastern)

    # Render the template, passing the project and company details as well
    return render_template(
        "view_ticket.html",
        ticket=ticket,
        note_form=note_form,
        project=project,
        company=company,
    )


# Function to send an email when a note is added
def send_note_email(requestor_email, ticket, note):
    subject = f"New Note Added to Ticket# {ticket.id}: {ticket.subject}"
    eastern = timezone("US/Eastern")
    note_start_time_eastern = (
        note.note_start_time.replace(tzinfo=UTC)
        .astimezone(eastern)
        .strftime("%m/%d/%y %H:%M")
    )
    note_finish_time_eastern = (
        note.note_finish_time.replace(tzinfo=UTC)
        .astimezone(eastern)
        .strftime("%m/%d/%y %H:%M")
    )

    # Link to the Google Drive image (replace with your correct link)
    logo_url = Config.LOGO_URL

    # HTML email body with linked logo image
    body = f"""
    <html>
    <head>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background-color: #e0e6ed;
                color: #2a3f54;
                padding: 20px;
            }}
            .container {{
                max-width: 600px;
                margin: 0 auto;
                background-color: #ffffff;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
            }}
            h2 {{
                color: #1b3a57;
                text-align: center;
            }}
            .note-details {{
                background-color: #cbd5e0;
                padding: 10px;
                border-radius: 5px;
            }}
            .time {{
                font-weight: bold;
                color: #0077b6;
            }}
            .footer {{
                text-align: center;
                font-size: 0.9em;
                color: #2a3f54;
                margin-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="text-align: center;">
                <img src="{logo_url}" alt="RVA IT Pros Logo" style="max-width: 200px; margin-bottom: 20px;">
            </div>
            <h2>New Note Added to Your Ticket</h2>
            <p>Hello <strong>{ticket.client.first_name}</strong>,</p>
            <p>A new note has been added to your ticket:</p>
            <div class="note-details">
                <p><strong>Ticket ID:</strong> {ticket.id}</p>
                <p><strong>Subject:</strong> {ticket.subject}</p>
                <p><strong>Note Content:</strong></p>
                <div style="background-color: #ffffff; padding: 15px; border-left: 4px solid #007bff; margin: 10px 0; border-radius: 3px;">{md.convert(note.content)}</div>
                <p><strong>Time Spent:</strong> <span class="time">{note_start_time_eastern} to {note_finish_time_eastern}</span></p>
            </div>
            <p>If you have any questions or need further assistance, feel free to contact us.</p>
            <p>Regards,<br>
            <strong>RVA IT Helpdesk Team</strong></p>
            <div class="footer">
                <p>{Config.COMPANY_NAME} | Contact Us: {Config.COMPANY_SUPPORT_PHONE} | {Config.COMPANY_SUPPORT_EMAIL}</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Send the email without attaching the logo since it's linked via URL
    send_gmail_message(to=requestor_email, subject=subject, html_body=body)


@app.route("/edit_ticket/<int:id>", methods=["GET", "POST"])
@login_required
def edit_ticket(id):
    ticket = Ticket.query.get_or_404(id)

    if ticket.due_date and isinstance(ticket.due_date, str):
        try:
            ticket.due_date = parser.parse(ticket.due_date)
        except ValueError:
            ticket.due_date = None

    update_form = UpdateTicketForm(obj=ticket)

    # Populate client choices
    clients = Client.query.all()
    update_form.client_id.choices = [
        (client.id, f"{client.first_name} {client.last_name} ({client.company.name})")
        for client in clients
    ]

    # ADDED: Populate assigned technician choices
    users = User.query.filter(User.role.in_(['admin', 'technician'])).all()
    if not users:
        users = User.query.all()
    update_form.assigned_tech_id.choices = [(0, "Unassigned")] + [
        (u.id, f"{u.username} ({u.email})") for u in users
    ]

    if ticket.project_id:
        project = Project.query.get(ticket.project_id)
        if project and project.phases:
            update_form.phase_id.choices = [(p.id, p.name) for p in project.phases]
        else:
            update_form.phase_id.choices = [(0, "No Phases Available")]
    else:
        update_form.phase_id.choices = [(0, "No Project Assigned")]

    # ADDED: Pre-select assigned tech on GET request
    if request.method == "GET":
        update_form.assigned_tech_id.data = ticket.assigned_tech_id or 0

    if request.method == "POST" and update_form.validate_on_submit():
        old_tech_id = ticket.assigned_tech_id # ADDED: Track previous tech

        ticket.subject = update_form.subject.data
        ticket.description = update_form.description.data
        ticket.status = update_form.status.data
        ticket.priority = update_form.priority.data
        ticket.client_id = update_form.client_id.data
        ticket.requestor_email = update_form.requestor_email.data
        ticket.cc_emails = update_form.cc_emails.data
        ticket.phase_id = (
            update_form.phase_id.data if update_form.phase_id.data != 0 else None
        )
        ticket.estimated_hours = update_form.estimated_hours.data

        # ADDED: Check and assign new tech ID
        new_tech_id = (
            update_form.assigned_tech_id.data
            if update_form.assigned_tech_id.data != 0
            else None
        )
        ticket.assigned_tech_id = new_tech_id

        if update_form.due_date.data:
            ticket.due_date = update_form.due_date.data

        db.session.commit()

        # ADDED: Send notification email if assigned technician changed
        if new_tech_id and new_tech_id != old_tech_id:
            send_assignment_email(ticket)

        flash("Ticket updated successfully!", "success")
        return "Updated", 200

    return render_template("edit_ticket.html", ticket=ticket, update_form=update_form)


@app.route("/edit_note/<int:note_id>", methods=["GET", "POST"])
@login_required
def edit_note(note_id):
    note = TicketNote.query.get_or_404(note_id)
    form = EditNoteForm(obj=note)
    eastern = timezone("US/Eastern")  # Define the Eastern timezone
    utc = timezone("UTC")  # Define UTC timezone

    if form.validate_on_submit():
        # Convert form times (in Eastern Time) back to UTC before saving
        if form.note_start_time.data:
            note.note_start_time = (
                eastern.localize(form.note_start_time.data)
                .astimezone(utc)
                .replace(tzinfo=None)
            )
        if form.note_finish_time.data:
            note.note_finish_time = (
                eastern.localize(form.note_finish_time.data)
                .astimezone(utc)
                .replace(tzinfo=None)
            )
        note.content = form.content.data

        db.session.commit()
        flash("Note updated successfully!", "success")
        return redirect(url_for("view_ticket", id=note.ticket_id))

    if request.method == "GET":
        # Convert stored UTC times to Eastern Time for form display
        if note.note_start_time:
            form.note_start_time.data = (
                utc.localize(note.note_start_time)
                .astimezone(eastern)
                .replace(tzinfo=None)
            )
        if note.note_finish_time:
            form.note_finish_time.data = (
                utc.localize(note.note_finish_time)
                .astimezone(eastern)
                .replace(tzinfo=None)
            )
        form.content.data = note.content

    return render_template("edit_note.html", form=form, ticket=note.ticket, note=note)


@app.route("/delete_note/<int:note_id>", methods=["POST"])
@login_required
def delete_note(note_id):
    note = TicketNote.query.get_or_404(note_id)

    db.session.delete(note)
    db.session.commit()
    flash("Ticket Note deleted successfully!", "success")
    return redirect(url_for("view_ticket", id=note.ticket_id))


@app.route("/toggle_resolution/<int:note_id>", methods=["POST"])
@login_required
def toggle_resolution(note_id):
    note = TicketNote.query.get_or_404(note_id)

    # Toggle the billable status
    if note.is_resolution == False:
        note.is_resolution = True  # Convert to resolution note
    else:
        note.is_resolution = False  # Convert to not resolution

    db.session.commit()
    flash(
        f'Note "{note.id}" updated to {"Resolution" if note.is_resolution == True else "Not-Resolution"}!',
        "success",
    )
    return redirect(url_for("view_ticket", id=note.ticket_id))


@app.route("/toggle_billable/<int:ticket_id>", methods=["POST"])
@login_required
def toggle_billable(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)

    # Toggle the billable status
    if ticket.billable == "NB":
        ticket.billable = "R"  # Convert to Billable
    else:
        ticket.billable = "NB"  # Convert to Non-Billable

    db.session.commit()
    flash(
        f'Ticket "{ticket.subject}" updated to {"Billable" if ticket.billable == "R" else "Non-Billable"}!',
        "success",
    )
    return redirect(url_for("view_ticket", id=ticket_id))


@app.route("/toggle_billable_reviewed/<int:ticket_id>", methods=["POST"])
@login_required
def toggle_billable_reviewed(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)

    # Toggle the billable status
    if ticket.billable == "NB":
        ticket.billable = "R"  # Convert to Billable
    else:
        ticket.billable = "NB"  # Convert to Non-Billable

    db.session.commit()
    flash(
        f'Ticket "{ticket.subject}" updated to {"Billable" if ticket.billable == "R" else "Non-Billable"}!',
        "success",
    )
    return redirect(url_for("billing_review_dashboard", id=ticket_id))


@app.route("/toggle_invoiced/<int:ticket_id>", methods=["POST"])
@login_required
def toggle_invoiced(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    # toggle invoiced status
    if ticket.billable == "R":
        ticket.billable = "I"
    else:
        ticket.billable = "R"
    db.session.commit()
    flash(
        f'Ticket "{ticket.subject}" updated to {"Invoiced" if ticket.billable == "I" else "Non-Billable"}!',
        "success",
    )
    return redirect(url_for("billing_dashboard"))


@app.route("/mark_reviewed_nonbillable/<int:ticket_id>", methods=["POST"])
@login_required
def mark_reviewed_nonbillable(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.billable == "NB":
        ticket.billable = "RNB"  # Mark as reviewed but not billable
        db.session.commit()
        flash(f'Ticket "{ticket.subject}" marked as Reviewed - Not Billable.', "info")
    return redirect(url_for("billing_review_dashboard"))


@app.route("/billing_dashboard", methods=["GET", "POST"])
@login_required
def billing_dashboard():
    # 1. Save dates to session ONLY if they aren't blank
    if request.method == "POST":
        start_val = request.form.get("start_date")
        end_val = request.form.get("end_date")

        if start_val:
            session["billing_start_date"] = start_val
        if end_val:
            session["billing_end_date"] = end_val

        return redirect(url_for("billing_dashboard"))

    # 2. Get dates from session
    start_str = session.get("billing_start_date")
    end_str = session.get("billing_end_date")

    # Force defaults if the session returned None or an empty string ""
    if not start_str:
        start_str = "2023-01-01"
    if not end_str:
        end_str = datetime.today().strftime("%Y-%m-%d")

    # 3. Parse strings into datetime objects and fix the midnight bug
    start_date = datetime.strptime(start_str, "%Y-%m-%d")
    # Add 1 day, subtract 1 second to make it 23:59:59 of the selected day
    end_date = (
        datetime.strptime(end_str, "%Y-%m-%d")
        + timedelta(days=1)
        - timedelta(seconds=1)
    )

    # Get billable tickets (B) within the selected date range
    tickets = (
        Ticket.query.filter(
            Ticket.billable == "R",
            Ticket.completed_at >= start_date,
            Ticket.completed_at <= end_date,
        )
        .order_by(
            db.case(
                (Ticket.status in ["Open", "In Progress"], 1),
                (Ticket.status == "Touched", 2),
                (Ticket.status == "On Hold", 3),
                (Ticket.status == "Closed", 4),
            ).asc(),
            db.case(
                (Ticket.priority == "Important-Urgent", 1),
                (Ticket.priority == "Important-NotUrgent", 2),
                (Ticket.priority == "NotImportant-Urgent", 3),
                (Ticket.priority == "NotImportant-NotUrgent", 4),
            ).asc(),
        )
        .all()
    )

    # Calculate total time spent per ticket from TicketNote
    ticket_totals = {}
    for ticket in tickets:
        total_time = (
            db.session.query(
                func.sum(
                    func.timestampdiff(
                        text("MINUTE"),
                        TicketNote.note_start_time,
                        TicketNote.note_finish_time,
                    )
                )
            )
            .filter_by(ticket_id=ticket.id)
            .scalar()
        )

        # Store the total time per ticket in minutes
        ticket_totals[ticket.id] = total_time / 60 if total_time else 0

    for ticket in tickets:
        if ticket.due_date and isinstance(ticket.due_date, str):
            try:
                ticket.due_date = parser.parse(ticket.due_date)
            except ValueError:
                ticket.due_date = None
    return render_template(
        "billing_dashboard.html",
        tickets=tickets,
        ticket_totals=ticket_totals,
        start_date=start_str,
        end_date=end_str,
    )


@app.route("/billing_review_dashboard", methods=["GET", "POST"])
@login_required
def billing_review_dashboard():
    # Default date range: last 7 days
    start_date = request.form.get("start_date") or datetime.now().replace(
        day=1
    ).strftime("%Y-%m-%d")
    end_date = request.form.get("end_date") or datetime.now().strftime("%Y-%m-%d")

    # Convert strings to datetime objects
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")

    # Get billable tickets (B) within the selected date range
    tickets = (
        Ticket.query.filter(
            Ticket.billable == "NB",
            Ticket.completed_at >= start_date_obj,
            Ticket.completed_at <= end_date_obj,
        )
        .order_by(Ticket.completed_at.asc())
        .all()
    )

    for ticket in tickets:
        # Fix due_date
        if ticket.due_date and isinstance(ticket.due_date, str):
            try:
                ticket.due_date = parser.parse(ticket.due_date)
            except ValueError:
                ticket.due_date = None

        # Fix completed_at
        if ticket.completed_at and isinstance(ticket.completed_at, str):
            try:
                ticket.completed_at = parser.parse(ticket.completed_at)
            except ValueError:
                ticket.completed_at = None

    # Calculate total time spent per ticket from TicketNote
    ticket_totals = {}
    for ticket in tickets:
        total_time = (
            db.session.query(
                func.sum(
                    func.timestampdiff(
                        text("MINUTE"),
                        TicketNote.note_start_time,
                        TicketNote.note_finish_time,
                    )
                )
            )
            .filter_by(ticket_id=ticket.id)
            .scalar()
        )

        # Store the total time per ticket in minutes
        ticket_totals[ticket.id] = total_time if total_time else 0

    return render_template(
        "review_for_billing.html",
        tickets=tickets,
        ticket_totals=ticket_totals,
        start_date=start_date,
        end_date=end_date,
    )


@app.route("/delete_ticket_page")
@login_required
def delete_ticket_page():
    tickets = Ticket.query.all()  # Or filter by user, etc.
    return render_template("delete_ticket.html", tickets=tickets)


@app.route("/delete_ticket/<int:id>", methods=["POST"])
@login_required
def delete_ticket(id):
    ticket = Ticket.query.get_or_404(id)
    if current_user.role != "admin":
        flash("You do not have permission to delete this ticket.", "danger")
        return redirect(url_for("view_ticket", id=ticket.id))

    db.session.delete(ticket)
    db.session.commit()
    flash("Ticket deleted successfully!", "success")
    return redirect(url_for("dashboard"))


# def send_complete_email(requestor_email, ticket):
#     subject = f"New Note Added to Ticket# {ticket.id}: {ticket.subject}"
#     eastern = timezone('US/Eastern')

#     # Link to the Google Drive image (replace with your correct link)
#     logo_url = Config.LOGO_URL

#     # HTML email body with linked logo image
#     body = f"""
#     <html>
#     <head>
#         <style>
#             body {{
#                 font-family: Arial, sans-serif;
#                 background-color: #e0e6ed;
#                 color: #2a3f54;
#                 padding: 20px;
#             }}
#             .container {{
#                 max-width: 600px;
#                 margin: 0 auto;
#                 background-color: #ffffff;
#                 padding: 20px;
#                 border-radius: 10px;
#                 box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
#             }}
#             h2 {{
#                 color: #1b3a57;
#                 text-align: center;
#             }}
#             .note-details {{
#                 background-color: #cbd5e0;
#                 padding: 10px;
#                 border-radius: 5px;
#             }}
#             .time {{
#                 font-weight: bold;
#                 color: #0077b6;
#             }}
#             .footer {{
#                 text-align: center;
#                 font-size: 0.9em;
#                 color: #2a3f54;
#                 margin-top: 20px;
#             }}
#         </style>
#     </head>
#     <body>
#         <div class="container">
#             <div style="text-align: center;">
#                 <img src="{logo_url}" alt="RVA IT Pros Logo" style="max-width: 200px; margin-bottom: 20px;">
#             </div>
#             <h2>Ticket Completed</h2>
#             <p>Hello <strong>{ticket.client.first_name}</strong>,</p>
#             <p>Your ticket has been completed:</p>
#             <div class="note-details">
#                 <p><strong>Ticket ID:</strong> {ticket.id}</p>
#                 <p><strong>Subject:</strong> {ticket.subject}</p>
#             </div>
#             <p>If you have any questions or need further assistance, feel free to contact us.</p>
#             <p>Regards,<br>
#             <strong>RVA IT Helpdesk Team</strong></p>
#             <div class="footer">
#                 <p>{Config.COMPANY_NAME} | Contact Us: {Config.COMPANY_SUPPORT_PHONE} | {Config.COMPANY_SUPPORT_EMAIL}</p>
#             </div>
#         </div>
#     </body>
#     </html>
#     """

#     msg = Message(subject, recipients=['andrew.bean@rvaitpros.com'])
#     msg.html = body

#     # Send the email without attaching the logo since it's linked via URL
#     mail.send(msg)


# Route to mark a ticket as complete (records completed_at time)
@app.route("/complete_ticket/<int:id>", methods=["POST"])
@login_required
def complete_ticket(id):
    ticket = Ticket.query.get_or_404(id)

    # Check for a resolution note
    has_resolution = any(note.is_resolution for note in ticket.notes)

    if not has_resolution:
        flash("Cannot complete ticket: no resolution note found.", "danger")
        return redirect(url_for("view_ticket", id=ticket.id))

    # Proceed with completion
    ticket.complete = True
    ticket.completed_at = datetime.now(UTC)
    ticket.status = "Closed"
    db.session.commit()

    flash("Ticket marked as complete!", "success")
    # send_complete_email(ticket.requestor_email, ticket)
    return redirect(url_for("view_ticket", id=ticket.id))


@app.route("/create_project", methods=["GET", "POST"])
@login_required
def create_project():
    form = ProjectForm()

    # Populate company choices from the Company model
    companies = Company.query.all()
    form.company_id.choices = [(company.id, company.name) for company in companies]

    if form.validate_on_submit():
        # Create the project with the status and company fields
        project = Project(
            name=form.name.data,
            description=form.description.data,
            status=form.status.data,  # Save the selected status
            company_id=form.company_id.data,  # Save the selected company
        )
        db.session.add(project)
        db.session.commit()

        # Check if a phase was provided, and create a phase if it was
        if form.phase_name.data:
            phase = Phase(
                name=form.phase_name.data, project_id=project.id, status="Open"
            )
            db.session.add(phase)
            db.session.commit()

        flash("Project created successfully!", "success")
        return redirect(url_for("projects_home"))

    return render_template("create_project.html", form=form)


# create phase of project
@app.route("/create_phase/<int:project_id>", methods=["GET", "POST"])
@login_required
def create_phase(project_id):
    project = Project.query.get_or_404(project_id)
    form = PhaseForm()
    if form.validate_on_submit():
        phase = Phase(
            name=form.name.data,
            project_id=project.id,
            status=form.status.data,  # Save the selected status
        )
        db.session.add(phase)
        db.session.commit()
        flash("Phase created successfully!", "success")
        return redirect(url_for("project_dashboard", project_id=project.id))

    return render_template("create_phase.html", form=form, project=project)


# Projects Home Page - lists all projects and links to their phases and tickets
@app.route("/projects")
@login_required
def projects_home():
    projects = Project.query.filter(Project.status != "Closed").all()
    session.pop("start_time_utc", None)
    return render_template("projects_home.html", projects=projects)


# View project dashboard - show phases and tickets under each phase
@app.route("/project_dashboard/<int:project_id>")
@login_required
def project_dashboard(project_id):
    project = Project.query.get_or_404(project_id)

    # Custom ordering for phases: Open > In Progress > Closed
    phases = (
        Phase.query.filter_by(project_id=project.id)
        .order_by(
            db.case(
                (Phase.status == "Open", 1),
                (Phase.status == "In Progress", 2),
                (Phase.status == "Closed", 3),
            ).asc()
        )
        .all()
    )

    # Query the tickets and order by their status
    tickets = (
        Ticket.query.filter_by(project_id=project.id)
        .order_by(
            db.case(
                (Ticket.status == "Open", 1),
                (Ticket.status == "In Progress", 2),
                (Ticket.status == "Closed", 3),
            ).asc()
        )
        .all()
    )

    return render_template(
        "project_dashboard.html", project=project, phases=phases, tickets=tickets
    )


@app.route("/select_project/<int:ticket_id>", methods=["GET", "POST"])
@login_required
def select_project(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    form = SelectProjectForm()

    project = Project.query.filter(~Project.status.in_(["Closed"])).all()

    # Populate the project choices
    form.project_id.choices = [(0, "Select a Project")] + [
        (p.id, p.name)
        for p in Project.query.filter(~Project.status.in_(["Closed"])).all()
    ]

    if form.validate_on_submit():
        selected_project = Project.query.get(form.project_id.data)
        ticket.project_id = selected_project.id
        db.session.commit()  # Save project_id to the ticket

        # Redirect to the phase selection page
        return redirect(
            url_for("select_phase", ticket_id=ticket_id, project_id=selected_project.id)
        )

    return render_template("select_project.html", form=form, ticket=ticket)


@app.route("/select_project_new_ticket", methods=["GET", "POST"])
@login_required
def select_project_new_ticket():
    form = SelectProjectForm()

    # Populate the project choices
    form.project_id.choices = [(0, "Select a Project")] + [
        (p.id, p.name)
        for p in Project.query.filter(~Project.status.in_(["Closed"])).all()
    ]

    if form.validate_on_submit():
        selected_project = Project.query.get(form.project_id.data)
        db.session.commit()  # Save project_id to the ticket

        # Redirect to the phase selection page
        return redirect(url_for("new_ticket", project_id=selected_project.id))

    return render_template("select_project_new_ticket.html", form=form)


@app.route("/select_phase/<int:ticket_id>/<int:project_id>", methods=["GET", "POST"])
@login_required
def select_phase(ticket_id, project_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    project = Project.query.get_or_404(project_id)
    form = SelectPhaseForm()

    # Populate the phase choices based on the selected project
    form.phase_id.choices = [(0, "Select a Phase")] + [
        (p.id, p.name) for p in project.phases
    ]

    if form.validate_on_submit():
        if form.phase_id.data != 0:  # Ensure a valid phase is selected
            ticket.phase_id = form.phase_id.data
            db.session.commit()  # Save phase_id to the ticket
            flash("Ticket successfully assigned to project and phase!", "success")
            return redirect(url_for("view_ticket", id=ticket.id))
        else:
            flash("Please select a valid phase.", "danger")

    return render_template(
        "select_phase.html", form=form, project=project, ticket=ticket
    )


@app.route("/edit_project/<int:project_id>", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)
    form = UpdateProjectForm(obj=project)

    # Query the list of companies to populate the SelectField
    companies = Company.query.all()
    form.company_id.choices = [(company.id, company.name) for company in companies]

    # Pre-select the current company in the form
    if request.method == "GET":
        form.company_id.data = project.company_id

    if form.validate_on_submit():
        project.name = form.name.data
        project.description = form.description.data
        project.status = form.status.data

        # Update the company_id with the selected company from the form
        project.company_id = form.company_id.data

        db.session.commit()
        flash("Project updated successfully!", "success")
        return redirect(url_for("project_dashboard", project_id=project.id))

    return render_template("edit_project.html", form=form, project=project)


@app.route("/edit_phase/<int:id>", methods=["GET", "POST"])
@login_required
def edit_phase(id):
    phase = Phase.query.get_or_404(id)
    form = PhaseForm(obj=phase)

    if form.validate_on_submit():
        # Update the phase details
        phase.name = form.name.data
        phase.status = form.status.data  # Update the status
        db.session.commit()
        flash("Phase updated successfully!", "success")
        return redirect(url_for("project_dashboard", project_id=phase.project_id))

    return render_template("edit_phase.html", form=form, phase=phase)


# Route to create a company
@app.route("/create_company", methods=["GET", "POST"])
@login_required  # If needed
def create_company():
    form = CompanyForm()

    if form.validate_on_submit():
        # Create a new company object
        company = Company(
            name=form.name.data,
            street_address=form.street_address.data,
            city=form.city.data,
            state=form.state.data,
            zip_code=form.zip_code.data,
            main_phone=form.main_phone.data,
            customer_type=form.customer_type.data,
        )
        db.session.add(company)
        db.session.commit()
        flash("Company created successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("create_company.html", form=form)


# Route to create a client
@app.route("/create_client", methods=["GET", "POST"])
@login_required
def create_client():
    form = ClientForm()
    form.company_id.choices = [(c.id, c.name) for c in Company.query.all()]

    if form.validate_on_submit():
        # 1. Create the Client (Contact) record
        client = Client(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            phone=form.phone.data,
            company_id=form.company_id.data,
        )
        db.session.add(client)
        db.session.flush()  # We use flush() to get the new client.id before committing

        # 2. Check if a password was provided. If yes, create their login User.
        if form.password.data:
            # We use their email as their username for simplicity
            hashed_password = generate_password_hash(
                form.password.data, method="pbkdf2:sha256"
            )
            user = User(
                username=form.email.data,
                email=form.email.data,
                password=hashed_password,
                role="client",  # Automatically assigns the 'client' role
                client_id=client.id,  # Links the login to the contact record we just made
            )
            db.session.add(user)

        db.session.commit()
        flash("Client created successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("create_client.html", form=form)


@app.route("/company_dashboard", methods=["GET"])
@login_required
def view_company():
    # Query the database to get all companies
    companies = Company.query.all()

    # Pass the companies to the template
    return render_template("company_dashboard.html", companies=companies)


@app.route("/company/<int:company_id>", methods=["GET", "POST"])
@login_required
def view_edit_company(company_id):
    company = Company.query.get_or_404(company_id)
    form = EditCompanyForm(obj=company)

    if form.validate_on_submit():
        company.name = form.name.data
        company.street_address = form.street_address.data
        company.city = form.city.data
        company.state = form.state.data
        company.zip_code = form.zip_code.data
        company.main_phone = form.main_phone.data
        company.customer_type = form.customer_type.data

        # ADD THIS: Save the QBO ID
        company.qbo_customer_id = form.qbo_customer_id.data

        db.session.commit()
        flash("Company details updated successfully!", "success")
        return redirect(url_for("view_edit_company", company_id=company.id))

    clients = Client.query.filter_by(company_id=company_id).all()
    return render_template(
        "view_edit_company.html", form=form, company=company, clients=clients
    )


@app.route("/client/<int:client_id>", methods=["GET", "POST"])
@login_required
def view_edit_client(client_id):
    if current_user.role == "client":
        return redirect(url_for("client_portal"))

    client = Client.query.get_or_404(client_id)
    form = ClientForm(obj=client)

    # Populate dynamic choices before validation to prevent WTForms errors
    if hasattr(form, "company_id"):
        form.company_id.choices = [(c.id, c.name) for c in Company.query.all()]

    # Fetch existing user account directly via Foreign Key
    user = User.query.filter_by(client_id=client.id).first()

    if request.method == "POST":
        # 1. Update basic client record details
        client.first_name = request.form.get("first_name", client.first_name)
        client.last_name = request.form.get("last_name", client.last_name)
        client.email = request.form.get("email", client.email)
        client.phone = request.form.get("phone", client.phone)

        # 2. Capture raw password input from form post
        entered_password = request.form.get("password")

        # 3. Create or update user account
        if entered_password and entered_password.strip():
            hashed_pw = generate_password_hash(
                entered_password.strip(), method="pbkdf2:sha256"
            )
            if not user:
                user = User(
                    username=client.email,
                    email=client.email,
                    password=hashed_pw,
                    role="client",
                    client_id=client.id,
                    is_company_admin=(request.form.get("is_company_admin") == "on"),
                )
                db.session.add(user)
            else:
                user.password = hashed_pw
                user.email = client.email
                user.username = client.email
                user.is_company_admin = request.form.get("is_company_admin") == "on"
        elif user:
            # Update permissions if user already has an active portal account
            user.is_company_admin = request.form.get("is_company_admin") == "on"

        db.session.commit()
        flash("Client details updated successfully!", "success")
        return redirect(url_for("view_edit_company", company_id=client.company_id))

    has_portal_access = user is not None
    return render_template(
        "view_edit_client.html",
        form=form,
        client=client,
        has_portal_access=has_portal_access,
    )

@app.route("/get_client_email", methods=["GET"])
@login_required
def get_client_email():
    client_id = request.args.get("client_id")
    client = Client.query.get_or_404(client_id)

    # Return the email in JSON format
    return jsonify(email=client.email)


@app.route("/admin_dashboard", methods=["GET", "POST"])
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("dashboard"))

    users = User.query.all()
    role_form = ChangeRoleForm()

    if request.method == "POST":
        user_id = request.form.get("user_id")
        new_role = request.form.get("role")
        
        # Fixed SQLAlchemy 2.0 syntax & converted ID to integer
        if user_id and user_id.isdigit():
            user = db.session.get(User, int(user_id))
            if user:
                user.role = new_role
                db.session.commit()
                flash(f"Role for {user.username} updated to {new_role}.", "success")
        
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_dashboard.html", users=users, role_form=role_form)


@app.route("/random_task")
@login_required
def random_task():
    tickets = (
        Ticket.query.filter(
            Ticket.due_date.between(
                func.date(func.now()),  # ✅ Fix: Gets today's date at 00:00:00
                func.date(
                    func.now() + text("INTERVAL 1 DAY")
                ),  # ✅ Fix: Gets tomorrow's date
            ),
            ~Ticket.status.in_(
                ["Touched", "Closed", "On Hold"]
            ),  # Exclude tickets with status 'Touched' or 'Closed'
        )
        .order_by(
            db.case(
                (Ticket.status == "Open", 1),
                (Ticket.status == "In Progress", 2),
                (Ticket.status == "Touched", 3),
                (Ticket.status == "Closed", 4),
                (Ticket.status == "On Hold", 5),
            ).asc(),
            db.case(
                (Ticket.priority == "Important-Urgent", 1),
                (Ticket.priority == "Important-NotUrgent", 2),
                (Ticket.priority == "NotImportant-Urgent", 3),
                (Ticket.priority == "NotImportant-NotUrgent", 4),
            ).asc(),
        )
        .all()
    )

    # Create a weighted list of tickets
    weighted_tickets = []
    for ticket in tickets:
        if ticket.priority == "Important-Urgent":
            weighted_tickets.extend([ticket] * 5)  # Add high-priority ticket 5 times
        elif ticket.priority == "Important-NotUrgent":
            weighted_tickets.extend(
                [ticket] * 3
            )  # Add medium-high-priority ticket 3 times
        elif ticket.priority == "NotImportant-Urgent":
            weighted_tickets.extend(
                [ticket] * 3
            )  # Add medium-low-priority ticket 3 times
        elif ticket.priority == "NotImportant-NotUrgent":
            weighted_tickets.append(ticket)  # Add low-priority ticket 1 time

    number_of_tickets = len(weighted_tickets)

    if number_of_tickets > 0:
        random_ticket = random.choice(
            weighted_tickets
        )  # Select a random ticket based on the weighted list
    else:
        random_ticket = None  # Handle case where there are no tickets

    return render_template("random_task.html", random_ticket=random_ticket)


@app.route("/random_number_generator", methods=["GET", "POST"])
@login_required
def random_number():
    form = RandomNumberForm()
    random_int = 0
    if form.validate_on_submit():
        random_int = random.randint(1, form.number.data)

    return render_template(
        "random_number_generator.html", form=form, random_int=random_int
    )


def execute_query(query, params=None):
    """Helper function to execute a query on MySQL."""
    conn = None
    try:
        # Connect to MySQL
        conn = pymysql.connect(
            host=Config.DB_HOST,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_NAME,
            cursorclass=pymysql.cursors.DictCursor,  # Returns results as dictionaries
            connect_timeout=30,
            autocommit=True,
        )
        cursor = conn.cursor()

        # Execute query
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        # Commit if it's an INSERT, UPDATE, or DELETE
        conn.commit()

        return {
            "success": True,
            "message": "Query executed successfully",
            "affected_rows": cursor.rowcount,
        }
    except (pymysql.OperationalError, pymysql.InterfaceError) as e:  # ✅ Fix syntax
        print(f"⚠️ Lost connection to MySQL: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.route("/update-tickets", methods=["GET", "POST"])
@login_required
def update_tickets():
    # Get today's date dynamically in the correct format
    today = datetime.now().strftime("%Y-%m-%d 00:00:00")

    # Define dynamic queries
    queries = [
        # ✅ Fix: Replace `?` with `%s`
        {
            "query": """UPDATE ticket
                        SET due_date = %s
                        WHERE due_date < %s
                        AND status NOT IN ('Closed', 'On Hold');""",
            "params": (today, today),
        },
        {
            "query": """UPDATE ticket
                        SET due_date = NULL
                        WHERE status = 'On Hold';""",
            "params": None,
        },
        {
            "query": """UPDATE ticket
                        SET status = 'In Progress'
                        WHERE status = 'Touched';""",
            "params": None,
        },
        {
            "query": """UPDATE ticket
                        SET completed_at = NULL, complete = 0
                        WHERE status <> 'Closed' AND completed_at IS NOT NULL AND complete = 1;""",
            "params": None,
        },
    ]

    print(queries)  # Debugging - Check if queries are correct

    # ✅ Fix: Ensure `None` queries pass correctly
    results = [execute_query(q["query"], q["params"] or ()) for q in queries]

    return render_template("clean_up_tickets.html", results=results)


from datetime import datetime
from io import BytesIO

import pandas as pd
from flask import send_file


@app.route("/download_project_excel/<int:project_id>")
@login_required
def download_project_excel(project_id):
    project = Project.query.get_or_404(project_id)

    rows = []

    for phase in project.phases:
        for ticket in phase.tickets:
            is_billable = ticket.billable == "R"
            billable_label = "Yes" if is_billable else "No"

            # Sum time deltas only if billable
            total_seconds = 0
            note_texts = []

            for note in ticket.notes:
                note_texts.append(note.content)
                if is_billable and note.note_start_time and note.note_finish_time:
                    delta = note.note_finish_time - note.note_start_time
                    total_seconds += delta.total_seconds()

            hours = round(total_seconds / 3600, 2) if is_billable else 0

            rows.append(
                {
                    "Project Name": project.name,
                    "Project Status": project.status,
                    "Company": project.company.name,
                    "Phase Name": phase.name,
                    "Phase Status": phase.status,
                    "Ticket Subject": ticket.subject,
                    "Ticket Status": ticket.status,
                    "Priority": ticket.priority,
                    "Billable": billable_label,
                    "Logged Hours": hours,
                    "Notes": "\n---\n".join(note_texts),
                }
            )

    df = pd.DataFrame(rows)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Project Details")

    output.seek(0)
    filename = f"{project.name.replace(' ', '_')}_details_{datetime.now().strftime('%Y%m%d')}.xlsx"

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/calendar")
@login_required
def calendar():
    return render_template("calendar.html")


@app.route("/authorize_gmail")
@login_required
def authorize_gmail():
    flow = InstalledAppFlow.from_client_secrets_file(
        Config.CLIENT_SECRET_PATH, ["https://www.googleapis.com/auth/gmail.send"]
    )
    creds = flow.run_local_server(port=0)
    with open("token_helpdesk.pickle", "wb") as token:
        pickle.dump(creds, token)
    return "Helpdesk Gmail authorized."


@app.route("/authorize_calendar")
@login_required
def authorize_calendar():
    flow = InstalledAppFlow.from_client_secrets_file(
        Config.CLIENT_SECRET_PATH, ["https://www.googleapis.com/auth/calendar.events"]
    )
    creds = flow.run_local_server(port=0)
    with open("token_personal.pickle", "wb") as token:
        pickle.dump(creds, token)
    return "Personal calendar authorized."


@app.template_filter("sanitize_html")
def sanitize_html_filter(s):
    ALLOWED_TAGS = [
        "b",
        "i",
        "u",
        "em",
        "strong",
        "a",
        "p",
        "br",
        "ul",
        "ol",
        "li",
        "pre",
        "code",
        "blockquote",
    ]
    ALLOWED_ATTRIBUTES = {
        "a": ["href", "title", "target"],
    }
    return bleach.clean(s, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)


@app.template_filter("markdown")
def markdown_filter(text):
    """Convert markdown text to HTML"""
    if text is None:
        return ""
    html = md.convert(str(text))
    return bleach.clean(html, tags=allowed_tags, attributes=allowed_attributes)


@app.template_filter("urlencode")
def urlencode_filter(s):
    if s is None:
        return ""
    return urllib.parse.quote(s)


@app.template_filter("clean_email_to_markdown")
def clean_email_to_markdown_filter(email_text):
    """Clean email text and convert to markdown format"""
    if email_text is None:
        return ""

    # Convert HTML email to markdown
    markdown_text = markdownify(email_text, heading_style="ATX")

    # Clean up common email artifacts
    lines = markdown_text.split("\n")
    cleaned_lines = []

    for line in lines:
        # Remove email headers and signatures
        if any(
            pattern in line.lower()
            for pattern in [
                "from:",
                "sent:",
                "to:",
                "subject:",
                "cc:",
                "bcc:",
                "best regards",
                "sincerely",
                "thank you",
                "thanks",
                "--",
                "---",
                "sent from my",
                "get outlook",
            ]
        ):
            continue

        # Remove empty lines at the beginning
        if not cleaned_lines and line.strip() == "":
            continue

        # Remove excessive whitespace
        line = line.strip()
        if line:
            cleaned_lines.append(line)

    # Join lines back together
    cleaned_text = "\n\n".join(cleaned_lines)

    return cleaned_text


@app.route("/mass_complete_automated_tickets", methods=["GET", "POST"])
@login_required
def mass_complete_automated_tickets():
    # Check if user is admin
    if current_user.role != "admin":
        flash("Access denied. Admin privileges required.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        # Get ticket IDs from form
        ticket_ids = request.form.getlist("ticket_ids")

        if not ticket_ids:
            flash("No tickets selected.", "warning")
            return redirect(url_for("mass_complete_automated_tickets"))

        completed_count = 0
        for ticket_id in ticket_ids:
            try:
                ticket = Ticket.query.get(int(ticket_id))
                if ticket and ticket.status != "Closed":
                    # Mark as complete without resolution check
                    ticket.status = "Closed"
                    ticket.complete = True
                    ticket.completed_at = datetime.now(UTC)
                    completed_count += 1
            except (ValueError, AttributeError):
                continue

        db.session.commit()
        flash(f"Successfully completed {completed_count} automated tickets.", "success")
        return redirect(url_for("dashboard"))

    # GET request - show form
    # Get tickets that are likely from automated scripts (based on subject patterns)
    automated_patterns = ["automate"]

    # automated_patterns = [
    #     'automated', 'script', 'cron', 'scheduled', 'backup', 'sync',
    #     'update', 'maintenance', 'monitoring', 'health check', 'status',
    #     'report', 'log', 'scan', 'cleanup', 'archive', 'notification',
    #     'alert', 'system', 'process', 'job', 'task', 'routine', 'daily',
    #     'weekly', 'monthly', 'periodic', 'automatic', 'batch', 'import',
    #     'export', 'sync', 'replication', 'mirror', 'copy', 'transfer'
    # ]

    # Get open tickets that match automated patterns
    automated_tickets = []
    all_open_tickets = Ticket.query.filter_by(status="Open").all()

    for ticket in all_open_tickets:
        subject_lower = ticket.subject.lower()
        if any(pattern in subject_lower for pattern in automated_patterns):
            automated_tickets.append(ticket)

    return render_template(
        "mass_complete_automated_tickets.html", tickets=automated_tickets
    )


import re
@app.route("/search", methods=["GET"])
@login_required
def search_tickets():
    q = (request.args.get("q") or "").strip()
    tickets = []
    total = 0

    if q:
        like = f"%{q}%"
        id_filter = []
        
        # Searching '#1' or '#2' -> Strictly filter by Project ID
        if q.startswith("#"):
            clean_proj_num = q.lstrip("#")
            if clean_proj_num.isdigit():
                id_filter = [Ticket.project_id == int(clean_proj_num)]
        # Searching plain '1', '2', '3' -> Strictly filter by Ticket ID
        elif q.isdigit():
            id_filter = [Ticket.id == int(q)]

        if id_filter:
            tickets = Ticket.query.filter(*id_filter).order_by(Ticket.created_at.desc()).all()
        else:
            tickets = (
                Ticket.query.filter(
                    or_(
                        Ticket.subject.ilike(like),
                        Ticket.description.ilike(like),
                        Ticket.project.has(Project.name.ilike(like)),
                        Ticket.client.has(
                            or_(
                                Client.first_name.ilike(like),
                                Client.last_name.ilike(like),
                                Client.email.ilike(like),
                                Client.company.has(Company.name.ilike(like)),
                            )
                        ),
                        Ticket.notes.any(TicketNote.content.ilike(like)),
                    )
                )
                .order_by(Ticket.created_at.desc())
                .limit(200)
                .all()
            )
        total = len(tickets)

    return render_template("search_tickets.html", q=q, tickets=tickets, total=total)

@app.route("/portal")
@login_required
def client_portal():
    if current_user.role != "client":
        return redirect(url_for("dashboard"))

    # Check if user is a Company Leader/Admin
    is_company_admin = current_user.is_company_admin

    # 1. Ticket Access Control
    if is_company_admin and current_user.client and current_user.client.company_id:
        # Company Admins see all tickets for their company
        client_tickets = (
            Ticket.query.join(Client)
            .filter(Client.company_id == current_user.client.company_id)
            .order_by(Ticket.updated_at.desc())
            .all()
        )
    else:
        # Standard Clients see tickets matched by user_id OR client_id OR requestor_email
        client_tickets = (
            Ticket.query.filter(
                or_(
                    Ticket.user_id == current_user.id,
                    and_(
                        Ticket.client_id == current_user.client_id,
                        Ticket.client_id.isnot(None),
                    ),
                    and_(
                        Ticket.requestor_email == current_user.email,
                        Ticket.requestor_email.isnot(None),
                    ),
                )
            )
            .order_by(Ticket.updated_at.desc())
            .all()
        )

    # 2. Project Visibility Control
    company_projects = []
    if current_user.client and current_user.client.company_id:
        if is_company_admin:
            company_projects = Project.query.filter_by(
                company_id=current_user.client.company_id
            ).all()
        else:
            company_projects = current_user.shared_projects.filter(
                Project.status != "Closed"
            ).all()

    return render_template(
        "client_portal.html",
        tickets=client_tickets,
        projects=company_projects,
        is_company_admin=is_company_admin,
    )

@app.route("/portal/ticket/<int:id>", methods=["GET", "POST"])
@login_required
def portal_view_ticket(id):
    if current_user.role != "client":
        return redirect(url_for("dashboard"))

    ticket = Ticket.query.get_or_404(id)

    # Permission Check
    user_owns_ticket = ticket.user_id == current_user.id
    client_owns_ticket = (current_user.client_id and ticket.client_id == current_user.client_id)
    
    is_company_admin = getattr(current_user, "is_company_admin", False)
    same_company = (
        current_user.client 
        and ticket.client 
        and current_user.client.company_id == ticket.client.company_id
    )
    is_authorized_admin = is_company_admin and same_company

    if not (user_owns_ticket or client_owns_ticket or is_authorized_admin):
        flash("You do not have permission to view this ticket.", "danger")
        return redirect(url_for("client_portal"))

    note_form = AddNoteForm()

    if request.method == "POST" and note_form.validate_on_submit():
        note = TicketNote(
            content=note_form.content.data,
            ticket_id=ticket.id,
            user_id=current_user.id,
            note_start_time=datetime.now(UTC),
            note_finish_time=datetime.now(UTC),
        )
        db.session.add(note)
        db.session.commit()
        flash("Comment added successfully!", "success")
        return redirect(url_for("portal_view_ticket", id=ticket.id))

    # Smart Back Navigation URL
    if ticket.project_id:
        back_url = url_for("portal_view_project", project_id=ticket.project_id)
    else:
        back_url = url_for("client_portal")

    return render_template(
        "portal_view_ticket.html", 
        ticket=ticket, 
        note_form=note_form, 
        back_url=back_url
    )

    
@app.route("/portal/ticket/new", methods=["GET", "POST"])
@login_required
def portal_new_ticket():
    if current_user.role != "client":
        return redirect(url_for("dashboard"))

    form = TicketForm()
    
    # Pre-fill client options
    form.client_id.choices = [(current_user.client_id, f"{current_user.client.first_name} {current_user.client.last_name}")] if current_user.client else []
    form.assigned_tech_id.choices = [(0, "Unassigned")]
    form.phase_id.choices = [(0, "No Project Assigned")]

    if request.method == "POST":
        ticket = Ticket(
            subject=request.form.get("subject"),
            description=request.form.get("description"),
            priority=request.form.get("priority", "Medium"),
            status="Open",
            user_id=current_user.id,
            client_id=current_user.client_id,
            requestor_email=current_user.email,
            estimated_hours=0.5
        )
        db.session.add(ticket)
        db.session.commit()
        
        # Send Email Notification to newticket@rvaitpros.com
        notification_email = app.config.get("NEWTICKET_EMAIL") or "newticket@rvaitpros.com"
        client_name = f"{current_user.client.first_name} {current_user.client.last_name}" if current_user.client else current_user.username
        company_name = current_user.client.company.name if (current_user.client and current_user.client.company) else "N/A"
        
        email_subject = f"[Portal Ticket #{ticket.id}] {ticket.subject}"
        email_body = f"""
        <html>
        <body>
            <h3>New Portal Support Ticket Created</h3>
            <p><strong>Ticket ID:</strong> #{ticket.id}</p>
            <p><strong>Created By:</strong> {client_name} ({company_name})</p>
            <p><strong>Requestor Email:</strong> {ticket.requestor_email}</p>
            <p><strong>Priority:</strong> {ticket.priority}</p>
            <p><strong>Subject:</strong> {ticket.subject}</p>
            <p><strong>Description:</strong></p>
            <div style="background-color: #f8f9fa; padding: 12px; border-left: 4px solid #0d6efd;">
                {md.convert(ticket.description or '')}
            </div>
        </body>
        </html>
        """
        try:
            send_gmail_message(to=notification_email, subject=email_subject, html_body=email_body)
        except Exception as e:
            print(f"Error sending portal ticket notification email: {e}")

        flash("Support ticket created successfully!", "success")
        return redirect(url_for("client_portal"))

    return render_template("portal_new_ticket.html", form=form)

@app.route("/portal/project/<int:project_id>")
@login_required
def portal_view_project(project_id):
    if current_user.role != "client":
        return redirect(url_for("dashboard"))

    project = Project.query.get_or_404(project_id)

    # Permission Check
    is_admin = getattr(current_user, "is_company_admin", False)
    is_shared = current_user in project.shared_users
    same_company = (
        current_user.client 
        and project.company_id == current_user.client.company_id
    )

    if not (same_company and (is_admin or is_shared)):
        flash("You do not have permission to view this project.", "danger")
        return redirect(url_for("client_portal"))

    # Pass all phases to Company Admins, but filter for standard shared clients
    if is_admin:
        visible_phases = project.phases
    else:
        visible_phases = [p for p in project.phases if current_user in p.shared_users]

    return render_template(
        "portal_view_project.html", 
        project=project, 
        phases=visible_phases
    )


@app.route("/project/<int:project_id>/share", methods=["GET", "POST"])
@login_required
def share_project(project_id):
    if current_user.role == "client":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    project = Project.query.get_or_404(project_id)
    company_users = (
        User.query.join(Client)
        .filter(Client.company_id == project.company_id)
        .all()
    )

    if request.method == "POST":
        # 1. Update Project-Level Sharing
        selected_user_ids = [int(uid) for uid in request.form.getlist("shared_user_ids")]
        project.shared_users = [u for u in company_users if u.id in selected_user_ids]

        # 2. Update Phase-Level Sharing
        for phase in project.phases:
            phase_user_ids = [int(uid) for uid in request.form.getlist(f"phase_{phase.id}_users")]
            phase.shared_users = [u for u in company_users if u.id in phase_user_ids]

        db.session.commit()
        flash(f"Permissions for {project.name} updated successfully!", "success")
        
        # STAY ON PERMISSIONS PAGE: Redirect back to share_project instead of project_dashboard
        return redirect(url_for("share_project", project_id=project.id))

    return render_template("share_project.html", project=project, company_users=company_users)


def get_qbo_auth_client():
    return AuthClient(
        client_id=app.config["QBO_CLIENT_ID"],
        client_secret=app.config["QBO_CLIENT_SECRET"],
        environment=app.config["QBO_ENVIRONMENT"],  # 'sandbox' or 'production'
        redirect_uri=app.config["QBO_REDIRECT_URI"],
    )


@app.route("/authorize_qbo")
@login_required
def authorize_qbo():
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    auth_client = get_qbo_auth_client()
    url = auth_client.get_authorization_url([Scopes.ACCOUNTING])
    return redirect(url)


@app.route("/qbo_callback")
@login_required
def qbo_callback():
    auth_client = get_qbo_auth_client()
    auth_code = request.args.get("code")
    realm_id = request.args.get("realmId")

    if auth_code:
        auth_client.get_bearer_token(auth_code, realm_id=realm_id)
        # Store in session for MVP workflow
        session["qbo_access_token"] = auth_client.access_token
        session["qbo_refresh_token"] = auth_client.refresh_token
        session["qbo_realm_id"] = realm_id
        flash("QuickBooks authorized successfully!", "success")

    return redirect(url_for("admin_dashboard"))


@app.route("/push_invoice_to_qbo/<int:ticket_id>", methods=["POST"])
@login_required
def push_invoice_to_qbo(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    company = ticket.client.company

    # Check session first (Production), fallback to .env (Local Dev)
    refresh_token = (
        session.get("qbo_refresh_token") or app.config["QBO_DEV_REFRESH_TOKEN"]
    )
    realm_id = session.get("qbo_realm_id") or app.config["QBO_DEV_REALM_ID"]

    if not refresh_token or not realm_id:
        flash("Please authorize QuickBooks first from the Admin Dashboard.", "warning")
        return redirect(url_for("billing_dashboard"))

    if not company.qbo_customer_id:
        flash(
            f"Please edit {company.name} and add their QuickBooks Customer ID first.",
            "danger",
        )
        return redirect(url_for("billing_dashboard"))

    # ... The rest of your exact same hours-calculation code ...
    total_time_minutes = (
        db.session.query(
            func.sum(
                func.timestampdiff(
                    text("MINUTE"),
                    TicketNote.note_start_time,
                    TicketNote.note_finish_time,
                )
            )
        )
        .filter_by(ticket_id=ticket.id)
        .scalar()
        or 0
    )

    total_hours = round(float(total_time_minutes) / 60.0, 2)

    if total_hours == 0:
        flash(
            f"Ticket #{ticket.id} has 0 billable hours. Invoice not created.", "warning"
        )
        return redirect(url_for("billing_dashboard"))

    try:
        # Initialize client with the tokens we grabbed above
        client = QuickBooks(
            auth_client=get_qbo_auth_client(),
            refresh_token=refresh_token,
            company_id=realm_id,
        )

        # Build the QBO Invoice
        invoice = Invoice()
        invoice.CustomerRef = Ref()
        invoice.CustomerRef.value = company.qbo_customer_id

        line = SalesItemLine()
        line.LineNum = 1
        line.Description = f"IT Services: {ticket.subject} (Ticket #{ticket.id})"
        line.Amount = total_hours * 195.00  # <--- Adjust to your rate

        line.SalesItemLineDetail = SalesItemLineDetail()
        line.SalesItemLineDetail.Qty = total_hours
        line.SalesItemLineDetail.UnitPrice = 195.00  # <--- Adjust to your rate

        # ItemRef '1' usually maps to the default "Services" item in QBO.
        # You may need to change this ID to match your specific "Hourly Labor" item.
        line.SalesItemLineDetail.ItemRef = Ref()
        line.SalesItemLineDetail.ItemRef.value = "1"

        invoice.Line.append(line)
        invoice.save(qb=client)

        # Mark as invoiced locally!
        ticket.billable = "I"
        db.session.commit()
        flash(f"Draft Invoice created in QBO for Ticket #{ticket.id}!", "success")

    except Exception as e:
        flash(f"QBO API Error: {str(e)}", "danger")

    return redirect(url_for("billing_dashboard"))


@app.route("/batch_push_qbo", methods=["POST"])
@login_required
def batch_push_qbo():
    # 1. Get the list of selected ticket IDs from the checkboxes
    ticket_ids = request.form.getlist("ticket_ids")

    if not ticket_ids:
        flash("No tickets selected for batch push.", "warning")
        return redirect(url_for("billing_dashboard"))

    if "qbo_access_token" not in session and not app.config.get(
        "QBO_DEV_REFRESH_TOKEN"
    ):
        flash("Please authorize QuickBooks first.", "warning")
        return redirect(url_for("billing_dashboard"))

    # Retrieve QBO credentials
    refresh_token = session.get("qbo_refresh_token") or app.config.get(
        "QBO_DEV_REFRESH_TOKEN"
    )
    realm_id = session.get("qbo_realm_id") or app.config.get("QBO_DEV_REALM_ID")

    try:
        client = QuickBooks(
            auth_client=get_qbo_auth_client(),
            refresh_token=refresh_token,
            company_id=realm_id,
        )
    except Exception as e:
        flash(f"QBO Authentication Error: {str(e)}", "danger")
        return redirect(url_for("billing_dashboard"))

    # 2. Query the selected tickets and group them by company
    selected_tickets = Ticket.query.filter(Ticket.id.in_(ticket_ids)).all()

    # Dictionary to hold tickets grouped by company: { company_id: [ticket1, ticket2] }
    company_batches = {}
    for t in selected_tickets:
        comp_id = t.client.company_id
        if comp_id not in company_batches:
            company_batches[comp_id] = []
        company_batches[comp_id].append(t)

    invoices_created = 0

    # 3. Build ONE invoice per company
    for comp_id, tickets in company_batches.items():
        company = Company.query.get(comp_id)

        if not company.qbo_customer_id:
            flash(f"Skipped {company.name} - Missing QBO Customer ID.", "danger")
            continue

        invoice = Invoice()
        invoice.CustomerRef = Ref()
        invoice.CustomerRef.value = company.qbo_customer_id

        has_billable_lines = False

        # Add a line item for each ticket in this batch
        line_num = 1
        for ticket in tickets:
            # Calculate hours
            total_time_minutes = (
                db.session.query(
                    func.sum(
                        func.timestampdiff(
                            text("MINUTE"),
                            TicketNote.note_start_time,
                            TicketNote.note_finish_time,
                        )
                    )
                )
                .filter_by(ticket_id=ticket.id)
                .scalar()
                or 0
            )

            total_hours = round(float(total_time_minutes) / 60.0, 2)

            if total_hours > 0:
                line = SalesItemLine()
                line.LineNum = line_num
                line.Description = f"Ticket #{ticket.id}: {ticket.subject}"
                line.Amount = total_hours * 195.00  # Adjust rate as needed

                line.SalesItemLineDetail = SalesItemLineDetail()
                line.SalesItemLineDetail.Qty = total_hours
                line.SalesItemLineDetail.UnitPrice = 195.00
                line.SalesItemLineDetail.ItemRef = Ref()
                line.SalesItemLineDetail.ItemRef.value = "1"  # Your QBO Item ID

                invoice.Line.append(line)
                has_billable_lines = True
                line_num += 1

        # Only save the invoice if there is actual billable time
        if has_billable_lines:
            try:
                invoice.save(qb=client)

                # 4. Mark tickets as invoiced and save the QBO Invoice ID locally
                for ticket in tickets:
                    ticket.billable = "I"
                    ticket.qbo_invoice_id = (
                        invoice.Id
                    )  # Save the ID we added in models.py

                invoices_created += 1
            except Exception as e:
                flash(f"Error creating invoice for {company.name}: {str(e)}", "danger")

    db.session.commit()

    if invoices_created > 0:
        flash(
            f"Successfully pushed {invoices_created} batch invoice(s) to QuickBooks!",
            "success",
        )

    return redirect(url_for("billing_dashboard"))


@app.route("/invoice_history")
@login_required
def invoice_history():
    # 1. Get all tickets that have been marked invoiced AND have a QBO ID attached
    invoiced_tickets = (
        Ticket.query.filter(Ticket.billable == "I", Ticket.qbo_invoice_id.isnot(None))
        .order_by(Ticket.completed_at.desc())
        .all()
    )

    # 2. Group them by QBO Invoice ID so we can display them nicely
    invoices = {}
    for ticket in invoiced_tickets:
        qbo_id = ticket.qbo_invoice_id

        # If this is the first time seeing this Invoice ID, create a fresh dictionary for it
        if qbo_id not in invoices:
            invoices[qbo_id] = {
                "company_name": ticket.client.company.name
                if ticket.client
                else "Unknown Company",
                "total_hours": 0,
                "tickets": [],
            }

        # Calculate the hours for this specific ticket
        total_time_minutes = (
            db.session.query(
                func.sum(
                    func.timestampdiff(
                        text("MINUTE"),
                        TicketNote.note_start_time,
                        TicketNote.note_finish_time,
                    )
                )
            )
            .filter_by(ticket_id=ticket.id)
            .scalar()
            or 0
        )

        hours = round(float(total_time_minutes) / 60.0, 2)

        # Add the ticket details to the invoice group
        invoices[qbo_id]["tickets"].append(
            {
                "id": ticket.id,
                "subject": ticket.subject,
                "hours": hours,
                "completed_at": ticket.completed_at.strftime("%m/%d/%Y")
                if ticket.completed_at
                else "N/A",
            }
        )

        # Add the ticket's hours to the invoice's total hours
        invoices[qbo_id]["total_hours"] += hours

    # 3. Round the final totals to prevent any floating-point weirdness
    for qbo_id in invoices:
        invoices[qbo_id]["total_hours"] = round(invoices[qbo_id]["total_hours"], 2)

    return render_template("invoice_history.html", invoices=invoices)


@app.route("/inventory", methods=["GET", "POST"])
@login_required  # Assuming you use Flask-Login
def inventory_dashboard():
    inventory_items = BulkInventory.query.all()
    deployed_assets = DeployedAsset.query.all()

    # Setup the forms
    inv_form = AddBulkInventoryForm()
    asset_form = AddDeployedAssetForm()

    # Populate the company dropdown for the asset form dynamically
    asset_form.company_id.choices = [
        (c.id, c.name) for c in Company.query.order_by("name").all()
    ]

    if inv_form.validate_on_submit() and "Add to Inventory" in request.form.values():
        new_item = BulkInventory(
            item_name=inv_form.item_name.data,
            sku=inv_form.sku.data,
            qty_in_stock=inv_form.qty_in_stock.data,
            landed_unit_cost=inv_form.landed_unit_cost.data,
            retail_unit_price=inv_form.retail_unit_price.data,
            low_stock_threshold=inv_form.low_stock_threshold.data,
        )
        db.session.add(new_item)
        db.session.commit()
        flash("Consumable inventory added successfully!", "success")
        return redirect(url_for("inventory_dashboard"))

    if asset_form.validate_on_submit() and "Log Asset" in request.form.values():
        new_asset = DeployedAsset(
            company_id=asset_form.company_id.data,
            model_name=asset_form.model_name.data,
            serial_number=asset_form.serial_number.data,
            purchase_cost=asset_form.purchase_cost.data,
            status=asset_form.status.data,
        )
        db.session.add(new_asset)
        db.session.commit()
        flash("Asset logged successfully!", "success")
        return redirect(url_for("inventory_dashboard"))

    return render_template(
        "inventory_dashboard.html",
        inventory_items=inventory_items,
        deployed_assets=deployed_assets,
        inv_form=inv_form,
        asset_form=asset_form,
    )


@app.route("/batch_assign_tech", methods=["POST"])
@login_required
def batch_assign_tech():
    ticket_ids = request.form.getlist("ticket_ids")
    tech_id = request.form.get("assigned_tech_id", type=int)

    if not ticket_ids:
        flash("No tickets selected for assignment.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    new_tech_id = tech_id if tech_id != 0 else None
    tickets = Ticket.query.filter(Ticket.id.in_(ticket_ids)).all()

    assigned_count = 0
    for ticket in tickets:
        old_tech_id = ticket.assigned_tech_id
        ticket.assigned_tech_id = new_tech_id
        assigned_count += 1

        # Send notification email if tech changed
        if new_tech_id and new_tech_id != old_tech_id:
            send_assignment_email(ticket)

    db.session.commit()
    flash(f"Successfully updated assignment for {assigned_count} ticket(s).", "success")
    return redirect(request.referrer or url_for("dashboard"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
#EASTER EGG
#New EASTER EGG