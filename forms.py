from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DateTimeField,
    DateTimeLocalField,
    FloatField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
    SelectField
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
)

from models import Project


class RegistrationForm(FlaskForm):
    username = StringField(
        "Username", validators=[InputRequired(), Length(min=4, max=20)]
    )
    email = StringField(
        "Email", validators=[InputRequired(), Email(message="Invalid email")]
    )
    password = PasswordField(
        "Password", validators=[InputRequired(), Length(min=6, max=20)]
    )
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[
            InputRequired(),
            EqualTo("password", message="Passwords must match"),
        ],
    )
    submit = SubmitField("Register")


# User Login Form
class LoginForm(FlaskForm):
    username = StringField(
        "Username", validators=[InputRequired(), Length(min=4, max=150)]
    )
    password = PasswordField(
        "Password", validators=[InputRequired(), Length(min=4, max=20)]
    )
    submit = SubmitField("Login")


class TicketForm(FlaskForm):
    subject = StringField("Subject", validators=[InputRequired()])
    description = TextAreaField("Description", validators=[InputRequired()])
    client_id = SelectField("Requestor", coerce=int, validators=[DataRequired()])
    requestor_email = StringField("Requestor Email", validators=[DataRequired()])
    cc_emails = TextAreaField("CC Emails (comma-separated)", validators=[Optional()])
    # Use consistent priority values for easy comparison in the app
    priority = SelectField(
        "Priority",
        choices=[
            ("Critical", "Critical"),
            ("High", "High"),
            ("Medium", "Medium"),
            ("Low", "Low"),
        ],
        default='Medium',
        validators=[InputRequired()],
    )
    status = SelectField(
        "Status",
        choices=[
            ("Open", "Open"),
            ("In Progress", "In Progress"),
            ("Closed", "Closed"),
        ],
        validators=[InputRequired()],
    )
    phase_id = SelectField("Phase", coerce=int)  # This will be populated dynamically
    due_date = DateField("Due Date", format="%Y-%m-%d", validators=[Optional()])
    estimated_hours = FloatField(
        "Estimated Hours",
        default=0.5,
        validators=[
            Optional(),
            NumberRange(min=0, message="Estimated hours must be positive"),
        ],
    )
    assigned_tech_id = SelectField('Assign Technician', coerce=int)
    submit = SubmitField("Create Ticket")


# Ticket Update Form
class UpdateTicketForm(FlaskForm):
    subject = StringField("Subject", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[DataRequired()])
    client_id = SelectField("Requestor", coerce=int, validators=[DataRequired()])
    requestor_email = StringField("Requestor Email", validators=[DataRequired()])
    cc_emails = TextAreaField("CC Emails (comma-separated)", validators=[Optional()])
    status = SelectField(
        "Status",
        choices=[
            ("Open", "Open"),
            ("In Progress", "In Progress"),
            ("Touched", "Touched"),
            ("On Hold", "On Hold"),
        ],
        validators=[DataRequired()],
    )
    priority = SelectField(
        "Priority",
        choices=[
            ("Critical", "Critical"),
            ("High", "High"),
            ("Medium", "Medium"),
            ("Low", "Low"),
        ],
        default='Medium',
        validators=[InputRequired()],
    )
    phase_id = SelectField(
        "Phase", coerce=int, validators=[Optional()]
    )  # Optional phase selection
    due_date = DateField("Due Date", format="%Y-%m-%d", validators=[Optional()])
    estimated_hours = FloatField(
        "Estimated Hours",
        default=0.5,
        validators=[
            Optional(),
            NumberRange(min=0, message="Estimated hours must be positive"),
        ],
    )
    assigned_tech_id = SelectField('Assign Technician', coerce=int, validators=[Optional()])
    submit = SubmitField("Update Ticket")


# Form for adding a note to a ticket
class AddNoteForm(FlaskForm):
    content = TextAreaField("Add a note", validators=[InputRequired()])
    note_start_time = DateTimeLocalField(
        "Note Start Time", format="%Y-%m-%dT%H:%M", validators=[Optional()]
    )
    note_finish_time = DateTimeLocalField(
        "Note Finish Time", format="%Y-%m-%dT%H:%M", validators=[Optional()]
    )
    submit = SubmitField("Add Note")


