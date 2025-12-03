#!/usr/bin/env python3
"""
Whoop Data Export Tool — Export ALL your Whoop data. No root required.

Usage:
  python3 whoop_export.py                          # interactive login
  python3 whoop_export.py --email user@email.com   # prompt for password
  python3 whoop_export.py --token TOKEN            # use existing token
"""

import json
import os
import sys
import time
import base64
import argparse
import getpass
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    print("Installing requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

# --- Constants ---
API_BASE = "https://api.prod.whoop.com"
COGNITO_URL = "https://api.prod.whoop.com/auth-service/v3/whoop/"
COGNITO_CLIENT_ID = os.environ.get("WHOOP_COGNITO_CLIENT_ID", "")
# Extract the Cognito Client ID from the official Whoop APK (decompile and search for "ClientId")
APP_HEADERS = {
    "User-Agent": "Whoop-Android\\5.430.0",
    "x-whoop-app-version": "5.430.0",
    "x-whoop-app-version-code": "375528",
    "x-whoop-device-platform": "ANDROID",
    "x-whoop-package-name": "com.whoop.android",
}
RATE_LIMIT = 0.2


# --- Auth ---


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    # Fix base64 padding
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def login_cognito(email: str, password: str) -> tuple:
    """Authenticate via AWS Cognito. Returns (access_token, user_id)."""
    print(f"  Logging in as {email}...")
    resp = requests.post(
        COGNITO_URL,
        json={
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
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
        error_type = body.get("__type", "")
        error_msg = body.get("message", "Unknown error")

        if "NotAuthorizedException" in error_type:
            print(f"  Login failed: {error_msg}")
        elif "UserNotFoundException" in error_type:
            print(f"  Login failed: No account found for {email}")
        elif "NEW_PASSWORD_REQUIRED" in str(body):
            print("  Your account requires a password change.")
            print("  Log in via the Whoop app first, then try again.")
        elif "SMS_MFA" in str(body) or "SOFTWARE_TOKEN_MFA" in str(body):
            print("  MFA is enabled on this account.")
            print("  Use --token with a manually obtained token instead.")
        else:
            print(f"  Login failed: {error_msg}")
        sys.exit(1)

    challenge = body.get("ChallengeName")
    if challenge:
        print(f"  Auth challenge: {challenge}")
        print("  This account requires additional verification.")
        print("  Use --token with a manually obtained token instead.")
        sys.exit(1)

    result = body.get("AuthenticationResult", {})
    token = result.get("AccessToken")
    if not token:
        print("  Login succeeded but no access token returned.")
        sys.exit(1)

    # Extract user_id from JWT
    claims = decode_jwt_payload(token)
    user_id = claims.get("custom:user_id") or claims.get("sub")

    print("  Login successful.")
    return token, user_id


def resolve_user_id(token: str) -> str:
    """Get user_id from JWT claims or API fallback."""
    claims = decode_jwt_payload(token)
    uid = claims.get("custom:user_id")
    if uid:
        return uid
    # Fallback: fetch from API
    data = api_get("developer/v1/user/profile/basic", token, quiet=True)
    if data:
        return str(data.get("user_id", ""))
    return ""


# --- API ---


def api_get(endpoint: str, token: str, params: dict = None, quiet: bool = False):
    """Make authenticated GET request."""
    tz = time.strftime("%Z") or "UTC"
    headers = {
        "Authorization": f"Bearer {token}",
        "x-whoop-time-zone": tz,
        **APP_HEADERS,
    }
    url = f"{API_BASE}/{endpoint}"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as e:
        if not quiet:
            print(f"    Request error: {e}")
        return None

    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError:
            return None
    elif resp.status_code == 401:
        print("    ERROR: Token expired or invalid (401).")
        sys.exit(1)
    elif resp.status_code == 403:
        if not quiet:
            print(f"    Forbidden: {endpoint}")
        return None
    else:
        if not quiet:
            print(f"    HTTP {resp.status_code}: {endpoint}")
        return None


def paginate_all(endpoint: str, token: str, limit: int = 25) -> list:
    """Paginate through all records."""
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
        print(f"    Page {page}: +{len(records)} records (total: {len(all_records)})")
        if not next_token or not records:
            break
        time.sleep(RATE_LIMIT)
    return all_records


# --- Export ---


def save_json(path: Path, data) -> int:
    """Save data as JSON, return file size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    size = path.stat().st_size
    return size


def progress(step: int, total: int, label: str):
    """Print a progress line."""
    bar_len = 30
    filled = int(bar_len * step / total)
    bar = "#" * filled + "-" * (bar_len - filled)
    print(f"\n[{bar}] Step {step}/{total}: {label}")


def export_all(token: str, user_id: str, output_dir: Path):
    """Main export routine."""
    api_dir = output_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    deep_dir = output_dir / "deep_dive"
    deep_dir.mkdir(parents=True, exist_ok=True)
    trends_dir = output_dir / "trends"
    trends_dir.mkdir(parents=True, exist_ok=True)
    activities_dir = output_dir / "activities"
    activities_dir.mkdir(parents=True, exist_ok=True)

    total_steps = 8
    files_saved = []

    def track(path, data):
        if data is not None:
            size = save_json(path, data)
            files_saved.append((path.name, size))
            print(f"    Saved: {path.relative_to(output_dir)} ({size:,} bytes)")

    # 1. User Profile
    progress(1, total_steps, "User Profile & Body Measurements")
    data = api_get("developer/v1/user/profile/basic", token)
    track(api_dir / "user_profile.json", data)
    time.sleep(RATE_LIMIT)

    data = api_get("developer/v1/user/measurement/body", token)
    track(api_dir / "body_measurement.json", data)
    time.sleep(RATE_LIMIT)

    # 2. All Cycles
    progress(2, total_steps, "All Cycles (paginated)")
    cycles = paginate_all("developer/v1/cycle", token)
    if cycles:
        track(api_dir / "all_cycles.json", cycles)
        first = cycles[0].get("start", "?")[:10]
        last = cycles[-1].get("start", "?")[:10]
        print(f"    Range: {last} to {first} ({len(cycles)} cycles)")

    # 3. Health Tab
    progress(3, total_steps, "Health Tab")
    data = api_get("health-tab-bff/v1/health-tab", token)
    track(api_dir / "health_tab.json", data)
    time.sleep(RATE_LIMIT)

    # 4. Rollups
    progress(4, total_steps, "Rollups (365 days)")
    if user_id:
        data = api_get(f"rollups-service/v1/rollups/{user_id}", token, {"days": 365})
        track(api_dir / "rollups_365d.json", data)
    else:
        print("    Skipped: no user_id available")
    time.sleep(RATE_LIMIT)

    # 5. Deep Dive (sleep, recovery, strain per date)
    progress(5, total_steps, "Deep Dive Data (per day)")
    dates = set()
    for c in cycles:
        start = c.get("start", "")
        if start:
            dates.add(start[:10])
    dates = sorted(dates, reverse=True)
    print(f"    Processing {len(dates)} dates...")

    deep_dive_endpoints = [
        ("sleep", "home-service/v1/deep-dive/sleep"),
        ("recovery", "home-service/v1/deep-dive/recovery"),
        ("strain", "home-service/v1/deep-dive/strain"),
        ("last_night", "home-service/v1/deep-dive/sleep/last-night"),
    ]

    all_deep = {}
    for i, date_str in enumerate(dates):
        day_data = {}
        for name, endpoint in deep_dive_endpoints:
            data = api_get(endpoint, token, {"date": date_str}, quiet=True)
            if data:
                day_data[name] = data
            time.sleep(RATE_LIMIT)
        if day_data:
            all_deep[date_str] = day_data
            save_json(deep_dir / f"{date_str}.json", day_data)
        if (i + 1) % 10 == 0 or i == len(dates) - 1:
            print(f"    {i + 1}/{len(dates)} dates processed")

    if all_deep:
        track(api_dir / "deep_dive_all.json", all_deep)

    # 6. Trends (latest date)
    progress(6, total_steps, "Trends")
    latest_date = dates[0] if dates else datetime.now().strftime("%Y-%m-%d")
    for trend_type in ["sleep", "strain", "recovery"]:
        endpoint = f"home-service/v1/deep-dive/{trend_type}/trends"
        data = api_get(endpoint, token, {"date": latest_date}, quiet=True)
        if data:
            track(trends_dir / f"{trend_type}_trends.json", data)
        time.sleep(RATE_LIMIT)

    # 7. Extract activity IDs and fetch details
    progress(7, total_steps, "Activity Details")
    activity_ids = set()
    for day_data in all_deep.values():
        strain = day_data.get("strain", {})
        # Look for activity IDs in various structures
        for act in strain.get("activities", []):
            aid = act.get("activity_id") or act.get("id")
            if aid:
                activity_ids.add(str(aid))
        for act in strain.get("cardio_activities", []):
            aid = act.get("activity_id") or act.get("id")
            if aid:
                activity_ids.add(str(aid))

    print(f"    Found {len(activity_ids)} activities")
    act_count = 0
    for aid in activity_ids:
        data = api_get(
            "core-details-bff/v1/cardio-details", token, {"activityId": aid}, quiet=True
        )
        if data:
            save_json(activities_dir / f"activity_{aid}.json", data)
            act_count += 1
        time.sleep(RATE_LIMIT)
    if act_count:
        print(f"    Saved {act_count} activity detail files")

    # 8. Additional rollup periods
    progress(8, total_steps, "Additional Rollups")
    if user_id:
        for days in [30, 90, 180]:
            data = api_get(
                f"rollups-service/v1/rollups/{user_id}", token, {"days": days}
            )
            if data:
                track(api_dir / f"rollups_{days}d.json", data)
            time.sleep(RATE_LIMIT)

    # --- Summary ---
    print("\n" + "=" * 55)
    print("  EXPORT COMPLETE")
    print("=" * 55)
    total_files = sum(1 for _ in output_dir.rglob("*.json"))
    total_bytes = sum(f.stat().st_size for f in output_dir.rglob("*.json"))
    print(f"  Location : {output_dir.resolve()}")
    print(f"  Files    : {total_files}")
    print(f"  Size     : {total_bytes:,} bytes ({total_bytes / 1024 / 1024:.1f} MB)")
    print(f"  Cycles   : {len(cycles)}")
    print(f"  Days     : {len(dates)}")
    print(f"  Activities: {act_count}")
    print()
    print("  To generate a dashboard:")
    print("    python3 scripts/generate_api_dashboard.py")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Export all your Whoop data. No root required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python3 whoop_export.py                          Interactive login
  python3 whoop_export.py --email you@mail.com     Prompt for password
  python3 whoop_export.py --token eyJhbG...        Use existing token

data exported:
  User profile, body measurements, all cycles, health tab,
  rollups (30/90/180/365 days), daily deep dives (sleep,
  recovery, strain), trends, and activity details.
""",
    )
    parser.add_argument("--email", "-e", help="Whoop account email")
    parser.add_argument("--password", "-p", help="Password (prompted if omitted)")
    parser.add_argument("--token", "-t", help="Bearer token (skip login)")
    parser.add_argument(
        "--output",
        "-o",
        default="whoop_backup",
        help="Output directory (default: whoop_backup)",
    )
    args = parser.parse_args()

    print()
    print("=" * 55)
    print("  WHOOP DATA EXPORT")
    print("=" * 55)
    print()

    token = None
    user_id = None

    if args.token:
        token = args.token.strip()
        print("  Using provided token.")
        user_id = resolve_user_id(token)

    elif args.email:
        password = args.password or getpass.getpass("  Password: ")
        token, user_id = login_cognito(args.email, password)

    else:
        # Interactive
        print("  Authentication")
        print("  1) Email + Password")
        print("  2) Paste a Bearer token")
        print()
        choice = input("  Choice [1/2]: ").strip()
        print()

        if choice == "2":
            token = input("  Bearer token: ").strip()
            user_id = resolve_user_id(token)
        else:
            email = input("  Email: ").strip()
            password = getpass.getpass("  Password: ")
            token, user_id = login_cognito(email, password)

    if not token:
        print("  No token obtained. Exiting.")
        sys.exit(1)

    if user_id:
        print(f"  User ID: {user_id}")
    else:
        print("  Warning: Could not determine user_id. Some endpoints will be skipped.")

    output_dir = Path(args.output)
    export_all(token, user_id, output_dir)


if __name__ == "__main__":
    main()
