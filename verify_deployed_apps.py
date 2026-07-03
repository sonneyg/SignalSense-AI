import urllib.request
import urllib.parse
import http.cookiejar
import json
import time
import base64
import hmac
import hashlib

# Configuration
BACKEND_URL = "https://signalsense-backend-1076893987381.us-central1.run.app"
MEMBER_APP_URL = "https://member-app-1076893987381.us-central1.run.app"
OPS_DASHBOARD_URL = "https://ops-dashboard-1076893987381.us-central1.run.app"

SECRET_KEY = "signalsense-super-secure-key-change-in-production"

# Helper for base64url encoding
def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

# Helper to create a signed JWT token
def create_access_token(payload: dict, secret: str = SECRET_KEY, expires_in: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload_copy = payload.copy()
    payload_copy["exp"] = int(time.time()) + expires_in
    
    header_b64 = base64url_encode(json.dumps(header).encode('utf-8'))
    payload_b64 = base64url_encode(json.dumps(payload_copy).encode('utf-8'))
    
    signature_input = f"{header_b64}.{payload_b64}".encode('utf-8')
    signature = hmac.new(secret.encode('utf-8'), signature_input, hashlib.sha256).digest()
    signature_b64 = base64url_encode(signature)
    
    return f"{header_b64}.{payload_b64}.{signature_b64}"

def run_test(name, fn):
    print(f"Running test: {name:50} ... ", end="", flush=True)
    try:
        fn()
        print("\033[92m[PASS]\033[0m")
        return True
    except Exception as e:
        print(f"\033[91m[FAIL]\033[0m ({e})")
        return False

def test_member_app_loads():
    req = urllib.request.Request(MEMBER_APP_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as response:
        assert response.status == 200, f"Expected status 200, got {response.status}"
        html = response.read().decode('utf-8')
        assert "Member" in html or "Ambassador" in html, "Page does not seem to contain Member/Ambassador elements"

def test_ops_dashboard_loads():
    req = urllib.request.Request(OPS_DASHBOARD_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as response:
        assert response.status == 200, f"Expected status 200, got {response.status}"
        html = response.read().decode('utf-8')
        assert "Operations" in html or "Dashboard" in html or "Associate" in html, "Page does not seem to contain Operations Dashboard elements"

def test_member_app_cookie_setting():
    # Call with LinkedIn token parameter
    token = "linkedin-demo-token-2026"
    url = f"{MEMBER_APP_URL}/?demo_token={token}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as response:
        assert response.status == 200
        headers = response.info()
        set_cookie = headers.get("Set-Cookie", "")
        assert f"demo_token={token}" in set_cookie or f"demo_token=\"{token}\"" in set_cookie, f"Cookie not set correctly in headers: {set_cookie}"

def test_ops_dashboard_cookie_setting():
    # Call with Capstone token parameter
    token = "capstone-test-token-2026"
    url = f"{OPS_DASHBOARD_URL}/?demo_token={token}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as response:
        assert response.status == 200
        headers = response.info()
        set_cookie = headers.get("Set-Cookie", "")
        assert f"demo_token={token}" in set_cookie or f"demo_token=\"{token}\"" in set_cookie, f"Cookie not set correctly in headers: {set_cookie}"

def test_backend_unauthorized():
    # Call backend /run without authorization header
    url = f"{BACKEND_URL}/run"
    req = urllib.request.Request(url, data=b"{}", headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("Expected HTTP 401 Unauthorized, but request succeeded")
    except urllib.error.HTTPError as err:
        assert err.code == 401, f"Expected HTTP 401 Unauthorized, got {err.code}"

def test_backend_authorized_valid_token():
    # Call backend /run with a valid signed JWT containing the Capstone token ID (jti)
    token_id = "capstone-test-token-2026"
    jwt_token = create_access_token({"role": "Associate", "jti": token_id})
    
    url = f"{BACKEND_URL}/run"
    req = urllib.request.Request(
        url, 
        data=b"{}", 
        headers={
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {jwt_token}",
            'User-Agent': 'Mozilla/5.0'
        }, 
        method="POST"
    )
    try:
        # Since payload is empty {}, ADK Fast API app will validate auth first, then try to parse payload.
        # It will either succeed (200) or return 422 (Unprocessable Entity because of empty payload).
        # Any result other than 401 or 403 means authentication succeeded!
        with urllib.request.urlopen(req, timeout=10) as response:
            assert response.status == 200, f"Expected status 200 or 422, got {response.status}"
    except urllib.error.HTTPError as err:
        assert err.code in (200, 422), f"Expected authentication success (HTTP 200 or 422), got {err.code}: {err.read()}"

def main():
    print("="*60)
    print("      SIGNALSENSE AI - DEPLOYMENT VERIFICATION SUITE")
    print("="*60)
    print(f"Backend URL:    {BACKEND_URL}")
    print(f"Member App:     {MEMBER_APP_URL}")
    print(f"Dashboard App:  {OPS_DASHBOARD_URL}")
    print("-"*60)
    
    results = []
    results.append(run_test("Verify Member Frontend App is live", test_member_app_loads))
    results.append(run_test("Verify Operations Dashboard is live", test_ops_dashboard_loads))
    results.append(run_test("Verify Member App handles demo_token parameter", test_member_app_cookie_setting))
    results.append(run_test("Verify Ops Dashboard handles demo_token parameter", test_ops_dashboard_cookie_setting))
    results.append(run_test("Verify Backend API blocks unauthorized access", test_backend_unauthorized))
    results.append(run_test("Verify Backend API authorizes valid JWT token", test_backend_authorized_valid_token))
    
    print("="*60)
    passed = sum(1 for r in results if r)
    total = len(results)
    if passed == total:
        print(f"\033[92mSUCCESS: {passed}/{total} checks passed! All deployed systems are fully operational.\033[0m")
    else:
        print(f"\033[91mWARNING: Only {passed}/{total} checks passed. Please review the failures above.\033[0m")
    print("="*60)

if __name__ == "__main__":
    main()