class ProjectForm(FlaskForm):
    name = StringField("Project Name", validators=[InputRequired()])
    description = TextAreaField("Project Description", validators=[InputRequired()])
    status = SelectField(
        "Status",
        choices=[
            ("Open", "Open"),
            ("In Progress", "In Progress"),
            ("On Hold", "On Hold"),
            ("Closed", "Closed"),
        ],
        validators=[InputRequired()],
    )
    company_id = SelectField("Company", coerce=int, validators=[DataRequired()])
    phase_name = StringField("Phase Name (Optional)", validators=[Optional()])
    submit = SubmitField("Create Project")


class PhaseForm(FlaskForm):
    name = StringField("Phase Name", validators=[InputRequired()])
    project_id = StringField("Project")  # This will be dynamically populated
    status = SelectField(
        "Status",
        choices=[
            ("Open", "Open"),
            ("In Progress", "In Progress"),
            ("On Hold", "On Hold"),
            ("Closed", "Closed"),
        ],
        validators=[InputRequired()],
    )
    submit = SubmitField("Create Phase")


class SelectProjectForm(FlaskForm):
    project_id = SelectField("Select Project", coerce=int, validators=[InputRequired()])
    submit = SubmitField("Next")


class SelectPhaseForm(FlaskForm):
    phase_id = SelectField("Select Phase", coerce=int, validators=[InputRequired()])
    submit = SubmitField("Assign")


# Form for creating a company
class CompanyForm(FlaskForm):
    name = StringField("Company Name", validators=[InputRequired(), Length(max=200)])
    street_address = StringField(
        "Street Address", validators=[InputRequired(), Length(max=200)]
    )
    city = StringField("City", validators=[InputRequired(), Length(max=100)])
    state = StringField("State", validators=[InputRequired(), Length(max=50)])
    zip_code = StringField("Zip Code", validators=[InputRequired(), Length(max=20)])
    main_phone = StringField("Main Phone", validators=[Optional(), Length(max=20)])
    customer_type = SelectField(
        "Customer Type",
        choices=[
            ("Lead", "Lead"),
            ("Customer", "Customer"),
            ("Former Customer", "Former Customer"),
            ("Internal", "Internal"),
        ],
        validators=[InputRequired()],
    )
    submit = SubmitField("Create Company")


# Form for creating a client (employee)
class ClientForm(FlaskForm):
    first_name = StringField(
        "First Name", validators=[InputRequired(), Length(max=100)]
    )
    last_name = StringField("Last Name", validators=[InputRequired(), Length(max=100)])
    email = StringField("Email", validators=[InputRequired(), Email(), Length(max=150)])
    phone = StringField("Phone", validators=[Optional(), Length(max=20)])
    company_id = SelectField(
        "Company", coerce=int, validators=[InputRequired()]
    )  # Populate with companies dynamically
    submit = SubmitField("Create Client")
    password = PasswordField(
        "Portal Password (Leave blank for no portal access)",
        validators=[Optional(), Length(min=6, max=20)],
    )


# Form for editing a company
class EditCompanyForm(FlaskForm):
    name = StringField("Company Name", validators=[InputRequired(), Length(max=200)])
    street_address = StringField(
        "Street Address", validators=[InputRequired(), Length(max=200)]
    )
    city = StringField("City", validators=[InputRequired(), Length(max=100)])
    state = StringField("State", validators=[InputRequired(), Length(max=50)])
    zip_code = StringField("Zip Code", validators=[InputRequired(), Length(max=20)])
    main_phone = StringField("Main Phone", validators=[Optional(), Length(max=20)])
    customer_type = SelectField(
        "Customer Type",
        choices=[
            ("Lead", "Lead"),
            ("Customer", "Customer"),
            ("Former Customer", "Former Customer"),
        ],
        validators=[InputRequired()],
    )
    qbo_customer_id = StringField(
        "QuickBooks Customer ID", validators=[Optional(), Length(max=100)]
    )

    submit = SubmitField("Update Company")

    submit = SubmitField("Update Company")


