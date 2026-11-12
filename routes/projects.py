from datetime import datetime
from io import BytesIO
import pandas as pd
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, send_file
from flask_login import login_required, current_user

from extensions import db
from models import Company, Project, Phase, Ticket, User, Client
from forms import ProjectForm, PhaseForm, SelectProjectForm, SelectPhaseForm, UpdateProjectForm

projects_bp = Blueprint("projects", __name__)


@projects_bp.route("/project_or_regular")
@login_required
def project_or_regular():
    return render_template("project_or_regular.html")


@projects_bp.route("/create_project", methods=["GET", "POST"])
@login_required
def create_project():
    form = ProjectForm()
    companies = Company.query.all()
    form.company_id.choices = [(company.id, company.name) for company in companies]

    if form.validate_on_submit():
        project = Project(
            name=form.name.data,
            description=form.description.data,
            status=form.status.data,
            company_id=form.company_id.data,
        )
        db.session.add(project)
        db.session.commit()

        if form.phase_name.data:
            phase = Phase(
                name=form.phase_name.data, project_id=project.id, status="Open"
            )
            db.session.add(phase)
            db.session.commit()

        flash("Project created successfully!", "success")
        return redirect(url_for("projects.projects_home"))

    return render_template("create_project.html", form=form)


@projects_bp.route("/create_phase/<int:project_id>", methods=["GET", "POST"])
@login_required
def create_phase(project_id):
    project = Project.query.get_or_404(project_id)
    form = PhaseForm()
    if form.validate_on_submit():
        phase = Phase(
            name=form.name.data,
            project_id=project.id,
            status=form.status.data,
        )
        db.session.add(phase)
        db.session.commit()
        flash("Phase created successfully!", "success")
        return redirect(url_for("projects.project_dashboard", project_id=project.id))

    return render_template("create_phase.html", form=form, project=project)


@projects_bp.route("/projects")
@login_required
def projects_home():
    projects = Project.query.filter(Project.status != "Closed").all()
    session.pop("start_time_utc", None)
    return render_template("projects_home.html", projects=projects)


@projects_bp.route("/project_dashboard/<int:project_id>")
@login_required
def project_dashboard(project_id):
    project = Project.query.get_or_404(project_id)

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


@projects_bp.route("/select_project/<int:ticket_id>", methods=["GET", "POST"])
@login_required
def select_project(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    form = SelectProjectForm()

    form.project_id.choices = [(0, "Select a Project")] + [
        (p.id, p.name)
        for p in Project.query.filter(~Project.status.in_(["Closed"])).all()
    ]

    if form.validate_on_submit():
        selected_project = Project.query.get(form.project_id.data)
        ticket.project_id = selected_project.id
        db.session.commit()
        return redirect(
            url_for("projects.select_phase", ticket_id=ticket_id, project_id=selected_project.id)
        )

    return render_template("select_project.html", form=form, ticket=ticket)


@projects_bp.route("/select_project_new_ticket", methods=["GET", "POST"])
@login_required
def select_project_new_ticket():
    form = SelectProjectForm()

    form.project_id.choices = [(0, "Select a Project")] + [
        (p.id, p.name)
        for p in Project.query.filter(~Project.status.in_(["Closed"])).all()
    ]

    if form.validate_on_submit():
        selected_project = Project.query.get(form.project_id.data)
        db.session.commit()
        return redirect(url_for("tickets.new_ticket", project_id=selected_project.id))

    return render_template("select_project_new_ticket.html", form=form)


@projects_bp.route("/select_phase/<int:ticket_id>/<int:project_id>", methods=["GET", "POST"])
@login_required
def select_phase(ticket_id, project_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    project = Project.query.get_or_404(project_id)
    form = SelectPhaseForm()

    form.phase_id.choices = [(0, "Select a Phase")] + [
        (p.id, p.name) for p in project.phases
    ]

    if form.validate_on_submit():
        if form.phase_id.data != 0:
            ticket.phase_id = form.phase_id.data
            db.session.commit()
            flash("Ticket successfully assigned to project and phase!", "success")
            return redirect(url_for("tickets.view_ticket", id=ticket.id))
        else:
            flash("Please select a valid phase.", "danger")

    return render_template(
        "select_phase.html", form=form, project=project, ticket=ticket
    )


@projects_bp.route("/edit_project/<int:project_id>", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)
    form = UpdateProjectForm(obj=project)

    companies = Company.query.all()
    form.company_id.choices = [(company.id, company.name) for company in companies]

    if request.method == "GET":
        form.company_id.data = project.company_id

    if form.validate_on_submit():
        project.name = form.name.data
        project.description = form.description.data
        project.status = form.status.data
        project.company_id = form.company_id.data
        db.session.commit()
        flash("Project updated successfully!", "success")
        return redirect(url_for("projects.project_dashboard", project_id=project.id))

    return render_template("edit_project.html", form=form, project=project)


@projects_bp.route("/edit_phase/<int:id>", methods=["GET", "POST"])
@login_required
def edit_phase(id):
    phase = Phase.query.get_or_404(id)
    form = PhaseForm(obj=phase)

    if form.validate_on_submit():
        phase.name = form.name.data
        phase.status = form.status.data
        db.session.commit()
        flash("Phase updated successfully!", "success")
        return redirect(url_for("projects.project_dashboard", project_id=phase.project_id))

    return render_template("edit_phase.html", form=form, phase=phase)


@projects_bp.route("/download_project_excel/<int:project_id>")
@login_required
def download_project_excel(project_id):
    project = Project.query.get_or_404(project_id)
    rows = []

    for phase in project.phases:
        for ticket in phase.tickets:
            is_billable = ticket.billable == "R"
            billable_label = "Yes" if is_billable else "No"

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


@projects_bp.route("/project/<int:project_id>/share", methods=["GET", "POST"])
@login_required
def share_project(project_id):
    if current_user.role == "client":
        flash("Access denied.", "danger")
        return redirect(url_for("tickets.dashboard"))

    project = Project.query.get_or_404(project_id)
    company_users = (
        User.query.join(Client)
        .filter(Client.company_id == project.company_id)
        .all()
    )

    if request.method == "POST":
        selected_user_ids = [int(uid) for uid in request.form.getlist("shared_user_ids")]
        project.shared_users = [u for u in company_users if u.id in selected_user_ids]

        for phase in project.phases:
            phase_user_ids = [int(uid) for uid in request.form.getlist(f"phase_{phase.id}_users")]
            phase.shared_users = [u for u in company_users if u.id in phase_user_ids]

        db.session.commit()
        flash(f"Permissions for {project.name} updated successfully!", "success")
        return redirect(url_for("projects.share_project", project_id=project.id))

    return render_template("share_project.html", project=project, company_users=company_users)