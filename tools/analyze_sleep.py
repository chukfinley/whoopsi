#!/usr/bin/env python3
"""Whoop Sleep Analysis - full sensor decode from HAR files."""

import json
import base64
import struct
import csv
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BERLIN = timedelta(hours=1)


def load_all_data():
    """Load and deduplicate all metrics from all HAR files."""
    all_points = {}
    for f in sorted(Path(".").glob("*.har")):
        har = json.load(open(f))
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
            except:
                continue
            for j in range(len(data) // 124):
                payload = data[j * 124 + 3 : j * 124 + 119]
                ts = struct.unpack("<I", payload[12:16])[0]
                if ts > 1_000_000_000:
                    all_points[ts] = payload
    return all_points


def extract(payload):
    """Extract all sensor values from 116-byte payload."""
    hr = payload[19]                                          # Heart Rate (BPM)
    rr = struct.unpack("<H", payload[21:23])[0]               # RR-Intervall (ms)

    # 3-Achsen Beschleunigungsmesser (BE float32, Einheit: g)
    acc_x = struct.unpack(">f", payload[45:49])[0]
    acc_y = struct.unpack(">f", payload[49:53])[0]
    acc_z = struct.unpack(">f", payload[53:57])[0]

    # Gyroscop / Rotationssensor (BE float32, vermutl. rad/s oder deg/s)
    gyro = struct.unpack(">f", payload[29:33])[0]

    # Weiterer Sensor (BE float32, offset 41) - evtl. Hauttemperatur-Delta oder Leitfähigkeit
    sensor41 = struct.unpack(">f", payload[41:45])[0]

    # Byte 17: zusätzlicher physiologischer Wert (0-126)
    val17 = payload[17]

    # Byte 55: meist konstant ~87, könnte SpO2-verwandt sein
    val55 = payload[55]

    # Bytes 66, 68: langsam variierende Werte (43-120, 62-151)
    val66 = payload[66]
    val68 = payload[68]

    # Bytes 103-106: float-bereich, evtl. Hauttemp oder Batterie
    skin_temp = struct.unpack(">f", payload[103:107])[0]

    # Bewegung = Abweichung von 1g (Erdanziehung abziehen)
    if all(math.isfinite(v) for v in [acc_x, acc_y, acc_z]):
        total_g = math.sqrt(acc_x**2 + acc_y**2 + acc_z**2)
        movement = abs(total_g - 1.0)  # 0 = ruhig, >0 = Bewegung
    else:
        movement = 0

    return {
        "hr": hr, "rr_ms": rr,
        "acc_x": acc_x, "acc_y": acc_y, "acc_z": acc_z,
        "movement": movement,
        "gyro": gyro, "sensor41": sensor41,
        "val17": val17, "val55": val55,
        "val66": val66, "val68": val68,
        "skin_temp_raw": skin_temp,
    }


def moving_avg(values, window):
    """Simple moving average."""
    result = []
    for i in range(len(values)):
        start = max(0, i - window // 2)
        end = min(len(values), i + window // 2 + 1)
        chunk = [v for v in values[start:end] if v > 0]
        result.append(sum(chunk) / len(chunk) if chunk else 0)
    return result


def classify_sleep_phase(avg_hr, hr_std, movement_dev, rhr):
    """Classify sleep phase based on HR and movement deviation from 1g."""
    if avg_hr == 0:
        return "?"

    hr_above_rhr = avg_hr - rhr

    # Wach: hohe HR ODER hohe Bewegung + HR deutlich über Ruhe
    if avg_hr > rhr + 15 and movement_dev > 0.4:
        return "WACH"
    # Tiefschlaf: niedrigste HR, kaum Variabilität, minimale Bewegung
    elif hr_above_rhr < 3 and hr_std < 3 and movement_dev < 0.3:
        return "TIEF"
    elif hr_above_rhr < 4 and hr_std < 3 and movement_dev < 0.4:
        return "TIEF"
    # REM: HR-Variabilität erhöht, aber wenig Bewegung (Muskelatonie)
    elif hr_std > 5 and movement_dev < 0.5:
        return "REM"
    # Leichtschlaf: moderate HR, moderate Variabilität
    elif hr_above_rhr < 8 and movement_dev < 0.6:
        return "LEICHT"
    elif hr_above_rhr < 12:
        return "LEICHT"
    else:
        return "WACH"


def main():
    print("Lade alle HAR-Dateien...\n")
    all_points = load_all_data()

    # Filter sample day data
    jan31 = {ts: p for ts, p in all_points.items() if ts > 1769800000}
    sorted_ts = sorted(jan31.keys())
    metrics = {ts: extract(jan31[ts]) for ts in sorted_ts}

    first_dt = datetime.fromtimestamp(sorted_ts[0], timezone.utc) + BERLIN
    last_dt = datetime.fromtimestamp(sorted_ts[-1], timezone.utc) + BERLIN

    print("=" * 65)
    print("  WHOOP DATENANALYSE — sample date")
    print("=" * 65)
    print(f"\n  Zeitraum:      {first_dt.strftime('%H:%M:%S')} – {last_dt.strftime('%H:%M:%S')} (local time)")
    print(f"  Datenpunkte:   {len(sorted_ts)} (1 Hz)")
    print(f"  Dauer:         {(sorted_ts[-1]-sorted_ts[0])//60} min "
          f"({(sorted_ts[-1]-sorted_ts[0])//3600}h {((sorted_ts[-1]-sorted_ts[0])%3600)//60}m)")

    # ═══════════════════════════════════════════════════
    # SENSOR-ÜBERSICHT
    # ═══════════════════════════════════════════════════
    hrs = [metrics[ts]["hr"] for ts in sorted_ts if metrics[ts]["hr"] > 0]
    rrs = [metrics[ts]["rr_ms"] for ts in sorted_ts if 0 < metrics[ts]["rr_ms"] < 2000]
    accx = [metrics[ts]["acc_x"] for ts in sorted_ts if math.isfinite(metrics[ts]["acc_x"])]
    accy = [metrics[ts]["acc_y"] for ts in sorted_ts if math.isfinite(metrics[ts]["acc_y"])]
    accz = [metrics[ts]["acc_z"] for ts in sorted_ts if math.isfinite(metrics[ts]["acc_z"])]
    gyros = [metrics[ts]["gyro"] for ts in sorted_ts if math.isfinite(metrics[ts]["gyro"]) and abs(metrics[ts]["gyro"]) < 100]
    s41 = [metrics[ts]["sensor41"] for ts in sorted_ts if 0 < metrics[ts]["sensor41"] < 100]
    v17 = [metrics[ts]["val17"] for ts in sorted_ts if metrics[ts]["val17"] > 0]
    v55 = [metrics[ts]["val55"] for ts in sorted_ts]
    v66 = [metrics[ts]["val66"] for ts in sorted_ts]
    v68 = [metrics[ts]["val68"] for ts in sorted_ts]

    print(f"\n{'─' * 65}")
    print("  ERKANNTE SENSOREN")
    print(f"{'─' * 65}")
    print(f"  {'Sensor':<30} {'Min':>8} {'Max':>8} {'Avg':>8} {'Einheit':<12}")
    print(f"  {'─'*30} {'─'*8} {'─'*8} {'─'*8} {'─'*12}")
    print(f"  {'Herzfrequenz':<30} {min(hrs):8d} {max(hrs):8d} {sum(hrs)/len(hrs):8.1f} {'BPM':<12}")
    if rrs:
        rrs_clean = [r for r in rrs if r > 0]
        print(f"  {'RR-Intervall':<30} {min(rrs_clean):8d} {max(rrs_clean):8d} {sum(rrs_clean)/len(rrs_clean):8.0f} {'ms':<12}")
    print(f"  {'Beschleunigung X':<30} {min(accx):8.3f} {max(accx):8.3f} {sum(accx)/len(accx):8.3f} {'g':<12}")
    print(f"  {'Beschleunigung Y':<30} {min(accy):8.3f} {max(accy):8.3f} {sum(accy)/len(accy):8.3f} {'g':<12}")
    print(f"  {'Beschleunigung Z':<30} {min(accz):8.3f} {max(accz):8.3f} {sum(accz)/len(accz):8.3f} {'g':<12}")
    if gyros:
        print(f"  {'Gyroskop/Rotation':<30} {min(gyros):8.3f} {max(gyros):8.3f} {sum(gyros)/len(gyros):8.3f} {'rad/s?':<12}")
    if s41:
        print(f"  {'Sensor (Offset 41)':<30} {min(s41):8.3f} {max(s41):8.3f} {sum(s41)/len(s41):8.3f} {'?':<12}")
    if v17:
        print(f"  {'Wert (Byte 17)':<30} {min(v17):8d} {max(v17):8d} {sum(v17)/len(v17):8.1f} {'?':<12}")
    print(f"  {'Wert (Byte 55)':<30} {min(v55):8d} {max(v55):8d} {sum(v55)/len(v55):8.1f} {'?':<12}")
    print(f"  {'Wert (Byte 66)':<30} {min(v66):8d} {max(v66):8d} {sum(v66)/len(v66):8.1f} {'?':<12}")
    print(f"  {'Wert (Byte 68)':<30} {min(v68):8d} {max(v68):8d} {sum(v68)/len(v68):8.1f} {'?':<12}")

    # ═══════════════════════════════════════════════════
    # HERZFREQUENZ
    # ═══════════════════════════════════════════════════
    print(f"\n{'─' * 65}")
    print("  HERZFREQUENZ")
    print(f"{'─' * 65}")
    print(f"  Durchschnitt:   {sum(hrs)/len(hrs):.0f} BPM")
    print(f"  Minimum:        {min(hrs)} BPM")
    print(f"  Maximum:        {max(hrs)} BPM")

    # Resting HR (lowest 5-min window)
    window = 300
    rhr = 999
    rhr_time = None
    for i in range(0, len(sorted_ts) - window, 30):
        whr = [metrics[sorted_ts[j]]["hr"] for j in range(i, min(i+window, len(sorted_ts)))
               if metrics[sorted_ts[j]]["hr"] > 0]
        if whr:
            avg = sum(whr) / len(whr)
            if avg < rhr:
                rhr = avg
                rhr_time = sorted_ts[i]

    if rhr_time:
        rhr_dt = datetime.fromtimestamp(rhr_time, timezone.utc) + BERLIN
        print(f"  Ruhe-HR (5min): {rhr:.0f} BPM (um {rhr_dt.strftime('%H:%M')})")

    # ═══════════════════════════════════════════════════
    # HRV
    # ═══════════════════════════════════════════════════
    rrs_clean = [r for r in rrs if 300 < r < 2000]
    if len(rrs_clean) > 10:
        diffs_sq = [(rrs_clean[i+1] - rrs_clean[i])**2 for i in range(len(rrs_clean)-1)]
        rmssd = math.sqrt(sum(diffs_sq) / len(diffs_sq))

        print(f"\n{'─' * 65}")
        print("  HRV (Herzratenvariabilität)")
        print(f"{'─' * 65}")
        print(f"  RR-Intervall Ø: {sum(rrs_clean)/len(rrs_clean):.0f} ms")
        print(f"  RMSSD:          {rmssd:.1f} ms")
        print(f"  Gültige RR:     {len(rrs_clean)} von {len(sorted_ts)} ({100*len(rrs_clean)/len(sorted_ts):.0f}%)")

    # ═══════════════════════════════════════════════════
    # SCHLAFPHASEN (5-Min Fenster)
    # ═══════════════════════════════════════════════════
    print(f"\n{'─' * 65}")
    print("  SCHLAFPHASEN-ANALYSE (5-Min Fenster)")
    print(f"{'─' * 65}\n")

    WINDOW = 300
    phases = []

    print(f"  {'Zeit':>5}  {'HR':>4} {'±':>4} {'Beweg.':>6} {'RR':>5} {'Phase':<10}")
    print(f"  {'─'*48}")

    for i in range(0, len(sorted_ts) - WINDOW, WINDOW):
        w_ts = sorted_ts[i:i+WINDOW]
        w_hr = [metrics[t]["hr"] for t in w_ts if metrics[t]["hr"] > 0]
        w_mv = [metrics[t]["movement"] for t in w_ts if math.isfinite(metrics[t]["movement"])]
        w_rr = [metrics[t]["rr_ms"] for t in w_ts if 300 < metrics[t]["rr_ms"] < 2000]

        avg_hr = sum(w_hr) / len(w_hr) if w_hr else 0
        std_hr = (sum((h - avg_hr)**2 for h in w_hr) / len(w_hr))**0.5 if len(w_hr) > 1 else 0
        avg_mv = sum(w_mv) / len(w_mv) if w_mv else 0
        avg_rr = sum(w_rr) / len(w_rr) if w_rr else 0

        phase = classify_sleep_phase(avg_hr, std_hr, avg_mv, rhr)
        t = datetime.fromtimestamp(w_ts[0], timezone.utc) + BERLIN

        phases.append((t, avg_hr, std_hr, avg_mv, avg_rr, phase))

        rr_str = f"{avg_rr:.0f}" if avg_rr > 0 else "  -"
        print(f"  {t.strftime('%H:%M'):>5}  {avg_hr:4.0f} {std_hr:4.1f} {avg_mv:6.2f} {rr_str:>5} {phase:<10}")

    # ═══════════════════════════════════════════════════
    # ZUSAMMENFASSUNG
    # ═══════════════════════════════════════════════════
    phase_mins = defaultdict(int)
    for _, _, _, _, _, phase in phases:
        phase_mins[phase] += 5

    total_mins = len(phases) * 5
    sleep_mins = sum(v for k, v in phase_mins.items() if k not in ("WACH", "?"))

    # Find sleep/wake boundaries
    sleep_start = None
    sleep_end = None
    for t, _, _, _, _, phase in phases:
        if phase in ("TIEF", "LEICHT", "REM") and sleep_start is None:
            sleep_start = t
        if phase in ("TIEF", "LEICHT", "REM"):
            sleep_end = t

    wake_time = None
    for t, _, _, _, _, phase in reversed(phases):
        if phase != "WACH":
            wake_time = t + timedelta(minutes=5)
            break

    print(f"\n{'═' * 65}")
    print("  SCHLAF-ZUSAMMENFASSUNG")
    print(f"{'═' * 65}\n")

    if sleep_start:
        print(f"  Schlafbeginn (aufgezeichnet): {sleep_start.strftime('%H:%M')} Uhr")
    if wake_time:
        print(f"  Aufwachzeit:                  {wake_time.strftime('%H:%M')} Uhr")
    if sleep_start and wake_time:
        bed_time = (wake_time - sleep_start).total_seconds() / 60
        print(f"  Zeit im Bett:                 {int(bed_time)} min ({int(bed_time)//60}h {int(bed_time)%60}m)")

    print(f"\n  Aufzeichnungsdauer:           {total_mins} min")
    print()

    labels = {"TIEF": "Tiefschlaf", "LEICHT": "Leichtschlaf", "REM": "REM-Schlaf", "WACH": "Wach"}
    colors = {"TIEF": "█", "LEICHT": "▓", "REM": "▒", "WACH": "░"}

    for phase_key in ["TIEF", "LEICHT", "REM", "WACH"]:
        mins = phase_mins.get(phase_key, 0)
        pct = mins / total_mins * 100 if total_mins else 0
        bar = colors[phase_key] * int(pct / 2)
        print(f"  {labels[phase_key]:<16} {mins:3d} min ({pct:4.1f}%) {bar}")

    print(f"\n  Geschätzte Schlafzeit:         {sleep_mins} min ({sleep_mins//60}h {sleep_mins%60}m)")
    print(f"  Schlafeffizienz:              {100*sleep_mins/total_mins:.0f}%" if total_mins else "")

    # ═══════════════════════════════════════════════════
    # HR TIMELINE
    # ═══════════════════════════════════════════════════
    print(f"\n{'─' * 65}")
    print("  HR-VERLAUF")
    print(f"{'─' * 65}\n")

    valid_phases = [(t, h, p) for t, h, _, _, _, p in phases if h > 0]
    if valid_phases:
        min_hr = min(h for _, h, _ in valid_phases)
        max_hr = max(h for _, h, _ in valid_phases)
        hr_range = max_hr - min_hr if max_hr > min_hr else 1
        W = 40

        phase_sym = {"TIEF": "T", "LEICHT": "L", "REM": "R", "WACH": "W", "?": "?"}

        for t, avg_hr, phase in valid_phases:
            bar_len = int((avg_hr - min_hr) / hr_range * W)
            bar = "▓" * bar_len + "░" * (W - bar_len)
            print(f"  {t.strftime('%H:%M')} {phase_sym[phase]} │{bar}│ {avg_hr:.0f}")

        print(f"         {'└' + '─' * W + '┘'}")
        print(f"          {min_hr:.0f}{' ' * (W - 6)}{max_hr:.0f} BPM")

    # ═══════════════════════════════════════════════════
    # HYPNOGRAM
    # ═══════════════════════════════════════════════════
    print(f"\n{'─' * 65}")
    print("  HYPNOGRAMM")
    print(f"{'─' * 65}\n")

    depth = {"WACH": 0, "REM": 1, "LEICHT": 2, "TIEF": 3, "?": 0}
    labels_y = ["Wach  ", "REM   ", "Leicht", "Tief  "]

    for t, _, _, _, _, phase in phases:
        d = depth[phase]
        line = "  " * d + "██"
        print(f"  {t.strftime('%H:%M')} {labels_y[d]} {'·' * d}██{'·' * (3-d)}")

    # ═══════════════════════════════════════════════════
    # CSV EXPORT
    # ═══════════════════════════════════════════════════
    csv_path = Path("whoop_data.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "timestamp_berlin", "hr_bpm", "rr_ms",
                     "acc_x_g", "acc_y_g", "acc_z_g", "movement_g",
                     "gyro", "sensor41", "val17", "val55", "val66", "val68"])
        for ts in sorted_ts:
            m = metrics[ts]
            dt_utc = datetime.fromtimestamp(ts, timezone.utc)
            dt_b = dt_utc + BERLIN
            w.writerow([
                dt_utc.isoformat(), dt_b.strftime("%Y-%m-%d %H:%M:%S"),
                m["hr"], m["rr_ms"],
                f"{m['acc_x']:.6f}", f"{m['acc_y']:.6f}", f"{m['acc_z']:.6f}",
                f"{m['movement']:.6f}",
                f"{m['gyro']:.6f}", f"{m['sensor41']:.6f}",
                m["val17"], m["val55"], m["val66"], m["val68"],
            ])

    print(f"\n  CSV exportiert: {csv_path} ({len(sorted_ts)} Zeilen, 14 Spalten)")


if __name__ == "__main__":
    main()
