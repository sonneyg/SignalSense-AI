import pytest
from fastapi.testclient import TestClient
from fastapi import status
import json
import time

from signalsense_agent.fast_api_app import app
from signalsense_agent.jwt_helper import create_access_token

client = TestClient(app)

def test_jwt_missing_token():
    """Verify that requests to /run without a token are unauthorized."""
    response = client.post("/run", json={})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Missing or malformed Authorization header" in response.json()["detail"]

def test_jwt_malformed_token():
    """Verify that requests to /run with a malformed token are unauthorized."""
    response = client.post(
        "/run", 
        json={}, 
        headers={"Authorization": "Bearer malformed_token_string"}
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Unauthorized" in response.json()["detail"]

def test_jwt_invalid_signature():
    """Verify that requests to /run with an invalid cryptographic signature are unauthorized."""
    # Create token signed with a different key
    bad_token = create_access_token({"role": "Member"}, secret="wrong-secret-key")
    response = client.post(
        "/run", 
        json={}, 
        headers={"Authorization": f"Bearer {bad_token}"}
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Unauthorized" in response.json()["detail"]

def test_jwt_unprivileged_role():
    """Verify that requests to /run with an unprivileged role are forbidden."""
    token = create_access_token({"role": "AttackerRole"})
    response = client.post(
        "/run", 
        json={}, 
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Forbidden: Unprivileged role" in response.json()["detail"]

def test_jwt_authorized_role():
    """Verify that requests to /run with an authorized role bypass JWT middleware checks."""
    token = create_access_token({"role": "Member"})
    # Since JWT validation succeeds, it will pass the middleware and reach the core application.
    # The application will return a 422 Unprocessable Entity or similar since we send an empty payload,
    # but it MUST NOT return 401 or 403.
    response = client.post(
        "/run", 
        json={}, 
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code != status.HTTP_401_UNAUTHORIZED
    assert response.status_code != status.HTTP_403_FORBIDDEN

def test_quota_limited_token():
    """Verify that quota-limited tokens reject requests once max_uses is reached."""
    import sqlite3
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.dirname(os.path.dirname(current_dir))
    db_path = os.path.join(workspace_root, "enterprise_db", "enterprise.db")
    if not os.path.exists(db_path):
        db_path = "enterprise.db"
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS shared_tokens (
        token_id TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        max_uses INTEGER DEFAULT 1000,
        current_uses INTEGER DEFAULT 0,
        contact_type TEXT,
        contact_info TEXT
    );
    """)
    
    token_id = "test-quota-token"
    cursor.execute("""
    INSERT OR REPLACE INTO shared_tokens (token_id, role, max_uses, current_uses, contact_type, contact_info)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (token_id, "Member", 1, 0, "linkedin", "https://linkedin.com/in/test"))
    conn.commit()
    conn.close()
    
    jwt_token = create_access_token({"role": "Member", "jti": token_id})
    headers = {"Authorization": f"Bearer {jwt_token}"}
    
    response = client.post("/run", json={}, headers=headers)
    assert response.status_code not in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
    
    response = client.post("/run", json={}, headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "Demo usage limit exceeded" in response.json()["detail"]
    assert "https://linkedin.com/in/test" in response.json()["detail"]

def test_rate_limiting():
    """Verify that the RateLimitingMiddleware restricts high request volume."""
    token = create_access_token({"role": "Member"})
    headers = {"Authorization": f"Bearer {token}"}
    
    # Send requests to trigger rate limit (max_requests is 60)
    # We send 65 requests and expect a 429 Too Many Requests on the subsequent calls.
    limit_reached = False
    for _ in range(70):
        response = client.post("/run", json={}, headers=headers)
        if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            limit_reached = True
            break
            
    assert limit_reached, "Expected to trigger rate limiter (HTTP 429)"
