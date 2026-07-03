import os
import sys
import json
import sqlite3
import datetime
import uuid
import random
from typing import Optional
from contextlib import aclosing
from fastapi import FastAPI, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx
from dotenv import load_dotenv

# Resolve paths
current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.dirname(current_dir)
backend_path = os.path.join(workspace_root, "signalsense_enterprise")

if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Load environment variables from the workspace root .env file in the frontend process
dotenv_path = os.path.join(workspace_root, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)

# Try importing ADK runner for in-process fallback
try:
    from google.genai import types
    from google.adk.runners import Runner
    from google.adk.apps import App
    from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.sessions import InMemorySessionService
    from signalsense_agent.agent import root_agent
    
    agentic_app = App(name="signalsense_enterprise", root_agent=root_agent)
    fallback_runner = Runner(
        app=agentic_app,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        credential_service=InMemoryCredentialService(),
        auto_create_session=True,
    )
except Exception as ie:
    fallback_runner = None
    print(f"ADK runner import failed, in-process fallback disabled: {ie}")

from jwt_helper import create_access_token, verify_access_token
from rate_limiter import RateLimitingMiddleware

app = FastAPI(title="Member Ambassador App")
app.add_middleware(RateLimitingMiddleware, max_requests=60, window_seconds=60)

# Get backend agent URL (defaults to localhost port 8080)
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8080")

def get_db_path():
    db_path = os.getenv("DB_PATH")
    if db_path:
        db_dir = os.path.dirname(db_path)
        if db_dir and os.path.exists(db_dir):
            return db_path
    db_path = os.path.join(workspace_root, "enterprise_db", "enterprise.db")
    if os.path.exists(db_path):
        return db_path
    return "enterprise.db"

# Helper to run database queries
def query_db(query, args=(), one=False):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(query, args)
    rv = cur.fetchall()
    conn.close()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(query, args)
    conn.commit()
    conn.close()


async def invoke_agent(signal_type: str, member_id: str, jti: Optional[str] = None, **kwargs) -> dict:
    payload = {
        "signal_type": signal_type,
        "member_id": member_id,
        **kwargs
    }
    
    # 1. Attempt HTTP call to standalone backend agent API on /run (with 30.0s timeout to allow LLM generation)
    try:
        async with httpx.AsyncClient() as client:
            adk_payload = {
                "app_name": "signalsense_agent",
                "user_id": "ambassador-app",
                "session_id": str(uuid.uuid4()),
                "new_message": {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(payload)
                        }
                    ]
                }
            }
            token_claims = {"member_id": member_id, "role": "Member"}
            if jti:
                token_claims["jti"] = jti
            token = create_access_token(token_claims)
            response = await client.post(
                f"{BACKEND_URL}/run",
                json=adk_payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0
            )
            response.raise_for_status()
            # Standard ADK response is a list of Event objects.
            # We return a placeholder success dictionary since the backend has executed and committed to the database.
            return {"status": "ok", "events": response.json()}
    except httpx.HTTPStatusError as hse:
        # For HTTP 401 or 403 authorization and quota errors, propagate directly without fallback
        error_detail = "Request rejected by backend"
        try:
            error_detail = hse.response.json().get("detail", error_detail)
        except Exception:
            pass
        raise ValueError(error_detail)
    except Exception as e:
        print(f"HTTP call to backend failed ({e}). Falling back to in-process execution...")
        
        # 2. In-process fallback execution
        if not fallback_runner:
            raise RuntimeError(f"Could not connect to backend and in-process fallback is disabled. Connection error: {e}")
            
        session_id = str(uuid.uuid4())
        message_text = json.dumps(payload)
        new_message = types.Content(
            role="user",
            parts=[types.Part(text=message_text)],
        )
        
        last_event = {}
        async with aclosing(
            fallback_runner.run_async(
                user_id="ambassador-app",
                session_id=session_id,
                new_message=new_message,
            )
        ) as agen:
            async for event in agen:
                if isinstance(event, dict):
                    last_event = event
                elif hasattr(event, "model_dump"):
                    last_event = event.model_dump()
                else:
                    last_event = {"event": str(event)}
        return last_event

def check_agent_rejection(result: dict):
    """Parses ADK Events list (from HTTP API) or single Event (from fallback runner)
    to check if the final workflow outcome status was 'Rejected' or 'Error'."""
    status_str = "Success"
    msg_str = ""
    
    # 1. HTTP API Response format: {"status": "ok", "events": [...]}
    if "events" in result and isinstance(result["events"], list):
        events = result["events"]
        if events:
            # Check the last event in the workflow execution
            for event in reversed(events):
                if isinstance(event, dict) and event.get("output"):
                    output_data = event["output"]
                    if isinstance(output_data, dict):
                        status_str = output_data.get("status", "Success")
                        msg_str = output_data.get("message", "")
                        break
                        
    # 2. In-Process Fallback/Direct Response format: Event dict
    elif "output" in result:
        output_data = result["output"]
        if isinstance(output_data, dict):
            status_str = output_data.get("status", "Success")
            msg_str = output_data.get("message", "")
            
    if status_str in ("Rejected", "Error"):
        raise ValueError(msg_str or "Your request was rejected by the system.")

# ------------------------------------------------------------------------------
# Frontend HTML Pages & Styling
# ------------------------------------------------------------------------------

GLASS_STYLE = """
<style>
    :root {
        --bg-primary: #080b16;
        --bg-radial: radial-gradient(circle at 50% 50%, #151a36 0%, #04060b 100%);
        --text-main: #f3f4f6;
        --text-muted: #9ca3af;
        --glass-bg: rgba(255, 255, 255, 0.03);
        --glass-border: rgba(255, 255, 255, 0.08);
        --accent-blue: #3b82f6;
        --accent-green: #10b981;
        --accent-red: #ef4444;
        --accent-yellow: #f59e0b;
        --glow-radial: radial-gradient(circle at 10% 20%, rgba(59, 130, 246, 0.15) 0%, transparent 50%);
    }

    * {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
    }

    body {
        font-family: 'Outfit', sans-serif;
        background: var(--bg-radial);
        color: var(--text-main);
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        overflow-x: hidden;
    }

    header {
        width: 100%;
        max-width: 1000px;
        padding: 24px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid var(--glass-border);
        backdrop-filter: blur(12px);
    }

    header h1 {
        font-size: 1.5rem;
        font-weight: 800;
        background: linear-gradient(135deg, #fff 0%, #88aaff 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    .user-pill {
        background: var(--glass-bg);
        border: 1px solid var(--glass-border);
        padding: 6px 16px;
        border-radius: 9999px;
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 0.9rem;
    }

    .user-avatar {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--accent-green);
        box-shadow: 0 0 8px var(--accent-green);
    }

    main {
        width: 100%;
        max-width: 1000px;
        padding: 32px 24px;
        flex-grow: 1;
        display: flex;
        flex-direction: column;
        gap: 32px;
    }

    .glass-card {
        background: var(--glass-bg);
        border: 1px solid var(--glass-border);
        border-radius: 20px;
        padding: 28px;
        backdrop-filter: blur(16px);
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4);
    }

    h2 {
        font-size: 1.25rem;
        font-weight: 700;
        margin-bottom: 20px;
        color: #fff;
        display: flex;
        align-items: center;
        gap: 10px;
    }

    .grid-2 {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 24px;
    }

    .dashboard-stats {
        display: flex;
        justify-content: space-around;
        align-items: center;
        padding: 16px;
        background: rgba(255,255,255,0.01);
        border-radius: 16px;
        border: 1px solid rgba(255,255,255,0.03);
    }

    .stat-box {
        text-align: center;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }

    .stat-val {
        font-size: 2.25rem;
        font-weight: 800;
        color: var(--accent-blue);
        text-shadow: 0 0 15px rgba(59, 130, 246, 0.3);
    }

    .stat-label {
        font-size: 0.8rem;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    .form-group {
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-bottom: 16px;
    }

    label {
        font-size: 0.85rem;
        color: var(--text-muted);
        font-weight: 600;
    }

    input, select, textarea {
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--glass-border);
        border-radius: 10px;
        padding: 12px;
        color: #fff;
        font-family: inherit;
        font-size: 0.95rem;
        transition: all 0.2s ease;
    }

    select option {
        background-color: #111827 !important;
        color: #ffffff !important;
    }

    input:focus, select:focus, textarea:focus {
        outline: none;
        border-color: var(--accent-blue);
        box-shadow: 0 0 8px rgba(59, 130, 246, 0.3);
    }

    .btn {
        background: var(--accent-blue);
        color: #fff;
        border: none;
        padding: 12px 24px;
        border-radius: 12px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.3s ease;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
    }

    .btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 0 15px rgba(59, 130, 246, 0.4);
    }

    .btn:active {
        transform: translateY(0);
    }

    .btn-green {
        background: var(--accent-green);
    }
    .btn-green:hover {
        box-shadow: 0 0 15px rgba(16, 185, 129, 0.4);
    }

    .item-list {
        display: flex;
        flex-direction: column;
        gap: 12px;
        max-height: 250px;
        overflow-y: auto;
        padding-right: 6px;
    }

    .item-row {
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--glass-border);
        border-radius: 12px;
        padding: 14px 20px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        transition: all 0.2s ease;
        cursor: pointer;
    }

    .item-row:hover {
        background: rgba(255,255,255,0.06);
        border-color: rgba(255,255,255,0.15);
    }

    .alert {
        padding: 16px;
        border-radius: 12px;
        border: 1px solid;
        margin-bottom: 24px;
        display: flex;
        align-items: center;
        gap: 12px;
        font-size: 0.95rem;
    }

    .alert-success {
        background: rgba(16, 185, 129, 0.1);
        border-color: rgba(16, 185, 129, 0.2);
        color: var(--accent-green);
    }

    .alert-danger {
        background: rgba(239, 68, 68, 0.1);
        border-color: rgba(239, 68, 68, 0.2);
        color: var(--accent-red);
    }

    .alert-warning {
        background: rgba(245, 158, 11, 0.1);
        border-color: rgba(245, 158, 11, 0.2);
        color: var(--accent-yellow);
    }

    ::-webkit-scrollbar {
        width: 6px;
    }
    ::-webkit-scrollbar-track {
        background: transparent;
    }
    @keyframes spin {{
        0% {{ transform: rotate(0deg); }}
        100% {{ transform: rotate(360deg); }}
    }}
    .spinner {{
        border: 4px solid rgba(255,255,255,0.1);
        border-top: 4px solid var(--accent-green);
        border-radius: 50%;
        width: 32px;
        height: 32px;
        animation: spin 1s linear infinite;
        margin: 0 auto;
    }}
</style>
"""

