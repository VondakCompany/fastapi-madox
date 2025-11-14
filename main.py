import os
import json
import logging
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends
from pydantic import BaseModel
import mysql.connector
from mysql.connector import pooling
import hashlib

# ----------------- Logging Setup -----------------
logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Madox Proxy")

# ----------------- Load Queries -----------------
QUERIES_PATH = Path(__file__).parent / "queries.json"
with open(QUERIES_PATH, "r", encoding="utf-8") as f:
    QUERY_DICT = json.load(f)

# ----------------- DB Connection Pool -----------------
dbconfig = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME"),
    "pool_name": "proxy_pool",
    "pool_size": 5
}
connection_pool = pooling.MySQLConnectionPool(**dbconfig)

# ----------------- Security -----------------
API_KEY = os.getenv("API_KEY")
def verify_api_key(request: Request):
    sent_key = request.headers.get("x-api-key")
    if not API_KEY or not sent_key or sent_key.strip() != API_KEY.strip():
        raise HTTPException(status_code=403, detail="Forbidden")

# ----------------- Request Model -----------------
class QueryRequest(BaseModel):
    query_code: str
    username: str = None
    password: str = None  # already hashed once on app

# ----------------- Helper Functions -----------------
def double_hash(username: str, password_hash: str) -> str:
    salted = f"{username}:{password_hash}"
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()

# ----------------- Single Query Endpoint -----------------
@app.post("/query", dependencies=[Depends(verify_api_key)])
def run_query(data: QueryRequest):
    conn = connection_pool.get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        conn.start_transaction()

        # ----------------- LOGIN -----------------
        if data.query_code == "001":
            cursor.execute(QUERY_DICT["001"], (data.username,))
            user = cursor.fetchone()
            if not user:
                conn.rollback()
                return {"code": "002"}  # invalid credentials

            hashed_pw = double_hash(data.username, data.password)
            if hashed_pw != user["password_hash"]:
                conn.rollback()
                return {"code": "002"}  # invalid credentials

            cursor.execute(QUERY_DICT["002"], (user["id"],))
            if cursor.rowcount == 0:
                conn.rollback()
                return {"code": "001"}  # simultaneous login limit exceeded

            conn.commit()
            return {"code": "000"}  # login successful

        # ----------------- LOGOUT -----------------
        elif data.query_code == "003":
            cursor.execute("SELECT id, status FROM users WHERE username=%s FOR UPDATE", (data.username,))
            user = cursor.fetchone()
            if not user:
                conn.rollback()
                return {"status": "user_not_found"}

            cursor.execute(QUERY_DICT["003"], (user["id"],))
            conn.commit()
            return {"status": "ok"}

        # ----------------- Unknown Query -----------------
        else:
            conn.rollback()
            return {"status": "unknown_query"}

    except mysql.connector.Error as e:
        conn.rollback()
        logging.error(f"DB Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        cursor.close()
        conn.close()

# ----------------- Health Check -----------------
@app.get("/health")
def health_check():
    return {"status": "ok"}
