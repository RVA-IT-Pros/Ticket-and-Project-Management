from datetime import datetime, timezone
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ---------------------------------------------------------------------------
# Many-to-Many Association Tables for Access Controls
# ---------------------------------------------------------------------------

# Project-Level Sharing
project_shares = db.Table(
    "project_shares",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("project_id", db.Integer, db.ForeignKey("project.id"), primary_key=True),
)

# Phase-Level Sharing
phase_shares = db.Table(
    "phase_shares",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("phase_id", db.Integer, db.ForeignKey("phase.id"), primary_key=True),
)


# ---------------------------------------------------------------------------
# User Model
# ---------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="technician")  # 'admin', 'technician', 'client'
    is_company_admin = db.Column(db.Boolean, default=False)

    # Links login accounts to physical Client/Employee entities
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)

    # Shared Projects Access
    shared_projects = db.relationship(
        "Project",
        secondary=project_shares,
        backref=db.backref("shared_users", lazy="dynamic"),
        lazy="dynamic",
    )

    # Shared Phases Access
    shared_phases = db.relationship(
        "Phase",
        secondary=phase_shares,
        backref=db.backref("shared_users", lazy="dynamic"),
        lazy="dynamic",
    )


# ---------------------------------------------------------------------------
# Company & Client Models
# ---------------------------------------------------------------------------
class Company(db.Model):
    __tablename__ = "company"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    street_address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    zip_code = db.Column(db.String(20), nullable=True)
    main_phone = db.Column(db.String(50), nullable=True)
    customer_type = db.Column(db.String(100), nullable=True)
    qbo_customer_id = db.Column(db.String(50), nullable=True)

    clients = db.relationship("Client", backref="company", lazy=True, cascade="all, delete-orphan")
    projects = db.relationship("Project", backref="company", lazy=True)


class Client(db.Model):
    __tablename__ = "client"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(50), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)

    user_account = db.relationship("User", backref="client", lazy=True)
    tickets = db.relationship("Ticket", back_populates="client")


# ---------------------------------------------------------------------------
# Project, Phase & Ticket Models
# ---------------------------------------------------------------------------
class Project(db.Model):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="Open")
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)

    phases = db.relationship("Phase", backref="project", lazy=True, cascade="all, delete-orphan")
    tickets = db.relationship("Ticket", backref="project", lazy=True)


class Phase(db.Model):
    __tablename__ = "phase"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    status = db.Column(db.String(50), default="Open")
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)

    tickets = db.relationship("Ticket", backref="phase", lazy=True)


class Ticket(db.Model):
    __tablename__ = "ticket"

    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="Open")
    priority = db.Column(db.String(50), default="Medium")
    billable = db.Column(db.String(10), default="NB")  # 'NB' = Non-Billable, 'R' = Review, 'I' = Invoiced
    qbo_invoice_id = db.Column(db.String(50), nullable=True)

    due_date = db.Column(db.DateTime, nullable=True)
    estimated_hours = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    completed_at = db.Column(db.DateTime, nullable=True)
    complete = db.Column(db.Boolean, default=False)

    requestor_email = db.Column(db.String(150), nullable=True)
    cc_emails = db.Column(db.String(255), nullable=True)

    # Relationships & Foreign Keys
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    assigned_tech_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=True)
    phase_id = db.Column(db.Integer, db.ForeignKey("phase.id"), nullable=True)

    assigned_tech = db.relationship("User", foreign_keys=[assigned_tech_id])
    notes = db.relationship("TicketNote", backref="ticket", lazy=True, cascade="all, delete-orphan")

    gmail_message_id = db.Column(db.String(128), nullable=True)

    client = db.relationship("Client", back_populates="tickets")
    project = db.relationship("Project", back_populates="tickets")
    phase = db.relationship("Phase", back_populates="tickets")

class TicketNote(db.Model):
    __tablename__ = "ticket_note"

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    note_start_time = db.Column(db.DateTime, nullable=True)
    note_finish_time = db.Column(db.DateTime, nullable=True)
    is_resolution = db.Column(db.Boolean, default=False)

    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])


# ---------------------------------------------------------------------------
# Inventory & Asset Tracking Models
# ---------------------------------------------------------------------------
class BulkInventory(db.Model):
    __tablename__ = "bulk_inventory"

    id = db.Column(db.Integer, primary_key=True)
    item_name = db.Column(db.String(150), nullable=False)
    sku = db.Column(db.String(100), unique=True, nullable=True)
    qty_in_stock = db.Column(db.Integer, default=0)
    landed_unit_cost = db.Column(db.Float, default=0.0)
    retail_unit_price = db.Column(db.Float, default=0.0)
    low_stock_threshold = db.Column(db.Integer, default=5)


class DeployedAsset(db.Model):
    __tablename__ = "deployed_asset"

    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(150), nullable=False)
    serial_number = db.Column(db.String(100), unique=True, nullable=True)
    purchase_cost = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(50), default="Deployed")

    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    company = db.relationship("Company", backref="assets")