# ------------------------------------------------------------------------------
# 1. Login & Registration Page
# ------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def get_login(error: Optional[str] = None, demo_token: Optional[str] = None):
    members = query_db("SELECT MemberID, Name, Ambassador FROM members")
    
    options = ""
    for m in members:
        badge = " [Ambassador]" if m['Ambassador'] == "Yes" else ""
        options += f"<option value='{m['MemberID']}'>{m['Name']}{badge}</option>"
        
    alert_html = ""
    if error:
        alert_html = f"<div class='alert alert-danger' style='width: 100%; max-width: 900px; margin-bottom: 24px;'><strong>Error!</strong> {error}</div>"
        
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Ambassador App - Portal</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        {GLASS_STYLE}
    </head>
    <body>
        <div style="flex-grow: 1; display: flex; align-items: center; justify-content: center; width: 100%;">
            <div style="width: 100%; max-width: 900px; padding: 20px; display: flex; flex-direction: column; gap: 32px; align-items: center;">
                {alert_html}
                <div style="text-align: center;">
                    <h1 style="font-size: 2.75rem; font-weight: 800; background: linear-gradient(135deg, #fff 0%, #88aaff 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 12px;">SignalSense AI</h1>
                    <p style="color: var(--text-muted); font-size: 1.1rem;">Member Ambassador Portal</p>
                </div>
                <div style="display: flex; gap: 24px; width: 100%; align-items: stretch; justify-content: center; flex-wrap: wrap;">
                    <div class="glass-card" style="flex: 1; min-width: 300px; max-width: 440px; display: flex; flex-direction: column; justify-content: space-between;">
                        <div>
                            <h2 style="font-size: 1.5rem; font-weight: 600; margin-bottom: 8px;">Existing Member Login</h2>
                            <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 20px;">
                                Select your member profile to enter the dashboard, view rewards, or report out-of-stock items.
                            </p>
                            <form action="/login" method="POST">
                                <div class="form-group">
                                    <label for="login_member">Select Member Profile</label>
                                    <select name="member_id" id="login_member" required>
                                        {options}
                                    </select>
                                </div>
                                <button type="submit" class="btn" style="width: 100%; margin-top: 10px;">Enter Portal</button>
                            </form>
                        </div>
                    </div>
                    
                    <div class="glass-card" style="flex: 1; min-width: 300px; max-width: 440px;">
                        <h2 style="font-size: 1.5rem; font-weight: 600; margin-bottom: 8px;">New Member Registration</h2>
                        <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 20px;">
                            Create a brand new member profile to go through the invitation, terms acceptance, and enrollment pipeline.
                        </p>
                        <form action="/register" method="POST">
                            <div class="form-group">
                                <label for="reg_name">Full Name</label>
                                <input type="text" name="name" id="reg_name" placeholder="e.g. Sarah Jenkins" required>
                            </div>
                            <div class="form-group">
                                <label for="reg_club">Select Preferred Club</label>
                                <select name="club_id" id="reg_club" required>
                                    <option value="C100">Bentonville Sam's Club (C100)</option>
                                    <option value="C200">Rogers Sam's Club (C200)</option>
                                </select>
                            </div>
                            <button type="submit" class="btn btn-green" style="width: 100%; margin-top: 10px;">Register & Sign Up</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    if demo_token:
        # Generate token carrying jti
        token = create_access_token({"role": "Member", "jti": demo_token})
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            max_age=86400 * 7,
            samesite="lax"
        )
    return response

@app.post("/login")
async def do_login(request: Request, member_id: str = Form(...)):
    try:
        execute_db("DELETE FROM checkout_sessions WHERE MemberID = ?", (member_id,))
    except Exception as e:
        print(f"Database error during login checkout session cleanup: {e}")
    
    # Check if a demo_token / jti was set in the cookies
    old_token = request.cookies.get("session_token")
    jti = None
    if old_token:
        try:
            claims = verify_access_token(old_token)
            jti = claims.get("jti")
        except Exception:
            pass
            
    # Generate JWT token for Member role, carrying over the jti if present
    token_claims = {"member_id": member_id, "role": "Member"}
    if jti:
        token_claims["jti"] = jti
    token = create_access_token(token_claims)
    
    response = RedirectResponse(url=f"/dashboard?member_id={member_id}", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=3600,
        samesite="lax"
    )
    return response

@app.post("/register")
async def do_register(name: str = Form(...), club_id: str = Form(...)):
    # Clean the input name
    clean_name = name.strip()
    
    # Check if a member with this name already exists in the database (case-insensitive)
    existing = query_db("SELECT MemberID FROM members WHERE LOWER(Name) = ?", (clean_name.lower(),), one=True)
    if existing:
        error_msg = f"A member profile named '{clean_name}' already exists. Please choose another name or select them from the dropdown list."
        return RedirectResponse(url=f"/?error={error_msg}", status_code=status.HTTP_303_SEE_OTHER)
        
    member_id = f"M{random.randint(1006, 9999)}"
    today_str = datetime.date.today().isoformat()
    
    # 1. Insert new member in database (initial points = 0, initial trust = 0)
    execute_db(
        "INSERT INTO members (MemberID, Name, Address, City, State, Zip, TrustScore, SamsPoints, Ambassador, JoinDate) "
        "VALUES (?, ?, '123 Main St', 'Bentonville', 'AR', '72712', 0, 0, 'No', ?)",
        (member_id, clean_name, today_str)
    )
    
    # 2. Insert dummy receipt for this member
    receipt_id = f"R{random.randint(9001, 9999)}"
    execute_db(
        "INSERT INTO member_receipts (ReceiptID, MemberID, ClubID, PurchaseDate) "
        "VALUES (?, ?, ?, ?)",
        (receipt_id, member_id, club_id, today_str)
    )
    execute_db(
        "INSERT INTO receipt_details (ReceiptID, ItemID, Qty, Price) "
        "VALUES (?, 'I1001', 1, 3.98)",
        (receipt_id,)
    )
    execute_db(
        "INSERT INTO receipt_details (ReceiptID, ItemID, Qty, Price) "
        "VALUES (?, 'I1002', 2, 1.98)",
        (receipt_id,)
    )
    execute_db(
        "INSERT INTO receipt_details (ReceiptID, ItemID, Qty, Price) "
        "VALUES (?, 'I1003', 1, 4.99)",
        (receipt_id,)
    )
    
    return RedirectResponse(url=f"/enroll?member_id={member_id}", status_code=status.HTTP_303_SEE_OTHER)

