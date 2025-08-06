import os
import json
import logging
from threading import Lock
from datetime import datetime, timezone, timedelta

# FastAPI and Pydantic
from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel

# Database and Google Sheets libraries
import mysql.connector
import gspread

# --- Basic Logging and App Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
app = FastAPI(title="Madox DB Proxy")

# --- Google Sheets Logging (in the background) ---
GOOGLE_CREDS_PATH = '/etc/secrets/google_credentials.json'
SPREADSHEET_NAME = 'MADOX-API-log'

def get_gspread_client():
    try:
        return gspread.service_account(filename=GOOGLE_CREDS_PATH)
    except Exception as e:
        logging.error(f"FATAL: Could not authenticate with Google Sheets. Check secret file. Error: {e}")
        return None

def log_to_google_sheet(user_id: str, query: str, params: list):
    """Appends a new row to the designated Google Sheet with detailed error logging."""
    client = get_gspread_client()
    if not client:
        return

    try:
        logging.info("Attempting to open Google Sheet...")
        sheet = client.open(SPREADSHEET_NAME).sheet1
        logging.info("Google Sheet opened successfully. Preparing to append row...")

        jst = timezone(offset=timedelta(hours=9))
        timestamp = datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")
        row_to_insert = [timestamp, user_id, query, json.dumps(params)]

        sheet.append_row(row_to_insert)
        logging.info("Successfully wrote log to Google Sheet.")

    except Exception as e:
        # --- NEW DETAILED ERROR LOGGING ---
        logging.error("--- FAILED TO WRITE LOG TO GOOGLE SHEET ---")
        logging.error(f"Exception Type: {type(e)}")
        logging.error(f"Exception Representation (repr): {repr(e)}")
        logging.error(f"Exception String (str): {e}")
        logging.error("-----------------------------------------")


# --- All other code remains the same ---
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
    if not API_KEY or not sent_key or sent_key.strip() != API_KEY.strip():
        raise HTTPException(status_code=403, detail="Forbidden: Invalid or missing API Key")

def get_db_connection():
    try:
        return mysql.connector.connect(
            host=os.getenv("DB_HOST"), user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"), database=os.getenv("DB_NAME"),
        )
    except mysql.connector.Error as err:
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
        raise HTTPException(status_code=500, detail=f"Database query error: {err}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()
        user_lock.release()

@app.get("/health")
async def health_check():
    """A simple endpoint for uptime monitoring that doesn't require a key."""
    return {"status": "ok"}
