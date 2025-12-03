#!/usr/bin/env python3
"""
Pull historical data from the Whoop cloud API.
Extracts auth token from the official Whoop app via ADB (requires root).
"""
import json
import os
import subprocess
import sys
import time
import re
import html
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    print("Installing requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

API_BASE = "https://api.prod.whoop.com"
DATA_DIR = Path(__file__).parent / "api"
DATA_DIR.mkdir(exist_ok=True)

def extract_token_from_device():
    """Extract auth token from Whoop app SharedPreferences via ADB root."""
    try:
        result = subprocess.run(
            ["adb", "shell", "su -c 'cat /data/data/com.whoop.android/shared_prefs/sessions.xml'"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            print(f"ADB error: {result.stderr}")
            return None, None, None

        xml = result.stdout
        # Extract JSON from the sessions string
        m = re.search(r'name="lastActiveSession">(.*?)</string>', xml, re.DOTALL)
        if not m:
            m = re.search(r'name="sessions">\[(.*?)\]</string>', xml, re.DOTALL)
        if not m:
            print("Could not find session data in XML")
            return None, None, None

        raw = html.unescape(m.group(1))
        session = json.loads(raw)
        token = session["authToken"]["accessToken"]
        refresh = session["authToken"]["refreshToken"]
        user_id = session["user"]["id"]
        print(f"Extracted token for user {user_id} ({session['user']['fullName']})")
        return token, refresh, user_id
    except Exception as e:
        print(f"Token extraction failed: {e}")
        return None, None, None


def api_get(endpoint, token, params=None):
    """Make authenticated GET request to Whoop API."""
    url = f"{API_BASE}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Whoop-Android\\5.430.0",
        "x-whoop-app-version": "5.430.0",
        "x-whoop-device-platform": "ANDROID",
        "x-whoop-package-name": "com.whoop.android",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    print(f"  {resp.status_code} {endpoint}" + (f"?{resp.request.path_url.split('?',1)[1]}" if '?' in resp.request.path_url else ""))
    if resp.status_code == 401:
        print("  TOKEN EXPIRED - need to refresh or re-login in Whoop app")
        return None
    if resp.status_code != 200:
        print(f"  Error: {resp.text[:200]}")
        return None
    return resp.json()


def pull_rollups(token, user_id, days=365):
    """Pull sleep/recovery/strain rollups."""
    print(f"\n=== Rollups (last {days} days) ===")
    data = api_get(f"rollups-service/v1/rollups/{user_id}", token, {"days": days})
    if data:
        save("rollups.json", data)
    # Also try with trends
    data2 = api_get(f"rollups-service/v1/rollups/{user_id}/trends", token, {"days": days})
    if data2:
        save("rollups_trends.json", data2)
    return data


def pull_metrics(token, start_ts, end_ts, step=60):
    """Pull HR metrics. step=6 means per-10s, step=60 means per-minute."""
    print(f"\n=== HR Metrics ({datetime.fromtimestamp(start_ts, tz=timezone.utc).date()} to {datetime.fromtimestamp(end_ts, tz=timezone.utc).date()}) ===")
    # Pull in chunks of 7 days to avoid API limits
    chunk_size = 7 * 86400
    all_metrics = []
    ts = start_ts
    while ts < end_ts:
        chunk_end = min(ts + chunk_size, end_ts)
        data = api_get("metrics-service/v1/metrics", token, {
            "start": int(ts * 1000),  # API may expect milliseconds
            "end": int(chunk_end * 1000),
            "step": step
        })
        if data:
            if isinstance(data, list):
                all_metrics.extend(data)
            else:
                all_metrics.append(data)
        ts = chunk_end
        time.sleep(0.5)  # Rate limit
    save("metrics.json", all_metrics)
    return all_metrics


def pull_health_tab(token):
    """Pull health tab overview."""
    print("\n=== Health Tab ===")
    data = api_get("health-tab-bff/v1/health-tab", token)
    if data:
        save("health_tab.json", data)
    return data


def pull_cycles(token, user_id):
    """Try to pull cycle data (sleep/strain/recovery per day)."""
    print("\n=== Cycles ===")
    # Try various known endpoints
    for endpoint in [
        f"users/{user_id}/cycles",
        f"v1/cycle",
        f"activities-service/v1/cycles",
    ]:
        data = api_get(endpoint, token)
        if data:
            save("cycles.json", data)
            return data
    return None


def pull_sleep_details(token):
    """Pull recent sleep details."""
    print("\n=== Sleep Details ===")
    # Try the core-details endpoint
    data = api_get("core-details-bff/v2/recovery-details", token)
    if data:
        save("recovery_details.json", data)

    # Try health-related endpoints
    for endpoint in [
        "sleep-service/v1/sleep",
        "activities-service/v1/activities/sleep",
    ]:
        data = api_get(endpoint, token)
        if data:
            save(f"sleep_{endpoint.replace('/', '_')}.json", data)
            return data
    return None


def pull_data_sync(token):
    """Pull data sync high water mark and recent data."""
    print("\n=== Data Sync ===")
    data = api_get("metrics-service/v1/consumerstats/mobile/highwatermark/min", token)
    if data:
        save("data_sync_hwm.json", data)
    return data


def save(filename, data):
    """Save JSON data to file."""
    path = DATA_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    size = path.stat().st_size
    print(f"  Saved: {path} ({size:,} bytes)")


def main():
    print("=== Whoop API Data Puller ===\n")

    # Extract token
    token, refresh, user_id = extract_token_from_device()
    if not token:
        print("\nFailed to extract token. Provide manually:")
        print("  python3 whoop_api_client.py --token 'eyJ...' --user-id <USER_ID>")
        return

    # Test token
    print("\nTesting API access...")
    test = api_get(f"rollups-service/v1/rollups/{user_id}", token, {"days": 1})
    if test is None:
        print("API access failed. Token may be expired.")
        return

    print("\nToken valid. Pulling data...\n")

    # Pull everything
    pull_rollups(token, user_id, days=365)
    pull_health_tab(token)
    pull_sleep_details(token)
    pull_data_sync(token)

    # HR metrics for the last 30 days
    now = int(time.time())
    thirty_days_ago = now - 30 * 86400
    pull_metrics(token, thirty_days_ago, now, step=60)

    # Try cycle data
    pull_cycles(token, user_id)

    print(f"\n=== Done! Data saved to {DATA_DIR} ===")
    print("Files:")
    for f in sorted(DATA_DIR.glob("*.json")):
        print(f"  {f.name} ({f.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