# ------------------------------------------------------------------------------
# 2. Enrollment
# ------------------------------------------------------------------------------
@app.get("/enroll", response_class=HTMLResponse)
async def get_enroll(member_id: str):
    member = query_db("SELECT Name, Ambassador FROM members WHERE MemberID = ?", (member_id,), one=True)
    if not member:
        return RedirectResponse(url="/")
    if member['Ambassador'] == "Yes":
        return RedirectResponse(url=f"/dashboard?member_id={member_id}")
        
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Become an Ambassador</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        {GLASS_STYLE}
    </head>
    <body>
        <header>
            <h1>SignalSense AI</h1>
            <div class="user-pill">
                <span class="user-avatar" style="background: var(--accent-yellow); box-shadow: 0 0 8px var(--accent-yellow)"></span>
                <span>{member['Name']}</span>
            </div>
        </header>
        <main style="max-width: 600px; margin: 40px auto;">
            <div class="glass-card" style="text-align: center; display: flex; flex-direction: column; gap: 20px;">
                <h2 style="justify-content: center; font-size: 1.5rem; color: var(--accent-yellow)">✨ Join the Member Ambassador Program</h2>
                <p style="color: var(--text-muted); line-height: 1.6;">
                    Welcome, <strong>{member['Name']}</strong>! Our Ambassador Program empowers trusted members to report out-of-stock items, propose new products, and help clean our club shelves in real-time. 
                </p>
                <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--glass-border); border-radius: 12px; padding: 20px; text-align: left; font-size: 0.9rem; line-height: 1.6; margin-bottom: 10px;">
                    <strong style="color:#fff; display:block; margin-bottom:10px;">Program Terms & Benefits:</strong>
                    - Earn <strong>Sam's Points</strong> for verified out-of-stock signals.<br>
                    - Increase your <strong>Trust Score</strong> as your reports are confirmed.<br>
                    - Free items for the first couple of members who successfully propose a new item that they intend to buy.<br>
                    - Top ambassadors with highest points get access to special offers for end of season items at prices as low as 40% of original price.
                </div>
                <form action="/enroll" method="POST" style="display:flex; flex-direction:column; gap:12px;">
                    <input type="hidden" name="member_id" value="{member_id}">
                    <button type="submit" class="btn btn-green" style="width: 100%;">I Accept Terms - Create My Profile</button>
                    <a href="/dashboard?member_id={member_id}" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); text-decoration:none; text-align:center; width:100%; box-sizing:border-box; display:block; padding:12px; font-weight:600;">Decline & Go to Dashboard</a>
                </form>
            </div>
        </main>
    </body>
    </html>
    """
    return html

@app.post("/enroll")
async def do_enroll(member_id: str = Form(...)):
    # Update to Ambassador = 'Yes' (preserves any existing points and trust score in the profile)
    execute_db("UPDATE members SET Ambassador = 'Yes' WHERE MemberID = ?", (member_id,))
    return RedirectResponse(url=f"/dashboard?member_id={member_id}", status_code=status.HTTP_303_SEE_OTHER)

# ------------------------------------------------------------------------------
# 3. Main Dashboard
# ------------------------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(
    request: Request,
    member_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    warning: Optional[str] = None
):
    # Authenticate session via JWT cookie
    token = request.cookies.get("session_token")
    global token_valid
    token_valid = False
    jti = None
    if token:
        try:
            claims = verify_access_token(token)
            jti = claims.get("jti")
            if claims.get("member_id") == member_id:
                token_valid = True
        except Exception:
            pass
            
    member = query_db("SELECT Name, TrustScore, SamsPoints, Ambassador FROM members WHERE MemberID = ?", (member_id,), one=True)
    if not member:
        return RedirectResponse(url="/")
        
    # Automatically register check-in status at checkout counter on login/page load and start live inquiry if none exists
    existing_session = query_db("SELECT Status FROM checkout_sessions WHERE MemberID = ?", (member_id,), one=True)
    if not existing_session:
        execute_db(
            "INSERT INTO checkout_sessions (MemberID, Status, AssociateQuestion, MemberResponse, EnrollmentAnswer, MatchedItemID, LastUpdated) "
            "VALUES (?, 'PendingInquiry', 'Were you able to find everything you came to buy today?', NULL, NULL, NULL, ?)",
            (member_id, datetime.datetime.now().isoformat())
        )
        is_checked_in = True
    else:
        is_checked_in = existing_session['Status'] != 'Done'

    if not is_checked_in:
        checkout_status_html = f"""
        <div class="glass-card" style="display:flex; flex-direction:column; gap:12px; margin-bottom:20px; border-color: rgba(255, 255, 255, 0.15);">
            <h2 style="color:var(--text-muted)">📍 Checkout Counter Status</h2>
            <div style="display:flex; align-items:center; justify-content:space-between; background:rgba(255, 255, 255, 0.02); border:1px solid var(--glass-border); padding:14px; border-radius:10px; margin-top:4px; flex-wrap:wrap; gap:12px;">
                <div style="display:flex; align-items:center; gap:10px;">
                    <span style="font-size:1.1rem; color:var(--text-muted);">⚫</span>
                    <span style="color:var(--text-muted); font-size:0.9rem; font-weight:600; line-height:1.4;">Status: Not checked in at Checkout Counter.</span>
                </div>
                <button id="checkout_arrive_btn" onclick="arriveAtCheckoutStation()" class="btn btn-blue" style="margin:0; padding:8px 20px; font-weight:700;">Check In</button>
            </div>
        </div>
        """
    else:
        checkout_status_html = f"""
        <div class="glass-card" style="display:flex; flex-direction:column; gap:12px; margin-bottom:20px; border-color: rgba(59, 130, 246, 0.25);">
            <h2 style="color:var(--accent-blue)">📍 Checkout Counter Status</h2>
            <div style="display:flex; align-items:center; gap:10px; background:rgba(16, 185, 129, 0.08); border:1px solid rgba(16, 185, 129, 0.3); padding:14px; border-radius:10px; margin-top:4px;">
                <span style="font-size:1.1rem; color:#10B981;">🟢</span>
                <span style="color:#fff; font-size:0.9rem; font-weight:600; line-height:1.4;">Status: Checked in at Checkout Counter. The Checkout Associate has been notified and will initiate the checkout inquiry shortly.</span>
            </div>
        </div>
        """
        
    is_ambassador = member['Ambassador'] == "Yes"
    
    promo_banner = ""
    if not is_ambassador:
        promo_banner = f"""
        <div id="promo_ambassador_banner" class="glass-card" style="background:rgba(245, 158, 11, 0.08); border:1px dashed rgba(245, 158, 11, 0.4); padding:16px; border-radius:12px; margin-bottom:20px; text-align:center; position:relative;">
            <button type="button" style="position:absolute; top:12px; right:16px; background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:1.1rem;" onclick="document.getElementById('promo_ambassador_banner').style.display='none'">✕</button>
            <span style="font-size:1.1rem; color:var(--accent-yellow); font-weight:700; display:block; margin-bottom:4px;">🌟 Unlock Sam's Points & Exclusive Rewards!</span>
            <p style="color:var(--text-muted); font-size:0.9rem; margin:0 0 12px 0; line-height:1.4;">Join the Member Ambassador program to report out-of-stock items, propose new products, and boost your Trust Score.</p>
            <div style="display:flex; justify-content:center; gap:12px; align-items:center; flex-wrap:wrap;">
                <a href="/enroll?member_id={member_id}" class="btn btn-green" style="display:inline-block; text-decoration:none; padding:8px 20px; font-weight:700; margin:0;">Join Ambassador Program</a>
                <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:8px 20px; font-weight:600; margin:0;" onclick="document.getElementById('promo_ambassador_banner').style.display='none'">No, Thank You</button>
            </div>
        </div>
        """
        
    receipts = query_db("SELECT ReceiptID, PurchaseDate FROM member_receipts WHERE MemberID = ?", (member_id,))
    signals = query_db(
        "SELECT s.SignalID, s.SignalType, s.ItemID, s.Status, s.Created, "
        "COALESCE(i.ItemDescription, cp.ItemDescription) AS ProductDescription "
        "FROM signals s "
        "LEFT JOIN items i ON s.ItemID = i.ItemID "
        "LEFT JOIN candidate_products cp ON s.CandidateID = cp.CandidateID "
        "WHERE s.MemberID = ? ORDER BY s.Created DESC",
        (member_id,)
    )
    products = query_db(
        "SELECT CandidateID, ItemDescription, StoreWhereFound, UpVotes, Status FROM candidate_products ORDER BY UpVotes DESC"
    )
    
    receipt_options = ""
    for r in receipts:
        receipt_options += f"<option value='{r['ReceiptID']}'>Receipt #{r['ReceiptID']} ({r['PurchaseDate']})</option>"

    # 1. Voice Signal Assistant Card content (Active for everyone!)
    voice_card_html = f"""
            <p style="color:var(--text-muted); font-size:0.9rem;">
                Speak into your microphone or choose a preset below to report an out-of-stock item (e.g. "I couldn't find organic bananas") or request a product suggestion (e.g. "We need kimchi").
            </p>
            {"<div style='background:rgba(245,158,11,0.06); border:1px solid rgba(245,158,11,0.15); padding:10px; border-radius:8px; font-size:0.85rem; line-height:1.4; color:var(--accent-yellow); margin-bottom:12px;'>⚠️ <strong>Note:</strong> Since you are not in the Ambassador program, submissions will not earn Sam's Points. <a href='/enroll?member_id=" + member_id + "' style='color:#fff; font-weight:700;'>Join program now</a>.</div>" if not is_ambassador else ""}
            <div style="display:flex; gap:16px; align-items:center; margin-bottom:8px;">
                <button type="button" class="btn" id="mic_btn" style="background:var(--accent-red); width:52px; height:52px; border-radius:50%; padding:0; display:flex; align-items:center; justify-content:center; font-size:1.6rem; cursor:pointer;" onclick="toggleSpeech()">🎙️</button>
                <div id="speech_status" style="color:var(--text-muted); font-size:0.9rem; font-weight:600;">Click microphone to start speaking...</div>
            </div>
            
            <form action="/process-member-voice" method="POST" style="display:flex; flex-direction:column; gap:12px;">
                <input type="hidden" name="member_id" value="{member_id}">
                <textarea name="transcript" id="voice_transcript" style="height:70px; background:rgba(255,255,255,0.04); border:1px solid var(--glass-border); color:#fff; padding:12px; border-radius:10px; width:100%; box-sizing:border-box; font-family:inherit; font-size:0.95rem; outline:none; resize:none;" placeholder="Your spoken text will be transcribed here..." required></textarea>
                
                <button type="submit" class="btn btn-green" style="width:100%; padding:12px; font-weight:700;">Submit Voice Signal</button>
            </form>

            <div style="margin-top:10px; border-top:1px solid var(--glass-border); padding-top:16px; display:flex; flex-direction:column; gap:10px;">
                <span style="font-size:0.8rem; color:var(--text-muted); font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">Simulate Speech Presets:</span>
                <div style="display:flex; flex-wrap:wrap; gap:10px;">
                    <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:8px 16px; font-size:0.8rem;" onclick="simulateVoice('I couldn\'t find any organic bananas on the shelf.')">"I couldn't find organic bananas" (OOS)</button>
                    <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:8px 16px; font-size:0.8rem;" onclick="simulateVoice('We need organic whole ginger in the produce section.')">"We need organic whole ginger" (OOS)</button>
                    <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:8px 16px; font-size:0.8rem;" onclick="simulateVoice('I wish the club sold fresh organic kimchi in the produce department.')">"I wish the club sold kimchi" (Suggestion)</button>
                </div>
            </div>
    """

    # 2. Report Out-of-Stock Item Card content
    if is_ambassador:
        oos_card_html = f"""
                    <h2>🚨 Report Out-of-Stock Item</h2>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin-bottom:12px;">
                        Select a past receipt purchase to report a shelf gap:
                    </p>
                    <form action="/report-oos-select" method="POST">
                        <input type="hidden" name="member_id" value="{member_id}">
                        <div class="form-group">
                            <label for="receipt_id">Select Previous Receipt</label>
                            <select name="receipt_id" id="receipt_id" class="text-input" style="width:100%;" required>
                                {receipt_options}
                            </select>
                        </div>
                        <button type="submit" class="btn" style="width: 100%; margin-top:10px;">View Items on Receipt</button>
                    </form>
        """
    else:
        oos_card_html = f"""
                    <h2>🚨 Report Out-of-Stock Item</h2>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin-bottom:12px;">
                        Select a past receipt purchase to report a shelf gap:
                    </p>
                    <div style="background:rgba(245,158,11,0.06); border:1px solid rgba(245,158,11,0.2); padding:12px; border-radius:10px; font-size:0.85rem; line-height:1.4; color:var(--accent-yellow); margin-bottom:12px;">
                        ⚠️ <strong>Notice:</strong> Since you are not in the Ambassador program, this report will not earn Sam's Points. <a href="/enroll?member_id={member_id}" style="color:#fff; font-weight:700;">Join program now</a>.
                    </div>
                    <form action="/report-oos-select" method="POST">
                        <input type="hidden" name="member_id" value="{member_id}">
                        <div class="form-group">
                            <label for="receipt_id">Select Previous Receipt</label>
                            <select name="receipt_id" id="receipt_id" class="text-input" style="width:100%;" required>
                                {receipt_options}
                            </select>
                        </div>
                        <button type="submit" class="btn" style="width: 100%; margin-top:10px;">View Items on Receipt</button>
                    </form>
        """

    # 3. Suggest New Product Card content
    if is_ambassador:
        suggest_card_html = f"""
                    <h2>💡 Suggest New Product</h2>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin-bottom:12px;">
                        Can't find a product you want? Suggest it to our merchants:
                    </p>
                    <form action="/suggest-product" method="POST" style="display:flex; flex-direction:column; gap:12px;">
                        <input type="hidden" name="member_id" value="{member_id}">
                        <div class="form-group">
                            <label for="description">Product Description</label>
                            <input type="text" name="description" id="description" class="text-input" style="width:100%; box-sizing:border-box;" placeholder="e.g. Frozen Garlic Naan" required>
                        </div>
                        <div class="form-group">
                            <label for="store">Store Where Found (Optional)</label>
                            <input type="text" name="store" id="store" class="text-input" style="width:100%; box-sizing:border-box;" placeholder="e.g. Patel Brothers">
                        </div>
                        <div class="form-group">
                            <label for="reason">Reason for Request (Optional)</label>
                            <textarea name="reason" id="reason" class="text-input" style="width:100%; height:60px; box-sizing:border-box; resize:none;" placeholder="e.g. High demand in our local community"></textarea>
                        </div>
                        <button type="submit" class="btn btn-green" style="width: 100%;">Submit Proposal</button>
                    </form>
        """
    else:
        suggest_card_html = f"""
                    <h2>💡 Suggest New Product</h2>
                    <div style="text-align:center; padding:40px 10px; color:var(--text-muted); display:flex; flex-direction:column; gap:12px; align-items:center;">
                        <span style="font-size:2rem;">💡🔒</span>
                        <h3 style="color:#fff; margin:0;">Suggestions Locked</h3>
                        <p style="font-size:0.85rem; max-width:300px; margin:0; line-height:1.4;">Unlock the ability to propose new products by joining the Ambassador program!</p>
                        <a href="/enroll?member_id={member_id}" class="btn btn-green" style="padding:8px 16px; font-weight:700; font-size:0.85rem; text-decoration:none;">Join Program</a>
                    </div>
        """

    alert_html = promo_banner
    if success:
        alert_html += f"<div class='alert alert-success'><strong>Success!</strong> {success}</div>"
    elif error:
        alert_html = f"<div class='alert alert-danger'><strong>Error!</strong> {error}</div>"
    elif warning:
        alert_html = f"<div class='alert alert-warning'><strong>Notice:</strong> {warning}</div>"

    signals_rows = ""
    if not signals:
        signals_rows = "<tr><td colspan='4' style='text-align:center; padding:20px; color:var(--text-muted);'>No signals submitted yet.</td></tr>"
    else:
        for sig in signals:
            status_color = "var(--accent-yellow)"
            if sig['Status'] == "Success" or sig['Status'] == "Pending":
                status_color = "var(--accent-green)"
            elif sig['Status'] == "Rejected" or sig['Status'] == "Unverified":
                status_color = "var(--accent-red)"
                
            item_desc = sig['ProductDescription'] or "N/A"
            signals_rows += f"""
            <tr style="border-bottom:1px solid var(--glass-border)">
                <td style="padding:12px; font-weight:600;">{sig['SignalID']}</td>
                <td style="padding:12px; color:var(--text-muted);">{sig['SignalType']}</td>
                <td style="padding:12px;">{item_desc}</td>
                <td style="padding:12px; color:{status_color}; font-weight:600;">{sig['Status']}</td>
            </tr>
            """

    trending_cards = ""
    if not products:
        trending_cards = "<p style='color:var(--text-muted); text-align:center; grid-column:1/-1;'>No proposed products yet.</p>"
    else:
        for prod in products:
            status_badge = "risk-low"
            if prod['Status'] == "Threshold Crossed":
                status_badge = "risk-high"
            elif prod['Status'] == "Trending":
                status_badge = "risk-medium"
                
            trending_cards += f"""
            <div class="item-row" style="cursor:default;">
                <div style="display:flex; flex-direction:column; gap:4px;">
                    <span style="font-weight:600; color:#fff;">{prod['ItemDescription']}</span>
                    <span style="font-size:0.8rem; color:var(--text-muted);">Proposer Store: {prod['StoreWhereFound']}</span>
                </div>
                <div style="display:flex; align-items:center; gap:16px;">
                    <span class="risk-badge {status_badge}" style="font-size:0.75rem;">{prod['Status']}</span>
                    <form action="/upvote" method="POST">
                        <input type="hidden" name="member_id" value="{member_id}">
                        <input type="hidden" name="candidate_id" value="{prod['CandidateID']}">
                        <button type="submit" class="btn btn-green" style="padding: 6px 12px; font-size:0.8rem;">👍 {prod['UpVotes']}</button>
                    </form>
                </div>
            </div>
            """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Ambassador Dashboard</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        {GLASS_STYLE}
        <style>
            .risk-badge {{
                padding: 4px 10px;
                border-radius: 9999px;
                font-size: 0.8rem;
                font-weight: 700;
                text-transform: uppercase;
                display: inline-block;
            }}
            .risk-low {{
                background: rgba(16, 185, 129, 0.15);
                color: var(--accent-green);
                border: 1px solid rgba(16, 185, 129, 0.3);
            }}
            .risk-medium {{
                background: rgba(245, 158, 11, 0.15);
                color: var(--accent-yellow);
                border: 1px solid rgba(245, 158, 11, 0.3);
            }}
            .risk-high {{
                background: rgba(59, 130, 246, 0.15);
                color: var(--accent-blue);
                border: 1px solid rgba(59, 130, 246, 0.3);
            }}
        </style>
    </head>
    <body>
        <header>
            <h1>SignalSense AI</h1>
            <div style="display:flex; gap:12px; align-items:center;">
                <div class="user-pill">
                    <span class="user-avatar"></span>
                    <span>{member['Name']}</span>
                </div>
                <a href="/" style="color:var(--text-muted); font-size:0.9rem; text-decoration:none;">Logout</a>
            </div>
        </header>
        
        <main>
            {alert_html}
            
            <div class="glass-card dashboard-stats">
                <div class="stat-box">
                    <span class="stat-val">{member['TrustScore']}%</span>
                    <span class="stat-label">Trust Score</span>
                </div>
                <div style="width:1px; height:40px; background:var(--glass-border)"></div>
                <div class="stat-box">
                    <span class="stat-val">{member['SamsPoints']}</span>
                    <span class="stat-label">Sam's Points</span>
                </div>
            </div>
            
            <!-- Voice Signal Assistant -->
            <div class="glass-card" style="display:flex; flex-direction:column; gap:16px; margin-bottom:20px;">
                <h2 style="color:var(--accent-blue)">🎙️ Voice Signal Assistant</h2>
                {voice_card_html}
            </div>

            <!-- Checkout Station Check-in -->
            {checkout_status_html}

            <script>
                let recognition;
                let isListening = false;
                
                if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {{
                    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
                    recognition = new SpeechRecognition();
                    recognition.continuous = false;
                    recognition.interimResults = false;
                    recognition.lang = 'en-US';
                    
                    recognition.onstart = function() {{
                        isListening = true;
                        document.getElementById('mic_btn').style.background = '#10b981'; // Green
                        document.getElementById('speech_status').innerText = '🎙️ Listening... Speak now!';
                    }};
                    
                    recognition.onend = function() {{
                        isListening = false;
                        document.getElementById('mic_btn').style.background = '#ef4444'; // Red
                    }};
                    
                    recognition.onresult = function(event) {{
                        const transcript = event.results[0][0].transcript;
                        document.getElementById('voice_transcript').value = transcript;
                        document.getElementById('speech_status').innerText = '✓ Speech captured successfully!';
                    }};
                    
                    recognition.onerror = function(event) {{
                        document.getElementById('speech_status').innerText = '⚠️ Speech error: ' + event.error;
                    }};
                }} else {{
                    document.getElementById('speech_status').innerText = '⚠️ Native speech-to-text not supported in this browser.';
                }}
                
                function toggleSpeech() {{
                    if (!recognition) return;
                    if (isListening) {{
                        recognition.stop();
                    }} else {{
                        recognition.start();
                    }}
                }}
                
                function simulateVoice(text) {{
                    document.getElementById('voice_transcript').value = text;
                    document.getElementById('speech_status').innerText = '✓ Simulated voice preset selected!';
                }}

                // Cross-App Live Voice Polling Setup
                const currentMemberId = "{member_id}";
                let memberLivePollInterval = null;
                let activeQuestionPlayed = "";
                let liveSpeechRecognition;
                let isLiveListening = false;
                let activeField = "response"; // 'response' or 'enrollment'
                let isLocalProgressActive = false;
                
                function initLivePolling() {{
                    memberLivePollInterval = setInterval(pollCheckoutSession, 1500);
                }}
                
                function pollCheckoutSession() {{
                    fetch('/checkout/poll-member?member_id=' + currentMemberId)
                    .then(res => res.json())
                    .then(data => {{
                        if (data.status === 'none' || !data.status || data.status === 'Done') {{
                            const modalVisible = document.getElementById('live_checkout_modal').style.display === 'flex';
                            document.getElementById('live_checkout_modal').style.display = 'none';
                            if (liveSpeechRecognition) {{
                                try {{
                                    liveSpeechRecognition.stop();
                                }} catch(e) {{}}
                            }}
                            if ('speechSynthesis' in window) {{
                                window.speechSynthesis.cancel();
                            }}
                            if (modalVisible) {{
                                window.location.reload();
                            }}
                            return;
                        }}
                        
                        document.getElementById('live_checkout_modal').style.display = 'flex';
                        if (isLocalProgressActive) {{
                            if (data.status === 'EnrollmentComplete') {{
                                // Enrollment done! Show enrollment confirmation, wait for TTS, then show checkout progress
                                isLocalProgressActive = false;
                                document.getElementById('checkout_progress_group').style.display = 'none';
                                
                                // Show enrollment success message
                                document.getElementById('member_question_text').innerText = '"' + data.associate_question + '"';
                                document.getElementById('member_mic_btn').parentElement.style.display = 'none';
                                document.getElementById('member_response_transcript').parentElement.style.display = 'none';
                                document.getElementById('member_live_presets').style.display = 'none';
                                document.getElementById('completed_actions_group').style.display = 'none';
                                
                                // Play TTS, then transition to checkout progress ONLY after speech ends
                                if (activeQuestionPlayed !== data.associate_question) {{
                                    activeQuestionPlayed = data.associate_question;
                                    speakThenDo(data.associate_question, function() {{
                                        isLocalProgressActive = true;
                                        document.getElementById('checkout_progress_group').style.display = 'flex';
                                        document.getElementById('checkout_progress_text').innerText = 'Now processing your checkout and submitting your stock report...';
                                        document.getElementById('member_question_text').innerText = 'Continuing with checkout... Please wait.';
                                    }});
                                }}
                                return;
                            }} else if (data.status === 'EnrollmentProposed') {{
                                isLocalProgressActive = false;
                                document.getElementById('checkout_progress_group').style.display = 'none';
                                // Fall through to normal rendering below
                            }} else if (data.status === 'Completed') {{
                                isLocalProgressActive = false;
                                document.getElementById('checkout_progress_group').style.display = 'none';
                                // Fall through to normal rendering below
                            }} else {{
                                document.getElementById('checkout_progress_group').style.display = 'flex';
                                document.getElementById('completed_actions_group').style.display = 'none';
                                return;
                            }}
                        }}
                        
                        // Normal rendering (not in progress lock)
                        document.getElementById('checkout_progress_group').style.display = 'none';
                        document.getElementById('member_question_text').innerText = '"' + data.associate_question + '"';
                        
                        if (data.status === 'Completed') {{
                            document.getElementById('member_mic_btn').parentElement.style.display = 'none';
                            document.getElementById('member_response_transcript').parentElement.style.display = 'none';
                            document.getElementById('member_live_presets').style.display = 'none';
                            document.getElementById('completed_actions_group').style.display = 'flex';
                        }} else if (data.status === 'EnrollmentComplete') {{
                            // EnrollmentComplete reached without progress lock (shouldn't happen but handle gracefully)
                            document.getElementById('member_mic_btn').parentElement.style.display = 'none';
                            document.getElementById('member_response_transcript').parentElement.style.display = 'none';
                            document.getElementById('member_live_presets').style.display = 'none';
                            document.getElementById('completed_actions_group').style.display = 'none';
                        }} else {{
                            document.getElementById('member_mic_btn').parentElement.style.display = 'flex';
                            document.getElementById('member_response_transcript').parentElement.style.display = 'block';
                            document.getElementById('member_live_presets').style.display = 'flex';
                            document.getElementById('completed_actions_group').style.display = 'none';
                            
                            if (data.status === 'PendingInquiry' || data.status === 'InquirySent') {{
                                activeField = "response";
                                document.getElementById('checkout_presets_group').style.display = 'flex';
                                document.getElementById('enrollment_presets_group').style.display = 'none';
                            }} else if (data.status === 'EnrollmentProposed') {{
                                activeField = "enrollment";
                                document.getElementById('checkout_presets_group').style.display = 'none';
                                document.getElementById('enrollment_presets_group').style.display = 'flex';
                            }}
                        }}
                        
                        if (activeQuestionPlayed !== data.associate_question) {{
                            activeQuestionPlayed = data.associate_question;
                            document.getElementById('member_response_transcript').value = '';
                            document.getElementById('member_speech_status').innerText = 'Click microphone to reply by voice...';
                            if (data.status === 'Completed') {{
                                speakThenDo(data.associate_question, null);
                            }} else {{
                                speakQuestion(data.associate_question);
                            }}
                        }}
                    }})
                    .catch(err => {{
                        console.error("Polling connection error:", err);
                        isLocalProgressActive = false;
                        document.getElementById('live_checkout_modal').style.display = 'none';
                        if (liveSpeechRecognition) {{
                            try {{ liveSpeechRecognition.stop(); }} catch(e) {{}}
                        }}
                        if ('speechSynthesis' in window) {{
                            window.speechSynthesis.cancel();
                        }}
                        // Stop the interval to prevent multiple alert boxes
                        if (memberLivePollInterval) {{
                            clearInterval(memberLivePollInterval);
                            memberLivePollInterval = null;
                        }}
                        alert("⚠️ A connection error occurred during checkout. Returning to dashboard.");
                    }});
                }}
                
                function speakQuestion(text) {{
                    if ('speechSynthesis' in window) {{
                        window.speechSynthesis.cancel();
                        const utterance = new SpeechSynthesisUtterance(text);
                        utterance.rate = 1.0;
                        utterance.pitch = 1.0;
                        utterance.onend = function() {{
                            try {{
                                if (liveSpeechRecognition && !isLiveListening) {{
                                    liveSpeechRecognition.start();
                                }}
                            }} catch(e) {{
                                console.error("Speech recognition start failed:", e);
                            }}
                        }};
                        window.speechSynthesis.speak(utterance);
                    }}
                }}
                
                function speakThenDo(text, callback) {{
                    if ('speechSynthesis' in window) {{
                        window.speechSynthesis.cancel();
                        const utterance = new SpeechSynthesisUtterance(text);
                        utterance.rate = 1.0;
                        utterance.pitch = 1.0;
                        utterance.onend = function() {{
                            if (callback) callback();
                        }};
                        window.speechSynthesis.speak(utterance);
                    }} else {{
                        if (callback) setTimeout(callback, 2000);
                    }}
                }}
                
                if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {{
                    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
                    liveSpeechRecognition = new SpeechRecognition();
                    liveSpeechRecognition.continuous = false;
                    liveSpeechRecognition.interimResults = false;
                    liveSpeechRecognition.lang = 'en-US';
                    
                    liveSpeechRecognition.onstart = function() {{
                        isLiveListening = true;
                        document.getElementById('member_mic_btn').style.background = '#10b981';
                        document.getElementById('member_speech_status').innerText = '🎙️ Listening to reply... Speak now!';
                    }};
                    
                    liveSpeechRecognition.onend = function() {{
                        isLiveListening = false;
                        document.getElementById('member_mic_btn').style.background = '#ef4444';
                        const statusElem = document.getElementById('member_speech_status');
                        if (statusElem && (statusElem.innerText.includes('Listening') || statusElem.innerText.includes('Speech error') || statusElem.innerText.includes('Microphone inactive'))) {{
                            statusElem.innerText = '🎙️ Microphone inactive. Click microphone to reply.';
                        }}
                    }};
                    
                    liveSpeechRecognition.onresult = function(event) {{
                        const transcript = event.results[0][0].transcript;
                        document.getElementById('member_response_transcript').value = transcript;
                        document.getElementById('member_speech_status').innerText = '✓ Speech captured.';
                        submitMemberLiveResponse(transcript);
                    }};
                    
                    liveSpeechRecognition.onerror = function(event) {{
                        if (event.error === 'not-allowed') {{
                            document.getElementById('member_speech_status').innerText = '⚠️ Microphone access blocked. Enable browser permissions to speak.';
                        }} else {{
                            document.getElementById('member_speech_status').innerText = '⚠️ Speech error: ' + event.error;
                        }}
                        if (event.error === 'no-speech') {{
                            setTimeout(() => {{
                                if (document.getElementById('live_checkout_modal').style.display === 'flex' && !isLocalProgressActive) {{
                                    try {{
                                        liveSpeechRecognition.start();
                                    } catch(e) {{}}
                                }}
                            }}, 500);
                        }}
                    }};
                }} else {{
                    // Set unsupported browser state immediately on DOM load
                    setTimeout(() => {{
                        const statusElem = document.getElementById('member_speech_status');
                        if (statusElem) {{
                            statusElem.innerText = '🎙️ Speech recognition not supported. Use presets below.';
                        }}
                        const micBtn = document.getElementById('member_mic_btn');
                        if (micBtn) {{
                            micBtn.style.background = '#4b5563';
                            micBtn.style.cursor = 'not-allowed';
                        }}
                    }}, 500);
                }}
                
                function toggleMemberLiveSpeech() {{
                    if (!liveSpeechRecognition) return;
                    if (isLiveListening) {{
                        liveSpeechRecognition.stop();
                    }} else {{
                        liveSpeechRecognition.start();
                    }}
                }}
                
                function simulateMemberLiveResponse(text) {{
                    document.getElementById('member_response_transcript').value = text;
                    submitMemberLiveResponse(text);
                }}
                
                function submitMemberLiveResponse(text) {{
                    const ans = text.toLowerCase();
                    const isClosingAction = activeField === "enrollment" && (ans.includes('yes') || ans.includes('no') || ans.includes('decline') || ans.includes('thank'));
                    const isOOSSubmit = activeField === "response" && (ans.includes('no') || ans.includes('not') || ans.includes('missing') || ans.includes('find') || ans.includes('gala') || ans.includes('apple') || ans.includes('banana'));
                    
                    if (isClosingAction || isOOSSubmit) {{
                        isLocalProgressActive = true;
                        document.getElementById('member_question_text').innerText = "Processing checkout... Please wait.";
                        document.getElementById('member_mic_btn').parentElement.style.display = 'none';
                        document.getElementById('member_response_transcript').parentElement.style.display = 'none';
                        document.getElementById('member_live_presets').style.display = 'none';
                        document.getElementById('completed_actions_group').style.display = 'none';
                        document.getElementById('checkout_progress_group').style.display = 'flex';
                        if (isClosingAction && ans.includes('yes')) {{
                            document.getElementById('checkout_progress_text').innerText = "Processing your Ambassador sign-up...";
                        }} else if (isOOSSubmit) {{
                            document.getElementById('checkout_progress_text').innerText = "Analyzing your response...";
                        }} else {{
                            document.getElementById('checkout_progress_text').innerText = "Processing checkout...";
                        }}
                    }}
                    
                    const formData = new FormData();
                    formData.append('member_id', currentMemberId);
                    formData.append('response', text);
                    formData.append('field', activeField);
                    
                    fetch('/checkout/respond-member', {{
                        method: 'POST',
                        body: formData
                    }})
                    .then(res => res.json())
                    .then(data => {{
                        document.getElementById('member_speech_status').innerText = '✓ Response synced back to Associate App.';
                        if (!isLocalProgressActive) {{
                            if (activeField === "response" && (ans.includes('yes') || ans.includes('everything') || ans.includes('got all') || ans.includes('thank'))) {{
                                document.getElementById('live_checkout_modal').style.display = 'none';
                                activeQuestionPlayed = "";
                                window.location.reload();
                            }}
                        }}
                    }})
                    .catch(err => {{
                        console.error("Submit response error:", err);
                        isLocalProgressActive = false;
                        document.getElementById('checkout_progress_group').style.display = 'none';
                        document.getElementById('live_checkout_modal').style.display = 'none';
                        alert("⚠️ Failed to send response to counter. Returning to dashboard.");
                        window.location.reload();
                    }});
                }}
                
                function closeCompletedCheckout() {{
                    const formData = new FormData();
                    formData.append('member_id', currentMemberId);
                    
                    fetch('/checkout/complete-close', {{
                        method: 'POST',
                        body: formData
                    }})
                    .then(() => {{
                        window.location.reload();
                    }})
                    .catch(err => {{
                        console.error("Close session error:", err);
                        window.location.reload();
                    }});
                }}
                
                function arriveAtCheckoutStation() {{
                    const formData = new FormData();
                    formData.append('member_id', currentMemberId);
                    
                    fetch('/checkout/arrive', {{
                        method: 'POST',
                        body: formData
                    }})
                    .then(res => res.json())
                    .then(data => {{
                        const btn = document.getElementById('checkout_arrive_btn');
                        if (btn) {{
                            btn.innerText = '✓ Checked In...';
                            btn.style.background = '#10b981';
                            btn.disabled = true;
                        }}
                        setTimeout(() => {{
                            window.location.reload();
                        }}, 1500);
                    }})
                    .catch(err => {{
                        console.error("Arrive error:", err);
                        alert("⚠️ Failed to check in. Please try again.");
                        const btn = document.getElementById('checkout_arrive_btn');
                        if (btn) {{
                            btn.innerText = 'Check In';
                            btn.disabled = false;
                        }}
                    }});
                }}
                
                window.addEventListener('DOMContentLoaded', () => {{
                    initLivePolling();
                }});
            </script>

            <div class="grid-2">
                <div class="glass-card" style="display:flex; flex-direction:column; gap:16px;">
                    {oos_card_html}
                </div>
                
                <div class="glass-card">
                    {suggest_card_html}
                </div>
            </div>
            
            <div class="glass-card">
                <h2>📈 Trending Candidate Products</h2>
                <div style="display:flex; flex-direction:column; gap:12px;">
                    {trending_cards}
                </div>
            </div>

            <div class="glass-card">
                <h2>📋 My Signal Submissions</h2>
                <table style="width:100%; border-collapse:collapse; text-align:left; font-size:0.95rem;">
                    <thead>
                        <tr style="border-bottom:2px solid var(--glass-border); color:var(--text-muted)">
                            <th style="padding:12px;">SignalID</th>
                            <th style="padding:12px;">Type</th>
                            <th style="padding:12px;">ItemID/Product</th>
                            <th style="padding:12px;">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {signals_rows}
                    </tbody>
                </table>
            </div>

            <!-- Live Checkout Overlay Modal -->
            <div id="live_checkout_modal" class="modal" style="display:none; justify-content:center; align-items:center; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.65); backdrop-filter:blur(8px);">
                <div class="modal-content" style="background:rgba(25,30,45,0.95); border:1px solid var(--glass-border); padding:30px; border-radius:16px; width:450px; max-width:90%; display:flex; flex-direction:column; gap:20px; box-shadow:0 8px 32px rgba(0,0,0,0.65);">
                    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--glass-border); padding-bottom:12px;">
                        <h2 style="color:var(--accent-blue); margin:0; display:flex; align-items:center; gap:8px;">🛒 Live Checkout Assistant</h2>
                        <span id="member_live_badge" style="background:#f59e0b; color:#fff; padding:4px 10px; border-radius:12px; font-size:0.75rem; font-weight:700; text-transform:uppercase;">Active Inquiry</span>
                    </div>
                    
                    <div style="display:flex; flex-direction:column; gap:8px;">
                        <div style="font-size:0.8rem; color:var(--text-muted); font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">Associate Question:</div>
                        <div id="member_question_text" style="font-size:1.1rem; color:#fff; font-weight:600; line-height:1.4;">"Were you able to find everything you came to buy today?"</div>
                    </div>

                    <div style="display:flex; gap:16px; align-items:center; background:rgba(255,255,255,0.02); padding:12px; border-radius:10px; border:1px solid var(--glass-border);">
                        <button type="button" class="btn" id="member_mic_btn" style="background:#ef4444; width:46px; height:46px; border-radius:50%; padding:0; display:flex; align-items:center; justify-content:center; font-size:1.4rem; cursor:pointer;" onclick="toggleMemberLiveSpeech()">🎙️</button>
                        <div id="member_speech_status" style="color:var(--text-muted); font-size:0.85rem; font-weight:600;">Click microphone to reply by voice...</div>
                    </div>
                    
                    <div>
                        <label style="font-size:0.8rem; color:var(--text-muted); font-weight:700; display:block; margin-bottom:6px; text-transform:uppercase;">Your Spoken Response:</label>
                        <textarea id="member_response_transcript" class="text-input" style="height:60px; width:100%; box-sizing:border-box; outline:none; resize:none; font-family:inherit; padding:10px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid var(--glass-border);" placeholder="Your spoken or selected response will appear here..."></textarea>
                    </div>

                    <div id="member_live_presets" style="border-top:1px solid var(--glass-border); padding-top:14px; display:flex; flex-direction:column; gap:8px;">
                        <span style="font-size:0.75rem; color:var(--text-muted); font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">Simulate Spoken Response Presets:</span>
                        <div style="display:flex; flex-direction:column; gap:8px;">
                            <div id="checkout_presets_group" style="display:flex; flex-wrap:wrap; gap:8px;">
                                <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:6px 12px; font-size:0.75rem;" onclick="simulateMemberLiveResponse('No, organic banana was missing.')">"No, organic banana was missing."</button>
                                <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:6px 12px; font-size:0.75rem;" onclick="simulateMemberLiveResponse('No, Gala Apple was missing.')">"No, Gala Apple was missing."</button>
                                <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:6px 12px; font-size:0.75rem;" onclick="simulateMemberLiveResponse('Yes, I was able to get everything. Thank you!')">"Yes, I got everything!"</button>
                            </div>
                            <div id="enrollment_presets_group" style="display:none; flex-wrap:wrap; gap:8px;">
                                <button type="button" class="btn btn-green" style="padding:6px 12px; font-size:0.75rem;" onclick="simulateMemberLiveResponse('Yes')">"Yes" (Enroll)</button>
                                <button type="button" class="btn btn-red" style="padding:6px 12px; font-size:0.75rem;" onclick="simulateMemberLiveResponse('No')">"No" (Decline)</button>
                                <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:6px 12px; font-size:0.75rem;" onclick="simulateMemberLiveResponse('What are the reward points?')">"What are the reward points?"</button>
                            </div>
                        </div>
                    </div>
                    
                    <div id="checkout_progress_group" style="display:none; width:100%; flex-direction:column; align-items:center; justify-content:center; gap:12px; margin-top:10px;">
                        <div class="spinner"></div>
                        <div id="checkout_progress_text" style="color:var(--text-muted); font-size:0.9rem; font-weight:600; text-align:center;">Checkout in progress...</div>
                    </div>
                    
                    <div id="completed_actions_group" style="display:none; width:100%; justify-content:center; margin-top:10px;">
                        <button type="button" class="btn btn-green" style="padding:10px 24px; font-weight:700; width:100%; margin:0;" onclick="closeCompletedCheckout()">Close & Go to Dashboard</button>
                    </div>
                </div>
            </div>
        </main>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    if not token_valid:
        token_claims = {"member_id": member_id, "role": "Member"}
        if jti:
            token_claims["jti"] = jti
        new_token = create_access_token(token_claims)
        response.set_cookie(
            key="session_token",
            value=new_token,
            httponly=True,
            max_age=3600,
            samesite="lax"
        )
    return response

# ------------------------------------------------------------------------------
# 4. Out-of-Stock Item Selection
# ------------------------------------------------------------------------------
@app.post("/report-oos-select", response_class=HTMLResponse)
async def report_oos_select(member_id: str = Form(...), receipt_id: str = Form(...)):
    member = query_db("SELECT Name FROM members WHERE MemberID = ?", (member_id,), one=True)
    items = query_db(
        "SELECT rd.ItemID, i.ItemDescription, rd.Price FROM receipt_details rd "
        "JOIN items i ON rd.ItemID = i.ItemID WHERE rd.ReceiptID = ?",
        (receipt_id,)
    )
    
    item_rows = ""
    for item in items:
        item_rows += f"""
        <div class="item-row" onclick="submitOOS('{item['ItemID']}')">
            <div style="display:flex; flex-direction:column; gap:4px;">
                <span style="font-weight:600; color:#fff;">{item['ItemDescription']}</span>
                <span style="font-size:0.8rem; color:var(--text-muted);">ItemID: {item['ItemID']}</span>
            </div>
            <div style="display:flex; align-items:center; gap:16px;">
                <span style="font-weight:700; color:var(--accent-blue);">${item['Price']:.2f}</span>
                <button class="btn" style="padding: 6px 12px; font-size:0.8rem;">Select</button>
            </div>
        </div>
        """
        
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Select Item to Report</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        {GLASS_STYLE}
    </head>
    <body>
        <header>
            <h1>SignalSense AI</h1>
            <div class="user-pill">
                <span class="user-avatar"></span>
                <span>{member['Name']}</span>
            </div>
        </header>
        <main style="max-width: 600px; margin: 0 auto;">
            <div class="glass-card" style="display:flex; flex-direction:column; gap:20px;">
                <h2>📋 Select Out-of-Stock Item (Receipt #{receipt_id})</h2>
                <p style="color:var(--text-muted); font-size:0.9rem;">
                    Click on the item below that was missing on the shelves during your visit.
                </p>
                <div class="item-list">
                    {item_rows}
                </div>
                <a href="/dashboard?member_id={member_id}" class="btn" style="background:transparent; border:1px solid var(--glass-border); color:#fff; text-decoration:none; text-align:center;">Back to Dashboard</a>
                
                <form id="oos_form" action="/report-oos-submit" method="POST" style="display:none;">
                    <input type="hidden" name="member_id" value="{member_id}">
                    <input type="hidden" name="receipt_id" value="{receipt_id}">
                    <input type="hidden" id="item_id_field" name="item_id">
                </form>
            </div>
        </main>
        
        <script>
            function submitOOS(itemId) {{
                document.getElementById('item_id_field').value = itemId;
                document.getElementById('oos_form').submit();
            }}
        </script>
    </body>
    </html>
    """
    return html

