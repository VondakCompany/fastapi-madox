import os
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone, timedelta
from threading import Lock
from pathlib import Path

# FastAPI & dependencies
from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel

# Database & Sheets
import mysql.connector
from mysql.connector import pooling, Error as MySQLError
import gspread

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
app = FastAPI(title="Madox DB Proxy")

# --- Load Queries JSON ---
QUERIES_PATH = Path(__file__).parent / "queries.json"
try:
    with open(QUERIES_PATH, "r", encoding="utf-8") as f:
        QUERY_DICT = json.load(f)
    logging.info(f"✅ Loaded {len(QUERY_DICT)} coded queries from {QUERIES_PATH}")
except Exception as e:
    logging.error(f"❌ Failed to load queries.json: {e}")
    QUERY_DICT = {}

# --- Google Sheets Configuration ---
GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS_PATH", "/etc/secrets/google_credentials.json")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "MADOX-API-log")

# Thread-safe queue for logs
log_queue = queue.Queue()
stop_logging_thread = threading.Event()


# --- Background Worker for Google Sheets Logging ---
def log_worker():
    """Consumes the log queue and writes entries to Google Sheets with rate-limit handling."""
    while not stop_logging_thread.is_set():
        try:
            log_item = log_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            user_id, sql_template, params = log_item
            client = gspread.service_account(filename=GOOGLE_CREDS_PATH)
            sheet = client.open(SPREADSHEET_NAME).sheet1

            jst = timezone(offset=timedelta(hours=9))
            timestamp = datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")
            row_to_insert = [timestamp, user_id, sql_template, json.dumps(params)]
            sheet.append_row(row_to_insert)

            logging.info(f"✅ Logged query for user '{user_id}' to Google Sheet.")
        except Exception as e:
            logging.error(f"⚠️ Google Sheets logging failed: {e}")
            # Exponential backoff retry
            time.sleep(2)
            log_queue.put(log_item)  # retry once
        finally:
            log_queue.task_done()


# Start the background logger thread
threading.Thread(target=log_worker, daemon=True).start()


# --- DB Connection Pool Setup ---
try:
    dbconfig = {
        "host": os.getenv("DB_HOST"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASS"),
        "database": os.getenv("DB_NAME"),
        "pool_name": "madox_pool",
        "pool_size": 5,  # max concurrent DB connections
        "pool_reset_session": True,
    }
    connection_pool = pooling.MySQLConnectionPool(**dbconfig)
    logging.info("✅ MySQL connection pool initialized.")
except MySQLError as e:
    logging.error(f"❌ Failed to create DB connection pool: {e}")
    connection_pool = None


# --- User Locks ---
user_locks = {}
dict_lock = Lock()


def get_user_lock(user_id: str):
    with dict_lock:
        if user_id not in user_locks:
            user_locks[user_id] = Lock()
        return user_locks[user_id]


# --- Security ---
API_KEY = os.getenv("API_KEY")


def verify_api_key(request: Request):
    sent_key = request.headers.get("x-api-key")
    if not API_KEY or not sent_key or sent_key.strip() != API_KEY.strip():
        raise HTTPException(status_code=403, detail="Forbidden: Invalid or missing API Key")


# --- Models ---
class CodedSQLRequest(BaseModel):
    user_id: str
    query_code: str
    params: list = []


# --- Main Endpoint ---
@app.post("/query", dependencies=[Depends(verify_api_key)])
async def run_coded_query(data: CodedSQLRequest, background_tasks: BackgroundTasks):
    user_lock = get_user_lock(data.user_id)
    user_lock.acquire()

    try:
        # Lookup the coded query
        if data.query_code not in QUERY_DICT:
            raise HTTPException(status_code=400, detail="Unknown query code")

        sql_template = QUERY_DICT[data.query_code]

        # Queue log for background writing
        log_queue.put((data.user_id, sql_template, data.params))
        logging.info(f"➡️ Processing query '{data.query_code}' for user '{data.user_id}'.")

        # Get DB connection
        if not connection_pool:
            raise HTTPException(status_code=503, detail="Database pool not initialized.")

        conn = connection_pool.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql_template, tuple(data.params))

        # Handle SELECT vs non-SELECT
        if sql_template.strip().lower().startswith("select"):
            result = cursor.fetchall()
            return {"status": "success", "data": result}
        else:
            conn.commit()
            return {"status": "success", "rows_affected": cursor.rowcount}

    except MySQLError as err:
        logging.error(f"❌ Database error: {err}")
        raise HTTPException(status_code=500, detail=f"Database query error: {err}")
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals() and conn.is_connected():
            conn.close()
        user_lock.release()


# --- Health Check ---
@app.get("/health")
async def health_check():
    return {"status": "ok", "active_logs": log_queue.qsize()}


# --- Graceful Shutdown ---
@app.on_event("shutdown")
def shutdown_event():
    logging.info("🛑 Shutting down logging thread...")
    stop_logging_thread.set()
    log_queue.join()
    logging.info("✅ Clean shutdown complete.")
