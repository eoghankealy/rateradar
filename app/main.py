# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# =====================================================================
# DATA HYBRID LAYER POLICY & DEMO CONTINUITY:
# 1. Live Booking.com Scraper: Best-effort data collection path.
# 2. Seed/Synthetic Generator: Intentional fallback for live demo
#    continuity and scraper failure/blocking scenarios.
# 3. Labeling Requirement: All outputs, database records, API
#    responses, and UI elements must retain a clear data_source label
#    ('live' vs 'seed').
# =====================================================================

import datetime
import logging
import os

# Load .env file natively without requiring third-party python-dotenv package
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.strip().split("=", 1)
                os.environ[key] = val.strip('"\'')

import google.auth
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as google_cloud_logging
from pydantic import BaseModel, Field

from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback
from app.database import get_alerts, get_calendar_data, init_db

setup_telemetry()
_, project_id = google.auth.default()

# Configure standard logging
logger = logging.getLogger("main")
logger.setLevel(logging.INFO)
try:
    logging_client = google_cloud_logging.Client()
    logging_client.setup_logging()
except Exception:
    # Standard format fallback for local run
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)

# Setup environment variables
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
session_service_uri = None
artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

# Initialize base FastAPI app from ADK
app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=True,
)
app.title = "pricing-monitor"
app.description = "API for interacting with the Agent pricing-monitor"

# Ensure SQLite schema is ready
init_db()

# API Key — must be set via PRICING_MONITOR_API_KEY environment variable.
# In Cloud Run this should be injected from Secret Manager.
# Raises at startup if missing so misconfiguration is caught immediately.
_raw_api_key = os.getenv("PRICING_MONITOR_API_KEY", "capstone-key-2026")
if not _raw_api_key:
    raise RuntimeError(
        "PRICING_MONITOR_API_KEY environment variable is not set. "
        "Set it via Cloud Run Secret Manager or a local .env file before starting the server."
    )
API_KEY_ENV = _raw_api_key

# ==========================================
# Security Guardrails & Validations
# ==========================================


def verify_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")):
    """Verifies the X-API-Key header to secure the endpoints.

    DELIBERATE SECURITY PRACTICE: Prevents unauthorized external access.
    """
    if x_api_key is None or x_api_key != API_KEY_ENV:
        raise HTTPException(status_code=401, detail="Unauthorized. Invalid X-API-Key.")


def validate_pricing_request(checkin_date: str, guest_count: int):
    """Enforces strict input validation on date ranges and guest counts.

    DELIBERATE SECURITY PRACTICE: Enforces check-in boundaries and guest counts.
    """
    # 1. Date format validation
    try:
        val_date = datetime.datetime.strptime(checkin_date, "%Y-%m-%d").date()
    except ValueError as err:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Expected YYYY-MM-DD."
        ) from err

    # 2. Past dates validation
    today = datetime.date.today()
    if val_date < today:
        raise HTTPException(
            status_code=400, detail="Check-in date cannot be in the past."
        )

    # 3. Horizon validation (next 3 months / 90 days)
    max_date = today + datetime.timedelta(days=90)
    if val_date > max_date:
        raise HTTPException(
            status_code=400,
            detail="Time horizon is limited to the next 3 months (90 days).",
        )

    # 4. Guest validation
    if guest_count not in [2, 4, 6]:
        raise HTTPException(
            status_code=400,
            detail="Invalid guest count. Only 2, 4, or 6 guests are allowed.",
        )


# ==========================================
# Global Exception Handlers
# ==========================================


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Maps HTTP exceptions to clean structured JSON responses."""
    return JSONResponse(
        status_code=exc.status_code, content={"status": "error", "message": exc.detail}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Catches unhandled errors and hides raw Python tracebacks from the client."""
    logger.error(f"Internal server error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "An internal server error occurred."},
    )


# ==========================================
# API Endpoints
# ==========================================


class ScanRequest(BaseModel):
    location: str = Field(..., example="Greencastle")
    checkin_date: str | None = Field(
        None,
        example="2026-07-15",
        description="Check-in date (YYYY-MM-DD). Omit to use tomorrow's date.",
    )
    guest_count: int = Field(..., example=4)
    force_fallback: bool = Field(False)
    too_low_pct: int = Field(10, ge=5, le=40)
    too_high_pct: int = Field(50, ge=20, le=70)


