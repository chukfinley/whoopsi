"""Load raw sensor data from HAR files and ground truth from Whoop API exports."""

import json
import base64
import struct
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HAR_DIR = REPO_ROOT / "captures" / "har"
DEEP_DIVE_DIR = REPO_ROOT / "whoop-companion" / "data" / "whoop_backup" / "deep_dive"
CYCLES_FILE = REPO_ROOT / "whoop-companion" / "data" / "whoop_backup" / "api" / "all_cycles.json"

RECORD_SIZE = 124
BERLIN = timedelta(hours=1)


def _extract_payload(payload: bytes) -> dict:
    """Extract sensor values from 116-byte payload (from HAR metrics-service)."""
    hr = payload[19]
    rr_count = payload[20]
    rr_intervals = []
    for k in range(min(rr_count, 3)):
        rr = struct.unpack("<H", payload[21 + k * 2: 23 + k * 2])[0]
        if 200 < rr < 2500:
            rr_intervals.append(rr)

    acc_x = struct.unpack(">f", payload[45:49])[0]
    acc_y = struct.unpack(">f", payload[49:53])[0]
    acc_z = struct.unpack(">f", payload[53:57])[0]

    if all(math.isfinite(v) for v in [acc_x, acc_y, acc_z]):
        movement = abs(math.sqrt(acc_x**2 + acc_y**2 + acc_z**2) - 1.0)
    else:
        acc_x = acc_y = acc_z = 0.0
        movement = 0.0

    gyro = struct.unpack(">f", payload[29:33])[0]
    if not math.isfinite(gyro) or abs(gyro) > 100:
        gyro = 0.0

    spo2_raw = payload[55]
    spo2 = spo2_raw + 10 if 0 < spo2_raw < 100 else None

    return {
        "hr": hr,
        "rr_count": rr_count,
        "rr_intervals": rr_intervals,
        "acc_x": acc_x,
        "acc_y": acc_y,
        "acc_z": acc_z,
        "movement": movement,
        "gyro": gyro,
        "spo2": spo2,
    }


