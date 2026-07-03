import base64
import hmac
import hashlib
import json
import time

SECRET_KEY = "signalsense-super-secure-key-change-in-production"

def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

def base64url_decode(data: str) -> bytes:
    padding = '=' * (4 - (len(data) % 4))
    return base64.urlsafe_b64decode(data + padding)

def create_access_token(payload: dict, secret: str = SECRET_KEY, expires_in: int = 3600) -> str:
    """
    Generates a cryptographically signed HMAC-SHA256 JWT Token.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload_copy = payload.copy()
    payload_copy["exp"] = int(time.time()) + expires_in
    
    header_b64 = base64url_encode(json.dumps(header).encode('utf-8'))
    payload_b64 = base64url_encode(json.dumps(payload_copy).encode('utf-8'))
    
    signature_input = f"{header_b64}.{payload_b64}".encode('utf-8')
    signature = hmac.new(secret.encode('utf-8'), signature_input, hashlib.sha256).digest()
    signature_b64 = base64url_encode(signature)
    
    return f"{header_b64}.{payload_b64}.{signature_b64}"

def verify_access_token(token: str, secret: str = SECRET_KEY) -> dict:
    """
    Decodes and validates a JWT token. Returns claims dict or raises exception.
    """
    try:
        parts = token.split('.')
        if len(parts) != 3:
            raise ValueError("Malformed token structure.")
        
        header_b64, payload_b64, signature_b64 = parts
        signature_input = f"{header_b64}.{payload_b64}".encode('utf-8')
        
        # Verify cryptographic integrity
        expected_signature = hmac.new(secret.encode('utf-8'), signature_input, hashlib.sha256).digest()
        expected_signature_b64 = base64url_encode(expected_signature)
        
        if not hmac.compare_digest(signature_b64, expected_signature_b64):
            raise ValueError("Invalid cryptographic signature.")
        
        # Parse payload and check expiration
        payload = json.loads(base64url_decode(payload_b64).decode('utf-8'))
        if payload.get("exp", 0) < time.time():
            raise ValueError("Token has expired.")
            
        return payload
    except Exception as e:
        raise ValueError(f"Token validation failed: {str(e)}")