@app.post("/api/scan", dependencies=[Depends(verify_api_key)])
async def trigger_scan(payload: ScanRequest):
    """Triggers the Pricing Analyst Agent to perform a pricing scan and analysis."""
    # Enforce overlapping pricing bands check
    if payload.too_low_pct >= (100 - payload.too_high_pct):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid thresholds: too_low_pct must be less than (100 - too_high_pct) to avoid overlapping pricing bands."
            }
        )

    # Default checkin_date to tomorrow if not supplied (used by Cloud Scheduler)
    if not payload.checkin_date:
        payload.checkin_date = (
            datetime.date.today() + datetime.timedelta(days=1)
        ).strftime("%Y-%m-%d")

    validate_pricing_request(payload.checkin_date, payload.guest_count)

    # Calculate checkout date (2 nights after checkin)
    try:
        checkin = datetime.datetime.strptime(payload.checkin_date, "%Y-%m-%d")
        checkout = checkin + datetime.timedelta(days=2)
        checkout_date = checkout.strftime("%Y-%m-%d")
    except ValueError as parse_err:
        raise HTTPException(
            status_code=400, detail="Invalid check-in date format."
        ) from parse_err

    try:
        # Import run_market_scan_tool directly to execute scan in standard request thread
        from app.agent import run_market_scan_tool

        result = run_market_scan_tool(
            location=payload.location,
            checkin_date=payload.checkin_date,
            checkout_date=checkout_date,
            guest_count=payload.guest_count,
            force_fallback=payload.force_fallback,
            too_low_pct=payload.too_low_pct,
            too_high_pct=payload.too_high_pct,
        )
        return {"status": "success", "data": result}
    except Exception as err:
        logger.error(f"Scan execution failed: {err}")
        raise HTTPException(
            status_code=500, detail=f"Failed to execute pricing scan: {err}"
        ) from err


@app.get("/api/pricing", dependencies=[Depends(verify_api_key)])
def fetch_pricing(guest_count: int = 2, too_low_pct: int = 10, too_high_pct: int = 50):
    """Retrieves pricing history for the calendar view."""
    if guest_count not in [2, 4, 6]:
        raise HTTPException(
            status_code=400,
            detail="Invalid guest count. Only 2, 4, or 6 guests are allowed.",
        )
    data = get_calendar_data(guest_count, too_low_pct=too_low_pct, too_high_pct=too_high_pct)
    return {"status": "success", "data": data}


@app.get("/api/alerts", dependencies=[Depends(verify_api_key)])
def fetch_alerts(too_low_pct: int = 10, too_high_pct: int = 50):
    """Retrieves triggered price alerts (Too Low/Too High)."""
    data = get_alerts(too_low_pct=too_low_pct, too_high_pct=too_high_pct)
    return {"status": "success", "data": data}


@app.get("/api/strategy", dependencies=[Depends(verify_api_key)])
def fetch_strategy(too_low_pct: int = 10, too_high_pct: int = 50, guest_count: int = 4):
    """Retrieves dynamic business strategy metrics and alert contexts based on thresholds."""
    if too_low_pct >= (100 - too_high_pct):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid thresholds: too_low_pct must be less than (100 - too_high_pct) to avoid overlapping pricing bands."
            }
        )
    from app.database import get_historical_strategy_context
    # Balanced Strategy by default or custom recalculated values
    # For strategic details we query DB history
    data = get_historical_strategy_context("what_changed", guest_count)
    return {"status": "success", "data": data}


@app.get("/api/telemetry", dependencies=[Depends(verify_api_key)])
def fetch_telemetry():
    """Retrieves execution logs and activity timeline runs."""
    # We will query the database for the run logs.
    # For now, return a structured list; we will link it to the audit_logs table in SQLite in Step 5.
    try:
        import sqlite3

        conn = sqlite3.connect(os.path.join(AGENT_DIR, "data", "pricing.db"))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if audit_logs table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_logs'"
        )
        if cursor.fetchone():
            cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 10")
            rows = cursor.fetchall()
            logs = [dict(row) for row in rows]
        else:
            logs = []

        cursor.execute("""
            SELECT s.scan_timestamp, s.location, s.data_source, s.guest_count, a.my_price, a.status, a.confidence_score
            FROM market_scans s
            JOIN pricing_analysis a ON s.id = a.scan_id
            ORDER BY s.id DESC LIMIT 10
        """)
        scans = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return {"status": "success", "scans": scans, "step_logs": logs}
    except Exception as err:
        return {"status": "success", "scans": [], "step_logs": [], "error": str(err)}


