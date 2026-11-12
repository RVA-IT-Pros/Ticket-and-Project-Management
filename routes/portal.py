from datetime import datetime
import markdown
from pytz import UTC
from sqlalchemy import and_, or_
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from extensions import db
from models import Ticket, TicketNote, Client, Project
from forms import AddNoteForm, TicketForm
from gmail_send import send_gmail_message

portal_bp = Blueprint("portal", __name__)

md = markdown.Markdown(
    extensions=["fenced_code", "tables", "codehilite", "nl2br", "sane_lists"]
)


@portal_bp.route("/portal")
@login_required
def client_portal():
    if current_user.role != "client":
        return redirect(url_for("tickets.dashboard"))

    is_company_admin = current_user.is_company_admin

    if is_company_admin and current_user.client and current_user.client.company_id:
        client_tickets = (
            Ticket.query.join(Client)
            .filter(Client.company_id == current_user.client.company_id)
            .order_by(Ticket.updated_at.desc())
            .all()
        )
    else:
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


@portal_bp.route("/portal/ticket/<int:id>", methods=["GET", "POST"])
@login_required
def portal_view_ticket(id):
    if current_user.role != "client":
        return redirect(url_for("tickets.dashboard"))

    ticket = Ticket.query.get_or_404(id)

    user_owns_ticket = ticket.user_id == current_user.id
    client_owns_ticket = (
        current_user.client_id and ticket.client_id == current_user.client_id
    )

    is_company_admin = getattr(current_user, "is_company_admin", False)
    same_company = (
        current_user.client
        and ticket.client
        and current_user.client.company_id == ticket.client.company_id
    )
    is_authorized_admin = is_company_admin and same_company

    if not (user_owns_ticket or client_owns_ticket or is_authorized_admin):
        flash("You do not have permission to view this ticket.", "danger")
        return redirect(url_for("portal.client_portal"))

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
        return redirect(url_for("portal.portal_view_ticket", id=ticket.id))

    if ticket.project_id:
        back_url = url_for("portal.portal_view_project", project_id=ticket.project_id)
    else:
        back_url = url_for("portal.client_portal")

    return render_template(
        "portal_view_ticket.html",
        ticket=ticket,
        note_form=note_form,
        back_url=back_url,
    )


@portal_bp.route("/portal/ticket/new", methods=["GET", "POST"])
@login_required
def portal_new_ticket():
    if current_user.role != "client":
        return redirect(url_for("tickets.dashboard"))

    form = TicketForm()

    form.client_id.choices = (
        [
            (
                current_user.client_id,
                f"{current_user.client.first_name} {current_user.client.last_name}",
            )
        ]
        if current_user.client
        else []
    )
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
            estimated_hours=0.5,
        )
        db.session.add(ticket)
        db.session.commit()

        notification_email = (
            current_app.config.get("NEWTICKET_EMAIL") or "newticket@rvaitpros.com"
        )
        client_name = (
            f"{current_user.client.first_name} {current_user.client.last_name}"
            if current_user.client
            else current_user.username
        )
        company_name = (
            current_user.client.company.name
            if (current_user.client and current_user.client.company)
            else "N/A"
        )

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
            send_gmail_message(
                to=notification_email, subject=email_subject, html_body=email_body
            )
        except Exception as e:
            print(f"Error sending portal ticket notification email: {e}")

        flash("Support ticket created successfully!", "success")
        return redirect(url_for("portal.client_portal"))

    return render_template("portal_new_ticket.html", form=form)


@portal_bp.route("/portal/project/<int:project_id>")
@login_required
def portal_view_project(project_id):
    if current_user.role != "client":
        return redirect(url_for("tickets.dashboard"))

    project = Project.query.get_or_404(project_id)

    is_admin = getattr(current_user, "is_company_admin", False)
    is_shared = current_user in project.shared_users
    same_company = (
        current_user.client
        and project.company_id == current_user.client.company_id
    )

    if not (same_company and (is_admin or is_shared)):
        flash("You do not have permission to view this project.", "danger")
        return redirect(url_for("portal.client_portal"))

    if is_admin:
        visible_phases = project.phases
    else:
        visible_phases = [p for p in project.phases if current_user in p.shared_users]

    return render_template(
        "portal_view_project.html",
        project=project,
        phases=visible_phases,
    )