import base64
from email.mime.text import MIMEText
from email.utils import formataddr
from email.header import Header
from googleapiclient.discovery import build
from config import Config
import pickle

SCOPES = ['https://www.googleapis.com/auth/gmail.send']

def get_gmail_service():
    with open(Config.GMAIL_TOKEN_PATH, 'rb') as token:
        creds = pickle.load(token)
    return build('gmail', 'v1', credentials=creds)

def create_message(sender, to, subject, html_body, thread_id=None):
    message = MIMEText(html_body, 'html')
    sender_name, sender_email = Config.MAIL_DEFAULT_SENDER or (None, sender)
    message['to'] = to
    formatted_sender = formataddr((str(Header(sender_name, 'utf-8')), sender_email))
    message['from'] = formatted_sender
    message['subject'] = subject

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
    body = {'raw': raw_message}

    if thread_id:
        body['threadId'] = thread_id

    return body

def send_gmail_message(to, subject, html_body, thread_id=None):
    service = get_gmail_service()
    sender = Config.MAIL_DEFAULT_SENDER_EMAIL
    message = create_message(sender, to, subject, html_body, thread_id)
    return service.users().messages().send(userId='me', body=message).execute()