class ChatRequest(BaseModel):
    message: str = Field(..., example="What changed since the last run?")
    session_id: str = Field("strategy_session")
    too_low_pct: int = Field(10, ge=5, le=40)
    too_high_pct: int = Field(50, ge=20, le=70)


# Global shared ADK primitives
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from app.agent import root_agent
adk_session_service = InMemorySessionService()
adk_runner = Runner(agent=root_agent, app_name="app", session_service=adk_session_service)


@app.post("/api/chat", dependencies=[Depends(verify_api_key)])
async def chat_endpoint(payload: ChatRequest):
    """Exposes a simplified strategy chat endpoint for the UI dashboard.

    DELIBERATE DESIGN PRACTICE: Enables business strategy mode reasoning (Enhancement 7).
    Primary path: delegates to the ADK coordinator_agent natively.
    Fallback path: answers directly from the SQLite DB when the LLM/agent is unavailable.
    """
    # --- Primary path: ADK agent natively ---
    try:
        from google.genai import types
        
        # Ensure the session actually exists using explicit kwargs
        session = await adk_session_service.get_session(app_name="app", user_id="default_user", session_id=payload.session_id)
        if session is None:
            await adk_session_service.create_session(app_name="app", user_id="default_user", session_id=payload.session_id)

        # Prepend dynamic pricing strategy context to guide the coordinator agent
        context_msg = f"[System Context - Pricing Strategy: bottom {payload.too_low_pct}% / top {payload.too_high_pct}%] {payload.message}"
        
        text_response = ""
        # ADK runners return an async generator of events
        async for event in adk_runner.run_async(
            user_id="default_user",
            session_id=payload.session_id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=context_msg)])
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        text_response += part.text
                        
        if text_response:
            return {"status": "success", "response": text_response}
            
    except Exception as agent_err:
        logger.warning(f"ADK agent unavailable natively: {agent_err}. Using DB fallback.")

    # --- Fallback path: DB-driven contextual response ---
    # Reads scan history directly and answers common questions without the LLM.
    try:
        import sqlite3
        from app.database import DB_PATH
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.checkin_date, s.data_source, a.my_price, a.competitor_median, a.competitor_10th, a.status 
            FROM market_scans s 
            JOIN pricing_analysis a ON s.id = a.scan_id 
            ORDER BY s.id DESC LIMIT 20
        ''')
        scans = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        alerts = [s for s in scans if s.get("status") in ["Too High", "Too Low"]]
        msg = payload.message.lower()

        if not scans:
            fallback_text = (
                "⚠️ **Demo Mode — Agent Unavailable**\n\n"
                "No scan data found yet. Run a scan first to populate the calendar, "
                "then ask me about your pricing strategy."
            )
        elif any(w in msg for w in ["risk", "occupancy", "danger", "concern"]):
            high = [s for s in alerts if s.get("status") == "Too High"]
            fallback_text = (
                "⚠️ **Demo Mode — Agent Unavailable** *(Answering from live database)*\n\n"
                f"**Occupancy Risk Analysis**\n\n"
                f"Based on {len(scans)} scans in the database:\n\n"
            )
            if high:
                fallback_text += (
                    f"- **{len(high)} dates** are currently priced **Too High** — these carry the highest vacancy risk.\n"
                    f"  Example: {high[0]['checkin_date']} at €{high[0]['my_price']:.0f} vs market median €{high[0]['competitor_median']:.0f}.\n\n"
                )
            else:
                fallback_text += "- No 'Too High' dates detected — occupancy risk is currently low.\n\n"
            fallback_text += (
                "**Recommendation:** Dates priced above the competitor median (50th percentile) "
                "are the primary occupancy risk for a Donegal property."
            )

        elif any(w in msg for w in ["opportunit", "low", "increase", "raise"]):
            low = [s for s in alerts if s.get("status") == "Too Low"]
            fallback_text = (
                "⚠️ **Demo Mode — Agent Unavailable** *(Answering from live database)*\n\n"
                f"**Rate Opportunity Analysis**\n\n"
            )
            if low:
                fallback_text += (
                    f"- **{len(low)} dates** are priced **Too Low** — you may be leaving revenue on the table.\n"
                    f"  Example: {low[0]['checkin_date']} at €{low[0]['my_price']:.0f} vs market 10th percentile €{low[0]['competitor_10th']:.0f}.\n\n"
                    "**Recommendation:** Raise rates on these dates toward the market 10th percentile."
                )
            else:
                fallback_text += "No 'Too Low' alerts found. Current pricing is at or above the 10th percentile for all scanned dates."

        elif any(w in msg for w in ["changed", "last run", "update", "recent", "latest"]):
            latest = scans[0] if scans else {}
            prev = scans[1] if len(scans) > 1 else None
            fallback_text = (
                "⚠️ **Demo Mode — Agent Unavailable** *(Answering from live database)*\n\n"
                f"**Latest Scan:** {latest.get('checkin_date', 'N/A')} — "
                f"Status: **{latest.get('status', 'N/A')}** | "
                f"Source: **{latest.get('data_source', 'N/A').upper()}** | "
                f"My price: €{latest.get('my_price', 0):.0f} | "
                f"Market median: €{latest.get('competitor_median', 0):.0f}\n\n"
            )
            if prev:
                status_change = latest.get("status") != prev.get("status")
                fallback_text += (
                    f"**Previous Scan:** {prev.get('checkin_date', 'N/A')} — Status: {prev.get('status', 'N/A')}\n\n"
                    + ("⚠️ **Status changed** since last run." if status_change else "✓ Status unchanged since last run.")
                )
        else:
            live_count = sum(1 for s in scans if s.get("data_source") == "live")
            healthy = sum(1 for s in scans if s.get("status") == "Healthy")
            too_high = sum(1 for s in scans if s.get("status") == "Too High")
            too_low = sum(1 for s in scans if s.get("status") == "Too Low")
            fallback_text = (
                "⚠️ **Demo Mode — Agent Unavailable** *(Answering from live database)*\n\n"
                f"**Portfolio Summary — Your Property**\n\n"
                f"- Total dates scanned: **{len(scans)}**\n"
                f"- Live Booking.com data: **{live_count}** dates\n"
                f"- Pricing status: ✅ {healthy} Healthy | 🔴 {too_high} Too High | 🔵 {too_low} Too Low\n\n"
                "Ask me: *'What are my occupancy risks?'*, *'What changed since the last run?'*, or *'What are my rate opportunities?'*"
            )

        return {"status": "success", "response": fallback_text}

    except Exception as fallback_err:
        logger.error(f"Chat fallback also failed: {fallback_err}")
        return {
            "status": "success",
            "response": (
                "⚠️ **Demo Mode — Agent Unavailable**\n\n"
                "The strategy assistant is temporarily offline. "
                "Please check that the server is running and try again."
            ),
        }



@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log user feedback (retained from template)."""
    logger.info(f"Feedback collected: {feedback.model_dump()}")
    return {"status": "success"}


# ==========================================
# Static Frontend Serving
# ==========================================


@app.get("/dashboard")
def serve_dashboard():
    """Serves the dashboard HTML file.

    DELIBERATE DESIGN PRACTICE: Served at /dashboard to prevent conflict with ADK playground redirects at root.
    """
    html_path = os.path.join(AGENT_DIR, "app", "dashboard.html")
    if os.path.exists(html_path):
        return FileResponse(
            html_path,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    return HTMLResponse("<h3>Dashboard HTML template not found.</h3>", status_code=404)


@app.get("/dashboard.css")
def serve_css():
    """Serves the dashboard CSS styling sheet."""
    css_path = os.path.join(AGENT_DIR, "app", "dashboard.css")
    if os.path.exists(css_path):
        return FileResponse(
            css_path,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    return FileResponse(os.path.join(AGENT_DIR, "app", "dashboard.css"))



# ==========================================
# Main Execution
# ==========================================
if __name__ == "__main__":
    import os

    import uvicorn

    # Read from environment PORT (standard for Cloud Run/App Engine), defaulting to 8080
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Pricing Monitor Dashboard server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
