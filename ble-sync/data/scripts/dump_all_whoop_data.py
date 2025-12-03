#!/usr/bin/env python3
"""
Complete Whoop data backup — pulls ALL available data from every API endpoint.
Extracts auth token automatically from rooted device.
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
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

API_BASE = "https://api.prod.whoop.com"
BACKUP_DIR = Path(__file__).parent.parent / "backup"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def get_token():
    result = subprocess.run(
        ["adb", "shell", "su -c 'cat /data/data/com.whoop.android/shared_prefs/sessions.xml'"],
        capture_output=True, text=True, timeout=10
    )
    m = re.search(r'name="lastActiveSession">(.*?)</string>', result.stdout, re.DOTALL)
    session = json.loads(html.unescape(m.group(1)))
    token = session["authToken"]["accessToken"]
    user = session["user"]
    print(f"User: {user['fullName']} (ID: {user['id']})")
    print(f"Email: {user['emailAddress']}")
    return token, user["id"], user


def api_get(endpoint, token, params=None):
    url = f"{API_BASE}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Whoop-Android\\5.430.0",
        "x-whoop-app-version": "5.430.0",
        "x-whoop-device-platform": "ANDROID",
        "x-whoop-package-name": "com.whoop.android",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except Exception as e:
        print(f"  ERROR: {e}")
        return None
    if resp.status_code == 200:
        return resp.json()
    elif resp.status_code == 401:
        print(f"  401 UNAUTHORIZED — token expired!")
        sys.exit(1)
    else:
        return None


def paginate(endpoint, token, limit=25):
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
        print(f"  Page {page}: {len(records)} records (total: {len(all_records)})")
        if not next_token or not records:
            break
        time.sleep(0.3)
    return all_records


def save(filename, data):
    path = BACKUP_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    size = path.stat().st_size
    print(f"  → Saved: {filename} ({size:,} bytes)")
    return path


def main():
    print("=" * 60)
    print("  COMPLETE WHOOP DATA BACKUP")
    print("=" * 60)
    print()

    token, user_id, user_data = get_token()
    save("user_profile.json", user_data)

    # ================================================================
    # 1. USER DATA
    # ================================================================
    print("\n[1/10] User Profile")
    data = api_get("developer/v1/user/profile/basic", token)
    if data: save("user_profile_basic.json", data)

    data = api_get("developer/v1/user/measurement/body", token)
    if data: save("user_body_measurement.json", data)

    # ================================================================
    # 2. ALL CYCLES (contains strain, avg HR, max HR, kJ per day)
    # ================================================================
    print("\n[2/10] All Cycles (paginated)")
    cycles = paginate("developer/v1/cycle", token)
    if cycles:
        save("all_cycles.json", cycles)
        print(f"  Total cycles: {len(cycles)}")
        if cycles:
            first = cycles[-1].get("start", "?")
            last = cycles[0].get("start", "?")
            print(f"  Range: {first} to {last}")

    # ================================================================
    # 3. HEALTH TAB (Whoop Age, pace of aging, etc.)
    # ================================================================
    print("\n[3/10] Health Tab")
    data = api_get("health-tab-bff/v1/health-tab", token)
    if data: save("health_tab.json", data)

    # ================================================================
    # 4. ROLLUPS (sleep/recovery/strain aggregates)
    # ================================================================
    print("\n[4/10] Rollups")
    for days in [30, 90, 180, 365]:
        data = api_get(f"rollups-service/v1/rollups/{user_id}", token, {"days": days})
        if data:
            save(f"rollups_{days}d.json", data)
            # Check if it has actual data
            has_data = any(data.get(k) is not None for k in ["recovery_rollups", "sleep_rollups", "strain_rollups"])
            print(f"  {days}d: {'has data' if has_data else 'empty'}")

    data = api_get(f"rollups-service/v1/rollups/{user_id}/trends", token, {"days": 365})
    if data: save("rollups_trends_365d.json", data)

    # ================================================================
    # 5. DAILY STRAIN DETAILS (per-day with optimal strain curves)
    # ================================================================
    print("\n[5/10] Daily Strain Details (per day)")
    all_strain = []
    # Go from today back to account creation
    d = datetime.now(timezone.utc).date()
    account_start = datetime(2025, 2, 16).date()
    empty_streak = 0
    day_count = 0
    while d >= account_start:
        date_str = d.strftime("%Y-%m-%d")
        data = api_get("core-details-bff/v1/start-activity/strain", token, {"date": date_str})
        if data:
            cm = data.get("cycle_metadata", {})
            strain = cm.get("strain", {}).get("scaled_strain", 0)
            cycle_id = cm.get("id")
            all_strain.append({"date": date_str, "cycle_id": cycle_id, "data": data})
            if strain > 0:
                empty_streak = 0
            else:
                empty_streak += 1
            day_count += 1
            if day_count % 30 == 0:
                print(f"  {date_str}: {day_count} days processed, {len(all_strain)} with data")
        else:
            empty_streak += 1

        d -= timedelta(days=1)
        time.sleep(0.15)  # Rate limit

    save("daily_strain_all.json", all_strain)
    print(f"  Total days with strain data: {len(all_strain)}")

    # ================================================================
    # 6. DATA SYNC HIGH WATER MARK
    # ================================================================
    print("\n[6/10] Data Sync Info")
    data = api_get("metrics-service/v1/consumerstats/mobile/highwatermark/min", token)
    if data: save("data_sync_hwm.json", data)

    # ================================================================
    # 7. PULL ALL SHARED PREFS FROM WHOOP APP (rooted)
    # ================================================================
    print("\n[7/10] Whoop App SharedPreferences (all files)")
    prefs_dir = BACKUP_DIR / "shared_prefs"
    prefs_dir.mkdir(exist_ok=True)
    result = subprocess.run(
        ["adb", "shell", "su -c 'ls /data/data/com.whoop.android/shared_prefs/'"],
        capture_output=True, text=True, timeout=10
    )
    pref_files = result.stdout.strip().split("\n")
    for pf in pref_files:
        pf = pf.strip()
        if not pf:
            continue
        result = subprocess.run(
            ["adb", "shell", f"su -c 'cat /data/data/com.whoop.android/shared_prefs/{pf}'"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            with open(prefs_dir / pf, "w") as f:
                f.write(result.stdout)
    print(f"  Saved {len(pref_files)} preference files")

    # ================================================================
    # 8. PULL ALL WHOOP APP DATABASES (rooted)
    # ================================================================
    print("\n[8/10] Whoop App Databases")
    db_dir = BACKUP_DIR / "databases"
    db_dir.mkdir(exist_ok=True)
    result = subprocess.run(
        ["adb", "shell", "su -c 'ls /data/data/com.whoop.android/databases/'"],
        capture_output=True, text=True, timeout=10
    )
    db_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    for dbf in db_files:
        out_path = db_dir / dbf
        result = subprocess.run(
            ["adb", "shell", f"su -c 'cat /data/data/com.whoop.android/databases/{dbf}'"],
            capture_output=True, timeout=30
        )
        if result.returncode == 0 and result.stdout:
            with open(out_path, "wb") as f:
                f.write(result.stdout)
            print(f"  {dbf}: {len(result.stdout):,} bytes")

    # ================================================================
    # 9. PULL OUR COMPANION APP DB
    # ================================================================
    print("\n[9/10] Our Companion App Database")
    our_db_dir = BACKUP_DIR / "companion_db"
    our_db_dir.mkdir(exist_ok=True)
    for dbf in ["whoop_capture.db", "whoop_capture.db-wal", "whoop_capture.db-shm"]:
        result = subprocess.run(
            ["adb", "shell", f"run-as com.whoopcapture cat /data/data/com.whoopcapture/databases/{dbf}"],
            capture_output=True, timeout=30
        )
        if result.returncode == 0 and result.stdout:
            with open(our_db_dir / dbf, "wb") as f:
                f.write(result.stdout)
            print(f"  {dbf}: {len(result.stdout):,} bytes")

    # ================================================================
    # 10. TRY ADDITIONAL API ENDPOINTS
    # ================================================================
    print("\n[10/10] Additional API Endpoints")
    extra_endpoints = [
        ("core-details-bff/v1/activity-score-type", {}),
        (f"auth-service/v1/account/user/{user_id}", {}),
    ]
    for ep, params in extra_endpoints:
        data = api_get(ep, token, params)
        if data:
            fname = ep.replace("/", "_") + ".json"
            save(fname, data)
        else:
            print(f"  {ep}: no data")

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n" + "=" * 60)
    print("  BACKUP COMPLETE")
    print("=" * 60)
    total_size = sum(f.stat().st_size for f in BACKUP_DIR.rglob("*") if f.is_file())
    file_count = sum(1 for f in BACKUP_DIR.rglob("*") if f.is_file())
    print(f"  Location: {BACKUP_DIR}")
    print(f"  Files: {file_count}")
    print(f"  Total size: {total_size:,} bytes ({total_size/1024/1024:.1f} MB)")
    print()
    print("  Contents:")
    for item in sorted(BACKUP_DIR.iterdir()):
        if item.is_file():
            print(f"    {item.name} ({item.stat().st_size:,} bytes)")
        elif item.is_dir():
            count = sum(1 for _ in item.rglob("*") if _.is_file())
            size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
            print(f"    {item.name}/ ({count} files, {size:,} bytes)")


if __name__ == "__main__":
    main()