# Form for editing a client (employee)
class EditClientForm(FlaskForm):
    first_name = StringField(
        "First Name", validators=[InputRequired(), Length(max=100)]
    )
    last_name = StringField("Last Name", validators=[InputRequired(), Length(max=100)])
    email = StringField("Email", validators=[InputRequired(), Email(), Length(max=150)])
    phone = StringField("Phone", validators=[Optional(), Length(max=20)])
    password = PasswordField(
        "Portal Password (Leave blank for no portal access)",
        validators=[Optional(), Length(min=6, max=20)],
    )
    submit = SubmitField("Update Client")


class ChangeRoleForm(FlaskForm):
    user_id = HiddenField("User ID", validators=[InputRequired()])
    role = SelectField(
        "Role",
        choices=[("user", "User"),("technician", "Technician"),("admin", "Admin")],
        validators=[InputRequired()],
    )


class EditNoteForm(FlaskForm):
    content = TextAreaField("Note Content", validators=[DataRequired()])
    note_start_time = DateTimeField(
        "Note Start Time", format="%Y-%m-%d %H:%M:%S", validators=[DataRequired()]
    )
    note_finish_time = DateTimeField(
        "Note Finish Time", format="%Y-%m-%d %H:%M:%S", validators=[DataRequired()]
    )
    submit = SubmitField("Update Note")


class UpdateProjectForm(FlaskForm):
    name = StringField("Project Name", validators=[InputRequired()])
    description = TextAreaField("Project Description", validators=[InputRequired()])
    status = SelectField(
        "Status",
        choices=[
            ("Open", "Open"),
            ("In Progress", "In Progress"),
            ("On Hold", "On Hold"),
            ("Closed", "Closed"),
        ],
        validators=[InputRequired()],
    )
    company_id = SelectField("Company", coerce=int, validators=[DataRequired()])
    submit = SubmitField("Update Project")


class UpdatePhaseForm(FlaskForm):
    name = StringField("Phase Name", validators=[InputRequired()])
    project_id = StringField("Project")  # Dynamically populated
    status = SelectField(
        "Status",
        choices=[
            ("Open", "Open"),
            ("In Progress", "In Progress"),
            ("On Hold", "On Hold"),
            ("Closed", "Closed"),
        ],
        validators=[InputRequired()],
    )
    submit = SubmitField("Update Phase")


class RandomNumberForm(FlaskForm):
    number = IntegerField("Range", validators=[InputRequired()])
    submit = SubmitField("Get Number")


class AddBulkInventoryForm(FlaskForm):
    item_name = StringField("Item Name", validators=[DataRequired()])
    sku = StringField("SKU / Model Number", validators=[Optional()])
    qty_in_stock = IntegerField("Quantity to Add", validators=[DataRequired()])
    landed_unit_cost = FloatField(
        "Landed Unit Cost (Your Cost)", validators=[DataRequired()]
    )
    retail_unit_price = FloatField(
        "Retail Unit Price (Client Cost)", validators=[DataRequired()]
    )
    low_stock_threshold = IntegerField("Low Stock Alert Threshold", default=5)
    submit = SubmitField("Add to Inventory")


class AddDeployedAssetForm(FlaskForm):
    company_id = SelectField(
        "Assign to Company", coerce=int, validators=[DataRequired()]
    )
    model_name = StringField("Model / Asset Name", validators=[DataRequired()])
    serial_number = StringField("Serial Number", validators=[Optional()])
    purchase_cost = FloatField("Fully Burdened Cost", validators=[DataRequired()])
    status = SelectField(
        "Status",
        choices=[
            ("Staged", "Staged (Internal)"),
            ("Deployed", "Deployed (Active)"),
            ("Decommissioned", "Decommissioned"),
        ],
        default="Staged",
    )
    submit = SubmitField("Log Asset")
