from datetime import datetime, timedelta
from dateutil import parser
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from flask_login import login_required, current_user
from sqlalchemy import func, text

from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from quickbooks import QuickBooks
from quickbooks.objects import Invoice, SalesItemLine, SalesItemLineDetail
from quickbooks.objects.base import Ref

from extensions import db
from models import Ticket, TicketNote, Company

billing_bp = Blueprint("billing", __name__)


def get_qbo_auth_client():
    return AuthClient(
        client_id=current_app.config["QBO_CLIENT_ID"],
        client_secret=current_app.config["QBO_CLIENT_SECRET"],
        environment=current_app.config["QBO_ENVIRONMENT"],
        redirect_uri=current_app.config["QBO_REDIRECT_URI"],
    )


@billing_bp.route("/toggle_billable_reviewed/<int:ticket_id>", methods=["POST"])
@login_required
def toggle_billable_reviewed(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    ticket.billable = "R" if ticket.billable == "NB" else "NB"
    db.session.commit()
    flash(
        f'Ticket "{ticket.subject}" updated to {"Billable" if ticket.billable == "R" else "Non-Billable"}!',
        "success",
    )
    return redirect(url_for("billing.billing_review_dashboard", id=ticket_id))


@billing_bp.route("/toggle_invoiced/<int:ticket_id>", methods=["POST"])
@login_required
def toggle_invoiced(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    ticket.billable = "I" if ticket.billable == "R" else "R"
    db.session.commit()
    flash(
        f'Ticket "{ticket.subject}" updated to {"Invoiced" if ticket.billable == "I" else "Non-Billable"}!',
        "success",
    )
    return redirect(url_for("billing.billing_dashboard"))


@billing_bp.route("/mark_reviewed_nonbillable/<int:ticket_id>", methods=["POST"])
@login_required
def mark_reviewed_nonbillable(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.billable == "NB":
        ticket.billable = "RNB"
        db.session.commit()
        flash(f'Ticket "{ticket.subject}" marked as Reviewed - Not Billable.', "info")
    return redirect(url_for("billing.billing_review_dashboard"))


@billing_bp.route("/billing_dashboard", methods=["GET", "POST"])
@login_required
def billing_dashboard():
    if request.method == "POST":
        start_val = request.form.get("start_date")
        end_val = request.form.get("end_date")
        if start_val:
            session["billing_start_date"] = start_val
        if end_val:
            session["billing_end_date"] = end_val
        return redirect(url_for("billing.billing_dashboard"))

    start_str = session.get("billing_start_date") or "2023-01-01"
    end_str = session.get("billing_end_date") or datetime.today().strftime("%Y-%m-%d")

    start_date = datetime.strptime(start_str, "%Y-%m-%d")
    end_date = (
        datetime.strptime(end_str, "%Y-%m-%d")
        + timedelta(days=1)
        - timedelta(seconds=1)
    )

    tickets = (
        Ticket.query.filter(
            Ticket.billable == "R",
            Ticket.completed_at >= start_date,
            Ticket.completed_at <= end_date,
        )
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


@billing_bp.route("/billing_review_dashboard", methods=["GET", "POST"])
@login_required
def billing_review_dashboard():
    start_date = request.form.get("start_date") or datetime.now().replace(day=1).strftime("%Y-%m-%d")
    end_date = request.form.get("end_date") or datetime.now().strftime("%Y-%m-%d")

    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")

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
        if ticket.due_date and isinstance(ticket.due_date, str):
            try:
                ticket.due_date = parser.parse(ticket.due_date)
            except ValueError:
                ticket.due_date = None

        if ticket.completed_at and isinstance(ticket.completed_at, str):
            try:
                ticket.completed_at = parser.parse(ticket.completed_at)
            except ValueError:
                ticket.completed_at = None

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
        ticket_totals[ticket.id] = total_time if total_time else 0

    return render_template(
        "review_for_billing.html",
        tickets=tickets,
        ticket_totals=ticket_totals,
        start_date=start_date,
        end_date=end_date,
    )


@billing_bp.route("/authorize_qbo")
@login_required
def authorize_qbo():
    if current_user.role != "admin":
        return redirect(url_for("tickets.dashboard"))

    auth_client = get_qbo_auth_client()
    url = auth_client.get_authorization_url([Scopes.ACCOUNTING])
    return redirect(url)


@billing_bp.route("/qbo_callback")
@login_required
def qbo_callback():
    auth_client = get_qbo_auth_client()
    auth_code = request.args.get("code")
    realm_id = request.args.get("realmId")

    if auth_code:
        auth_client.get_bearer_token(auth_code, realm_id=realm_id)
        session["qbo_access_token"] = auth_client.access_token
        session["qbo_refresh_token"] = auth_client.refresh_token
        session["qbo_realm_id"] = realm_id
        flash("QuickBooks authorized successfully!", "success")

    return redirect(url_for("admin.admin_dashboard"))


@billing_bp.route("/push_invoice_to_qbo/<int:ticket_id>", methods=["POST"])
@login_required
def push_invoice_to_qbo(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)
    company = ticket.client.company

    refresh_token = (
        session.get("qbo_refresh_token") or current_app.config["QBO_DEV_REFRESH_TOKEN"]
    )
    realm_id = session.get("qbo_realm_id") or current_app.config["QBO_DEV_REALM_ID"]

    if not refresh_token or not realm_id:
        flash("Please authorize QuickBooks first from the Admin Dashboard.", "warning")
        return redirect(url_for("billing.billing_dashboard"))

    if not company.qbo_customer_id:
        flash(
            f"Please edit {company.name} and add their QuickBooks Customer ID first.",
            "danger",
        )
        return redirect(url_for("billing.billing_dashboard"))

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
        return redirect(url_for("billing.billing_dashboard"))

    try:
        client = QuickBooks(
            auth_client=get_qbo_auth_client(),
            refresh_token=refresh_token,
            company_id=realm_id,
        )

        invoice = Invoice()
        invoice.CustomerRef = Ref()
        invoice.CustomerRef.value = company.qbo_customer_id

        line = SalesItemLine()
        line.LineNum = 1
        line.Description = f"IT Services: {ticket.subject} (Ticket #{ticket.id})"
        line.Amount = total_hours * 195.00

        line.SalesItemLineDetail = SalesItemLineDetail()
        line.SalesItemLineDetail.Qty = total_hours
        line.SalesItemLineDetail.UnitPrice = 195.00
        line.SalesItemLineDetail.ItemRef = Ref()
        line.SalesItemLineDetail.ItemRef.value = "1"

        invoice.Line.append(line)
        invoice.save(qb=client)

        ticket.billable = "I"
        db.session.commit()
        flash(f"Draft Invoice created in QBO for Ticket #{ticket.id}!", "success")

    except Exception as e:
        flash(f"QBO API Error: {str(e)}", "danger")

    return redirect(url_for("billing.billing_dashboard"))


@billing_bp.route("/batch_push_qbo", methods=["POST"])
@login_required
def batch_push_qbo():
    ticket_ids = request.form.getlist("ticket_ids")

    if not ticket_ids:
        flash("No tickets selected for batch push.", "warning")
        return redirect(url_for("billing.billing_dashboard"))

    if "qbo_access_token" not in session and not current_app.config.get(
        "QBO_DEV_REFRESH_TOKEN"
    ):
        flash("Please authorize QuickBooks first.", "warning")
        return redirect(url_for("billing.billing_dashboard"))

    refresh_token = session.get("qbo_refresh_token") or current_app.config.get(
        "QBO_DEV_REFRESH_TOKEN"
    )
    realm_id = session.get("qbo_realm_id") or current_app.config.get("QBO_DEV_REALM_ID")

    try:
        client = QuickBooks(
            auth_client=get_qbo_auth_client(),
            refresh_token=refresh_token,
            company_id=realm_id,
        )
    except Exception as e:
        flash(f"QBO Authentication Error: {str(e)}", "danger")
        return redirect(url_for("billing.billing_dashboard"))

    selected_tickets = Ticket.query.filter(Ticket.id.in_(ticket_ids)).all()

    company_batches = {}
    for t in selected_tickets:
        comp_id = t.client.company_id
        if comp_id not in company_batches:
            company_batches[comp_id] = []
        company_batches[comp_id].append(t)

    invoices_created = 0

    for comp_id, tickets in company_batches.items():
        company = Company.query.get(comp_id)

        if not company.qbo_customer_id:
            flash(f"Skipped {company.name} - Missing QBO Customer ID.", "danger")
            continue

        invoice = Invoice()
        invoice.CustomerRef = Ref()
        invoice.CustomerRef.value = company.qbo_customer_id

        has_billable_lines = False
        line_num = 1
        for ticket in tickets:
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
                line.Amount = total_hours * 195.00

                line.SalesItemLineDetail = SalesItemLineDetail()
                line.SalesItemLineDetail.Qty = total_hours
                line.SalesItemLineDetail.UnitPrice = 195.00
                line.SalesItemLineDetail.ItemRef = Ref()
                line.SalesItemLineDetail.ItemRef.value = "1"

                invoice.Line.append(line)
                has_billable_lines = True
                line_num += 1

        if has_billable_lines:
            try:
                invoice.save(qb=client)
                for ticket in tickets:
                    ticket.billable = "I"
                    ticket.qbo_invoice_id = invoice.Id
                invoices_created += 1
            except Exception as e:
                flash(f"Error creating invoice for {company.name}: {str(e)}", "danger")

    db.session.commit()

    if invoices_created > 0:
        flash(
            f"Successfully pushed {invoices_created} batch invoice(s) to QuickBooks!",
            "success",
        )

    return redirect(url_for("billing.billing_dashboard"))


@billing_bp.route("/invoice_history")
@login_required
def invoice_history():
    invoiced_tickets = (
        Ticket.query.filter(Ticket.billable == "I", Ticket.qbo_invoice_id.isnot(None))
        .order_by(Ticket.completed_at.desc())
        .all()
    )

    invoices = {}
    for ticket in invoiced_tickets:
        qbo_id = ticket.qbo_invoice_id
        if qbo_id not in invoices:
            invoices[qbo_id] = {
                "company_name": ticket.client.company.name
                if ticket.client
                else "Unknown Company",
                "total_hours": 0,
                "tickets": [],
            }

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
        invoices[qbo_id]["total_hours"] += hours

    for qbo_id in invoices:
        invoices[qbo_id]["total_hours"] = round(invoices[qbo_id]["total_hours"], 2)

    return render_template("invoice_history.html", invoices=invoices)