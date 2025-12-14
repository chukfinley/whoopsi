"""Load sensor data from the companion app's SQLite database.

Re-decodes rawHex AA01 frames to extract correct HR, RR, accel, gyro values
that were stored with wrong offsets in the original DB schema.
"""

import sqlite3
import struct
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

DB_PATH = Path(__file__).resolve().parent / "raw" / "whoop_capture.db"
BERLIN = timedelta(hours=1)


def decode_aa01_inner(raw: bytes) -> dict | None:
    """Decode an AA01-framed 0x2F sensor packet from raw bytes."""
    if len(raw) < 16:
        return None
    if raw[0] != 0xAA or raw[1] != 0x01:
        return None

    inner = raw[8:-4]  # strip 8-byte header + 4-byte CRC
    if len(inner) < 52 or inner[0] != 0x2F:
        return None

    ts = struct.unpack("<I", inner[7:11])[0]
    if ts < 1600000000 or ts > 1800000000:  # valid: ~2020 to ~2027
        return None

    spo2_raw = inner[14]
    spo2 = spo2_raw + 10 if 0 < spo2_raw < 100 else None

    # HR/RR extraction based on inner size
    if len(inner) <= 80:
        # 76-byte format: direct HR
        hr = inner[19] if len(inner) > 19 else 0
        rr_count = inner[20] if len(inner) > 20 else 0
        rr1 = struct.unpack("<H", inner[21:23])[0] if len(inner) >= 23 else 0
        rr2 = struct.unpack("<H", inner[23:25])[0] if len(inner) >= 25 else 0
    else:
        # 112-byte format: RR intervals, compute HR
        rr_count = inner[15]
        rr1 = struct.unpack("<H", inner[16:18])[0] if rr_count >= 1 and len(inner) >= 18 else 0
        rr2 = struct.unpack("<H", inner[18:20])[0] if rr_count >= 2 and len(inner) >= 20 else 0
        hr = 60000 // rr1 if 200 < rr1 < 2000 else 0

    # Accel/Gyro (verified offsets)
    gyro = struct.unpack(">f", inner[36:40])[0] if len(inner) >= 40 else 0.0
    acc_x = struct.unpack(">f", inner[40:44])[0] if len(inner) >= 44 else 0.0
    acc_y = struct.unpack(">f", inner[44:48])[0] if len(inner) >= 48 else 0.0
    acc_z = struct.unpack(">f", inner[48:52])[0] if len(inner) >= 52 else 0.0

    # Validate floats
    for v in [gyro, acc_x, acc_y, acc_z]:
        if not math.isfinite(v) or abs(v) > 100:
            gyro = acc_x = acc_y = acc_z = 0.0
            break

    movement = abs(math.sqrt(acc_x**2 + acc_y**2 + acc_z**2) - 1.0) if all(
        math.isfinite(v) for v in [acc_x, acc_y, acc_z]) else 0.0

    return {
        "timestamp": ts,
        "hr": hr,
        "rr_count": rr_count,
        "rr1_ms": rr1 if 200 < rr1 < 2500 else np.nan,
        "rr2_ms": rr2 if 200 < rr2 < 2500 else np.nan,
        "acc_x": acc_x,
        "acc_y": acc_y,
        "acc_z": acc_z,
        "movement": movement,
        "gyro": gyro,
        "spo2": spo2,
    }


def load_from_db(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Load and re-decode all sensor records from the companion app DB."""
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return pd.DataFrame()

    db = sqlite3.connect(str(db_path))
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM sensor_records")
    total = cur.fetchone()[0]
    print(f"  DB has {total} records, re-decoding rawHex...")

    cur.execute("SELECT timestamp, heartRate, rr1Ms, rrCount, rawHex FROM sensor_records ORDER BY timestamp")

    rows = []
    decoded = 0
    direct = 0

    for db_ts, db_hr, db_rr1, db_rrc, raw_hex in cur:
        if raw_hex and len(raw_hex) >= 32 and not raw_hex.startswith("hr_ble:"):
            try:
                raw = bytes.fromhex(raw_hex)
                rec = decode_aa01_inner(raw)
                if rec:
                    decoded += 1
                    rec["datetime_utc"] = datetime.fromtimestamp(rec["timestamp"], timezone.utc)
                    rec["datetime_local"] = rec["datetime_utc"] + BERLIN
                    rec["date"] = rec["datetime_local"].date()
                    rows.append(rec)
                    continue
            except (ValueError, struct.error):
                pass

        # Fallback: use DB values directly (for hr_ble: records or failed decodes)
        if db_hr and db_hr > 30:
            direct += 1
            ts = db_ts if db_ts > 1600000000 else int(datetime.now(timezone.utc).timestamp())
            rows.append({
                "timestamp": ts,
                "hr": db_hr,
                "rr_count": db_rrc or 0,
                "rr1_ms": db_rr1 if db_rr1 and 200 < db_rr1 < 2500 else np.nan,
                "rr2_ms": np.nan,
                "acc_x": 0.0, "acc_y": 0.0, "acc_z": 0.0,
                "movement": 0.0, "gyro": 0.0, "spo2": None,
                "datetime_utc": datetime.fromtimestamp(ts, timezone.utc),
                "datetime_local": datetime.fromtimestamp(ts, timezone.utc) + BERLIN,
                "date": (datetime.fromtimestamp(ts, timezone.utc) + BERLIN).date(),
            })

    db.close()

    print(f"  Decoded {decoded} from rawHex, {direct} from DB fields")
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Deduplicate by timestamp
    df = df.drop_duplicates(subset=["timestamp"], keep="first")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


if __name__ == "__main__":
    df = load_from_db()
    print(f"\nTotal: {len(df)} records")
    print(f"Date range: {df['date'].min()} to {df['date'].max()}")
    print(f"\nPer day:")
    for d, g in df.groupby("date"):
        valid_hr = g["hr"][g["hr"] > 30]
        valid_rr = g["rr1_ms"].dropna()
        print(f"  {d}: {len(g)} samples, {len(valid_hr)} valid HR "
              f"(avg={valid_hr.mean():.0f}), {len(valid_rr)} RR intervals")
