"""Shared Whoop API client — authenticated requests, pagination, rate limiting."""

import sys
import time

import requests

API_BASE = "https://api.prod.whoop.com"
APP_HEADERS = {
    "User-Agent": "Whoop-Android\\5.430.0",
    "x-whoop-app-version": "5.430.0",
    "x-whoop-app-version-code": "375528",
    "x-whoop-device-platform": "ANDROID",
    "x-whoop-package-name": "com.whoop.android",
}
RATE_LIMIT = 0.2


def api_get(endpoint: str, token: str, params: dict = None, quiet: bool = False):
    """Make authenticated GET request to Whoop API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "x-whoop-time-zone": time.strftime("%Z") or "UTC",
        **APP_HEADERS,
    }
    try:
        resp = requests.get(f"{API_BASE}/{endpoint}", headers=headers,
                            params=params, timeout=30)
    except requests.RequestException as e:
        if not quiet:
            print(f"  Request error: {e}", file=sys.stderr)
        return None

    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError:
            return None
    elif resp.status_code == 401:
        if not quiet:
            print("  Token expired (401).", file=sys.stderr)
        return None
    elif resp.status_code == 403:
        if not quiet:
            print(f"  Forbidden: {endpoint}", file=sys.stderr)
        return None
    else:
        if not quiet:
            print(f"  HTTP {resp.status_code}: {endpoint}", file=sys.stderr)
        return None


def paginate(endpoint: str, token: str, limit: int = 25) -> list:
    """Paginate through all records of an endpoint."""
    all_records = []
    next_token = None
    page = 0
    while True:
        params = {"limit": limit}
        if next_token:
            params["nextToken"] = next_token
        data = api_get(endpoint, token, params)
        if data is None:
            break
        records = data.get("records", [])
        all_records.extend(records)
        next_token = data.get("next_token")
        page += 1
        if not next_token or not records:
            break
        time.sleep(RATE_LIMIT)
    return all_records


def save_json(path, data):
    """Save data as formatted JSON."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path.stat().st_size
