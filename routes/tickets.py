from datetime import datetime
from io import BytesIO
import markdown
import pandas as pd
from dateutil import parser
from pytz import UTC, timezone
from sqlalchemy import or_
from sqlalchemy.sql import text
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, send_file
from flask_login import login_required, current_user

from config import Config
from extensions import db
from models import Ticket, TicketNote, User, Client, Company, Project
from forms import TicketForm, AddNoteForm, UpdateTicketForm, EditNoteForm
from gmail_send import send_gmail_message
from google_calendar import create_event

tickets_bp = Blueprint("tickets", __name__)

md = markdown.Markdown(
    extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"]
)


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


def send_note_email(requestor_email, ticket, note):
    """Helper function to send an email when a note is added."""
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
    logo_url = Config.LOGO_URL

    if ticket.client and getattr(ticket.client, "first_name", None):
        client_name = ticket.client.first_name
    elif getattr(ticket, "requestor_name", None):
        client_name = ticket.requestor_name.split()[0]
    elif requestor_email:
        client_name = requestor_email.split("@")[0].replace(".", " ").title()
    else:
        client_name = "there"

    body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #e0e6ed; color: #2a3f54; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1); }}
            h2 {{ color: #1b3a57; text-align: center; }}
            .note-details {{ background-color: #cbd5e0; padding: 10px; border-radius: 5px; }}
            .time {{ font-weight: bold; color: #0077b6; }}
            .footer {{ text-align: center; font-size: 0.9em; color: #2a3f54; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="text-align: center;">
                <img src="{logo_url}" alt="RVA IT Pros Logo" style="max-width: 200px; margin-bottom: 20px;">
            </div>
            <h2>New Note Added to Your Ticket</h2>
            <p>Hello <strong>{client_name}</strong>,</p>
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
    send_gmail_message(to=requestor_email, subject=subject, html_body=body)


@tickets_bp.route("/")
@tickets_bp.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "client":
        return redirect(url_for("portal.client_portal"))

    tickets = (
        Ticket.query.filter(~Ticket.status.in_(["Closed"]))
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

    for ticket in tickets:
        if ticket.created_at:
            try:
                if isinstance(ticket.created_at, str):
                    if ticket.created_at not in [None, "0000-00-00 00:00:00"]:
                        ticket.created_at = datetime.strptime(
                            ticket.created_at, "%Y-%m-%d %H:%M:%S"
                        )
                    else:
                        ticket.created_at = None

                if ticket.created_at:
                    ticket.created_at = ticket.created_at.replace(
                        tzinfo=UTC
                    ).astimezone(eastern)
                    age = current_time - ticket.created_at.astimezone(UTC)
                    days = age.days
                    hours = age.seconds // 3600
                    ticket.age = f"{days}d:{hours}h"
                else:
                    ticket.age = "N/A"
            except ValueError:
                ticket.created_at = None
                ticket.age = "N/A"

    session.pop("start_time_utc", None)
    technicians = User.query.filter(User.role.in_(["admin", "technician"])).all()
    return render_template("dashboard.html", tickets=tickets, technicians=technicians)


@tickets_bp.route("/download_tickets_excel")
@login_required
def download_tickets_excel():
    tickets = (
        Ticket.query.filter(Ticket.status != "Closed")
        .order_by(
            db.case(
                (Ticket.status.in_(["Open", "In Progress"]), 1),
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

    df = pd.DataFrame(ticket_data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Tickets")

    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="tickets.xlsx",
    )


@tickets_bp.route("/new_ticket", methods=["GET", "POST"])
@login_required
def new_ticket():
    project_id = request.args.get("project_id", type=int)
    phase_id = request.args.get("phase_id", type=int)

    form = TicketForm()

    companies = Company.query.order_by(Company.name.asc()).all()
    form.company_id.choices = [(0, "-- Select company --")] + [(c.id, c.name) for c in companies]

    clients = Client.query.all()
    form.client_id.choices = [
        (client.id, f"{client.first_name} {client.last_name} ({client.company.name})")
        for client in clients
    ]

    users = User.query.filter(User.role.in_(["admin", "technician"])).all()
    if not users:
        users = User.query.all()
    form.assigned_tech_id.choices = [(0, "Unassigned")] + [
        (u.id, f"{u.username} ({u.email})") for u in users
    ]

    if project_id:
        project = Project.query.get_or_404(project_id)
        phases = [(p.id, p.name) for p in project.phases]
        form.phase_id.choices = phases if phases else [(0, "No Phases Available")]
        if phase_id:
            form.phase_id.data = phase_id
    else:
        form.phase_id.choices = [(0, "No Project Assigned")]

    if form.validate_on_submit():
        assigned_tech_id = (
            form.assigned_tech_id.data
            if form.assigned_tech_id.data and form.assigned_tech_id.data != 0
            else None
        )
        selected_company_id = form.company_id.data if form.company_id.data != 0 else None
        selected_client_id = form.client_id.data if form.client_id.data != 0 else None

        if selected_company_id is None and selected_client_id:
            client_obj = Client.query.get(selected_client_id)
            if client_obj:
                selected_company_id = client_obj.company_id

        ticket = Ticket(
            subject=form.subject.data,
            description=form.description.data,
            priority=form.priority.data,
            status=form.status.data,
            user_id=current_user.id,
            assigned_tech_id=assigned_tech_id,
            phase_id=form.phase_id.data if form.phase_id.data != 0 else None,
            project_id=project_id,
            company_id=selected_company_id,
            client_id=selected_client_id,
            requestor_email=form.requestor_email.data,
            cc_emails=form.cc_emails.data,
            due_date=form.due_date.data,
            estimated_hours=form.estimated_hours.data if form.estimated_hours.data is not None else 0.5,
        )

        db.session.add(ticket)
        db.session.commit()
        if ticket.assigned_tech_id:
            send_assignment_email(ticket)

        flash("Ticket created successfully!", "success")
        if phase_id:
            return redirect(url_for("projects.project_dashboard", project_id=project_id))
        return redirect(url_for("tickets.dashboard"))

    return render_template(
        "new_ticket.html", form=form, project_id=project_id, phase_id=phase_id
    )


@tickets_bp.route("/view_ticket/<int:id>", methods=["GET", "POST"])
@login_required
def view_ticket(id):
    ticket = Ticket.query.get_or_404(id)
    note_form = AddNoteForm()
    eastern = timezone("US/Eastern")

    all_tickets = (
        Ticket.query.filter(Ticket.id != id, ~Ticket.status.in_(["Closed", "Completed"]))
        .order_by(Ticket.id.desc())
        .all()
    )

    project = ticket.project
    company = project.company if project else None

    if "start_time_utc" not in session:
        session["start_time_utc"] = datetime.now(UTC).isoformat()

    start_time_utc = datetime.fromisoformat(session["start_time_utc"])

    if request.method == "POST":
        if (
            request.form.get("form_name") == "note_form"
            and note_form.validate_on_submit()
        ):
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
                note_finish_time = datetime.now(UTC)

            note = TicketNote(
                content=note_form.content.data,
                ticket_id=ticket.id,
                user_id=current_user.id,
                note_start_time=note_start_time,
                note_finish_time=note_finish_time,
            )
            db.session.add(note)
            db.session.commit()

            create_event(
                summary=ticket.subject,
                start_time=note_start_time.isoformat(),
                end_time=note_finish_time.isoformat(),
                description=note_form.content.data,
                location="",
            )

            session.pop("start_time_utc", None)

            if request.form.get("send_email") == "true" and ticket.requestor_email:
                send_note_email(ticket.requestor_email, ticket, note)

            flash("Note added successfully!", "success")
            return redirect(url_for("tickets.view_ticket", id=ticket.id))

    if ticket.completed_at:
        ticket.completed_at = ticket.completed_at.replace(tzinfo=UTC).astimezone(eastern)

    for note in ticket.notes:
        if note.created_at:
            note.created_at = note.created_at.replace(tzinfo=UTC).astimezone(eastern)
        if note.note_start_time:
            note.note_start_time = note.note_start_time.replace(tzinfo=UTC).astimezone(eastern)
        if note.note_finish_time:
            note.note_finish_time = note.note_finish_time.replace(tzinfo=UTC).astimezone(eastern)

    return render_template(
        "view_ticket.html",
        ticket=ticket,
        note_form=note_form,
        project=project,
        company=company,
        all_tickets=all_tickets,
    )


@tickets_bp.route("/edit_ticket/<int:id>", methods=["GET", "POST"])
@login_required
def edit_ticket(id):
    ticket = Ticket.query.get_or_404(id)

    if ticket.due_date and isinstance(ticket.due_date, str):
        try:
            ticket.due_date = parser.parse(ticket.due_date)
        except ValueError:
            ticket.due_date = None

    update_form = UpdateTicketForm()

    companies = Company.query.order_by(Company.name.asc()).all()
    update_form.company_id.choices = [(0, "-- Select Company --")] + [(c.id, c.name) for c in companies]

    clients = Client.query.join(Company).order_by(Company.name.asc(), Client.first_name.asc()).all()
    update_form.client_id.choices = [(0, "-- Select Requestor Individual (Optional) --")] + [
        (client.id, f"{client.first_name} {client.last_name} ({client.company.name})")
        for client in clients
    ]

    users = User.query.filter(User.role.in_(["admin", "technician"])).all()
    if not users:
        users = User.query.all()
    update_form.assigned_tech_id.choices = [(0, "Unassigned")] + [
        (u.id, f"{u.username} ({u.email})") for u in users
    ]

    if ticket.project_id:
        project = Project.query.get(ticket.project_id)
        if project and project.phases:
            update_form.phase_id.choices = [(0, "No Phase Assigned")] + [(p.id, p.name) for p in project.phases]
        else:
            update_form.phase_id.choices = [(0, "No Phases Available")]
    else:
        update_form.phase_id.choices = [(0, "No Project Assigned")]

    if request.method == "GET":
        update_form.subject.data = ticket.subject
        update_form.description.data = ticket.description
        update_form.status.data = ticket.status
        update_form.priority.data = ticket.priority
        update_form.company_id.data = ticket.company_id or 0
        update_form.client_id.data = ticket.client_id or 0
        update_form.requestor_email.data = ticket.requestor_email
        update_form.cc_emails.data = ticket.cc_emails
        update_form.phase_id.data = ticket.phase_id or 0
        update_form.due_date.data = ticket.due_date
        update_form.estimated_hours.data = ticket.estimated_hours
        update_form.assigned_tech_id.data = ticket.assigned_tech_id or 0

    if request.method == "POST":
        if update_form.validate_on_submit():
            old_tech_id = ticket.assigned_tech_id

            selected_company_id = update_form.company_id.data if update_form.company_id.data and update_form.company_id.data != 0 else None
            selected_client_id = update_form.client_id.data if update_form.client_id.data and update_form.client_id.data != 0 else None

            if selected_company_id is None and selected_client_id:
                client_obj = Client.query.get(selected_client_id)
                if client_obj:
                    selected_company_id = client_obj.company_id

            ticket.subject = update_form.subject.data
            ticket.description = update_form.description.data
            ticket.status = update_form.status.data
            ticket.priority = update_form.priority.data
            ticket.company_id = selected_company_id
            ticket.client_id = selected_client_id
            ticket.requestor_email = update_form.requestor_email.data
            ticket.cc_emails = update_form.cc_emails.data
            ticket.phase_id = update_form.phase_id.data if update_form.phase_id.data and update_form.phase_id.data != 0 else None
            ticket.estimated_hours = update_form.estimated_hours.data
            ticket.due_date = update_form.due_date.data

            new_tech_id = update_form.assigned_tech_id.data if update_form.assigned_tech_id.data and update_form.assigned_tech_id.data != 0 else None
            ticket.assigned_tech_id = new_tech_id

            db.session.commit()

            if new_tech_id and new_tech_id != old_tech_id:
                send_assignment_email(ticket)

            flash("Ticket updated successfully!", "success")
            return redirect(url_for("tickets.view_ticket", id=ticket.id))
        else:
            for field, errors in update_form.errors.items():
                for error in errors:
                    flash(f"Error in {getattr(update_form, field).label.text}: {error}", "danger")

    return render_template("edit_ticket.html", ticket=ticket, update_form=update_form)


@tickets_bp.route("/edit_note/<int:note_id>", methods=["GET", "POST"])
@login_required
def edit_note(note_id):
    note = TicketNote.query.get_or_404(note_id)
    form = EditNoteForm(obj=note)
    eastern = timezone("US/Eastern")
    utc = timezone("UTC")

    if form.validate_on_submit():
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
        return redirect(url_for("tickets.view_ticket", id=note.ticket_id))

    if request.method == "GET":
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


@tickets_bp.route("/delete_note/<int:note_id>", methods=["POST"])
@login_required
def delete_note(note_id):
    note = TicketNote.query.get_or_404(note_id)
    db.session.delete(note)
    db.session.commit()
    flash("Ticket Note deleted successfully!", "success")
    return redirect(url_for("tickets.view_ticket", id=note.ticket_id))


@tickets_bp.route("/merge_tickets", methods=["POST"])
@login_required
def merge_tickets():
    source_id = request.form.get("source_ticket_id", type=int)
    target_id = request.form.get("target_ticket_id", type=int)

    if not source_id or not target_id:
        flash("Both Source and Target tickets must be selected.", "danger")
        return redirect(request.referrer or url_for("tickets.dashboard"))

    if source_id == target_id:
        flash("Cannot merge a ticket into itself.", "warning")
        return redirect(request.referrer or url_for("tickets.dashboard"))

    source_ticket = Ticket.query.get_or_404(source_id)
    target_ticket = Ticket.query.get_or_404(target_id)
    now_utc = datetime.now(UTC)

    source_notes = TicketNote.query.filter_by(ticket_id=source_ticket.id).all()
    for note in source_notes:
        note.ticket_id = target_ticket.id
        if note.is_resolution:
            note.content = f"**[Former Resolution from Ticket #{source_ticket.id}]**\n{note.content}"
            note.is_resolution = False

    target_summary_content = (
        f"**[Ticket Consolidated]** Merged Ticket #{source_ticket.id}\n"
        f"**Original Subject:** {source_ticket.subject}\n"
        f"**Original Requestor:** {source_ticket.requestor_email or 'N/A'}\n"
        f"**Original Description:**\n{source_ticket.description or 'No description provided.'}"
    )
    target_note = TicketNote(
        content=target_summary_content,
        ticket_id=target_ticket.id,
        user_id=current_user.id,
        note_start_time=now_utc,
        note_finish_time=now_utc,
        is_resolution=False,
    )
    db.session.add(target_note)

    source_closure_content = (
        f"**[Ticket Closed - Consolidated]** This ticket has been closed and consolidated into **Ticket #{target_ticket.id}** "
        f"('{target_ticket.subject}'). All previous notes have been transferred to Ticket #{target_ticket.id}."
    )
    source_note = TicketNote(
        content=source_closure_content,
        ticket_id=source_ticket.id,
        user_id=current_user.id,
        note_start_time=now_utc,
        note_finish_time=now_utc,
        is_resolution=True,
    )
    db.session.add(source_note)

    source_ticket.status = "Closed"
    source_ticket.complete = True
    source_ticket.completed_at = now_utc

    db.session.commit()
    flash(
        f"Successfully consolidated Ticket #{source_id} into Ticket #{target_id}. Ticket #{source_id} is now closed.",
        "success",
    )
    return redirect(url_for("tickets.view_ticket", id=target_ticket.id))


@tickets_bp.route("/toggle_resolution/<int:note_id>", methods=["POST"])
@login_required
def toggle_resolution(note_id):
    note = TicketNote.query.get_or_404(note_id)
    note.is_resolution = not note.is_resolution
    db.session.commit()
    flash(
        f'Note "{note.id}" updated to {"Resolution" if note.is_resolution else "Not-Resolution"}!',
        "success",
    )
    return redirect(url_for("tickets.view_ticket", id=note.ticket_id))


@tickets_bp.route("/toggle_billable/<int:ticket_id>", methods=["POST"])
@login_required
def toggle_billable(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    ticket.billable = "R" if ticket.billable == "NB" else "NB"
    db.session.commit()
    flash(
        f'Ticket "{ticket.subject}" updated to {"Billable" if ticket.billable == "R" else "Non-Billable"}!',
        "success",
    )
    return redirect(url_for("tickets.view_ticket", id=ticket_id))


@tickets_bp.route("/delete_ticket_page")
@login_required
def delete_ticket_page():
    tickets = Ticket.query.all()
    return render_template("delete_ticket.html", tickets=tickets)


@tickets_bp.route("/delete_ticket/<int:id>", methods=["POST"])
@login_required
def delete_ticket(id):
    ticket = Ticket.query.get_or_404(id)
    if current_user.role != "admin":
        flash("You do not have permission to delete this ticket.", "danger")
        return redirect(url_for("tickets.view_ticket", id=ticket.id))

    db.session.delete(ticket)
    db.session.commit()
    flash("Ticket deleted successfully!", "success")
    return redirect(url_for("tickets.dashboard"))


@tickets_bp.route("/complete_ticket/<int:id>", methods=["POST"])
@login_required
def complete_ticket(id):
    ticket = Ticket.query.get_or_404(id)
    has_resolution = any(note.is_resolution for note in ticket.notes)

    if not has_resolution:
        flash("Cannot complete ticket: no resolution note found.", "danger")
        return redirect(url_for("tickets.view_ticket", id=ticket.id))

    ticket.complete = True
    ticket.completed_at = datetime.now(UTC)
    ticket.status = "Closed"
    db.session.commit()

    flash("Ticket marked as complete!", "success")
    return redirect(url_for("tickets.view_ticket", id=ticket.id))


@tickets_bp.route("/update-tickets", methods=["GET", "POST"])
@login_required
def update_tickets():
    today = datetime.now().strftime("%Y-%m-%d 00:00:00")
    statements = [
        (
            "Overdue open tickets moved to today",
            text("""
                UPDATE ticket
                SET due_date = :today
                WHERE due_date < :today
                  AND status NOT IN ('Closed', 'On Hold')
            """),
            {"today": today},
        ),
        (
            "On-hold tickets cleared of due dates",
            text("UPDATE ticket SET due_date = NULL WHERE status = 'On Hold'"),
            {},
        ),
        (
            "Touched tickets moved to In Progress",
            text("UPDATE ticket SET status = 'In Progress' WHERE status = 'Touched'"),
            {},
        ),
        (
            "Reopened tickets cleared of completion flags",
            text("""
                UPDATE ticket
                SET completed_at = NULL, complete = 0
                WHERE status <> 'Closed'
                  AND completed_at IS NOT NULL
                  AND complete = 1
            """),
            {},
        ),
    ]

    results = []
    for label, stmt, params in statements:
        try:
            res = db.session.execute(stmt, params)
            results.append({
                "success": True,
                "message": f"{label}: {res.rowcount} row(s) updated.",
            })
        except Exception as e:
            db.session.rollback()
            results.append({
                "success": False,
                "message": f"{label} failed: {str(e)}",
            })

    db.session.commit()
    return render_template("clean_up_tickets.html", results=results)


@tickets_bp.route("/mass_complete_automated_tickets", methods=["GET", "POST"])
@login_required
def mass_complete_automated_tickets():
    if request.method == "POST":
        ticket_ids = request.form.getlist("ticket_ids")
        if not ticket_ids:
            flash("No tickets selected.", "warning")
            return redirect(url_for("tickets.mass_complete_automated_tickets"))

        completed_count = 0
        for ticket_id in ticket_ids:
            try:
                ticket = Ticket.query.get(int(ticket_id))
                if ticket and ticket.status != "Closed":
                    ticket.status = "Closed"
                    ticket.complete = True
                    ticket.completed_at = datetime.now(UTC)
                    completed_count += 1
            except (ValueError, AttributeError):
                continue

        db.session.commit()
        flash(f"Successfully completed {completed_count} automated tickets.", "success")
        return redirect(url_for("tickets.dashboard"))

    target_sender = "notify@itsupport247.net"
    automated_tickets = Ticket.query.filter(
        Ticket.status == "Open",
        Ticket.requestor_email.ilike(f"%{target_sender}%"),
    ).all()

    return render_template(
        "mass_complete_automated_tickets.html", tickets=automated_tickets
    )


@tickets_bp.route("/search", methods=["GET"])
@login_required
def search_tickets():
    q = (request.args.get("q") or "").strip()
    tickets = []
    total = 0

    if q:
        if q.startswith("#") and q[1:].isdigit():
            project_id_num = int(q[1:])
            tickets = (
                Ticket.query.filter(Ticket.project_id == project_id_num)
                .order_by(Ticket.created_at.desc())
                .all()
            )
        elif q.isdigit():
            ticket_id_num = int(q)
            tickets = (
                Ticket.query.filter(Ticket.id == ticket_id_num)
                .order_by(Ticket.created_at.desc())
                .all()
            )
        else:
            like = f"%{q}%"
            tickets = (
                Ticket.query.filter(
                    or_(
                        Ticket.subject.ilike(like),
                        Ticket.description.ilike(like),
                        Ticket.project.has(Project.name.ilike(like)),
                        Ticket.requestor_email.ilike(like),
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


@tickets_bp.route("/batch_assign_tech", methods=["POST"])
@login_required
def batch_assign_tech():
    ticket_ids = request.form.getlist("ticket_ids")
    tech_id = request.form.get("assigned_tech_id", type=int)

    if not ticket_ids:
        flash("No tickets selected for assignment.", "warning")
        return redirect(request.referrer or url_for("tickets.dashboard"))

    new_tech_id = tech_id if tech_id != 0 else None
    tickets = Ticket.query.filter(Ticket.id.in_(ticket_ids)).all()

    assigned_count = 0
    for ticket in tickets:
        old_tech_id = ticket.assigned_tech_id
        ticket.assigned_tech_id = new_tech_id
        assigned_count += 1
        if new_tech_id and new_tech_id != old_tech_id:
            send_assignment_email(ticket)

    db.session.commit()
    flash(f"Successfully updated assignment for {assigned_count} ticket(s).", "success")
    return redirect(request.referrer or url_for("tickets.dashboard"))