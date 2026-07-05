import os
import sys
import json
import sqlite3
import datetime
import uuid
from typing import Optional, List
from fastapi import FastAPI, Form, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx
from dotenv import load_dotenv

# Resolve paths
current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.dirname(current_dir)
backend_path = os.path.join(workspace_root, "signalsense_enterprise")

if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Load environment variables
dotenv_path = os.path.join(workspace_root, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
local_dotenv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(local_dotenv):
    load_dotenv(local_dotenv)

from jwt_helper import create_access_token, verify_access_token
from rate_limiter import RateLimitingMiddleware

app = FastAPI(title="Operations Dashboard")
app.add_middleware(RateLimitingMiddleware, max_requests=300, window_seconds=60)

# Backend URL config
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8080")

from contextlib import aclosing

# Try importing ADK runner for in-process fallback
try:
    from google.genai import types as genai_types
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
    print(f"ADK runner import failed in Operations Dashboard, in-process fallback disabled: {ie}")

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

from db_helper import query_db, execute_db, get_db_conn

# Initialize test member Sarah Jenkins (non-ambassador) and checkout_sessions table
try:
    execute_db("""
    CREATE TABLE IF NOT EXISTS checkout_sessions (
        MemberID TEXT PRIMARY KEY,
        Status TEXT,
        AssociateQuestion TEXT,
        MemberResponse TEXT,
        EnrollmentAnswer TEXT,
        MatchedItemID TEXT,
        LastUpdated TEXT
    );
    """)
    # Clear any previous active sessions to start clean
    execute_db("DELETE FROM checkout_sessions")
    
    execute_db("DELETE FROM members WHERE MemberID = 'M1009'")
    execute_db(
        "INSERT OR IGNORE INTO members (MemberID, Name, Address, City, State, Zip, TrustScore, SamsPoints, Ambassador, JoinDate) "
        "VALUES ('M1009', 'Sarah Jenkins', '123 Main St', 'Bentonville', 'AR', '72712', 0, 0, 'No', '2026-06-25')"
    )
    execute_db(
        "INSERT OR IGNORE INTO member_receipts (ReceiptID, MemberID, ClubID, PurchaseDate) "
        "VALUES ('R5001', 'M1009', 'C100', '2026-06-25')"
    )
    execute_db(
        "INSERT OR IGNORE INTO receipt_details (ReceiptID, ItemID, Qty, Price) "
        "VALUES ('R5001', 'I1001', 1, 1.99)"
    )
except Exception as e:
    print(f"Error seeding member M1009 or checkout_sessions table: {e}")

async def invoke_agent_action(signal_id: str, action: str, jti: Optional[str] = None) -> dict:
    payload = {
        "signal_type": "AssociateAction",
        "signal_id": signal_id,
        "action": action
    }
    
    # Send request to backend ADK Agent API
    try:
        async with httpx.AsyncClient() as client:
            adk_payload = {
                "app_name": "signalsense_agent",
                "user_id": "operations-app",
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
            token_claims = {"role": "Associate"}
            if jti:
                token_claims["jti"] = jti
            token = create_access_token(token_claims)
            response = await client.post(
                f"{BACKEND_URL}/run",
                json=adk_payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=3.0
            )
            response.raise_for_status()
            
            # Check backend response status and message
            events = response.json()
            status_str = "Success"
            msg_str = ""
            for event in reversed(events):
                if isinstance(event, dict) and event.get("output"):
                    output_data = event["output"]
                    if isinstance(output_data, dict):
                        status_str = output_data.get("status", "Success")
                        msg_str = output_data.get("message", "")
                        break
            if status_str in ("Error", "Rejected"):
                raise ValueError(msg_str or "Associate action was rejected by backend.")
            return {"status": status_str, "message": msg_str}
    except httpx.HTTPStatusError as hse:
        error_detail = "Action rejected by backend"
        try:
            error_detail = hse.response.json().get("detail", error_detail)
        except Exception:
            pass
        raise ValueError(error_detail)

# ------------------------------------------------------------------------------
# Premium CSS Styling (Glassmorphic Dark Mode)
# ------------------------------------------------------------------------------
GLASS_STYLE = """
<style>
    :root {
        --bg-primary: #05070f;
        --bg-radial: radial-gradient(circle at 50% 50%, #10142b 0%, #030408 100%);
        --text-main: #f3f4f6;
        --text-muted: #9ca3af;
        --glass-bg: rgba(255, 255, 255, 0.02);
        --glass-border: rgba(255, 255, 255, 0.08);
        --accent-blue: #3b82f6;
        --accent-green: #10b981;
        --accent-red: #ef4444;
        --accent-yellow: #f59e0b;
        --accent-purple: #8b5cf6;
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
        max-width: 1200px;
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
        background: linear-gradient(135deg, #fff 0%, #a8c0ff 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    .role-badge {
        background: rgba(59, 130, 246, 0.15);
        color: var(--accent-blue);
        border: 1px solid rgba(59, 130, 246, 0.3);
        padding: 6px 16px;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    main {
        width: 100%;
        max-width: 1200px;
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
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.5);
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

    /* Grid of OOS Tasks */
    .tasks-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        gap: 20px;
    }

    .task-card {
        background: rgba(255,255,255,0.01);
        border: 1px solid var(--glass-border);
        border-radius: 16px;
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 14px;
        transition: all 0.3s ease;
        cursor: pointer;
    }

    .task-card:hover {
        background: rgba(255,255,255,0.04);
        border-color: rgba(59, 130, 246, 0.3);
        transform: translateY(-2px);
    }

    .task-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .task-id {
        font-weight: 700;
        color: var(--accent-blue);
        font-size: 0.9rem;
    }

    .task-time {
        font-size: 0.75rem;
        color: var(--text-muted);
    }

    .task-title {
        font-size: 1.1rem;
        font-weight: 700;
        color: #fff;
    }

    .task-detail {
        font-size: 0.85rem;
        color: var(--text-muted);
        display: flex;
        justify-content: space-between;
        padding: 4px 0;
        border-bottom: 1px solid rgba(255,255,255,0.02);
    }

    .task-detail span:last-child {
        color: #fff;
        font-weight: 600;
    }

    .btn {
        background: var(--accent-blue);
        color: #fff;
        border: none;
        padding: 10px 20px;
        border-radius: 10px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.3s ease;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        text-decoration: none;
    }

    .btn:hover {
        transform: translateY(-1px);
        box-shadow: 0 0 12px rgba(59, 130, 246, 0.4);
    }

    .btn-green {
        background: var(--accent-green);
    }
    .btn-green:hover {
        box-shadow: 0 0 12px rgba(16, 185, 129, 0.4);
    }

    .btn-red {
        background: var(--accent-red);
    }
    .btn-red:hover {
        box-shadow: 0 0 12px rgba(239, 68, 68, 0.4);
    }

    .btn-orange {
        background: var(--accent-yellow);
    }
    .btn-orange:hover {
        box-shadow: 0 0 12px rgba(245, 158, 11, 0.4);
    }

    .role-tabs {
        display: flex;
        gap: 12px;
        border-bottom: 1px solid var(--glass-border);
        padding-bottom: 12px;
        margin-bottom: 20px;
    }

    .tab {
        padding: 8px 20px;
        border-radius: 10px;
        font-size: 0.9rem;
        font-weight: 600;
        color: var(--text-muted);
        cursor: pointer;
        background: transparent;
        border: 1px solid transparent;
        transition: all 0.2s ease;
    }

    .tab.active {
        background: rgba(59, 130, 246, 0.1);
        color: var(--accent-blue);
        border-color: rgba(59, 130, 246, 0.2);
    }

    .tab.locked {
        opacity: 0.4;
        cursor: not-allowed;
    }

    /* Modal styles */
    .modal {
        display: none;
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.7);
        backdrop-filter: blur(8px);
        z-index: 1000;
        align-items: center;
        justify-content: center;
    }

    .modal.active {
        display: flex;
    }

    .modal-content {
        width: 100%;
        max-width: 500px;
        background: #0f1225;
        border: 1px solid var(--glass-border);
        border-radius: 20px;
        padding: 32px;
        box-shadow: 0 12px 48px rgba(0,0,0,0.6);
        display: flex;
        flex-direction: column;
        gap: 20px;
        max-height: 90vh;
        overflow-y: auto;
    }

    .action-btn-opt {
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--glass-border);
        border-radius: 10px;
        padding: 12px;
        text-align: center;
        cursor: pointer;
        font-weight: 600;
        color: var(--text-muted);
        transition: all 0.2s ease;
        font-size: 0.85rem;
    }
    .action-btn-opt:hover {
        background: rgba(255,255,255,0.05);
    }
    .action-btn-opt.active {
        background: rgba(59, 130, 246, 0.15);
        border-color: var(--accent-blue);
        color: #fff;
    }

    .text-input {
        width: 100%;
        background: rgba(255,255,255,0.05);
        border: 1px solid var(--glass-border);
        border-radius: 8px;
        padding: 10px;
        color: #fff;
        font-size: 0.9rem;
        outline: none;
        margin-top: 4px;
        margin-bottom: 12px;
        box-sizing: border-box;
        font-family: inherit;
    }
    .text-input:focus {
        border-color: var(--accent-blue);
    }
    select option {
        background-color: #111825 !important;
        color: #ffffff !important;
    }
    .tab {
        text-decoration: none;
    }
</style>
"""

# ------------------------------------------------------------------------------
# 1. Landing / Role Selection Page
# ------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def get_landing(demo_token: Optional[str] = None):
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Associate App - Role Selector</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        {GLASS_STYLE}
    </head>
    <body>
        <div style="flex-grow:1; display:flex; align-items:center; justify-content:center; width:100%;">
            <div class="glass-card" style="width:100%; max-width:600px; text-align:center; display:flex; flex-direction:column; gap:24px; padding:48px;">
                <div>
                    <h1 style="font-size: 2.5rem; font-weight: 800; background: linear-gradient(135deg, #fff 0%, #a8c0ff 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 8px;">SignalSense AI</h1>
                    <p style="color: var(--text-muted); font-size:1.1rem;">Operations Management App</p>
                </div>
                <div style="margin-top:10px; display:flex; flex-direction:column; gap:16px;">
                    <a href="/dashboard?role=Club_Associate" class="btn" style="padding: 16px; font-size:1.05rem; justify-content:space-between;">
                        <span>👤 Enter as Club Associate</span>
                        <span>➔</span>
                    </a>
                    
                    <a href="/dashboard?role=Merchant" class="btn" style="padding: 16px; font-size:1.05rem; justify-content:space-between;">
                        <span>💼 Enter as Merchant</span>
                        <span>➔</span>
                    </a>
                    
                    <a href="/dashboard?role=Inventory_Associate" class="btn" style="padding: 16px; font-size:1.05rem; justify-content:space-between;">
                        <span>📦 Enter as Inventory Associate</span>
                        <span>➔</span>
                    </a>

                    <a href="/dashboard?role=Checkout_Associate" class="btn" style="padding: 16px; font-size:1.05rem; justify-content:space-between; background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);">
                        <span>🛒 Enter as Checkout Associate</span>
                        <span>➔</span>
                    </a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    if demo_token:
        token = create_access_token({"role": "Associate", "jti": demo_token})
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            max_age=86400 * 7,
            samesite="lax"
        )
    return response

# ------------------------------------------------------------------------------
# 2. Operations Dashboard (Associate App)
# ------------------------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(
    request: Request,
    role: str = "Club_Associate",
    success: Optional[str] = None,
    error: Optional[str] = None
):
    if role not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
        return RedirectResponse(url="/")

    # Authenticate associate session via JWT cookie
    token = request.cookies.get("session_token")
    global token_valid
    token_valid = False
    jti = None
    if token:
        try:
            claims = verify_access_token(token)
            jti = claims.get("jti")
            if claims.get("role") == role:
                token_valid = True
        except Exception:
            pass
        
    checkout_members = []
    checkout_items = []
    active_session_member_id = ""
        
    similar_map = {}
    if role == "Merchant":
        # Query active Merchant signals
        active_tasks = query_db(
            "SELECT "
            "  s.SignalID, s.CandidateID, s.Status, s.Created, "
            "  m.Name AS MemberName, m.TrustScore AS MemberTrust, "
            "  cp.ItemDescription, cp.UpVotes, cp.Threshold, cp.StoreWhereFound "
            "FROM signals s "
            "JOIN members m ON s.MemberID = m.MemberID "
            "JOIN candidate_products cp ON s.CandidateID = cp.CandidateID "
            "WHERE s.AssignedRole = 'Merchant' AND s.Status NOT LIKE 'Closed%' "
            "ORDER BY s.Created ASC"
        )
        
        # Build similar products map for duplicate risk check
        all_items = query_db("SELECT ItemDescription FROM items")
        all_candidates = query_db("SELECT CandidateID, ItemDescription FROM candidate_products")
        for t in active_tasks:
            cand_id = t['CandidateID']
            desc = t['ItemDescription']
            
            # Clean description (extract core proposed item name)
            core_desc = desc
            if " - " in desc:
                core_desc = desc.split(" - ", 1)[1]
            if " (" in core_desc:
                core_desc = core_desc.rsplit(" (", 1)[0]
                
            words = [w.strip().lower() for w in core_desc.split() if len(w.strip()) > 2]
            
            matches = []
            if words:
                for item in all_items:
                    item_desc = item['ItemDescription']
                    if any(w in item_desc.lower() for w in words):
                        matches.append(f"Master: {item_desc}")
                for cand in all_candidates:
                    if cand['CandidateID'] != cand_id:
                        cand_desc = cand['ItemDescription']
                        if any(w in cand_desc.lower() for w in words):
                            matches.append(f"Proposed: {cand_desc}")
            similar_map[cand_id] = matches[:5]
            
    elif role == "Inventory_Associate":
        # Query active Inventory signals
        active_tasks = query_db(
            "SELECT "
            "  s.SignalID, s.ItemID, s.ClubID, s.Status, s.Created, "
            "  m.Name AS MemberName, m.TrustScore AS MemberTrust, "
            "  i.ItemDescription, "
            "  inv.OnHand, inv.BackRoom, inv.LostSalesToday "
            "FROM signals s "
            "JOIN members m ON s.MemberID = m.MemberID "
            "JOIN items i ON s.ItemID = i.ItemID "
            "JOIN club_inventories inv ON s.ClubID = inv.ClubID AND s.ItemID = inv.ItemID "
            "WHERE s.AssignedRole = 'Inventory Associate' AND s.Status NOT LIKE 'Closed%' "
            "ORDER BY s.Created ASC"
        )
        
    elif role == "Checkout_Associate":
        active_tasks = []
        checkout_members = query_db("SELECT MemberID, Name, Ambassador FROM members")
        checkout_items = query_db("SELECT ItemID, ItemDescription FROM items")
        try:
            five_mins_ago = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat()
            execute_db("UPDATE checkout_sessions SET Status = 'Done' WHERE LastUpdated < ? AND Status != 'Done'", (five_mins_ago,))
        except Exception as e:
            print(f"Error cleaning up stale sessions: {e}")
        active_session = query_db("SELECT MemberID FROM checkout_sessions WHERE Status NOT IN ('Done', 'ReadyToCheckout')", one=True)
        active_session_member_id = active_session["MemberID"] if active_session else ""
        
    else:
        # Query OOS signals assigned to Club Associate that are active
        active_tasks = query_db(
            "SELECT "
            "  s.SignalID, s.ItemID, s.ClubID, s.Status, s.Created, "
            "  m.Name AS MemberName, m.TrustScore AS MemberTrust, "
            "  i.ItemDescription, "
            "  inv.OnHand, inv.BackRoom "
            "FROM signals s "
            "JOIN members m ON s.MemberID = m.MemberID "
            "JOIN items i ON s.ItemID = i.ItemID "
            "JOIN club_inventories inv ON s.ClubID = inv.ClubID AND s.ItemID = inv.ItemID "
            "WHERE s.AssignedRole = 'Club Associate' AND s.Status NOT LIKE 'Closed%' "
            "ORDER BY s.Created ASC"
        )
        
    # Query history of tasks verified/closed
    history_tasks = query_db(
        "SELECT "
        "  s.SignalID, s.ItemID, s.CandidateID, s.ClubID, s.Status, s.Created, "
        "  m.Name AS MemberName, "
        "  COALESCE(i.ItemDescription, cp.ItemDescription) AS ItemDescription "
        "FROM signals s "
        "JOIN members m ON s.MemberID = m.MemberID "
        "LEFT JOIN items i ON s.ItemID = i.ItemID "
        "LEFT JOIN candidate_products cp ON s.CandidateID = cp.CandidateID "
        "WHERE s.Status LIKE 'Closed%' "
        "ORDER BY s.Created DESC LIMIT 20"
    )
    
    alert_html = ""
    if success:
        alert_html = f"<div class='alert alert-success'><strong>Success!</strong> {success}</div>"
    elif error:
        alert_html = f"<div class='alert alert-danger'><strong>Error!</strong> {error}</div>"
        
    # Render active cards
    cards_html = ""
    if role == "Merchant":
        if not active_tasks:
            cards_html = "<div style='grid-column: 1/-1; text-align:center; padding:40px; color:var(--text-muted);'>🎉 No pending candidate product reviews! All quiet in Merchant space.</div>"
        else:
            for t in active_tasks:
                cards_html += f'''
                <div class="task-card" onclick="openMerchantModal('{t['SignalID']}', '{t['CandidateID']}', '{t['ItemDescription']}', '{t['MemberName']}', '{t['MemberTrust']}%', {t['UpVotes']}, {t['Threshold']}, '{t['StoreWhereFound']}')">
                    <div class="task-header">
                        <span class="task-id">TASK #{t['SignalID']}</span>
                        <span class="task-time">{t['Created']}</span>
                    </div>
                    <div class="task-title">{t['ItemDescription']}</div>
                    <div style="display:flex; flex-direction:column; gap:4px; margin-top:8px;">
                        <div class="task-detail"><span>Reporting Member:</span> <span>{t['MemberName']}</span></div>
                        <div class="task-detail"><span>Member Trust Score:</span> <span>{t['MemberTrust']}%</span></div>
                        <div class="task-detail"><span>Demand Status:</span> <span style="color:var(--accent-yellow); font-weight:600;">{t['UpVotes']} Upvotes (Target: {t['Threshold']})</span></div>
                    </div>
                </div>
                '''
    elif role == "Inventory_Associate":
        if not active_tasks:
            cards_html = "<div style='grid-column: 1/-1; text-align:center; padding:40px; color:var(--text-muted);'>🎉 No pending replenishment exceptions! All supply lines are healthy.</div>"
        else:
            for t in active_tasks:
                cards_html += f'''
                <div class="task-card" onclick="openInventoryModal('{t['SignalID']}', '{t['ItemID']}', '{t['ItemDescription']}', '{t['ClubID']}', '{t['MemberName']}', '{t['MemberTrust']}%', {t['LostSalesToday']}, {t['OnHand']}, {t['BackRoom']})">
                    <div class="task-header">
                        <span class="task-id">TASK #{t['SignalID']}</span>
                        <span class="task-time">{t['Created']}</span>
                    </div>
                    <div class="task-title">{t['ItemDescription']}</div>
                    <div style="display:flex; flex-direction:column; gap:4px; margin-top:8px;">
                        <div class="task-detail"><span>Club ID:</span> <span>{t['ClubID']}</span></div>
                        <div class="task-detail"><span>Lost Sales Today:</span> <span style="color:var(--accent-red); font-weight:600;">{t['LostSalesToday']} units lost</span></div>
                        <div class="task-detail"><span>Stock Level:</span> <span style="color:var(--accent-red)">0 On-Hand / 0 Backroom</span></div>
                    </div>
                </div>
                '''
    else:
        if not active_tasks:
            cards_html = "<div style='grid-column: 1/-1; text-align:center; padding:40px; color:var(--text-muted);'>🎉 No pending OOS tasks assigned! All shelves are clear.</div>"
        else:
            for t in active_tasks:
                cards_html += f'''
                <div class="task-card" onclick="openVerificationModal('{t['SignalID']}', '{t['ItemDescription']}', '{t['ItemID']}', '{t['ClubID']}', '{t['MemberName']}', '{t['MemberTrust']}%', {t['OnHand']}, {t['BackRoom']})">
                    <div class="task-header">
                        <span class="task-id">TASK #{t['SignalID']}</span>
                        <span class="task-time">{t['Created']}</span>
                    </div>
                    <div class="task-title">{t['ItemDescription']}</div>
                    <div style="display:flex; flex-direction:column; gap:4px; margin-top:8px;">
                        <div class="task-detail"><span>Reporting Member:</span> <span>{t['MemberName']}</span></div>
                        <div class="task-detail"><span>Club ID:</span> <span style="color:var(--accent-blue); font-weight:600;">{t['ClubID']}</span></div>
                        <div class="task-detail"><span>Member Trust Score:</span> <span>{t['MemberTrust']}%</span></div>
                        <div class="task-detail"><span>Shelf Stock Status:</span> <span style="color:var(--accent-red)">Out of Stock</span></div>
                    </div>
                </div>
                '''

    # Render history table rows
    history_rows = ""
    if not history_tasks:
        history_rows = "<tr><td colspan='5' style='text-align:center; padding:16px; color:var(--text-muted);'>No task history found.</td></tr>"
    else:
        for h in history_tasks:
            status_color = "var(--text-muted)"
            status_str = h['Status']
            if "Restocked" in status_str or "Launch Approved" in status_str or "Expedite" in status_str or "Forecast" in status_str or "Transfer" in status_str:
                status_color = "var(--accent-green)"
            elif "Exploration" in status_str or "Threshold Increased" in status_str or "False Alarm" in status_str or "Monitoring" in status_str:
                status_color = "var(--accent-yellow)"
            elif "Archived" in status_str or "Rejected" in status_str or "Constraint" in status_str:
                status_color = "var(--accent-red)"
            elif "Escalated" in status_str or "Inventory Associate" in status_str:
                status_color = "var(--accent-purple)"
                
            club_disp = h['ClubID'] if h['ClubID'] else "All Clubs"
            history_rows += f'''
            <tr style="border-bottom: 1px solid var(--glass-border); font-size:0.9rem;">
                <td style="padding:12px; font-weight:600; color:var(--accent-blue)">{h['SignalID']}</td>
                <td style="padding:12px;">{h['ItemDescription']}</td>
                <td style="padding:12px; color:var(--text-muted)">{club_disp}</td>
                <td style="padding:12px; color:var(--text-muted)">{h['MemberName']}</td>
                <td style="padding:12px; color:{status_color}; font-weight:600;">{status_str}</td>
            </tr>
            '''
            
    role_badge = "👤 Club Associate"
    if role == "Merchant":
        role_badge = "💼 Merchant"
    elif role == "Inventory_Associate":
        role_badge = "📦 Inventory Associate"
    elif role == "Checkout_Associate":
        role_badge = "🛒 Checkout Associate"
        
    associate_tab_class = "tab active" if role == "Club_Associate" else "tab"
    merchant_tab_class = "tab active" if role == "Merchant" else "tab"
    inventory_tab_class = "tab active" if role == "Inventory_Associate" else "tab"
    checkout_tab_class = "tab active" if role == "Checkout_Associate" else "tab"
    similar_map_js = json.dumps(similar_map)
    
    # Conditional Workspace Card
    if role == "Checkout_Associate":
        member_options = ""
        for m in checkout_members:
            amb_str = m['Ambassador']
            member_options += f'<option value="{m["MemberID"]}" data-ambassador="{amb_str}">{m["Name"]} ({m["MemberID"]})</option>'
            
        items_json = json.dumps([{"ItemID": item["ItemID"], "ItemDescription": item["ItemDescription"]} for item in checkout_items])
        
        workspace_html = f"""
        <!-- Arrival Alert Notification -->
        <div id="checkout_arrival_alert" style="display:none; background:rgba(59, 130, 246, 0.08); border:1px dashed rgba(59, 130, 246, 0.4); padding:16px; border-radius:12px; margin-bottom:16px; align-items:center; justify-content:space-between; gap:12px; animation: pulse 2s infinite;">
            <div style="display:flex; align-items:center; gap:8px; color:#fff;">
                <span>🔔</span>
                <span id="arrival_alert_text" style="font-weight:600; font-size:0.9rem;">A member has arrived at the checkout counter!</span>
            </div>
            <button type="button" class="btn btn-blue" id="arrival_alert_action_btn" style="padding:6px 12px; font-size:0.8rem; margin:0;" onclick="acceptArrival()">Select & Start Live Inquiry</button>
        </div>

        <div class="glass-card" style="display:flex; flex-direction:column; gap:20px; margin-bottom:20px;">
            <h2 style="color:var(--accent-blue)">🛒 Member Checkout Inquiry</h2>
            <p style="color:var(--text-muted); font-size:0.9rem; margin-bottom:10px;">
                Ask the member checking out: <strong>"Were you able to find everything you came to buy today?"</strong>
            </p>
            
            <div style="display:flex; flex-direction:column; gap:16px;">
                <div>
                    <label style="font-size:0.85rem; color:var(--text-muted); font-weight:700; display:block; margin-bottom:6px; text-transform:uppercase;">Select Checkout Member:</label>
                    <select id="checkout_member_select" class="text-input" style="width:100%;" onchange="updateMemberStatus()">
                        {member_options}
                    </select>
                </div>
                
                <div style="display:flex; flex-direction:column; gap:10px;">
                    <button type="button" class="btn btn-blue" id="checkout_start_btn" onclick="startLiveInquiry()" style="padding:12px; font-weight:600; display:flex; align-items:center; justify-content:center; gap:8px;">🎙️ Start Live Voice Inquiry on Member App</button>
                </div>

                <!-- Live coordination status -->
                <div id="checkout_live_status_panel" style="background:rgba(255,255,255,0.02); border:1px solid var(--glass-border); padding:16px; border-radius:12px; display:none; flex-direction:column; gap:10px; margin-top:10px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-weight:700; color:var(--text-muted); font-size:0.8rem; text-transform:uppercase; letter-spacing:0.5px;">Live Coordination Status</span>
                        <span id="live_status_badge" style="background:#3b82f6; color:#fff; padding:4px 10px; border-radius:12px; font-size:0.75rem; font-weight:700;">Idle</span>
                    </div>
                    <div id="live_status_detail" style="font-size:0.95rem; color:#fff; font-weight:600;">Ready to start live checkout inquiry.</div>
                </div>
                
                <div style="display:none; gap:16px; align-items:center; margin-top:8px;">
                    <button type="button" class="btn" id="checkout_mic_btn" style="background:var(--accent-red); width:50px; height:50px; border-radius:50%; padding:0; display:flex; align-items:center; justify-content:center; font-size:1.5rem; cursor:pointer;" onclick="toggleCheckoutSpeech()">🎙️</button>
                    <div id="checkout_speech_status" style="color:var(--text-muted); font-size:0.9rem; font-weight:600;">Click microphone to start listening to response...</div>
                </div>
                
                <div>
                    <label style="font-size:0.85rem; color:var(--text-muted); font-weight:700; display:block; margin-bottom:6px; text-transform:uppercase;">Member Voice Transcript / Response:</label>
                    <textarea id="checkout_transcript" class="text-input" style="height:70px; width:100%; box-sizing:border-box; outline:none; resize:none; font-family:inherit; padding:12px; border-radius:10px; background:rgba(255,255,255,0.04); border:1px solid var(--glass-border);" placeholder="Member response transcript will automatically sync here..." oninput="analyzeTranscript()"></textarea>
                </div>

                <!-- Simulation controls -->
                <div style="border-top:1px solid var(--glass-border); padding-top:16px; display:flex; flex-direction:column; gap:10px;">
                    <span style="font-size:0.8rem; color:var(--text-muted); font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">Simulate Voice Responses:</span>
                    <div style="display:flex; flex-wrap:wrap; gap:10px;">
                        <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:8px 16px; font-size:0.8rem;" onclick="simulateCheckoutVoice('No, organic banana was missing.')">"No, organic banana was missing." (OOS)</button>
                        <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:8px 16px; font-size:0.8rem;" onclick="simulateCheckoutVoice('I couldn\'t find any gala apples on the shelf today.')">"I couldn\'t find gala apples." (OOS)</button>
                        <button type="button" class="btn" style="background:rgba(255,255,255,0.05); border:1px solid var(--glass-border); padding:8px 16px; font-size:0.8rem;" onclick="simulateCheckoutVoice('Yes, I was able to get everything I has come to buy. Thank you!')">"Yes, I got everything!" (SLA Clear)</button>
                    </div>
                </div>

                <!-- Analysis and resolution card -->
                <div id="checkout_analysis_card" class="glass-card" style="background:rgba(255,255,255,0.02); display:none; flex-direction:column; gap:14px; border-color:rgba(59, 130, 246, 0.2); margin-top:10px;">
                    <h3 style="color:#fff; font-size:1.05rem; display:flex; align-items:center; gap:8px;">🔍 Analysis & Resolution</h3>
                    
                    <div class="task-detail">
                        <span>Negative Intent / Stock Out detected:</span>
                        <span id="analysis_oos_detected" style="color:var(--accent-red); font-weight:700;">No</span>
                    </div>
                    
                    <div class="task-detail">
                        <span>Matched Catalog Product:</span>
                        <span id="analysis_matched_product" style="color:var(--accent-blue); font-weight:700;">None</span>
                    </div>

                    <!-- Member Ambassador Status section -->
                    <div id="member_enrollment_panel" style="border-top:1px solid var(--glass-border); padding-top:14px; display:flex; flex-direction:column; gap:12px;">
                        <div class="task-detail">
                            <span>Ambassador Status:</span>
                            <span id="analysis_ambassador_status" style="font-weight:700;">Yes</span>
                        </div>
                        
                        <div id="enroll_action_box" style="display:none; background:rgba(245,158,11,0.08); border:1px solid rgba(245,158,11,0.2); padding:16px; border-radius:12px; text-align:center;">
                            <p style="color:var(--accent-yellow); font-size:0.85rem; margin-bottom:12px; font-weight:600;">
                                ⚠️ Member is not enrolled in the Ambassador program.
                            </p>
                            <div style="display:flex; flex-direction:column; gap:8px;">
                                <button type="button" class="btn btn-orange" onclick="enrollMember()">✍️ Manually Enroll Member</button>
                                <button type="button" class="btn btn-blue" id="propose_enrollment_btn" onclick="proposeAmbassadorEnrollment()">📢 Send Voice Enrollment Prompt</button>
                            </div>
                        </div>

                        <div id="enrollment_agreed_box" style="display:none; border:1px solid rgba(16,185,129,0.2); background:rgba(16,185,129,0.08); padding:14px; border-radius:12px; text-align:center; font-weight:700; font-size:0.9rem;">
                            ✓ Member replied YES via Voice! Auto-enrolled.
                        </div>
                    </div>

                    <!-- Final Submission form -->
                    <form id="checkout_oos_form" action="/execute-checkout-oos" method="POST" style="margin-top:10px;">
                        <input type="hidden" id="submit_member_id" name="member_id">
                        <input type="hidden" id="submit_item_id" name="item_id">
                        <input type="hidden" id="submit_enroll_ambassador" name="enroll_ambassador" value="false">
                        
                        <button type="submit" id="submit_oos_pipeline_btn" class="btn btn-red" style="width:100%; padding:14px; font-size:1rem; font-weight:700; display:none;">🚨 Log Stock Out and Trigger Pipeline</button>
                    </form>
                </div>
            </div>
        </div>

        <script>
            const masterItems = {items_json};
            let checkoutRecognition;
            let isCheckoutListening = false;
            let selectedItemId = null;
            let isAmbassador = false;
            let hasNeg = false;
            
            let livePollInterval = null;
            let currentSessionMemberId = null;
            let isLiveCheckoutActive = false;
            
            function startLiveInquiry() {{
                const select = document.getElementById('checkout_member_select');
                if (!select) return;
                const memberId = select.value;
                currentSessionMemberId = memberId;
                isLiveCheckoutActive = true;
                
                // Fetch real-time member ambassador status first
                fetch('/checkout/member-realtime-status?member_id=' + memberId)
                .then(res => res.json())
                .then(realtimeData => {{
                    if (realtimeData && realtimeData.ambassador !== undefined) {{
                        const isAmb = realtimeData.ambassador === 'Yes';
                        isAmbassador = isAmb;
                        const opt = select.options[select.selectedIndex];
                        if (opt) {{
                            opt.setAttribute('data-ambassador', isAmb ? 'Yes' : 'No');
                        }}
                        updateMemberStatus();
                    }}
                    
                    document.getElementById('checkout_live_status_panel').style.display = 'flex';
                    document.getElementById('live_status_badge').innerText = 'Pending';
                    document.getElementById('live_status_badge').style.background = '#3b82f6';
                    document.getElementById('live_status_detail').innerText = 'Sending voice inquiry to Member App...';
                    
                    document.getElementById('checkout_transcript').value = '';
                    document.getElementById('checkout_analysis_card').style.display = 'none';
                    document.getElementById('enrollment_agreed_box').style.display = 'none';
                    
                    const formData = new FormData();
                    formData.append('member_id', memberId);
                    
                    return fetch('/checkout/start', {{
                        method: 'POST',
                        body: formData
                    }});
                }})
                .then(res => res.json())
                .then(data => {{
                    document.getElementById('live_status_detail').innerText = 'Voice inquiry sent! Waiting for member reply...';
                    if (livePollInterval) clearInterval(livePollInterval);
                    livePollInterval = setInterval(pollSessionStatus, 400);
                }})
                .catch(err => {{
                    document.getElementById('live_status_detail').innerText = 'Error initiating inquiry: ' + err;
                }});
            }}
            
            function pollSessionStatus() {{
                if (!currentSessionMemberId) return;
                
                fetch('/checkout/poll-associate?member_id=' + currentSessionMemberId)
                .then(res => {{
                    if (!res.ok) throw new Error("HTTP error " + res.status);
                    return res.json();
                }})
                .then(data => {{
                    if (data.status === 'none') {{
                        isLiveCheckoutActive = false;
                        if (livePollInterval) {{
                            clearInterval(livePollInterval);
                            livePollInterval = null;
                        }}
                        return;
                    }}
                    
                    if (data.is_ambassador !== undefined) {{
                        isAmbassador = data.is_ambassador;
                        const sel = document.getElementById('checkout_member_select');
                        if (sel) {{
                            const opt = sel.options[sel.selectedIndex];
                            if (opt) {{
                                opt.setAttribute('data-ambassador', isAmbassador ? 'Yes' : 'No');
                            }}
                        }}
                    }}
                    
                    const badge = document.getElementById('live_status_badge');
                    const detail = document.getElementById('live_status_detail');
                    
                    if (data.status === 'PendingInquiry') {{
                        badge.innerText = 'Pending';
                        badge.style.background = '#3b82f6';
                        detail.innerText = 'Inquiry sent. Waiting for member to play voice...';
                    }} else if (data.status === 'InquirySent') {{
                        badge.innerText = 'Speaking';
                        badge.style.background = '#f59e0b';
                        detail.innerText = 'Speaking voice inquiry on member\\\'s device...';
                    }} else if (data.status === 'ResponseReceived') {{
                        badge.innerText = 'Received';
                        badge.style.background = '#10b981';
                        detail.innerText = 'Received member response transcript: "' + data.member_response + '"';
                        
                        document.getElementById('checkout_transcript').value = data.member_response;
                        analyzeTranscript();
                        
                        clearInterval(livePollInterval);
                        livePollInterval = null;
                        
                        // Automated Dialogue Routing:
                        if (hasNeg) {{
                            if (selectedItemId) {{
                                if (!isAmbassador) {{
                                    // Not an ambassador: automatically propose enrollment!
                                    setTimeout(proposeAmbassadorEnrollment, 300);
                                }} else {{
                                    // Already an ambassador: automatically submit stock-out to pipeline!
                                    setTimeout(() => {{
                                        document.getElementById('checkout_oos_form').submit();
                                    }}, 800);
                                }}
                            }} else {{
                                // Negative intent but item not matched (not carried): propose new product suggestion!
                                setTimeout(proposeProductProposal, 300);
                            }}
                        }} else {{
                            // Satisfied customer (no stock gap): automatically close session after 800ms
                            setTimeout(closeCheckoutSession, 800);
                        }}
                    }} else if (data.status === 'EnrollmentProposed') {{
                        badge.innerText = 'Enrollment';
                        badge.style.background = '#3b82f6';
                        detail.innerText = 'Speaking enrollment offer on member\\\'s device...';
                    }} else if (data.status === 'EnrollmentResponseReceived') {{
                        badge.innerText = 'Decision';
                        badge.style.background = '#10b981';
                        detail.innerText = 'Member voice decision received: "' + data.enrollment_answer + '"';
                        
                        clearInterval(livePollInterval);
                        livePollInterval = null;
                        
                        const ans = data.enrollment_answer.toLowerCase();
                        const positiveWords = ['yes', 'yeah', 'sure', 'ok', 'enroll', 'join', 'sign up', 'interest', 'agree', 'please', 'would like to', 'yup', 'yep'];
                        const negativeWords = ['no', 'dont', "don't", 'decline', 'nah', 'not', 'skip', 'pass', 'thank', 'reject', 'cancel'];
                        
                        const hasPositive = positiveWords.some(w => ans.includes(w));
                        const hasNegative = negativeWords.some(w => ans.includes(w));
                        
                        // Check if this was a product suggestion proposal or ambassador enrollment proposal
                        const isProductProposal = data.associate_question.toLowerCase().includes("carry") || data.associate_question.toLowerCase().includes("suggest");
                        
                        if (isProductProposal) {{
                            if (hasPositive && !hasNegative) {{
                                document.getElementById('enrollment_agreed_box').style.display = 'block';
                                document.getElementById('enrollment_agreed_box').innerText = '✓ Member agreed to propose new product!';
                                document.getElementById('enrollment_agreed_box').style.borderColor = 'rgba(16, 185, 129, 0.2)';
                                document.getElementById('enrollment_agreed_box').style.color = '#10b981';
                                submitProductProposalFromCheckout();
                            }} else {{
                                document.getElementById('enrollment_agreed_box').style.display = 'block';
                                document.getElementById('enrollment_agreed_box').innerText = '✗ Member declined to propose new product.';
                                document.getElementById('enrollment_agreed_box').style.borderColor = 'rgba(239, 68, 68, 0.2)';
                                document.getElementById('enrollment_agreed_box').style.color = '#ef4444';
                                setTimeout(closeCheckoutSession, 800);
                            }}
                        }} else {{
                            // Ambassador enrollment flow
                            if (hasPositive && !hasNegative) {{
                                enrollMemberAsync();
                            }} else if (hasNegative) {{
                                document.getElementById('enrollment_agreed_box').style.display = 'block';
                                document.getElementById('enrollment_agreed_box').innerText = '✗ Member replied NO / declined enrollment.';
                                document.getElementById('enrollment_agreed_box').style.borderColor = 'rgba(239, 68, 68, 0.2)';
                                document.getElementById('enrollment_agreed_box').style.color = '#ef4444';
                                document.getElementById('submit_oos_pipeline_btn').style.display = 'block';
                                setTimeout(() => {{
                                    document.getElementById('checkout_oos_form').submit();
                                }}, 800);
                            }} else {{
                                // Ambiguous, questions, or transcript errors (e.g. "bachelor program") -> explain benefits!
                                setTimeout(explainAmbassadorBenefits, 300);
                            }}
                        }}
                        
                        document.getElementById('enroll_action_box').style.display = 'none';
                    }} else if (data.status === 'EnrollmentComplete') {{
                        badge.innerText = 'Enrolled!';
                        badge.style.background = '#10b981';
                        detail.innerText = 'Member enrolled as Ambassador! Proceeding with checkout...';
                        
                        // Update local ambassador state
                        isAmbassador = true;
                        const ambStatusText = document.getElementById('analysis_ambassador_status');
                        ambStatusText.innerText = 'SignalSense Ambassador (Active)';
                        ambStatusText.style.color = '#10b981';
                        document.getElementById('enroll_action_box').style.display = 'none';
                        document.getElementById('submit_enroll_ambassador').value = 'true';
                        
                        document.getElementById('enrollment_agreed_box').style.display = 'block';
                        document.getElementById('enrollment_agreed_box').innerText = '✓ Member enrolled! Now submitting stock-out report...';
                        document.getElementById('enrollment_agreed_box').style.borderColor = 'rgba(16, 185, 129, 0.2)';
                        document.getElementById('enrollment_agreed_box').style.color = '#10b981';
                        
                        clearInterval(livePollInterval);
                        livePollInterval = null;
                        
                        // Step 2: Now submit OOS pipeline after member TTS finishes enrollment confirmation
                        setTimeout(() => {{
                            document.getElementById('checkout_oos_form').submit();
                        }}, 4000);
                    }}
                }})
                .catch(err => {{
                    console.error("Poll session status error:", err);
                    document.getElementById('live_status_detail').innerText = '⚠️ Connection issue. Retrying polling...';
                }});
            }}
            
            function proposeAmbassadorEnrollment() {{
                if (!currentSessionMemberId) return;
                
                document.getElementById('live_status_detail').innerText = 'Sending enrollment voice prompt to member app...';
                document.getElementById('live_status_badge').innerText = 'Sending';
                document.getElementById('live_status_badge').style.background = '#3b82f6';
                
                const formData = new FormData();
                formData.append('member_id', currentSessionMemberId);
                
                fetch('/checkout/propose-enrollment', {{
                    method: 'POST',
                    body: formData
                }})
                .then(res => res.json())
                .then(data => {{
                    document.getElementById('live_status_detail').innerText = 'Enrollment prompt playing. Waiting for reply...';
                    if (livePollInterval) clearInterval(livePollInterval);
                    livePollInterval = setInterval(pollSessionStatus, 400);
                }})
                .catch(err => {{
                    console.error("Propose enrollment error:", err);
                    document.getElementById('live_status_detail').innerText = '⚠️ Error: Failed to send enrollment prompt.';
                    document.getElementById('live_status_badge').innerText = 'Error';
                    document.getElementById('live_status_badge').style.background = '#ef4444';
                }});
            }}
            
            function extractItemName(text) {{
                const keywords = ["couldn't find", "could not find", "did not find", "didn't find", "don't have", "do not have", "missing", "find"];
                let lower = text.toLowerCase();
                for (let kw of keywords) {{
                    let idx = lower.indexOf(kw);
                    if (idx !== -1) {{
                        let item = text.substring(idx + kw.length).trim();
                        item = item.replace(/^(the|a|an|some)\s+/i, '');
                        item = item.replace(/[.,\/#!$%\^&\*;:{{}}=\-_`~()?]/g, "");
                        return item;
                    }}
                }}
                return text;
            }}

            function proposeProductProposal() {{
                if (!currentSessionMemberId) return;
                
                const transcript = document.getElementById('checkout_transcript').value;
                const itemName = extractItemName(transcript) || "this item";
                
                document.getElementById('live_status_badge').innerText = 'Proposal';
                document.getElementById('live_status_badge').style.background = '#3b82f6';
                document.getElementById('live_status_detail').innerText = 'Proposing new product suggestion...';
                
                const formData = new FormData();
                formData.append('member_id', currentSessionMemberId);
                formData.append('item_name', itemName);
                
                fetch('/checkout/propose-proposal', {{
                    method: 'POST',
                    body: formData
                }})
                .then(res => res.json())
                .then(data => {{
                    document.getElementById('live_status_detail').innerText = 'Proposing new product suggestion: ' + itemName;
                    
                    if (livePollInterval) clearInterval(livePollInterval);
                    livePollInterval = setInterval(pollSessionStatus, 400);
                }})
                .catch(err => {{
                    console.error("Propose proposal error:", err);
                    document.getElementById('live_status_detail').innerText = 'Error proposing product suggestion. Completing checkout...';
                    setTimeout(closeCheckoutSession, 1200);
                }});
            }}

            function submitProductProposalFromCheckout() {{
                if (!currentSessionMemberId) return;
                
                document.getElementById('live_status_badge').innerText = 'Proposing';
                document.getElementById('live_status_badge').style.background = '#3b82f6';
                document.getElementById('live_status_detail').innerText = 'Logging product suggestion to merchants...';
                
                const formData = new FormData();
                formData.append('member_id', currentSessionMemberId);
                
                fetch('/checkout/submit-proposal-from-checkout', {{
                    method: 'POST',
                    body: formData
                }})
                .then(res => res.json())
                .then(data => {{
                    document.getElementById('live_status_detail').innerText = 'Proposal successfully logged! Completing checkout...';
                    setTimeout(closeCheckoutSession, 1200);
                }})
                .catch(err => {{
                    console.error("Propose error:", err);
                    document.getElementById('live_status_detail').innerText = 'Error proposing product. Completing checkout...';
                    setTimeout(closeCheckoutSession, 1200);
                }});
            }}

            function requestRepeatInquiry() {{
                if (!currentSessionMemberId) return;
                
                const formData = new FormData();
                formData.append('member_id', currentSessionMemberId);
                
                fetch('/checkout/request-repeat', {{
                    method: 'POST',
                    body: formData
                }})
                .then(res => res.json())
                .then(data => {{
                    document.getElementById('live_status_detail').innerText = 'Asking member to repeat missing item...';
                    document.getElementById('live_status_badge').innerText = 'Clarifying';
                    document.getElementById('live_status_badge').style.background = '#f59e0b';
                    
                    document.getElementById('checkout_transcript').value = '';
                    
                    if (livePollInterval) clearInterval(livePollInterval);
                    livePollInterval = setInterval(pollSessionStatus, 400);
                }})
                .catch(err => {{
                    console.error("Request repeat error:", err);
                    document.getElementById('live_status_detail').innerText = '⚠️ Error requesting repeat.';
                }});
            }}
            
            function explainAmbassadorBenefits() {{
                if (!currentSessionMemberId) return;
                
                const formData = new FormData();
                formData.append('member_id', currentSessionMemberId);
                
                fetch('/checkout/explain-benefits', {{
                    method: 'POST',
                    body: formData
                }})
                .then(res => res.json())
                .then(data => {{
                    document.getElementById('live_status_detail').innerText = 'Explaining benefits to member...';
                    document.getElementById('live_status_badge').innerText = 'Explaining';
                    document.getElementById('live_status_badge').style.background = '#3b82f6';
                    
                    if (livePollInterval) clearInterval(livePollInterval);
                    livePollInterval = setInterval(pollSessionStatus, 400);
                }})
                .catch(err => {{
                    console.error("Explain benefits error:", err);
                    document.getElementById('live_status_detail').innerText = '⚠️ Error: Failed to send benefits explanation.';
                }});
            }}
            
            function closeCheckoutSession() {{
                if (!currentSessionMemberId) return;
                
                const formData = new FormData();
                formData.append('member_id', currentSessionMemberId);
                
                fetch('/checkout/close', {{
                    method: 'POST',
                    body: formData
                }})
                .then(res => res.json())
                .then(data => {{
                    document.getElementById('live_status_detail').innerText = 'Checkout completed successfully.';
                    document.getElementById('live_status_badge').innerText = 'Completed';
                    document.getElementById('live_status_badge').style.background = '#10b981';
                    
                    setTimeout(() => {{
                        document.getElementById('checkout_live_status_panel').style.display = 'none';
                        document.getElementById('checkout_analysis_card').style.display = 'none';
                    }}, 3000);
                }})
                .catch(err => {{
                    console.error("Close checkout session error:", err);
                    document.getElementById('live_status_detail').innerText = '⚠️ Error closing session.';
                    document.getElementById('live_status_badge').innerText = 'Error';
                    document.getElementById('live_status_badge').style.background = '#ef4444';
                }});
            }}
            
            if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {{
                const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
                checkoutRecognition = new SpeechRecognition();
                checkoutRecognition.continuous = false;
                checkoutRecognition.interimResults = false;
                checkoutRecognition.lang = 'en-US';
                
                checkoutRecognition.onstart = function() {{
                    isCheckoutListening = true;
                    document.getElementById('checkout_mic_btn').style.background = '#10b981';
                    document.getElementById('checkout_speech_status').innerText = '🎙️ Listening... Speak response now!';
                }};
                
                checkoutRecognition.onend = function() {{
                    isCheckoutListening = false;
                    document.getElementById('checkout_mic_btn').style.background = '#ef4444';
                }};
                
                checkoutRecognition.onresult = function(event) {{
                    const transcript = event.results[0][0].transcript;
                    document.getElementById('checkout_transcript').value = transcript;
                    document.getElementById('checkout_speech_status').innerText = '✓ Speech captured.';
                    analyzeTranscript();
                }};
                
                checkoutRecognition.onerror = function(event) {{
                    document.getElementById('checkout_speech_status').innerText = '⚠️ Speech error: ' + event.error;
                }};
            }} else {{
                document.getElementById('checkout_speech_status').innerText = '⚠️ Native speech-to-text not supported in this browser.';
            }}
            
            function toggleCheckoutSpeech() {{
                if (!checkoutRecognition) return;
                if (isCheckoutListening) {{
                    checkoutRecognition.stop();
                }} else {{
                    checkoutRecognition.start();
                }}
            }}
            
            function simulateCheckoutVoice(text) {{
                document.getElementById('checkout_transcript').value = text;
                
                if (currentSessionMemberId) {{
                    const formData = new FormData();
                    formData.append('member_id', currentSessionMemberId);
                    formData.append('response', text);
                    
                    fetch('/checkout/respond-member-simulated', {{
                        method: 'POST',
                        body: formData
                    }}).then(() => {{
                        if (!livePollInterval) {{
                            livePollInterval = setInterval(pollSessionStatus, 1000);
                        }}
                    }});
                }} else {{
                    analyzeTranscript();
                }}
            }}
            
            function updateMemberStatus() {{
                const select = document.getElementById('checkout_member_select');
                if (!select) return;
                const opt = select.options[select.selectedIndex];
                if (!opt) return;
                isAmbassador = opt.getAttribute('data-ambassador') === 'Yes';
                
                const ambStatusText = document.getElementById('analysis_ambassador_status');
                const enrollBox = document.getElementById('enroll_action_box');
                const submitBtn = document.getElementById('submit_oos_pipeline_btn');
                const isTempEnrolled = document.getElementById('submit_enroll_ambassador').value === 'true';
                
                if (isAmbassador || isTempEnrolled) {{
                    ambStatusText.innerText = isAmbassador ? 'SignalSense Ambassador (Active)' : 'Enrolled (Pending Submission)';
                    ambStatusText.style.color = '#10b981';
                    enrollBox.style.display = 'none';
                    if (selectedItemId && hasNeg) {{
                        submitBtn.style.display = 'block';
                    }} else {{
                        submitBtn.style.display = 'none';
                    }}
                }} else {{
                    ambStatusText.innerText = 'Not Enrolled';
                    ambStatusText.style.color = '#f59e0b';
                    
                    if (selectedItemId && hasNeg) {{
                        enrollBox.style.display = 'block';
                    }} else {{
                        enrollBox.style.display = 'none';
                    }}
                    submitBtn.style.display = 'none';
                }}
                
                document.getElementById('submit_member_id').value = select.value;
                currentSessionMemberId = select.value;
            }}
            
            function enrollMember() {{
                isAmbassador = true;
                const ambStatusText = document.getElementById('analysis_ambassador_status');
                ambStatusText.innerText = 'Enrolled (Pending Submission)';
                ambStatusText.style.color = '#10b981';
                document.getElementById('enroll_action_box').style.display = 'none';
                document.getElementById('submit_enroll_ambassador').value = 'true';
                
                if (selectedItemId) {{
                    document.getElementById('submit_oos_pipeline_btn').style.display = 'block';
                }}
            }}
            
            function enrollMemberAsync() {{
                if (!currentSessionMemberId) return;
                
                document.getElementById('live_status_detail').innerText = 'Enrolling member in Ambassador program...';
                document.getElementById('live_status_badge').innerText = 'Enrolling';
                document.getElementById('live_status_badge').style.background = '#f59e0b';
                
                document.getElementById('enrollment_agreed_box').style.display = 'block';
                document.getElementById('enrollment_agreed_box').innerText = '⏳ Member replied YES! Enrolling in Ambassador program...';
                document.getElementById('enrollment_agreed_box').style.borderColor = 'rgba(245, 158, 11, 0.2)';
                document.getElementById('enrollment_agreed_box').style.color = '#f59e0b';
                
                const formData = new FormData();
                formData.append('member_id', currentSessionMemberId);
                
                fetch('/checkout/enroll-member', {{
                    method: 'POST',
                    body: formData
                }})
                .then(res => res.json())
                .then(data => {{
                    // Resume polling to pick up the EnrollmentComplete status
                    if (livePollInterval) clearInterval(livePollInterval);
                    livePollInterval = setInterval(pollSessionStatus, 400);
                }})
                .catch(err => {{
                    document.getElementById('live_status_detail').innerText = 'Error enrolling: ' + err;
                }});
            }}
            
            function analyzeTranscript() {{
                const text = document.getElementById('checkout_transcript').value.trim().toLowerCase().replace(/’/g, "'");
                const analysisCard = document.getElementById('checkout_analysis_card');
                
                if (!text) {{
                    analysisCard.style.display = 'none';
                    return;
                }}
                
                analysisCard.style.display = 'flex';
                
                const negKeywords = [
                    'missing', 'out of stock', 'empty', "couldn't", "could not", "not find", 
                    "no", "don't have", "unable", "can't", "cant", "cannot", "wasn't", 
                    "was not", "didn't", "did not", "unavailable", "out of", "ran out", 
                    "no more", "sold out", "gone", "empty shelf", "empty shelves", 
                    "not in stock", "not on the shelf", "not on shelf", "not here"
                ];
                hasNeg = negKeywords.some(kw => text.includes(kw));
                
                const oosDetectedSpan = document.getElementById('analysis_oos_detected');
                if (hasNeg) {{
                    oosDetectedSpan.innerText = 'Yes (Stock Out Indicated)';
                    oosDetectedSpan.style.color = '#ef4444';
                }} else {{
                    oosDetectedSpan.innerText = 'No (Satisfied checkout)';
                    oosDetectedSpan.style.color = '#10b981';
                }}
                
                selectedItemId = null;
                let matchedName = 'None';
                
                for (let item of masterItems) {{
                    const desc = item.ItemDescription.toLowerCase();
                    if (text.includes(desc) || desc.includes(text) || 
                        (text.includes('banana') && desc.includes('banana')) || 
                        (text.includes('apple') && desc.includes('apple')) ||
                        (text.includes('gala') && desc.includes('gala'))) {{
                        selectedItemId = item.ItemID;
                        matchedName = item.ItemDescription + ' (' + item.ItemID + ')';
                        break;
                    }}
                }}
                
                document.getElementById('analysis_matched_product').innerText = matchedName;
                document.getElementById('submit_item_id').value = selectedItemId;
                
                updateMemberStatus();
            }}
            
            let arrivalPollInterval = null;
            let activeArrivalMemberId = null;
            
            function initArrivalPolling() {{
                arrivalPollInterval = setInterval(pollArrivals, 2000);
            }}
            
            function pollArrivals() {{
                if (isLiveCheckoutActive) return;
                
                fetch('/checkout/active-arrivals')
                .then(res => res.json())
                .then(data => {{
                    const alertDiv = document.getElementById('checkout_arrival_alert');
                    const select = document.getElementById('checkout_member_select');
                    if (!alertDiv || !select) return;
                    
                    for (let opt of select.options) {{
                        opt.text = opt.text.replace(' (Arrived 📍)', '');
                    }}
                    
                    if (data.arrivals && data.arrivals.length > 0) {{
                        const arrivedId = data.arrivals[0];
                        activeArrivalMemberId = arrivedId;
                        
                        let memberName = "Member";
                        for (let opt of select.options) {{
                            if (opt.value === arrivedId) {{
                                memberName = opt.text.split(' (')[0];
                                opt.text = opt.text + ' (Arrived 📍)';
                                break;
                            }}
                        }}
                        
                        document.getElementById('arrival_alert_text').innerText = '🔔 Member ' + memberName + ' has arrived at the checkout counter!';
                        alertDiv.style.display = 'flex';
                        
                        // Automatically select and initiate live checkout voice inquiry!
                        select.value = arrivedId;
                        updateMemberStatus();
                        startLiveInquiry();
                        alertDiv.style.display = 'none';
                    }} else {{
                        alertDiv.style.display = 'none';
                        activeArrivalMemberId = null;
                    }}
                }});
            }}
            
            function acceptArrival() {{
                if (!activeArrivalMemberId) return;
                const select = document.getElementById('checkout_member_select');
                if (!select) return;
                select.value = activeArrivalMemberId;
                
                updateMemberStatus();
                
                document.getElementById('checkout_arrival_alert').style.display = 'none';
                startLiveInquiry();
            }}
            
            window.addEventListener('DOMContentLoaded', () => {{
                updateMemberStatus();
                initArrivalPolling();
                
                // Resuming active session if one exists
                const activeResumedMemberId = "{active_session_member_id}";
                if (activeResumedMemberId) {{
                    const select = document.getElementById('checkout_member_select');
                    if (select) {{
                        select.value = activeResumedMemberId;
                        updateMemberStatus();
                        currentSessionMemberId = activeResumedMemberId;
                        isLiveCheckoutActive = true;
                        document.getElementById('checkout_live_status_panel').style.display = 'flex';
                        if (livePollInterval) clearInterval(livePollInterval);
                        livePollInterval = setInterval(pollSessionStatus, 400);
                    }}
                }}
            }});
        </script>
        """
    else:
        workspace_html = f"""
        <div class="glass-card">
            <h2 style="color:var(--accent-blue)">📋 Assigned tasks</h2>
            <p style="color:var(--text-muted); font-size:0.85rem; margin-bottom:20px;">
                Select a task card below to open the review panel and execute decisions.
            </p>
            <div class="tasks-grid">
                {cards_html}
            </div>
        </div>
        """
        
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Associate App - Task Board</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        {GLASS_STYLE}
    </head>
    <body>
        <header>
            <h1>SignalSense AI</h1>
            <span class="role-badge">{role_badge}</span>
        </header>
        
        <main>
            {alert_html}
            
            <div class="role-tabs">
                <a href="/dashboard?role=Club_Associate" class="{associate_tab_class}">Club Associate Workspace</a>
                <a href="/dashboard?role=Merchant" class="{merchant_tab_class}">Merchant Workspace</a>
                <a href="/dashboard?role=Inventory_Associate" class="{inventory_tab_class}">Inventory Associate Workspace</a>
                <a href="/dashboard?role=Checkout_Associate" class="{checkout_tab_class}">Checkout Associate Workspace</a>
            </div>
            
            {workspace_html}
            
            <div class="glass-card">
                <h2>📈 Signal Processing History</h2>
                <table style="width:100%; border-collapse:collapse; text-align:left; font-size:0.95rem;">
                    <thead>
                        <tr style="border-bottom:2px solid var(--glass-border); color:var(--text-muted)">
                            <th style="padding:12px;">SignalID</th>
                            <th style="padding:12px;">Item Description</th>
                            <th style="padding:12px;">Club</th>
                            <th style="padding:12px;">Reporter</th>
                            <th style="padding:12px;">Resolution Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {history_rows}
                    </tbody>
                </table>
            </div>
        </main>
        
        <!-- Verification modal (Club Associate) -->
        <div id="verification_modal" class="modal">
            <div class="modal-content">
                <h2 id="modal_item_title" style="margin-bottom:10px; color:#fff;">Item Description</h2>
                <div style="display:flex; flex-direction:column; gap:10px; margin-bottom:20px; font-size:0.9rem;">
                    <div class="task-detail"><span>Signal ID:</span> <span id="modal_signal_id" style="color:var(--accent-blue)">S0000</span></div>
                    <div class="task-detail"><span>Item ID:</span> <span id="modal_item_id">I0000</span></div>
                    <div class="task-detail"><span>Club ID:</span> <span id="modal_club_id">C000</span></div>
                    <div class="task-detail"><span>Reporter Name:</span> <span id="modal_reporter_name">Jane</span></div>
                    <div class="task-detail"><span>Reporter Trust:</span> <span id="modal_reporter_trust">80%</span></div>
                    <div class="task-detail"><span>Shelf Stock Status:</span> <span style="color:var(--accent-red)">Reported Empty</span></div>
                </div>
                
                <h3 style="color:#fff; font-size:1.05rem; margin-bottom:10px;">Verification Workflow</h3>
                <p style="color:var(--text-muted); font-size:0.8rem; margin-bottom:15px;">
                    Go check the club shelf first. Is the item physically available on the shelf?
                </p>
                
                <div style="display:flex; gap:12px; margin-bottom:20px;">
                    <button class="btn btn-red" style="flex:1;" onclick="submitAction('false_alarm')">👍 Yes (False Alarm)</button>
                    <button class="btn" style="flex:1;" onclick="showBackroomVerification()">👎 No (Empty Shelf)</button>
                </div>
                
                <div id="backroom_panel" style="display:none; border-top:1px solid var(--glass-border); padding-top:20px; flex-direction:column; gap:10px;">
                    <h3 style="color:#fff; font-size:1.05rem;">Backroom Stock Status</h3>
                    <p style="color:var(--text-muted); font-size:0.8rem; margin-bottom:10px;">
                        Checking backroom database stock count...
                    </p>
                    <div class="task-detail" style="margin-bottom:15px;">
                        <span>Backroom Inventory Count:</span>
                        <span id="modal_backroom_qty" style="color:var(--accent-green)">0 items</span>
                    </div>
                    
                    <div id="restock_option" style="display:none;">
                        <p style="color:var(--accent-green); font-size:0.8rem; margin-bottom:15px;">
                            Stock is available in the backroom. Pull items and restock the shelf.
                        </p>
                        <button class="btn btn-green" style="width:100%;" onclick="submitAction('restock')">📦 Confirm Shelf Restocked</button>
                    </div>
                    
                    <div id="no_stock_option" style="display:none;">
                        <p style="color:var(--accent-red); font-size:0.8rem; margin-bottom:15px;">
                            No stock is available in the backroom. Mark as verified empty.
                        </p>
                        <button class="btn btn-orange" style="width:100%;" onclick="submitAction('verified_oos')">🚨 Confirm Out of Stock</button>
                    </div>
                </div>
                
                <button class="btn" style="background:transparent; border:1px solid var(--glass-border); margin-top:10px;" onclick="closeVerificationModal()">Cancel & Close</button>
            </div>
        </div>
        
        <!-- Merchant modal -->
        <div id="merchant_modal" class="modal">
            <div class="modal-content">
                <h2 id="merchant_item_title" style="margin-bottom:10px; color:#fff;">Candidate Item Description</h2>
                <div style="display:flex; flex-direction:column; gap:10px; margin-bottom:20px; font-size:0.9rem;">
                    <div class="task-detail"><span>Signal ID:</span> <span id="m_modal_signal_id" style="color:var(--accent-blue)">S0000</span></div>
                    <div class="task-detail"><span>Candidate ID:</span> <span id="m_modal_candidate_id">C0000</span></div>
                    <div class="task-detail"><span>Reporting Member:</span> <span id="m_modal_reporter_name">Jane</span></div>
                    <div class="task-detail"><span>Reporter Trust:</span> <span id="m_modal_reporter_trust">80%</span></div>
                    <div class="task-detail"><span>Upvotes Received:</span> <span id="m_modal_upvotes" style="color:var(--accent-yellow)">10 (Review Threshold: 15)</span></div>
                    <div class="task-detail"><span>Original Store:</span> <span id="m_modal_store">C100</span></div>
                </div>
                
                <h3 style="color:#fff; font-size:1.05rem; margin-bottom:8px;">Similar Products / Duplicate Risk</h3>
                <div style="background:rgba(255,255,255,0.03); border:1px solid var(--glass-border); border-radius:10px; padding:12px; max-height:100px; overflow-y:auto; font-size:0.8rem; margin-bottom:20px;">
                    <ul id="merchant_similar_list" style="list-style-type:none; display:flex; flex-direction:column; gap:6px; color:var(--text-muted);">
                        <li>No similar products detected in database.</li>
                    </ul>
                </div>
                
                <h3 style="color:#fff; font-size:1.05rem; margin-bottom:10px;">Merchant Decision</h3>
                
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:15px;">
                    <div class="action-btn-opt active" id="opt_explore" onclick="selectMerchantAction('explore')">🔍 Explore</div>
                    <div class="action-btn-opt" id="opt_approve_launch" onclick="selectMerchantAction('approve_launch')">🚀 Approve Launch</div>
                    <div class="action-btn-opt" id="opt_increase_threshold" onclick="selectMerchantAction('increase_threshold')">📈 Threshold +</div>
                    <div class="action-btn-opt" id="opt_archive" onclick="selectMerchantAction('archive')">📦 Archive</div>
                </div>
                
                <form id="merchant_action_form" action="/execute-merchant-action" method="POST" onsubmit="submitMerchantForm(event)" style="display:flex; flex-direction:column; gap:12px;">
                    <input type="hidden" id="form_merchant_signal_id" name="signal_id">
                    <input type="hidden" id="form_merchant_candidate_id" name="candidate_id">
                    <input type="hidden" id="form_merchant_action" name="action" value="explore">
                    
                    <input type="hidden" id="form_merchant_next_threshold" name="next_threshold">
                    <input type="hidden" id="form_merchant_launch_date" name="launch_date">
                    <input type="hidden" id="form_merchant_free_count" name="free_unit_eligible_count">
                    <input type="hidden" id="form_merchant_expiration" name="expiration_window">
                    <input type="hidden" id="form_merchant_archive_reason" name="archive_reason">
                    
                    <!-- Panels container -->
                    <div id="panel_explore" style="display:block;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">Define Next Review Threshold (Upvotes):</label>
                        <input type="number" id="input_explore_threshold" class="text-input" value="20" min="1">
                    </div>
                    
                    <div id="panel_approve_launch" style="display:none;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">Enter Launch Date:</label>
                        <input type="date" id="input_launch_date" class="text-input">
                        
                        <label style="font-size:0.85rem; color:var(--text-muted); margin-top:8px;">Number of Free-Unit Eligible Members:</label>
                        <input type="number" id="input_free_count" class="text-input" value="5" min="1">
                        
                        <label style="font-size:0.85rem; color:var(--text-muted); margin-top:8px;">Eligibility Expiration Window:</label>
                        <select id="select_expiration" class="text-input">
                            <option value="30 days">30 days</option>
                            <option value="15 days">15 days</option>
                            <option value="60 days">60 days</option>
                        </select>
                    </div>
                    
                    <div id="panel_increase_threshold" style="display:none;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">Set New Upvote Threshold:</label>
                        <input type="number" id="input_new_threshold" class="text-input" value="30" min="1">
                    </div>
                    
                    <div id="panel_archive" style="display:none;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">General Archive / Rejection Reason:</label>
                        <textarea id="input_archive_reason" class="text-input" style="height:60px;" placeholder="e.g., Low member demand, duplicate suggestion, or unsafe description."></textarea>
                    </div>
                    
                    <div style="display:flex; gap:12px; margin-top:10px;">
                        <button type="button" class="btn" style="flex:1; background:transparent; border:1px solid var(--glass-border);" onclick="closeMerchantModal()">Cancel</button>
                        <button type="submit" id="btn_submit_merchant" class="btn" style="flex:1;">Confirm Explore</button>
                    </div>
                </form>
            </div>
        </div>
        
        <!-- Inventory Associate modal -->
        <div id="inventory_modal" class="modal">
            <div class="modal-content">
                <h2 id="inv_item_title" style="margin-bottom:10px; color:#fff;">Inventory Exception Description</h2>
                <div style="display:flex; flex-direction:column; gap:10px; margin-bottom:20px; font-size:0.9rem;">
                    <div class="task-detail"><span>Signal ID:</span> <span id="i_modal_signal_id" style="color:var(--accent-blue)">S0000</span></div>
                    <div class="task-detail"><span>Item ID:</span> <span id="i_modal_item_id">I0000</span></div>
                    <div class="task-detail"><span>Club ID:</span> <span id="i_modal_club_id">C000</span></div>
                    <div class="task-detail"><span>Reporter Name:</span> <span id="i_modal_reporter_name">Jane</span></div>
                    <div class="task-detail"><span>Reporter Trust:</span> <span id="i_modal_reporter_trust">80%</span></div>
                    <div class="task-detail"><span>Lost Sales Today:</span> <span id="i_modal_lost_sales" style="color:var(--accent-red); font-weight:600;">3 units</span></div>
                </div>
                
                <h3 style="color:#fff; font-size:1.05rem; margin-bottom:8px;">Replenishment & Vendor Status</h3>
                <div style="background:rgba(255,255,255,0.03); border:1px solid var(--glass-border); border-radius:10px; padding:12px; font-size:0.85rem; display:flex; flex-direction:column; gap:6px; margin-bottom:20px;">
                    <div class="task-detail"><span>Vendor Name:</span> <span id="i_modal_vendor" style="color:#fff;">Del Monte Foods</span></div>
                    <div class="task-detail"><span>Last Order Date:</span> <span id="i_modal_last_order" style="color:var(--text-muted);">2026-06-25</span></div>
                    <div class="task-detail"><span>Carrier Status:</span> <span id="i_modal_carrier_status" style="color:var(--accent-yellow); font-weight:600;">In Transit - ETA 2 Days</span></div>
                </div>
                
                <h3 style="color:#fff; font-size:1.05rem; margin-bottom:10px;">Exception Resolution</h3>
                
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:15px;">
                    <div class="action-btn-opt active" id="opt_inv_expedite" onclick="selectInventoryAction('expedite')">🚀 Expedite</div>
                    <div class="action-btn-opt" id="opt_inv_adjust_forecast" onclick="selectInventoryAction('adjust_forecast')">📈 Forecast +</div>
                    <div class="action-btn-opt" id="opt_inv_transfer" onclick="selectInventoryAction('transfer')">🔄 Transfer</div>
                    <div class="action-btn-opt" id="opt_inv_vendor_issue" onclick="selectInventoryAction('vendor_issue')">⚠️ Vendor Issue</div>
                    <div class="action-btn-opt" id="opt_inv_monitor" onclick="selectInventoryAction('monitor')" style="grid-column: 1/-1;">⏱️ Monitor / No Action</div>
                </div>
                
                <form id="inventory_action_form" action="/execute-inventory-action" method="POST" onsubmit="submitInventoryForm(event)" style="display:flex; flex-direction:column; gap:12px;">
                    <input type="hidden" id="form_inventory_signal_id" name="signal_id">
                    <input type="hidden" id="form_inventory_item_id" name="item_id">
                    <input type="hidden" id="form_inventory_club_id" name="club_id">
                    <input type="hidden" id="form_inventory_action" name="action" value="expedite">
                    
                    <input type="hidden" id="form_inventory_res_plan" name="resolution_plan">
                    <input type="hidden" id="form_inventory_fore_adj" name="forecast_adjustment">
                    <input type="hidden" id="form_inventory_tf_source" name="transfer_source">
                    <input type="hidden" id="form_inventory_sup_const" name="supply_constraint">
                    
                    <!-- Panels container -->
                    <div id="panel_inv_expedite" style="display:block;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">Enter Expedited Resolution Plan:</label>
                        <textarea id="input_inv_res_plan" class="text-input" style="height:60px;" placeholder="e.g., Contact carrier to upgrade shipping option and advance delivery slot."></textarea>
                    </div>
                    
                    <div id="panel_inv_adjust_forecast" style="display:none;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">Increase Forecast & Reorder Signal:</label>
                        <select id="select_inv_fore_adj" class="text-input">
                            <option value="+20%">+20% Demand Forecast</option>
                            <option value="+50%">+50% Demand Forecast</option>
                            <option value="+10%">+10% Demand Forecast</option>
                        </select>
                    </div>
                    
                    <div id="panel_inv_transfer" style="display:none;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">Request Transfer From Nearby Club or DC:</label>
                        <select id="select_inv_tf_source" class="text-input">
                            <option value="Distribution Center 402">DC 402 (Atlanta)</option>
                            <option value="Club C102">Club C102 (North Atlanta)</option>
                            <option value="Club C103">Club C103 (East Atlanta)</option>
                        </select>
                    </div>
                    
                    <div id="panel_inv_vendor_issue" style="display:none;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">Select Supply Constraint Issue Type:</label>
                        <select id="select_inv_sup_const" class="text-input">
                            <option value="Logistics/Carrier Delay">Logistics/Carrier Delay</option>
                            <option value="Raw Material Shortage">Raw Material Shortage</option>
                            <option value="Crop/Seasonal Shortage">Crop/Seasonal Shortage</option>
                        </select>
                    </div>
                    
                    <div id="panel_inv_monitor" style="display:none;">
                        <label style="font-size:0.85rem; color:var(--text-muted);">Monitor Task Status:</label>
                        <p style="color:var(--accent-yellow); font-size:0.8rem; margin-top:4px;">
                            💡 This starts a 24-hour monitoring timer. If the item stock remains empty after the SLA timer fires, it escalates to the Inventory Manager automatically.
                        </p>
                    </div>
                    
                    <div style="display:flex; gap:12px; margin-top:10px;">
                        <button type="button" class="btn" style="flex:1; background:transparent; border:1px solid var(--glass-border);" onclick="closeInventoryModal()">Cancel</button>
                        <button type="submit" id="btn_submit_inventory" class="btn" style="flex:1;">Confirm Expedite</button>
                    </div>
                </form>
            </div>
        </div>
        
        <form id="action_form" action="/execute-action" method="POST" style="display:none;">
            <input type="hidden" id="form_signal_id" name="signal_id">
            <input type="hidden" id="form_action" name="action">
        </form>
        
        <script>
            let currentBackroomQty = 0;
            const similarMap = {similar_map_js};
            
            function openVerificationModal(signalId, description, itemId, clubId, memberName, memberTrust, onHand, backRoom) {{
                document.getElementById('modal_signal_id').innerText = signalId;
                document.getElementById('modal_item_title').innerText = description;
                document.getElementById('modal_item_id').innerText = itemId;
                document.getElementById('modal_club_id').innerText = clubId;
                document.getElementById('modal_reporter_name').innerText = memberName;
                document.getElementById('modal_reporter_trust').innerText = memberTrust;
                document.getElementById('modal_backroom_qty').innerText = backRoom + ' items';
                document.getElementById('form_signal_id').value = signalId;
                
                currentBackroomQty = backRoom;
                
                // Reset panel states
                document.getElementById('backroom_panel').style.display = 'none';
                document.getElementById('restock_option').style.display = 'none';
                document.getElementById('no_stock_option').style.display = 'none';
                
                document.getElementById('verification_modal').classList.add('active');
            }}
            
            function closeVerificationModal() {{
                document.getElementById('verification_modal').classList.remove('active');
            }}
            
            function openMerchantModal(signalId, candidateId, description, memberName, memberTrust, upvotes, threshold, storeWhereFound) {{
                document.getElementById('m_modal_signal_id').innerText = signalId;
                document.getElementById('m_modal_candidate_id').innerText = candidateId;
                document.getElementById('merchant_item_title').innerText = description;
                document.getElementById('m_modal_reporter_name').innerText = memberName;
                document.getElementById('m_modal_reporter_trust').innerText = memberTrust;
                document.getElementById('m_modal_upvotes').innerText = upvotes + ' (Review Threshold: ' + threshold + ')';
                document.getElementById('m_modal_store').innerText = storeWhereFound && storeWhereFound !== 'None' ? storeWhereFound : 'N/A';
                
                document.getElementById('form_merchant_signal_id').value = signalId;
                document.getElementById('form_merchant_candidate_id').value = candidateId;
                
                // Populate dynamic similar list
                const similarList = document.getElementById('merchant_similar_list');
                similarList.innerHTML = '';
                const matches = similarMap[candidateId] || [];
                if (matches.length === 0) {{
                    similarList.innerHTML = '<li>🎉 No similar products detected. Low duplicate risk!</li>';
                }} else {{
                    matches.forEach(item => {{
                        const li = document.createElement('li');
                        li.innerText = '⚠️ ' + item;
                        similarList.appendChild(li);
                    }});
                }}
                
                // Default date to today
                document.getElementById('input_launch_date').value = new Date().toISOString().split('T')[0];
                
                // Reset to default Action
                selectMerchantAction('explore');
                
                document.getElementById('merchant_modal').classList.add('active');
            }}
            
            function closeMerchantModal() {{
                document.getElementById('merchant_modal').classList.remove('active');
            }}
            
            function openInventoryModal(signalId, itemId, description, clubId, memberName, memberTrust, lostSales, onHand, backRoom) {{
                document.getElementById('i_modal_signal_id').innerText = signalId;
                document.getElementById('i_modal_item_id').innerText = itemId;
                document.getElementById('inv_item_title').innerText = description;
                document.getElementById('i_modal_club_id').innerText = clubId;
                document.getElementById('i_modal_reporter_name').innerText = memberName;
                document.getElementById('i_modal_reporter_trust').innerText = memberTrust;
                document.getElementById('i_modal_lost_sales').innerText = lostSales + ' units';
                
                document.getElementById('form_inventory_signal_id').value = signalId;
                document.getElementById('form_inventory_item_id').value = itemId;
                document.getElementById('form_inventory_club_id').value = clubId;
                
                // Dynamic Vendor info mock
                let vendorName = "Del Monte Foods";
                if (description.toLowerCase().includes("banana") || description.toLowerCase().includes("apple")) {{
                    vendorName = "Fresh Del Monte Produce";
                }} else if (description.toLowerCase().includes("coconut") || description.toLowerCase().includes("cake")) {{
                    vendorName = "Dole Food Company";
                }}
                document.getElementById('i_modal_vendor').innerText = vendorName;
                
                // Default date to 4 days ago
                const lastOrder = new Date();
                lastOrder.setDate(lastOrder.getDate() - 4);
                document.getElementById('i_modal_last_order').innerText = lastOrder.toISOString().split('T')[0];
                
                // Reset to default Action
                selectInventoryAction('expedite');
                
                document.getElementById('inventory_modal').classList.add('active');
            }}
            
            function closeInventoryModal() {{
                document.getElementById('inventory_modal').classList.remove('active');
            }}
            
            function selectMerchantAction(actionType) {{
                document.getElementById('form_merchant_action').value = actionType;
                
                // Toggle active state of option buttons
                document.querySelectorAll('.action-btn-opt').forEach(el => el.classList.remove('active'));
                document.getElementById('opt_' + actionType).classList.add('active');
                
                // Toggle active state of panels
                document.getElementById('panel_explore').style.display = (actionType === 'explore') ? 'block' : 'none';
                document.getElementById('panel_approve_launch').style.display = (actionType === 'approve_launch') ? 'block' : 'none';
                document.getElementById('panel_increase_threshold').style.display = (actionType === 'increase_threshold') ? 'block' : 'none';
                document.getElementById('panel_archive').style.display = (actionType === 'archive') ? 'block' : 'none';
                
                // Toggle action submit buttons
                document.getElementById('btn_submit_merchant').innerText = 'Confirm ' + (actionType.charAt(0).toUpperCase() + actionType.slice(1).replace('_', ' '));
            }}
            
            function submitMerchantForm(e) {{
                e.preventDefault();
                const action = document.getElementById('form_merchant_action').value;
                
                if (action === 'explore') {{
                    document.getElementById('form_merchant_next_threshold').value = document.getElementById('input_explore_threshold').value;
                }} else if (action === 'approve_launch') {{
                    document.getElementById('form_merchant_launch_date').value = document.getElementById('input_launch_date').value;
                    document.getElementById('form_merchant_free_count').value = document.getElementById('input_free_count').value;
                    document.getElementById('form_merchant_expiration').value = document.getElementById('select_expiration').value;
                }} else if (action === 'increase_threshold') {{
                    document.getElementById('form_merchant_next_threshold').value = document.getElementById('input_new_threshold').value;
                }} else if (action === 'archive') {{
                    document.getElementById('form_merchant_archive_reason').value = document.getElementById('input_archive_reason').value;
                }}
                
                document.getElementById('merchant_action_form').submit();
            }}
            
            function selectInventoryAction(actionType) {{
                document.getElementById('form_inventory_action').value = actionType;
                
                // Toggle active state of option buttons
                document.querySelectorAll('#inventory_modal .action-btn-opt').forEach(el => el.classList.remove('active'));
                document.getElementById('opt_inv_' + actionType).classList.add('active');
                
                // Toggle active state of panels
                document.getElementById('panel_inv_expedite').style.display = (actionType === 'expedite') ? 'block' : 'none';
                document.getElementById('panel_inv_adjust_forecast').style.display = (actionType === 'adjust_forecast') ? 'block' : 'none';
                document.getElementById('panel_inv_transfer').style.display = (actionType === 'transfer') ? 'block' : 'none';
                document.getElementById('panel_inv_vendor_issue').style.display = (actionType === 'vendor_issue') ? 'block' : 'none';
                document.getElementById('panel_inv_monitor').style.display = (actionType === 'monitor') ? 'block' : 'none';
                
                // Toggle action submit buttons
                document.getElementById('btn_submit_inventory').innerText = 'Confirm ' + (actionType.charAt(0).toUpperCase() + actionType.slice(1).replace('_', ' '));
            }}
            
            function submitInventoryForm(e) {{
                e.preventDefault();
                const action = document.getElementById('form_inventory_action').value;
                
                if (action === 'expedite') {{
                    document.getElementById('form_inventory_res_plan').value = document.getElementById('input_inv_res_plan').value;
                }} else if (action === 'adjust_forecast') {{
                    document.getElementById('form_inventory_fore_adj').value = document.getElementById('select_inv_fore_adj').value;
                }} else if (action === 'transfer') {{
                    document.getElementById('form_inventory_tf_source').value = document.getElementById('select_inv_tf_source').value;
                }} else if (action === 'vendor_issue') {{
                    document.getElementById('form_inventory_sup_const').value = document.getElementById('select_inv_sup_const').value;
                }}
                
                document.getElementById('inventory_action_form').submit();
            }}
            
            function showBackroomVerification() {{
                document.getElementById('backroom_panel').style.display = 'flex';
                if (currentBackroomQty > 0) {{
                    document.getElementById('restock_option').style.display = 'block';
                    document.getElementById('no_stock_option').style.display = 'none';
                }} else {{
                    document.getElementById('restock_option').style.display = 'none';
                    document.getElementById('no_stock_option').style.display = 'block';
                }}
            }}
            
            function submitAction(actionType) {{
                document.getElementById('form_action').value = actionType;
                document.getElementById('action_form').submit();
            }}
        </script>
    </body>
    </html>
    """
    response = HTMLResponse(content=html)
    if not token_valid:
        token_claims = {"role": role}
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
# 3. Action Execution Handlers
# ------------------------------------------------------------------------------
@app.post("/execute-action")
async def execute_action(request: Request, signal_id: str = Form(...), action: str = Form(...)):
    try:
        # Extract jti from request cookies
        token_cookie = request.cookies.get("session_token")
        jti = None
        if token_cookie:
            try:
                claims = verify_access_token(token_cookie)
                jti = claims.get("jti")
            except Exception:
                pass
        result = await invoke_agent_action(signal_id=signal_id, action=action, jti=jti)
        msg = result.get("message") or f"Successfully executed action '{action}' on Signal S{signal_id}!"
        return RedirectResponse(
            url=f"/dashboard?success={msg}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard?error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

@app.post("/execute-merchant-action")
async def execute_merchant_action(
    request: Request,
    signal_id: str = Form(...),
    candidate_id: str = Form(...),
    action: str = Form(...),
    next_threshold: Optional[int] = Form(None),
    launch_date: Optional[str] = Form(None),
    free_unit_eligible_count: Optional[int] = Form(None),
    expiration_window: Optional[str] = Form(None),
    archive_reason: Optional[str] = Form(None)
):
    try:
        # Extract jti from request cookies
        token_cookie = request.cookies.get("session_token")
        jti = None
        if token_cookie:
            try:
                claims = verify_access_token(token_cookie)
                jti = claims.get("jti")
            except Exception:
                pass

        # Construct payload for backend runner
        payload = {
            "signal_type": "MerchantAction",
            "signal_id": signal_id,
            "candidate_id": candidate_id,
            "action": action,
            "next_threshold": next_threshold,
            "launch_date": launch_date,
            "free_unit_eligible_count": free_unit_eligible_count,
            "expiration_window": expiration_window,
            "archive_reason": archive_reason
        }
        
        try:
            async with httpx.AsyncClient() as client:
                adk_payload = {
                    "app_name": "signalsense_agent",
                    "user_id": "operations-app",
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
                token_claims = {"role": "Associate"}
                if jti:
                    token_claims["jti"] = jti
                token = create_access_token(token_claims)
                response = await client.post(
                    f"{BACKEND_URL}/run",
                    json=adk_payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=3.0
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as hse:
            error_detail = "Merchant action rejected by backend"
            try:
                error_detail = hse.response.json().get("detail", error_detail)
            except Exception:
                pass
            raise ValueError(error_detail)
            
        # Check backend response
        events = response.json()
        status_str = "Success"
        msg_str = ""
        for event in reversed(events):
            if isinstance(event, dict) and event.get("output"):
                output_data = event["output"]
                if isinstance(output_data, dict):
                    status_str = output_data.get("status", "Success")
                    msg_str = output_data.get("message", "")
                    break
        if status_str in ("Error", "Rejected"):
            raise ValueError(msg_str or "Merchant action was rejected by backend.")
            
        return RedirectResponse(
            url=f"/dashboard?role=Merchant&success={msg_str or 'Successfully executed merchant action!'}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard?role=Merchant&error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

@app.post("/execute-inventory-action")
async def execute_inventory_action(
    request: Request,
    signal_id: str = Form(...),
    item_id: str = Form(...),
    club_id: str = Form(...),
    action: str = Form(...),
    resolution_plan: Optional[str] = Form(None),
    forecast_adjustment: Optional[str] = Form(None),
    transfer_source: Optional[str] = Form(None),
    supply_constraint: Optional[str] = Form(None)
):
    try:
        # Extract jti from request cookies
        token_cookie = request.cookies.get("session_token")
        jti = None
        if token_cookie:
            try:
                claims = verify_access_token(token_cookie)
                jti = claims.get("jti")
            except Exception:
                pass

        # Construct payload for backend runner
        payload = {
            "signal_type": "InventoryAction",
            "signal_id": signal_id,
            "item_id": item_id,
            "club_id": club_id,
            "action": action,
            "resolution_plan": resolution_plan,
            "forecast_adjustment": forecast_adjustment,
            "transfer_source": transfer_source,
            "supply_constraint": supply_constraint
        }
        
        try:
            async with httpx.AsyncClient() as client:
                adk_payload = {
                    "app_name": "signalsense_agent",
                    "user_id": "operations-app",
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
                token_claims = {"role": "Associate"}
                if jti:
                    token_claims["jti"] = jti
                token = create_access_token(token_claims)
                response = await client.post(
                    f"{BACKEND_URL}/run",
                    json=adk_payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=3.0
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as hse:
            error_detail = "Inventory action rejected by backend"
            try:
                error_detail = hse.response.json().get("detail", error_detail)
            except Exception:
                pass
            raise ValueError(error_detail)
            
        # Check backend response
        events = response.json()
        status_str = "Success"
        msg_str = ""
        for event in reversed(events):
            if isinstance(event, dict) and event.get("output"):
                output_data = event["output"]
                if isinstance(output_data, dict):
                    status_str = output_data.get("status", "Success")
                    msg_str = output_data.get("message", "")
                    break
        if status_str in ("Error", "Rejected"):
            raise ValueError(msg_str or "Inventory action was rejected by backend.")
            
        return RedirectResponse(
            url=f"/dashboard?role=Inventory_Associate&success={msg_str or 'Successfully executed inventory action!'}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard?role=Inventory_Associate&error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

@app.post("/execute-checkout-oos")
async def execute_checkout_oos(
    request: Request,
    member_id: str = Form(...),
    item_id: str = Form(...),
    enroll_ambassador: str = Form("false")
):
    custom_completed_msg = None
    try:
        # Extract jti from request cookies
        token_cookie = request.cookies.get("session_token")
        jti = None
        if token_cookie:
            try:
                claims = verify_access_token(token_cookie)
                jti = claims.get("jti")
            except Exception:
                pass

        # 1. Enroll if selected
        if enroll_ambassador == "true":
            execute_db("UPDATE members SET Ambassador = 'Yes' WHERE MemberID = ?", (member_id,))
            
        # 2. Invoke the backend agent on OOS signal
        payload = {
            "signal_type": "OOS",
            "member_id": member_id,
            "club_id": "C100",
            "item_id": item_id
        }
        
        events = []
        try:
            async with httpx.AsyncClient() as client:
                adk_payload = {
                    "app_name": "signalsense_agent",
                    "user_id": "operations-app",
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
                token_claims = {"role": "Associate"}
                if jti:
                    token_claims["jti"] = jti
                token = create_access_token(token_claims)
                response = await client.post(
                    f"{BACKEND_URL}/run",
                    json=adk_payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=3.0
                )
                response.raise_for_status()
                events = response.json()
        except Exception as http_err:
            if not fallback_runner:
                raise ValueError(f"HTTP call to backend failed and in-process fallback is disabled. Connection error: {http_err}")
            
            print(f"HTTP call to backend failed ({http_err}). Falling back to in-process execution in Operations Dashboard...")
            
            session_id = str(uuid.uuid4())
            new_message = genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=json.dumps(payload))],
            )
            
            async with aclosing(
                fallback_runner.run_async(
                    user_id="operations-app",
                    session_id=session_id,
                    new_message=new_message,
                )
            ) as agen:
                async for event in agen:
                    if hasattr(event, 'model_dump'):
                        events.append(event.model_dump())
                    elif isinstance(event, dict):
                        events.append(event)
            
        # Check backend response
        status_str = "Success"
        msg_str = ""
        for event in reversed(events):
            if isinstance(event, dict) and event.get("output"):
                output_data = event["output"]
                if isinstance(output_data, dict):
                    status_str = output_data.get("status", "Success")
                    msg_str = output_data.get("message", "")
                    break
        if status_str in ("Error", "Rejected"):
            raise ValueError(msg_str or "OOS action was rejected by backend.")
            
        enroll_status = "Enrolled member & triggered pipeline." if enroll_ambassador == "true" else "Triggered stock-out pipeline."
        full_msg = f"{enroll_status} {msg_str}"
        
        # Set custom completed message for the member's device to provide feedback on the OOS report!
        custom_completed_msg = f"Thank you! Your checkout is complete. {msg_str}"
        
        return RedirectResponse(
            url=f"/dashboard?role=Checkout_Associate&success={full_msg}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        err_msg = str(e)
        if "already reported" in err_msg.lower() or "duplicate" in err_msg.lower() or "under review" in err_msg.lower():
            enroll_status = "Enrolled member!" if enroll_ambassador == "true" else "Checkout completed."
            full_msg = f"{enroll_status} (Note: Stock-out report was already submitted and is under review)"
            custom_completed_msg = "Thank you! Your checkout is complete. Note that this item was already reported and is under review."
            return RedirectResponse(
                url=f"/dashboard?role=Checkout_Associate&success={full_msg}",
                status_code=status.HTTP_303_SEE_OTHER
            )
        return RedirectResponse(
            url=f"/dashboard?role=Checkout_Associate&error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    finally:
        try:
            msg = custom_completed_msg or "Thank you! Your checkout is complete. Your out-of-stock report has been successfully submitted!"
            execute_db(
                "UPDATE checkout_sessions SET Status = 'Completed', AssociateQuestion = ?, LastUpdated = ? WHERE MemberID = ?",
                (msg, datetime.datetime.now().isoformat(), member_id)
            )
        except Exception as e:
            print(f"Database error completing checkout session: {e}")

@app.post("/checkout/enroll-member")
async def enroll_member_checkout(request: Request, member_id: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        # 1. Enroll the member as an Ambassador in the members table
        execute_db("UPDATE members SET Ambassador = 'Yes' WHERE MemberID = ?", (member_id,))
        
        # 2. Update session to EnrollmentComplete with a congratulatory message
        msg = "Congratulations! You have been successfully enrolled in the SignalSense Ambassador program! We are now processing your checkout and submitting your stock report..."
        execute_db(
            "UPDATE checkout_sessions SET Status = 'EnrollmentComplete', "
            "AssociateQuestion = ?, LastUpdated = ? WHERE MemberID = ?",
            (msg, datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success", "message": "Member enrolled and session updated."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/start")
async def start_checkout_session(request: Request, member_id: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        # Create or replace active session
        execute_db(
            "INSERT OR REPLACE INTO checkout_sessions (MemberID, Status, AssociateQuestion, MemberResponse, EnrollmentAnswer, MatchedItemID, LastUpdated) "
            "VALUES (?, 'PendingInquiry', 'Were you able to find everything you came to buy today?', NULL, NULL, NULL, ?)",
            (member_id, datetime.datetime.now().isoformat())
        )
        return {"status": "success", "message": "Voice inquiry sent to member app."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/propose-enrollment")
async def propose_enrollment(request: Request, member_id: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        execute_db(
            "UPDATE checkout_sessions SET Status = 'EnrollmentProposed', "
            "AssociateQuestion = 'Would you like to enroll in our free Ambassador program to earn points for reporting empty shelves?', "
            "EnrollmentAnswer = NULL, LastUpdated = ? WHERE MemberID = ?",
            (datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success", "message": "Enrollment voice inquiry sent to member app."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/propose-proposal")
async def propose_proposal(request: Request, member_id: str = Form(...), item_name: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        question_text = f"We don't currently carry {item_name}. Would you like to suggest it as a new product for our merchants to consider?"
        execute_db(
            "UPDATE checkout_sessions SET Status = 'ProposalProposed', AssociateQuestion = ?, MemberResponse = ?, LastUpdated = ? WHERE MemberID = ?",
            (question_text, item_name, datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success", "message": "Product proposal inquiry sent to member app."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/submit-proposal-from-checkout")
async def submit_proposal_from_checkout(request: Request, member_id: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        import random
        # Get the uncarried item name stored in MemberResponse
        row = query_db("SELECT MemberResponse FROM checkout_sessions WHERE MemberID = ?", (member_id,), one=True)
        item_name = row["MemberResponse"] if row else "New Product"
        
        conn = get_db_conn()
        cursor = conn.cursor()
        
        today_str = datetime.date.today().isoformat()
        candidate_id = f"P{int(datetime.datetime.now().timestamp())}{random.randint(10, 99)}"
        signal_id = f"S{int(datetime.datetime.now().timestamp())}{random.randint(10, 99)}"
        
        # 1. Insert new candidate product
        cursor.execute(
            "INSERT INTO candidate_products (CandidateID, ItemDescription, PhotoURL, StoreWhereFound, MemberIDProposer, ProposalDate, UpVotes, Status, Threshold) "
            "VALUES (?, ?, NULL, NULL, ?, ?, 1, 'New', 10)",
            (candidate_id, f"Produce - {item_name} (Trending Items)", member_id, today_str)
        )
        
        # 2. Log signal
        cursor.execute(
            "INSERT INTO signals (SignalID, MemberID, ClubID, SignalType, ItemID, CandidateID, Status, AssignedRole, Created) "
            "VALUES (?, ?, NULL, 'ProductSuggestion', NULL, ?, 'New', 'Merchant', ?)",
            (signal_id, member_id, candidate_id, today_str)
        )
        
        # 3. Check if member is Ambassador to award points
        cursor.execute("SELECT Ambassador FROM members WHERE MemberID = ?", (member_id,))
        m_row = cursor.fetchone()
        is_ambassador = m_row and m_row[0] == "Yes"
        
        points = 0
        if is_ambassador:
            cursor.execute("SELECT Points, TrustIncrease FROM reward_rules WHERE RuleID = 'RR002'")
            pts_res = cursor.fetchone()
            points = pts_res[0] if pts_res else 1
            trust = pts_res[1] if pts_res else 0
            
            cursor.execute(
                "UPDATE members SET SamsPoints = SamsPoints + ?, TrustScore = TrustScore + ? WHERE MemberID = ?",
                (points, trust, member_id)
            )
            
        conn.commit()
        conn.close()
        
        # Update session question to show success message
        msg = f"Thank you! Your checkout is complete. Your product proposal for '{item_name}' was submitted!"
        if is_ambassador and points > 0:
            msg += f" Awarded {points} points."
            
        execute_db(
            "UPDATE checkout_sessions SET Status = 'Done', AssociateQuestion = ?, LastUpdated = ? WHERE MemberID = ?",
            (msg, datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success", "message": "Proposal successfully submitted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/checkout/member-realtime-status")
async def member_realtime_status(request: Request, member_id: str):
    # Verify JWT associate/checkout role
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden")
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    row = query_db("SELECT Ambassador FROM members WHERE MemberID = ?", (member_id,), one=True)
    if not row:
        return {"ambassador": "No"}
    return {"ambassador": row["Ambassador"]}

@app.get("/checkout/poll-associate")
async def poll_associate(request: Request, member_id: str):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        row = query_db(
            "SELECT c.Status, c.AssociateQuestion, c.MemberResponse, c.EnrollmentAnswer, c.MatchedItemID, m.Ambassador "
            "FROM checkout_sessions c "
            "LEFT JOIN members m ON c.MemberID = m.MemberID "
            "WHERE c.MemberID = ?", (member_id,), one=True
        )
        if not row:
            return {"status": "none"}
        is_amb_val = row["Ambassador"] == "Yes"
        return {
            "status": row["Status"],
            "associate_question": row["AssociateQuestion"],
            "member_response": row["MemberResponse"],
            "enrollment_answer": row["EnrollmentAnswer"],
            "matched_item_id": row["MatchedItemID"],
            "is_ambassador": is_amb_val
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/respond-member-simulated")
async def respond_member_simulated(request: Request, member_id: str = Form(...), response: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        execute_db(
            "UPDATE checkout_sessions SET Status = 'ResponseReceived', MemberResponse = ?, "
            "LastUpdated = ? WHERE MemberID = ?",
            (response, datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/request-repeat")
async def request_repeat(request: Request, member_id: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        execute_db(
            "UPDATE checkout_sessions SET Status = 'PendingInquiry', "
            "AssociateQuestion = ?, "
            "MemberResponse = NULL, LastUpdated = ? WHERE MemberID = ?",
            ("I'm sorry, I didn't quite catch that. Could you please repeat which item was missing?",
             datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success", "message": "Repeat request sent to member app."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/explain-benefits")
async def explain_benefits(request: Request, member_id: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        execute_db(
            "UPDATE checkout_sessions SET Status = 'EnrollmentProposed', "
            "AssociateQuestion = ?, "
            "EnrollmentAnswer = NULL, LastUpdated = ? WHERE MemberID = ?",
            ("Our Ambassador program is free! You earn 10 Sam's Points for every verified out-of-stock item you report. These points can be redeemed for discounts. Would you like to sign up?",
             datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success", "message": "Benefits explanation sent to member app."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/close")
async def close_checkout_session(request: Request, member_id: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

    try:
        execute_db(
            "UPDATE checkout_sessions SET Status = 'Completed', "
            "AssociateQuestion = 'Thank you for shopping with us! Have a wonderful day!', "
            "LastUpdated = ? WHERE MemberID = ?",
            (datetime.datetime.now().isoformat(), member_id)
        )
        return {"status": "success", "message": "Checkout session closed."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/checkout/complete-close")
async def checkout_complete_close(request: Request, member_id: str = Form(...)):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
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

@app.get("/checkout/active-arrivals")
async def get_active_arrivals(request: Request):
    # Verify JWT associate role
    token = request.cookies.get("session_token") or (
        request.headers.get("Authorization").split(" ")[1]
        if request.headers.get("Authorization") and request.headers.get("Authorization").startswith("Bearer ")
        else None
    )
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
    try:
        claims = verify_access_token(token)
        if claims.get("role") not in ("Club_Associate", "Merchant", "Inventory_Associate", "Checkout_Associate"):
            raise HTTPException(status_code=403, detail="Forbidden: Unprivileged role")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")
    try:
        rows = query_db("SELECT MemberID FROM checkout_sessions WHERE Status = 'ReadyToCheckout'")
        return {"arrivals": [r["MemberID"] for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8085)
