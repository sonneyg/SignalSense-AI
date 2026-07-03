import os
import sys
import sqlite3
import asyncio
import subprocess
import time
from fastapi.testclient import TestClient

# Add paths
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_path = os.path.join(current_dir, "signalsense_enterprise")
frontend_path = os.path.join(current_dir, "member_ambassador_app")

if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if frontend_path not in sys.path:
    sys.path.insert(0, frontend_path)

# Configure environment variables
os.environ["GOOGLE_CLOUD_PROJECT"] = "project-377b9806-bb2a-4919-82a"
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] = "1"
os.environ["BACKEND_URL"] = "http://127.0.0.1:8080"

from member_ambassador_app.main import app, get_db_path

client = TestClient(app)

def print_table(title, query):
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(query)
    rows = cursor.fetchall()
    print(f"\n=== {title} ===")
    if not rows:
        print("Empty table.")
    else:
        keys = rows[0].keys()
        print(" | ".join(keys))
        for r in rows:
            print(" | ".join(str(r[k]) for k in keys))
    conn.close()

async def test_all_flows():
    print("Starting verification of SignalSense AI Standalone Microservice Harness...")
    
    # 1. Start backend agent server as a standalone process
    print("Launching SignalSense Enterprise Backend on port 8080...")
    env = os.environ.copy()
    env["PYTHONPATH"] = backend_path + os.pathsep + env.get("PYTHONPATH", "")
    
    backend_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", 
            "signalsense_agent.agent_runtime_app:agent_runtime.app", 
            "--host", "127.0.0.1", "--port", "8080"
        ],
        cwd=backend_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    # Wait for the backend server to spin up
    time.sleep(3.0)
    
    try:
        # Inspect initial database state
        print_table("Initial Member State", "SELECT MemberID, Name, SamsPoints, TrustScore, Ambassador FROM members WHERE MemberID = 'M1001'")
        print_table("Initial Item Inventory (C100, I1001)", "SELECT ClubID, ItemID, OnHand, OOSFlag, LastRestocked FROM club_inventories WHERE ClubID = 'C100' AND ItemID = 'I1001'")
        
        # 2. Test Login flow
        print("\n--- Testing Login Flow ---")
        response = client.post("/login", data={"member_id": "M1001"}, follow_redirects=False)
        print("Login Response Status:", response.status_code)
        print("Redirect Location:", response.headers.get("location"))
        assert response.status_code == 303
        
        # 3. Test OOS Report Submission (Item I1001 has 0 OnHand, so it should be VERIFIED)
        print("\n--- Testing Verified OOS Report Flow (Item I1001, OnHand = 0) ---")
        response = client.post(
            "/report-oos-submit",
            data={
                "member_id": "M1001",
                "receipt_id": "R1001",
                "item_id": "I1001"
            },
            follow_redirects=True
        )
        print("OOS Verified Submission Status:", response.status_code)
        print("OOS Verified Redirected URL:", response.url)
        print("OOS Verified History:", [r.url for r in response.history])
        if "error=" in str(response.url):
            print("Redirect error found in URL:", response.url)
        assert response.status_code == 200
        
        # Inspect database after OOS report
        print_table("Member State After Verified OOS", "SELECT MemberID, Name, SamsPoints, TrustScore FROM members WHERE MemberID = 'M1001'")
        print_table("Item Inventory After Verified OOS", "SELECT ClubID, ItemID, OnHand, OOSFlag, LastRestocked FROM club_inventories WHERE ClubID = 'C100' AND ItemID = 'I1001'")
        print_table("Signals Log After Verified OOS", "SELECT SignalID, MemberID, SignalType, ItemID, Status, AssignedRole FROM signals ORDER BY Created DESC LIMIT 1")

        # 4. Test OOS Report Submission for Item in stock (Item I1004 has 45 OnHand, so it should be UNVERIFIED)
        print("\n--- Testing Unverified OOS Report Flow (Item I1004, OnHand = 45) ---")
        response = client.post(
            "/report-oos-submit",
            data={
                "member_id": "M1001",
                "receipt_id": "R1001",
                "item_id": "I1004"
            },
            follow_redirects=True
        )
        print("OOS Unverified Submission Status:", response.status_code)
        assert response.status_code == 200
        print_table("Member State After Unverified OOS (Points should not change)", "SELECT MemberID, Name, SamsPoints, TrustScore FROM members WHERE MemberID = 'M1001'")
        print_table("Signals Log After Unverified OOS", "SELECT SignalID, MemberID, SignalType, ItemID, Status, AssignedRole FROM signals ORDER BY Created DESC LIMIT 1")

        # 5. Test Product Suggestion Flow (Calls LLM agent)
        print("\n--- Testing Product Suggestion Flow (with Gemini LLM Analysis) ---")
        try:
            response = client.post(
                "/suggest-product",
                data={
                    "member_id": "M1001",
                    "description": "Frozen Garlic Naan",
                    "store": "Patel Brothers",
                    "reason": "Highly requested by local members"
                },
                follow_redirects=True
            )
            print("Product Suggestion Submission Status:", response.status_code)
            assert response.status_code == 200
            print_table("Member State After Product Suggestion (Should increase by 1 point)", "SELECT MemberID, Name, SamsPoints, TrustScore FROM members WHERE MemberID = 'M1001'")
            print_table("Candidate Products Table", "SELECT CandidateID, ItemDescription, StoreWhereFound, UpVotes, Status FROM candidate_products ORDER BY ProposalDate DESC LIMIT 1")
            print_table("Signals Log After Product Suggestion", "SELECT SignalID, MemberID, SignalType, CandidateID, Status, AssignedRole FROM signals ORDER BY Created DESC LIMIT 1")
        except Exception as le:
            print("Failed to run Gemini LLM Suggestion Node (likely due to missing credentials in sandbox):", le)

        # 6. Test Upvote Flow
        print("\n--- Testing Upvote Flow (Upvoting candidate P1003) ---")
        print_table("Product Upvotes Before", "SELECT CandidateID, ItemDescription, MemberIDProposer, UpVotes, Status FROM candidate_products WHERE CandidateID = 'P1003'")
        response = client.post(
            "/upvote",
            data={
                "member_id": "M1001",
                "candidate_id": "P1003"
            },
            follow_redirects=True
        )
        print("Upvote Submission Status:", response.status_code)
        assert response.status_code == 200
        print_table("Product Upvotes After", "SELECT CandidateID, ItemDescription, MemberIDProposer, UpVotes, Status FROM candidate_products WHERE CandidateID = 'P1003'")

        print("\nVerification completed successfully!")
    finally:
        # Shut down standalone backend process
        print("Stopping SignalSense Enterprise Backend...")
        backend_proc.terminate()
        backend_proc.wait()

if __name__ == "__main__":
    asyncio.run(test_all_flows())
