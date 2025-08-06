from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from threading import Lock
import mysql.connector
import os
import json
import logging
from datetime import datetime, timezone
import gspread

# --- Basic Logging and App Setup ---
# This logs to Render's console for live debugging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
app = FastAPI(title="Madox DB Proxy")

# --- Google Sheets Logging (in the background) ---
# Render securely provides the secret file at this path
GOOGLE_CREDS_PATH = '/etc/secrets/google_credentials.json'
SPREADSHEET_NAME = 'API Action Logs' # Must match the name of your sheet

def get_gspread_client():
    """Authenticates with Google using the secret file and returns a gspread client."""
    try:
        return gspread.service_account(filename=GOOGLE_CREDS_PATH)
    except Exception as e:
        logging.error(f"FATAL: Could not authenticate with Google Sheets. Check secret file. Error: {e}")
        return None

def log_to_google_sheet(user_id: str, query: str, params: list):
    """Appends a new row to the designated Google Sheet."""
    client = get_gspread_client()
    if not client:
        return # Exit if authentication failed

    try:
        # Get the current time in JST (UTC+9) for the log entry
        jst = timezone(offset=datetime.timedelta(hours=9))
        timestamp = datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")

        sheet = client.open(SPREADSHEET_NAME).sheet1
        # The row that will be added to the sheet
        row_to_insert = [timestamp, user_id, query, json.dumps(params)]
        sheet.append_row(row_to_insert)
        logging.info("Successfully logged action to Google Sheet.")
    except Exception as e:
        logging.error(f"Failed to write log to Google Sheet: {e}")


# --- All other code (Locks, Security, DB Connection) ---
user_locks = {}
dict_lock = Lock()
def get_user_lock(user_id: str):
    with dict_lock:
        if user_id not in user_locks:
            user_locks[user_id] = Lock()
        return user_locks[user_id]

API_KEY = os.getenv("API_KEY")
def verify_api_key(request: Request):
    sent_key = request.headers.get("x-api-key")
    if not API_KEY or sent_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid or missing API Key")

def get_db_connection():
    try:
        return mysql.connector.connect(
            host=os.getenv("DB_HOST"), user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"), database=os.getenv("DB_NAME"),
        )
    except mysql.connector.Error as err:
        logging.error(f"Database connection failed: {err}")
        raise HTTPException(status_code=503, detail=f"Database connection failed: {err}")

class SQLRequest(BaseModel):
    user_id: str
    query: str
    params: list = []

@app.post("/query", dependencies=[Depends(verify_api_key)])
async def run_query(data: SQLRequest, background_tasks: BackgroundTasks):
    if data.query.strip().lower().startswith("delete"):
        raise HTTPException(status_code=403, detail="DELETE operations are blocked.")

    user_lock = get_user_lock(data.user_id)
    user_lock.acquire()
    try:
        background_tasks.add_task(log_to_google_sheet, data.user_id, data.query, data.params)
        
        logging.info(f"Processing query for user '{data.user_id}'.")

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(data.query, tuple(data.params))

        if data.query.strip().lower().startswith("select"):
            result = cursor.fetchall()
            return {"status": "success", "data": result}
        else:
            conn.commit()
            return {"status": "success", "rows_affected": cursor.rowcount}
    except mysql.connector.Error as err:
        logging.error(f"Database query error for user '{data.user_id}': {err}")
        raise HTTPException(status_code=500, detail=f"Database query error: {err}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()
        user_lock.release()