# ------------------------------------------------------------------------------
# 5. Form Submission Endpoints (routing to Backend ADK Agent HTTP server)
# ------------------------------------------------------------------------------

@app.post("/report-oos-submit")
async def report_oos_submit(request: Request, member_id: str = Form(...), receipt_id: str = Form(...), item_id: str = Form(...)):
    try:
        receipt = query_db("SELECT ClubID FROM member_receipts WHERE ReceiptID = ?", (receipt_id,), one=True)
        club_id = receipt['ClubID'] if receipt else "C100"
        
        token = request.cookies.get("session_token")
        jti = None
        if token:
            try:
                claims = verify_access_token(token)
                jti = claims.get("jti")
            except Exception:
                pass
                
        # Invoke standalone backend ADK Agent API
        result = await invoke_agent(
            signal_type="OOS",
            member_id=member_id,
            club_id=club_id,
            item_id=item_id,
            jti=jti
        )
        check_agent_rejection(result)
        
        # Cross-reference database state
        inv = query_db("SELECT OOSFlag FROM club_inventories WHERE ClubID = ? AND ItemID = ?", (club_id, item_id), one=True)
        if inv and inv['OOSFlag'] == "Yes":
            msg = "Verified OOS Report recorded! Checked inventory database and confirmed shelf is empty."
            return RedirectResponse(
                url=f"/dashboard?member_id={member_id}&success={msg}",
                status_code=status.HTTP_303_SEE_OTHER
            )
        else:
            msg = "OOS Report submitted. Notice: Database records show stock is on hand. Logged as Unverified."
            return RedirectResponse(
                url=f"/dashboard?member_id={member_id}&warning={msg}",
                status_code=status.HTTP_303_SEE_OTHER
            )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard?member_id={member_id}&error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

