import os
import json
import gspread
from google.oauth2.service_account import Credentials

def get_sheet():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS not set")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(creds)
    sheet_id = os.getenv("SHEET_ID")
    return client.open_by_key(sheet_id).sheet1

def append_result(user_id, name, email, language, score):
    sheet = get_sheet()
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([str(user_id), name, email, language, str(score), timestamp])

def user_exists(user_id):
    sheet = get_sheet()
    ids = sheet.col_values(1)
    return str(user_id) in ids