import os
import base64
import pickle
from email import message_from_bytes
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from flask import Flask
from models import db, Ticket, Client, User  # Added User import
from config import Config
from gmail_send import send_gmail_message
from datetime import datetime, date
from pytz import UTC

# --- Flask app context setup ---
from app import app

# --- Gmail API setup ---
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
TOKEN_PATH = Config.TOKEN_PATH  # e.g., 'token_helpdesk.pickle'
CLIENT_SECRET_PATH = Config.CLIENT_SECRET_PATH

ADMIN_EMAIL = Config.ADMIN_EMAIL  # Change to your admin email
NEW_TICKET_EMAIL = Config.NEWTICKET_EMAIL
HELPDESK_EMAIL = Config.COMPANY_SUPPORT_EMAIL
DEFAULT_USER_ID = 1  # Default user_id for tickets created from email


def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, 'wb') as token:
            pickle.dump(creds, token)
    service = build('gmail', 'v1', credentials=creds)
    return service


def get_unread_emails(service):
    results = service.users().messages().list(userId='me', labelIds=['INBOX', 'UNREAD']).execute()
    messages = results.get('messages', [])
    return messages


def parse_email(service, msg_id):
    msg = service.users().messages().get(userId='me', id=msg_id, format='raw').execute()
    msg_bytes = base64.urlsafe_b64decode(msg['raw'].encode('ASCII'))
    mime_msg = message_from_bytes(msg_bytes)
    sender = mime_msg['From']
    subject = mime_msg['Subject']
    # Get plain text body
    body = ""
    if mime_msg.is_multipart():
        for part in mime_msg.walk():
            if part.get_content_type() == 'text/plain':
                body = part.get_payload(decode=True).decode(errors='ignore')
                break
    else:
        body = mime_msg.get_payload(decode=True).decode(errors='ignore')
    return sender, subject, body


def mark_as_read(service, msg_id):
    service.users().messages().modify(userId='me', id=msg_id, body={'removeLabelIds': ['UNREAD']}).execute()


def extract_email_address(sender):
    import re
    match = re.search(r'<(.+?)>', sender)
    if match:
        return match.group(1)
    return sender.strip()


def notify_admin(sender_email, subject, body):
    send_gmail_message(
        to=NEW_TICKET_EMAIL,
        subject=f"[Helpdesk] Email from non-client: {sender_email}",
        html_body=f"""
        <p>An email was received from <b>{sender_email}</b> who is not a registered client.</p>
        <p><b>Subject:</b> {subject}</p>
        <p><b>Body:</b><br>{body}</p>
        """
    )


def create_ticket_from_email(sender_email, subject, body, gmail_message_id=None):
    with app.app_context():
        client = Client.query.filter_by(email=sender_email).first()
        today = date.today()
        
        # Clean and convert email body to markdown
        from markdownify import markdownify
        import re
        
        # Convert HTML email to markdown
        markdown_text = markdownify(body, heading_style="ATX")
        
        # Clean up common email artifacts
        lines = markdown_text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            # Remove email headers and signatures
            if any(pattern in line.lower() for pattern in [
                'from:', 'sent:', 'to:', 'subject:', 'cc:', 'bcc:',
                'best regards', 'sincerely', 'thank you', 'thanks',
                '--', '---', 'sent from my', 'get outlook'
            ]):
                continue
            
            # Remove empty lines at the beginning
            if not cleaned_lines and line.strip() == '':
                continue
                
            # Remove excessive whitespace
            line = line.strip()
            if line:
                cleaned_lines.append(line)
        
        # Join lines back together
        cleaned_description = '\n\n'.join(cleaned_lines)
        
        ticket = Ticket(
            subject=subject or '(No Subject)',
            description=cleaned_description,
            status='Open',
            priority='Medium',
            user_id=DEFAULT_USER_ID,  # Always assign to default user
            client_id=client.id if client else None,
            requestor_email=sender_email,
            cc_emails=None,
            due_date=today,  # Set due_date to today
            estimated_hours=0.5,
            created_at=datetime.now(UTC),
            gmail_message_id=gmail_message_id  # Store Gmail message ID
        )
        db.session.add(ticket)
        db.session.commit()
        db.session.refresh(ticket)  # Ensure ticket is bound to session
        return ticket, client is not None


def send_ticket_confirmation(ticket):
    # Look up the user with DEFAULT_USER_ID
    with app.app_context():
        user = User.query.get(DEFAULT_USER_ID)
        if not user:
            return
        subject = f"[Helpdesk] Ticket #{ticket.id} Created"
        html_body = f"""
        <p>A new ticket has been created from email.</p>
        <ul>
            <li><b>Ticket Number:</b> {ticket.id}</li>
            <li><b>Subject:</b> {ticket.subject}</li>
            <li><b>Requestor:</b> {ticket.requestor_email}</li>
        </ul>
        <b>Description:</b>
        <pre style='background:#f4f4f4;padding:10px;border-radius:5px;'>{ticket.description}</pre>
        """
        send_gmail_message(
            to=NEW_TICKET_EMAIL,
            subject=subject,
            html_body=html_body
        )


def main():
    service = get_gmail_service()
    messages = get_unread_emails(service)
    print(f"Found {len(messages)} unread emails.")
    for msg in messages:
        msg_id = msg['id']
        sender, subject, body = parse_email(service, msg_id)
        sender_email = extract_email_address(sender)
        print(f"Processing email from {sender_email} - Subject: {subject}")
        ticket, client_exists = create_ticket_from_email(sender_email, subject, body, gmail_message_id=msg_id)
        send_ticket_confirmation(ticket)
        if not client_exists:
            notify_admin(sender_email, subject, body)
        mark_as_read(service, msg_id)
        print(f"Processed and marked as read: {subject}")

if __name__ == '__main__':
    main() 