def load_har_data() -> pd.DataFrame:
    """Load all sensor data from HAR files into a DataFrame (1 row per second)."""
    all_points = {}
    for f in sorted(HAR_DIR.glob("*.har")):
        try:
            har = json.loads(f.read_text())
        except Exception:
            continue
        for entry in har["log"]["entries"]:
            req = entry["request"]
            if "metrics-service" not in req.get("url", ""):
                continue
            content = req.get("_content") or req.get("postData", {})
            text = content.get("text", "")
            if not text:
                continue
            try:
                data = base64.b64decode(text)
            except Exception:
                continue
            for j in range(len(data) // RECORD_SIZE):
                payload = data[j * RECORD_SIZE + 3: j * RECORD_SIZE + 119]
                if len(payload) < 116:
                    continue
                ts = struct.unpack("<I", payload[12:16])[0]
                if ts > 1_000_000_000:
                    all_points[ts] = payload

    if not all_points:
        print("WARNING: No HAR data found. Check HAR_DIR:", HAR_DIR)
        return pd.DataFrame()

    rows = []
    for ts in sorted(all_points):
        rec = _extract_payload(all_points[ts])
        rec["timestamp"] = ts
        rec["datetime_utc"] = datetime.fromtimestamp(ts, timezone.utc)
        rec["datetime_local"] = rec["datetime_utc"] + BERLIN
        rows.append(rec)

    df = pd.DataFrame(rows)
    # Expand rr_intervals into separate columns
    df["rr1_ms"] = df["rr_intervals"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
    df["rr2_ms"] = df["rr_intervals"].apply(lambda x: x[1] if len(x) > 1 else np.nan)
    df.drop(columns=["rr_intervals"], inplace=True)
    df["date"] = df["datetime_local"].dt.date
    return df


def load_ground_truth() -> pd.DataFrame:
    """Load ground truth scores from deep dive JSONs and cycles API."""
    records = []
    for f in sorted(DEEP_DIVE_DIR.glob("*.json")):
        date_str = f.stem  # e.g. "2025-01-15"
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue

        rec = {"date": date_str}

        # Extract sleep score
        for section in data.get("sleep", {}).get("sections", []):
            for item in section.get("items", []):
                if item["type"] == "SCORE_GAUGE":
                    c = item["content"]
                    if c.get("id") == "SLEEP_SCORE_GAUGE":
                        try:
                            rec["sleep_score"] = float(c["score_display"])
                        except (ValueError, TypeError):
                            pass

        # Extract recovery score and contributors
        for section in data.get("recovery", {}).get("sections", []):
            for item in section.get("items", []):
                if item["type"] == "SCORE_GAUGE":
                    c = item["content"]
                    if c.get("id") == "RECOVERY_SCORE_GAUGE":
                        try:
                            rec["recovery_score"] = float(c["score_display"])
                        except (ValueError, TypeError):
                            pass
                if item["type"] == "CONTRIBUTORS_TILE":
                    for metric in item["content"].get("metrics", []):
                        mid = metric.get("id", "")
                        val = metric.get("status", "")
                        try:
                            val_f = float(val)
                        except (ValueError, TypeError):
                            continue
                        if "HRV" in mid:
                            rec["hrv_ms"] = val_f
                        elif "RHR" in mid:
                            rec["rhr_bpm"] = val_f
                        elif "RESPIRATORY" in mid:
                            rec["resp_rate"] = val_f

        # Extract strain score
        for section in data.get("strain", {}).get("sections", []):
            for item in section.get("items", []):
                if item["type"] == "SCORE_GAUGE":
                    c = item["content"]
                    if c.get("id") == "STRAIN_SCORE_GAUGE":
                        try:
                            rec["strain_score"] = float(c["score_display"])
                        except (ValueError, TypeError):
                            pass

        records.append(rec)

    gt = pd.DataFrame(records)
    if gt.empty:
        return gt

    # Merge with cycles data for additional strain info
    if CYCLES_FILE.exists():
        cycles = json.loads(CYCLES_FILE.read_text())
        cycle_rows = []
        for c in cycles:
            if c.get("score_state") != "SCORED" or not c.get("score"):
                continue
            start = datetime.fromisoformat(c["start"].replace("Z", "+00:00"))
            end_str = c.get("end")
            if not end_str:
                continue
            end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            # Use the end date (local timezone) as the cycle date
            end_berlin = end + BERLIN
            cycle_rows.append({
                "date": end_berlin.strftime("%Y-%m-%d"),
                "cycle_strain": c["score"]["strain"],
                "cycle_kj": c["score"]["kilojoule"],
                "cycle_avg_hr": c["score"]["average_heart_rate"],
                "cycle_max_hr": c["score"]["max_heart_rate"],
            })
        if cycle_rows:
            cycles_df = pd.DataFrame(cycle_rows)
            gt = gt.merge(cycles_df, on="date", how="left")

    return gt


def load_all():
    """Load both raw data and ground truth."""
    print("Loading HAR sensor data...")
    sensor_df = load_har_data()
    print(f"  {len(sensor_df)} sensor records loaded")
    if not sensor_df.empty:
        dates = sensor_df["date"].unique()
        print(f"  Dates: {min(dates)} to {max(dates)}")

    print("Loading ground truth...")
    gt_df = load_ground_truth()
    print(f"  {len(gt_df)} ground truth days loaded")
    if not gt_df.empty:
        print(f"  Dates: {gt_df['date'].min()} to {gt_df['date'].max()}")

    return sensor_df, gt_df


if __name__ == "__main__":
    sensor_df, gt_df = load_all()
    print("\nSensor columns:", list(sensor_df.columns))
    print("\nGround truth:")
    print(gt_df.to_string(index=False))
