from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_
from werkzeug.security import generate_password_hash

from extensions import db
from models import Company, Client, User
from forms import CompanyForm, ClientForm, EditCompanyForm

clients_bp = Blueprint("clients", __name__)


@clients_bp.route("/create_company", methods=["GET", "POST"])
@login_required
def create_company():
    form = CompanyForm()
    if form.validate_on_submit():
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
        return redirect(url_for("tickets.dashboard"))

    return render_template("create_company.html", form=form)


@clients_bp.route("/create_client", methods=["GET", "POST"])
@login_required
def create_client():
    form = ClientForm()
    form.company_id.choices = [(c.id, c.name) for c in Company.query.all()]

    if form.validate_on_submit():
        client = Client(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            phone=form.phone.data,
            company_id=form.company_id.data,
        )
        db.session.add(client)
        db.session.flush()

        if form.password.data:
            hashed_password = generate_password_hash(
                form.password.data, method="pbkdf2:sha256"
            )
            user = User(
                username=form.email.data,
                email=form.email.data,
                password=hashed_password,
                role="client",
                client_id=client.id,
            )
            db.session.add(user)

        db.session.commit()
        flash("Client created successfully!", "success")
        return redirect(url_for("tickets.dashboard"))

    return render_template("create_client.html", form=form)


@clients_bp.route("/company_dashboard", methods=["GET"])
@login_required
def view_company():
    companies = Company.query.all()
    return render_template("company_dashboard.html", companies=companies)


@clients_bp.route("/company/<int:company_id>", methods=["GET", "POST"])
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
        company.qbo_customer_id = form.qbo_customer_id.data

        db.session.commit()
        flash("Company details updated successfully!", "success")
        return redirect(url_for("clients.view_edit_company", company_id=company.id))

    clients = Client.query.filter_by(company_id=company_id).all()
    return render_template(
        "view_edit_company.html", form=form, company=company, clients=clients
    )


@clients_bp.route("/client/<int:client_id>", methods=["GET", "POST"])
@login_required
def view_edit_client(client_id):
    if current_user.role == "client":
        return redirect(url_for("portal.client_portal"))

    client = Client.query.get_or_404(client_id)
    form = ClientForm(obj=client)

    if hasattr(form, "company_id"):
        form.company_id.choices = [(c.id, c.name) for c in Company.query.all()]

    user = User.query.filter_by(client_id=client.id).first()

    if request.method == "POST":
        client.first_name = request.form.get("first_name", client.first_name)
        client.last_name = request.form.get("last_name", client.last_name)
        client.email = request.form.get("email", client.email)
        client.phone = request.form.get("phone", client.phone)

        entered_password = request.form.get("password")
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
            user.is_company_admin = request.form.get("is_company_admin") == "on"

        db.session.commit()
        flash("Client details updated successfully!", "success")
        return redirect(url_for("clients.view_edit_company", company_id=client.company_id))

    has_portal_access = user is not None
    return render_template(
        "view_edit_client.html",
        form=form,
        client=client,
        has_portal_access=has_portal_access,
    )


@clients_bp.route("/get_client_email", methods=["GET"])
@login_required
def get_client_email():
    client_id = request.args.get("client_id")
    client = Client.query.get_or_404(client_id)
    return jsonify(email=client.email)


@clients_bp.route("/get_company_clients", methods=["GET"])
@login_required
def get_company_clients():
    company_id = request.args.get("company_id", type=int)

    internal_company = Company.query.filter(
        or_(
            Company.name.ilike("%RVA IT Pros%"),
            Company.customer_type == "Internal"
        )
    ).first()

    internal_company_id = internal_company.id if internal_company else None

    if not company_id or company_id == 0:
        clients = Client.query.all()
    else:
        company_ids = [company_id]
        if internal_company_id and internal_company_id != company_id:
            company_ids.append(internal_company_id)
        clients = Client.query.filter(Client.company_id.in_(company_ids)).all()

    client_data = [
        {
            "id": c.id,
            "name": f"{c.first_name} {c.last_name}",
            "email": c.email,
            "company_id": c.company_id,
            "is_internal": (c.company_id == internal_company_id)
        }
        for c in clients
    ]
    return jsonify(clients=client_data)