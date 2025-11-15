"""Whoop authentication — Cognito login, JWT decode, token persistence."""

import json
import base64
import os
import time
from pathlib import Path

import requests

COGNITO_URL = "https://api.prod.whoop.com/auth-service/v3/whoop/"
CLIENT_ID = os.environ.get("WHOOP_COGNITO_CLIENT_ID", "")
# Extract the Cognito Client ID from the official Whoop APK (decompile and search for "ClientId")
TOKEN_DIR = Path.home() / ".whoop"
TOKEN_FILE = TOKEN_DIR / "token.json"


def decode_jwt(token: str) -> dict:
    """Decode JWT payload without verification."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(payload := parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def login(email: str, password: str) -> dict:
    """Authenticate via Cognito. Returns token dict for persistence."""
    resp = requests.post(
        COGNITO_URL,
        json={
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": CLIENT_ID,
            "AuthParameters": {"USERNAME": email, "PASSWORD": password},
        },
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "User-Agent": "Whoop-Android\\5.430.0",
        },
        timeout=30,
    )

    body = resp.json()
    if resp.status_code != 200:
        error = body.get("message", body.get("__type", "Unknown error"))
        raise RuntimeError(f"Login failed: {error}")

    if body.get("ChallengeName"):
        raise RuntimeError(f"Auth challenge required: {body['ChallengeName']}")

    result = body.get("AuthenticationResult", {})
    access = result.get("AccessToken")
    refresh = result.get("RefreshToken")
    if not access:
        raise RuntimeError("Login succeeded but no access token returned")

    claims = decode_jwt(access)
    user_id = claims.get("custom:user_id") or claims.get("sub", "")
    expires_at = claims.get("exp", int(time.time()) + 86400)

    token_data = {
        "access_token": access,
        "refresh_token": refresh,
        "user_id": user_id,
        "email": email,
        "expires_at": expires_at,
    }
    save_token(token_data)
    return token_data


def refresh(token_data: dict) -> dict:
    """Refresh an expired access token using the refresh token."""
    rt = token_data.get("refresh_token")
    if not rt:
        raise RuntimeError("No refresh token available. Run: whoop login")

    resp = requests.post(
        COGNITO_URL,
        json={
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": rt},
        },
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            "User-Agent": "Whoop-Android\\5.430.0",
        },
        timeout=30,
    )

    body = resp.json()
    if resp.status_code != 200:
        error = body.get("message", "Refresh failed")
        raise RuntimeError(f"Token refresh failed: {error}")

    result = body.get("AuthenticationResult", {})
    access = result.get("AccessToken")
    if not access:
        raise RuntimeError("Refresh succeeded but no new access token")

    claims = decode_jwt(access)
    token_data["access_token"] = access
    token_data["user_id"] = claims.get("custom:user_id") or token_data.get(
        "user_id", ""
    )
    token_data["expires_at"] = claims.get("exp", int(time.time()) + 86400)
    # Refresh token stays the same (long-lived)
    save_token(token_data)
    return token_data


def save_token(token_data: dict):
    """Persist token to ~/.whoop/token.json."""
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))
    TOKEN_FILE.chmod(0o600)


def load_token() -> dict | None:
    """Load persisted token, return None if missing."""
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def get_token() -> dict:
    """Get a valid token — load from disk, auto-refresh if expired."""
    td = load_token()
    if not td:
        raise RuntimeError("Not logged in. Run: whoop login")

    # Check expiry (refresh 5 min before actual expiry)
    if td.get("expires_at", 0) < time.time() + 300:
        try:
            td = refresh(td)
        except RuntimeError:
            raise RuntimeError("Token expired and refresh failed. Run: whoop login")

    return td


def resolve_user_id(token: str) -> str:
    """Get user_id from JWT or API fallback."""
    claims = decode_jwt(token)
    uid = claims.get("custom:user_id")
    if uid:
        return uid
    from whoop_cli.api import api_get

    data = api_get("developer/v1/user/profile/basic", token)
    return str(data.get("user_id", "")) if data else ""
