import os
import pickle
from config import Config
from googleapiclient.discovery import build

# Define the scopes required for Google Calendar API
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/gmail.send'
]

BASE_DIR =  Config.BASE_DIR
TOKEN_PATH = Config.CALENDAR_TOKEN_PATH
CLIENT_SECRET_PATH = Config.CLIENT_SECRET_PATH


def get_calendar_service():
    creds = None
    # Load credentials if previously saved
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)


    with open(Config.CALENDAR_TOKEN_PATH, 'rb') as token:
        creds = pickle.load(token)
    return build('calendar', 'v3', credentials=creds)

def create_event(summary, start_time, end_time, description='', location=''):
    service = get_calendar_service()
    event = {
        'summary': summary,
        'location': location,
        'description': description,
        'start': {'dateTime': start_time, 'timeZone': 'America/New_York'},
        'end': {'dateTime': end_time, 'timeZone': 'America/New_York'},
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'email', 'minutes': 24 * 60},
                {'method': 'popup', 'minutes': 10},
            ],
        },
    }
    calendar_id = Config.CALENDAR_ID
    event = service.events().insert(calendarId=calendar_id, body=event).execute()

# def list_calendars():
#     service = get_calendar_service()
#     calendars = service.calendarList().list().execute()
    
#     for calendar in calendars['items']:
#         print(f"Calendar Name: {calendar['summary']}, ID: {calendar['id']}")


# list_calendars()



