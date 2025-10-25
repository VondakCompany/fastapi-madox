Madox DB Proxy

A secure, lightweight FastAPI proxy for MySQL, with coded query execution, connection pooling, and asynchronous Google Sheets logging. Designed for safe desktop-app integration and deployment on Fly.io Free Tier.

Features

Coded queries only: clients send query codes instead of raw SQL.

Connection pooling: prevents DB overload with multiple concurrent users.

Thread-safe per-user locks: serializes requests per user.

Async Google Sheets logging: logs queries without blocking API responses.

Retry/backoff for failed logs: ensures resilience to Google API errors.

DELETE blocked: enhances security.

Health endpoint: includes active log queue monitoring.

Fly.io optimized: stateless, low memory footprint, always-on.

Table of Contents

Requirements

Environment Variables

Installation & Deployment

Queries JSON

API Usage

Endpoints

Logging

Monitoring

Fly.io Configuration

Requirements

Python 3.11+

MySQL database (e.g., Clever Cloud)

Google Service Account JSON

Python packages:

fastapi
uvicorn
pydantic
mysql-connector-python
gspread

Environment Variables
Variable	Description
API_KEY	API key for authentication.
DB_HOST	MySQL host.
DB_USER	MySQL username.
DB_PASS	MySQL password.
DB_NAME	MySQL database name.
GOOGLE_CREDS_PATH	Path to Google service account JSON. Default: /etc/secrets/google_credentials.json.
SPREADSHEET_NAME	Google Sheet for logging. Default: MADOX-API-log.
Installation & Deployment
Clone Repository
git clone <your-repo-url>
cd madox-db-proxy

Install Dependencies
pip install -r requirements.txt

Configure Environment Variables
export API_KEY="YOUR_API_KEY"
export DB_HOST="your-db-host"
export DB_USER="your-db-user"
export DB_PASS="your-db-pass"
export DB_NAME="your-db-name"
export GOOGLE_CREDS_PATH="/path/to/google_credentials.json"
export SPREADSHEET_NAME="MADOX-API-log"

Run Locally
uvicorn main:app --host 0.0.0.0 --port 8080

Queries JSON

Create queries.json in the same directory:

{
  "get_user_by_id": "SELECT * FROM users WHERE id=%s",
  "update_user_email": "UPDATE users SET email=%s WHERE id=%s",
  "insert_order": "INSERT INTO orders (user_id, product_id, quantity) VALUES (%s, %s, %s)"
}


Keys: query codes clients can request.

Values: SQL templates with placeholders %s for parameters.

API Usage

Header Required: x-api-key: <API_KEY>

Payload (JSON):

{
  "user_id": "user123",
  "query_code": "get_user_by_id",
  "params": [1]
}


Responses:

SELECT Example:

{
  "status": "success",
  "data": [{"id":1,"name":"Example"}]
}


INSERT/UPDATE Example:

{
  "status": "success",
  "rows_affected": 1
}

Endpoints
Endpoint	Method	Description
/query	POST	Execute coded SQL queries safely.
/health	GET	Check service health and active log queue.
Logging

Queries are logged to Google Sheets asynchronously.

Each log entry includes:

Timestamp (JST)

User ID

Query template

Parameters

Retry/backoff ensures temporary Google API failures are handled.

Monitoring

/health endpoint returns:

{
  "status": "ok",
  "active_logs": 0
}


Recommended: external uptime monitor (UptimeRobot/Cronitor) pinging /health.

Fly.io Configuration

fly.toml optimized for Free Tier:

app = "madox-db-proxy"
primary_region = "nrt"

[build]
  builder = "paketobuildpacks/builder:base"

[env]
  PORT = "8080"

[[services]]
  internal_port = 8080
  protocol = "tcp"

  [[services.ports]]
    handlers = ["http"]
    port = 80
  [[services.ports]]
    handlers = ["tls", "http"]
    port = 443

  [services.concurrency]
    hard_limit = 25
    soft_limit = 10
    type = "requests"

  [services.autoscale]
    min = 1
    max = 1

Fly.io Deployment Commands
fly launch
fly secrets set API_KEY="YOUR_API_KEY" \
  DB_HOST="your-db-host" \
  DB_USER="your-db-user" \
  DB_PASS="your-db-pass" \
  DB_NAME="your-db-name"
fly deploy