@app.post("/suggest-product")
async def suggest_product(request: Request, member_id: str = Form(...), description: str = Form(...), store: Optional[str] = Form(None), reason: Optional[str] = Form(None)):
    try:
        token = request.cookies.get("session_token")
        jti = None
        if token:
            try:
                claims = verify_access_token(token)
                jti = claims.get("jti")
            except Exception:
                pass
                
        result = await invoke_agent(
            signal_type="ProductSuggestion",
            member_id=member_id,
            description=description,
            store_where_found=store,
            reason=reason,
            jti=jti
        )
        check_agent_rejection(result)
        msg = f"Successfully proposed '{description}'! The suggestion has been logged for Merchant review."
        return RedirectResponse(
            url=f"/dashboard?member_id={member_id}&success={msg}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard?member_id={member_id}&error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

@app.post("/upvote")
async def upvote_product(request: Request, member_id: str = Form(...), candidate_id: str = Form(...)):
    try:
        token = request.cookies.get("session_token")
        jti = None
        if token:
            try:
                claims = verify_access_token(token)
                jti = claims.get("jti")
            except Exception:
                pass
                
        result = await invoke_agent(
            signal_type="Upvote",
            member_id=member_id,
            candidate_id=candidate_id,
            jti=jti
        )
        check_agent_rejection(result)
        prod = query_db("SELECT Status, UpVotes FROM candidate_products WHERE CandidateID = ?", (candidate_id,), one=True)
        if prod and prod['Status'] == "Threshold Crossed":
            msg = f"Upvote successful! Candidate product has crossed the upvote threshold ({prod['UpVotes']} upvotes) and is promoted to Merchant review!"
            return RedirectResponse(
                url=f"/dashboard?member_id={member_id}&success={msg}",
                status_code=status.HTTP_303_SEE_OTHER
            )
        
        msg = "Upvote logged successfully!"
        return RedirectResponse(
            url=f"/dashboard?member_id={member_id}&success={msg}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard?member_id={member_id}&error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

@app.post("/process-member-voice")
async def process_member_voice(request: Request, member_id: str = Form(...), transcript: str = Form(...)):
    try:
        # Resolve a default club ID for this member
        receipt = query_db("SELECT ClubID FROM member_receipts WHERE MemberID = ? LIMIT 1", (member_id,), one=True)
        club_id = receipt['ClubID'] if receipt else "C100"
        
        token = request.cookies.get("session_token")
        jti = None
        if token:
            try:
                claims = verify_access_token(token)
                jti = claims.get("jti")
            except Exception:
                pass
                
        # Invoke the backend agent on VoiceSignal route
        result = await invoke_agent(
            signal_type="VoiceSignal",
            member_id=member_id,
            club_id=club_id,
            description=transcript,
            jti=jti
        )
        check_agent_rejection(result)
        
        msg = f"Voice signal successfully processed: '{transcript}'"
        
        # Inspect backend returned status
        events = result.get("events", [])
        outcome_status = "ok"
        outcome_msg = ""
        for ev in reversed(events):
            if isinstance(ev, dict) and ev.get("output"):
                out = ev["output"]
                if isinstance(out, dict):
                    outcome_status = out.get("status", "Success")
                    outcome_msg = out.get("message", "")
                    break
                    
        if outcome_status in ("Success", "Closed - Restocked"):
            return RedirectResponse(
                url=f"/dashboard?member_id={member_id}&success={outcome_msg or msg}",
                status_code=status.HTTP_303_SEE_OTHER
            )
        elif outcome_status == "Unverified":
            return RedirectResponse(
                url=f"/dashboard?member_id={member_id}&warning={outcome_msg or msg}",
                status_code=status.HTTP_303_SEE_OTHER
            )
        else:
            return RedirectResponse(
                url=f"/dashboard?member_id={member_id}&success={outcome_msg or msg}",
                status_code=status.HTTP_303_SEE_OTHER
            )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard?member_id={member_id}&error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

@app.get("/checkout/poll-member")
async def poll_member(request: Request, member_id: str):
    # Verify JWT session token matches member_id
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("member_id") != member_id:
            raise HTTPException(status_code=403, detail="Forbidden: Session ID mismatch")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        row = query_db("SELECT * FROM checkout_sessions WHERE MemberID = ?", (member_id,), one=True)
        if not row:
            return {"status": "none"}
            
        status_val = row["Status"]
        # If status is PendingInquiry, update to InquirySent
        if status_val == "PendingInquiry":
            execute_db("UPDATE checkout_sessions SET Status = 'InquirySent', LastUpdated = ? WHERE MemberID = ?", 
                       (datetime.datetime.now().isoformat(), member_id))
        
        return {
            "status": status_val,
            "associate_question": row["AssociateQuestion"],
            "member_response": row["MemberResponse"],
            "enrollment_answer": row["EnrollmentAnswer"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/respond-member")
async def respond_member(request: Request, member_id: str = Form(...), response: str = Form(...), field: str = Form(...)):
    # Verify JWT session token matches member_id
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("member_id") != member_id:
            raise HTTPException(status_code=403, detail="Forbidden: Session ID mismatch")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        if field == "response":
            execute_db(
                "UPDATE checkout_sessions SET Status = 'ResponseReceived', MemberResponse = ?, LastUpdated = ? WHERE MemberID = ?",
                (response, datetime.datetime.now().isoformat(), member_id)
            )
        elif field == "enrollment":
            execute_db(
                "UPDATE checkout_sessions SET Status = 'EnrollmentResponseReceived', EnrollmentAnswer = ?, LastUpdated = ? WHERE MemberID = ?",
                (response, datetime.datetime.now().isoformat(), member_id)
            )
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/arrive")
async def checkout_arrive(request: Request, member_id: str = Form(...)):
    # Verify JWT session token matches member_id
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("member_id") != member_id:
            raise HTTPException(status_code=403, detail="Forbidden: Session ID mismatch")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        execute_db(
            "INSERT OR REPLACE INTO checkout_sessions (MemberID, Status, AssociateQuestion, MemberResponse, EnrollmentAnswer, MatchedItemID, LastUpdated) "
            "VALUES (?, 'ReadyToCheckout', NULL, NULL, NULL, NULL, ?)",
            (member_id, datetime.datetime.now().isoformat())
        )
        return {"status": "success", "message": "Checked in at checkout station."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/complete-close")
async def checkout_complete_close(request: Request, member_id: str = Form(...)):
    # Verify JWT session token matches member_id
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("member_id") != member_id:
            raise HTTPException(status_code=403, detail="Forbidden: Session ID mismatch")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        execute_db(
            "UPDATE checkout_sessions SET Status = 'Done', LastUpdated = ? WHERE MemberID = ?",
            (datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8083)
