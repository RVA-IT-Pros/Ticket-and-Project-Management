import pickle
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from google_auth_oauthlib.flow import InstalledAppFlow

from extensions import db
from models import User
from forms import ChangeRoleForm

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin_dashboard", methods=["GET", "POST"])
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        flash("You do not have permission to access this page.", "danger")
        return redirect(url_for("tickets.dashboard"))

    users = User.query.all()
    role_form = ChangeRoleForm()

    if request.method == "POST":
        user_id = request.form.get("user_id")
        new_role = request.form.get("role")
        new_color = request.form.get("tech_color") or "#007bff"

        if user_id and user_id.isdigit():
            user = db.session.get(User, int(user_id))
            if user:
                user.role = new_role
                user.tech_color = new_color
                db.session.commit()
                flash(f"Role for {user.username} updated to {new_role}.", "success")

        return redirect(url_for("admin.admin_dashboard"))

    return render_template("admin_dashboard.html", users=users, role_form=role_form)

@admin_bp.route("/authorize_gmail")
@login_required
def authorize_gmail():
    if current_user.role != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("tickets.dashboard"))
    flow = InstalledAppFlow.from_client_secrets_file(
        current_app.config["CLIENT_SECRET_PATH"],
        ["https://www.googleapis.com/auth/gmail.send"],
    )
    creds = flow.run_local_server(port=0)
    with open("token_helpdesk.pickle", "wb") as token:
        pickle.dump(creds, token)
    return "Helpdesk Gmail authorized."