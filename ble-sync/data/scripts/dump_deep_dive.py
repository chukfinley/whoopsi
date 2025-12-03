#!/usr/bin/env python3
"""
Pull ALL deep-dive data from Whoop mobile API for every cycle date.
Includes: home, sleep, sleep/last-night, strain, recovery, and activity details.
"""

import json
import os
import sys
import time
import re
import html
import subprocess
from pathlib import Path
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

API_BASE = "https://api.prod.whoop.com"
BACKUP_DIR = Path(__file__).parent.parent / "backup"
DEEP_DIVE_DIR = BACKUP_DIR / "api" / "deep_dive"
HEADERS_BASE = {
    "User-Agent": "Whoop-Android\\5.430.0",
    "x-whoop-app-version": "5.430.0",
    "x-whoop-app-version-code": "375528",
    "x-whoop-device-platform": "ANDROID",
    "x-whoop-package-name": "com.whoop.android",
    "x-whoop-strap-id": "5<STRAP_SERIAL>",
    "x-whoop-time-zone": "UTC",  # Change to your timezone
    "accept": "application/json",
    "accept-encoding": "gzip",
}


def get_token():
    """Get token from arg, env, or device."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.environ.get("WHOOP_TOKEN"):
        return os.environ["WHOOP_TOKEN"]
    # Try ADB
    result = subprocess.run(
        [
            "adb",
            "shell",
            "su -c 'cat /data/data/com.whoop.android/shared_prefs/sessions.xml'",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    m = re.search(r'name="lastActiveSession">(.*?)</string>', result.stdout, re.DOTALL)
    session = json.loads(html.unescape(m.group(1)))
    return session["authToken"]["accessToken"]


def api_get(endpoint, token, params=None):
    url = f"{API_BASE}/{endpoint}"
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except Exception as e:
        print(f"  ERROR: {e}")
        return None
    if resp.status_code == 200:
        return resp.json()
    elif resp.status_code == 401:
        print("401 UNAUTHORIZED — token expired!")
        sys.exit(1)
    return None


def find_activity_ids(data):
    """Recursively find all activity_id values in home data."""
    ids = set()

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if (
                    ("activity_id" in k.lower() or "activityid" in k.lower())
                    and isinstance(v, str)
                    and len(v) > 10
                ):
                    ids.add(v)
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(data)
    return ids


def main():
    token = get_token()

    # Get all dates from cycles
    cycles_path = BACKUP_DIR / "api" / "all_cycles.json"
    if not cycles_path.exists():
        print("ERROR: all_cycles.json not found. Run dump_all_whoop_data.py first.")
        sys.exit(1)

    cycles = json.load(open(cycles_path))
    dates = sorted(set(c["start"][:10] for c in cycles if c.get("start")))
    print(f"Total dates to process: {len(dates)}")

    # Track all activity IDs found
    all_activity_ids = {}

    endpoints = [
        ("home", "home-service/v1/home"),
        ("sleep", "home-service/v1/deep-dive/sleep"),
        ("sleep_lastnight", "home-service/v1/deep-dive/sleep/last-night"),
        ("strain", "home-service/v1/deep-dive/strain"),
        ("recovery", "home-service/v1/deep-dive/recovery"),
    ]

    for i, date in enumerate(dates):
        day_dir = DEEP_DIVE_DIR / date

        # Skip if already fully downloaded
        existing = list(day_dir.glob("*.json")) if day_dir.exists() else []
        if len(existing) >= 5:
            # Still check home data for activity IDs
            home_path = day_dir / "home.json"
            if home_path.exists():
                try:
                    home_data = json.load(open(home_path))
                    for aid in find_activity_ids(home_data):
                        all_activity_ids[aid] = date
                except:
                    pass
            print(
                f"[{i + 1}/{len(dates)}] {date} — already done ({len(existing)} files), skipping"
            )
            continue

        day_dir.mkdir(parents=True, exist_ok=True)
        fetched = 0

        for name, ep in endpoints:
            out = day_dir / f"{name}.json"
            if out.exists() and out.stat().st_size > 50:
                fetched += 1
                if name == "home":
                    try:
                        for aid in find_activity_ids(json.load(open(out))):
                            all_activity_ids[aid] = date
                    except:
                        pass
                continue

            data = api_get(ep, token, {"date": date})
            if data:
                with open(out, "w") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                fetched += 1
                if name == "home":
                    for aid in find_activity_ids(data):
                        all_activity_ids[aid] = date
            time.sleep(0.2)

        print(f"[{i + 1}/{len(dates)}] {date} — {fetched}/5 endpoints")

    # Pull trends for latest date
    latest = dates[-1]
    trends_dir = DEEP_DIVE_DIR / "_trends"
    trends_dir.mkdir(parents=True, exist_ok=True)
    for name, ep in [
        ("sleep_trends", "home-service/v1/deep-dive/sleep/trends"),
        ("strain_trends", "home-service/v1/deep-dive/strain/trends"),
        ("recovery_trends", "home-service/v1/deep-dive/recovery/trends"),
    ]:
        out = trends_dir / f"{name}.json"
        if not out.exists():
            data = api_get(ep, token, {"date": latest})
            if data:
                with open(out, "w") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                print(f"Saved trends: {name}")
            time.sleep(0.2)

    # Pull activity details
    print(f"\nFound {len(all_activity_ids)} activities across all dates")
    activities_dir = DEEP_DIVE_DIR / "_activities"
    activities_dir.mkdir(parents=True, exist_ok=True)

    for aid, date in sorted(all_activity_ids.items(), key=lambda x: x[1]):
        out = activities_dir / f"{aid}.json"
        if out.exists():
            continue
        data = api_get("core-details-bff/v1/cardio-details", token, {"activityId": aid})
        if data:
            with open(out, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(
                f"  Activity {date}: {data.get('title_bar', {}).get('title_display', '?')}"
            )
        time.sleep(0.2)

    # Summary
    total_files = sum(1 for _ in DEEP_DIVE_DIR.rglob("*.json"))
    total_size = sum(f.stat().st_size for f in DEEP_DIVE_DIR.rglob("*.json"))
    print(
        f"\nDone! {total_files} files, {total_size / 1024 / 1024:.1f} MB in {DEEP_DIVE_DIR}"
    )


if __name__ == "__main__":
    main()
