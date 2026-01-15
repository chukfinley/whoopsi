#!/usr/bin/env python3
"""Optimize sleep phase classification against Whoop's per-minute timeline.

Compares our 10-minute window classification against Whoop's exact phase
assignment at each point. Optimizes for per-window accuracy, not just
percentage distribution.

Ground truth: deep_dive JSONs contain time_bound_ranges (fractional 0.0-1.0)
for each sleep stage within the exact sleep window start/end times.
"""

import sys
import json
import re
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone, date as date_cls

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.optimize import differential_evolution

from data.db_loader import load_from_db

DEEP_DIVE_DIR = Path(__file__).resolve().parent.parent / "whoop-companion" / "data" / "whoop_backup" / "deep_dive"
PHASE_MAP = {"AWAKE": "awake", "LIGHT_SLEEP": "light", "SWS_SLEEP": "deep", "REM_SLEEP": "rem"}


def extract_whoop_timeline(date_str):
    """Extract per-minute phase from Whoop deep_dive.

    Returns: (dict of {unix_minute: phase}, sleep_start_unix, sleep_end_unix)
    where unix_minute = unix_timestamp // 60
    """
    f = DEEP_DIVE_DIR / f"{date_str}.json"
    if not f.exists():
        return None, None, None

    data = json.load(open(f))
    last = data.get("last_night", {})
    header = last.get("header_section")
    if not header:
        return None, None, None
    dest = header.get("destination")
    if not dest:
        return None, None, None
    params = dest.get("parameters", {})
    start_str = params.get("start_time")
    end_str = params.get("end_time")
    if not start_str or not end_str:
        return None, None, None

    start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    total_secs = end_ts - start_ts
    if total_secs <= 0:
        return None, None, None

    try:
        zones = last["sections"][0]["items"][0]["content"]["card_content"][2]["content"]["heart_rate_zones"]
    except (KeyError, IndexError):
        return None, None, None

    minute_gt = {}
    for z in zones:
        phase = PHASE_MAP.get(z["id"])
        if not phase:
            continue
        tbr = z.get("bar_graph", {}).get("time_bound_ranges", [])
        for r in tbr:
            lo = r["lower_endpoint"]
            hi = r["upper_endpoint"]
            seg_start_ts = start_ts + lo * total_secs
            seg_end_ts = start_ts + hi * total_secs
            # Fill per-minute
            t = int(seg_start_ts) // 60
            end_min = int(seg_end_ts) // 60
            while t <= end_min:
                minute_gt[t] = phase
                t += 1

    return minute_gt, int(start_ts), int(end_ts)


def compute_rhr(sleep_df):
    """Compute resting heart rate from sleep data."""
    hr = sleep_df["hr"].values
    hr_valid = hr[hr > 30]
    if len(hr_valid) < 10:
        return 55.0
    window = 300
    best = 999
    for i in range(0, len(hr_valid) - window, 60):
        chunk = hr_valid[i:i + window]
        if len(chunk) > 50:
            best = min(best, float(np.median(chunk)))
    return best if best < 999 else float(np.percentile(hr_valid, 10))


def classify_and_compare(sleep_df, rhr, params, minute_gt):
    """Classify 10-minute windows and compare against Whoop per-minute GT.

    Returns: (correct, total, confusion_counter)
    """
    (awake_ha, awake_mv, deep_ha, deep_iqr, deep_mv,
     rem_iqr, rem_hrv, rem_mv, mv_high) = params

    window = 600
    correct = 0
    total = 0
    confusion = Counter()

    for i in range(0, len(sleep_df) - window, window):
        chunk = sleep_df.iloc[i:i + window]
        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            continue

        avg_hr = float(np.median(hr_v))
        hr_iqr = float(np.percentile(hr_v, 75) - np.percentile(hr_v, 25)) if len(hr_v) > 10 else float(hr_v.std()) if len(hr_v) > 1 else 0
        avg_mv = float(mv.mean())
        max_mv = float(mv.max())
        ha = avg_hr - rhr

        local_hrv = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_hrv = float(np.sqrt(np.mean(diffs**2)))

        is_moving = avg_mv > mv_high or max_mv > mv_high * 3

        if is_moving and ha > awake_ha:
            pred = "awake"
        elif ha <= deep_ha and hr_iqr < deep_iqr and avg_mv < deep_mv:
            pred = "deep"
        elif hr_iqr > rem_iqr and avg_mv < rem_mv:
            pred = "rem"
        elif local_hrv > rem_hrv and avg_mv < rem_mv:
            pred = "rem"
        else:
            pred = "light"

        # Get the unix timestamp of the window center
        mid_idx = len(chunk) // 2
        mid_ts = chunk.iloc[mid_idx]["timestamp"]
        mid_min = int(mid_ts) // 60

        # Find Whoop's dominant phase in the ±5 minute range
        whoop_phases = []
        for offset in range(-5, 5):
            p = minute_gt.get(mid_min + offset)
            if p:
                whoop_phases.append(p)

        if not whoop_phases:
            continue

        dominant = Counter(whoop_phases).most_common(1)[0][0]
        total += 1
        if pred == dominant:
            correct += 1
        else:
            confusion[(dominant, pred)] += 1

    return correct, total, confusion


def score_params(params, day_datasets):
    """Score: (1 - accuracy) * 100. Lower = better."""
    total_correct = 0
    total_windows = 0

    for sleep_df, rhr, minute_gt in day_datasets:
        c, t, _ = classify_and_compare(sleep_df, rhr, params, minute_gt)
        total_correct += c
        total_windows += t

    if total_windows == 0:
        return 1000.0

    return (1.0 - total_correct / total_windows) * 100


def main():
    print("=" * 70)
    print("  SLEEP PHASE TIMELINE OPTIMIZER")
    print("  (per-window accuracy against Whoop's exact phase assignments)")
    print("=" * 70)

    print("\nLoading sensor data...")
    df = load_from_db()

    sensor_dates = sorted(set(
        str(d) for d in df["date"].unique()
        if hasattr(d, "year") and d.year >= 2025
    ))
    print(f"  Sensor dates: {sensor_dates}")

    day_datasets = []
    day_labels = []

    for date_str in sensor_dates:
        minute_gt, sleep_start_ts, sleep_end_ts = extract_whoop_timeline(date_str)
        if minute_gt is None or len(minute_gt) < 100:
            continue

        parts = date_str.split("-")
        day_date = date_cls(int(parts[0]), int(parts[1]), int(parts[2]))
        day_df = df[df["date"] == day_date]

        # Filter to sleep window using unix timestamps
        mask = (day_df["timestamp"] >= sleep_start_ts) & (day_df["timestamp"] <= sleep_end_ts)
        sleep_df = day_df[mask]

        if len(sleep_df) < 300:
            print(f"  {date_str}: only {len(sleep_df)} sensor records in sleep window, skipping")
            continue

        rhr = compute_rhr(sleep_df)
        gt_phases = Counter(minute_gt.values())
        total_min = sum(gt_phases.values())
        print(f"  {date_str}: {len(sleep_df)} sensor, RHR={rhr:.0f}, "
              f"GT={total_min}min (d:{gt_phases.get('deep',0)}, l:{gt_phases.get('light',0)}, "
              f"r:{gt_phases.get('rem',0)}, a:{gt_phases.get('awake',0)})")

        day_datasets.append((sleep_df, rhr, minute_gt))
        day_labels.append(date_str)

    if not day_datasets:
        print("\nERROR: No days with both sensor data and Whoop timeline!")
        return None, None

    print(f"\n  {len(day_datasets)} days for optimization")

    # Current thresholds
    current_params = [16.3, 0.6, 0.9, 9.9, 1.4, 12.8, 84.1, 0.4, 0.6]

    current_score = score_params(current_params, day_datasets)
    current_acc = 100 - current_score
    print(f"\n  Current accuracy: {current_acc:.1f}%")

    for i, (sleep_df, rhr, minute_gt) in enumerate(day_datasets):
        c, t, conf = classify_and_compare(sleep_df, rhr, current_params, minute_gt)
        if t > 0:
            print(f"    {day_labels[i]}: {c}/{t} correct ({c/t*100:.0f}%)")
            top_err = conf.most_common(3)
            if top_err:
                errs = ", ".join(f"{w}->{p}:{n}" for (w, p), n in top_err)
                print(f"      Errors: {errs}")

    # Bounds
    bounds = [
        (3.0, 30.0),    # awake_ha
        (0.1, 3.0),     # awake_mv (unused directly, part of is_moving)
        (-5.0, 15.0),   # deep_ha
        (2.0, 20.0),    # deep_iqr
        (0.1, 3.0),     # deep_mv
        (3.0, 25.0),    # rem_iqr
        (20.0, 200.0),  # rem_hrv
        (0.1, 3.0),     # rem_mv
        (0.1, 3.0),     # mv_high
    ]

    print("\n  Running differential evolution (maxiter=200, popsize=40)...")
    iteration = [0]

    def objective(params):
        iteration[0] += 1
        if iteration[0] % 500 == 0:
            s = score_params(params, day_datasets)
            print(f"    Iter {iteration[0]}: accuracy={100-s:.1f}%")
        return score_params(params, day_datasets)

    result = differential_evolution(
        objective, bounds,
        seed=42, maxiter=200, popsize=40, tol=0.005,
        mutation=(0.5, 1.5), recombination=0.8,
    )

    opt_params = result.x
    opt_score = result.fun
    opt_acc = 100 - opt_score

    print(f"\n{'='*70}")
    print(f"  RESULTS")
    print(f"{'='*70}")
    print(f"  Accuracy: {current_acc:.1f}% → {opt_acc:.1f}% ({iteration[0]} evals)")

    for i, (sleep_df, rhr, minute_gt) in enumerate(day_datasets):
        c, t, conf = classify_and_compare(sleep_df, rhr, opt_params, minute_gt)
        if t > 0:
            print(f"    {day_labels[i]}: {c}/{t} ({c/t*100:.0f}%)")
            if conf:
                top_err = conf.most_common(3)
                errs = ", ".join(f"{w}->{p}:{n}" for (w, p), n in top_err)
                print(f"      Errors: {errs}")

    names = ["awake_ha", "awake_mv", "deep_ha", "deep_iqr", "deep_mv",
             "rem_iqr", "rem_hrv", "rem_mv", "mv_high"]
    print(f"\n  Thresholds:")
    for n, v, old in zip(names, opt_params, current_params):
        changed = " *" if abs(v - old) > 0.5 else ""
        print(f"    {n:12s}: {old:6.1f} → {v:6.1f}{changed}")

    # Apply to engine.py and analyze_all.py
    def replace_thresholds(code, opt):
        code = re.sub(r'(is_moving = avg_mv > )[\d.]+( or max_mv > )[\d.]+',
                       f'\\g<1>{opt[8]:.1f}\\g<2>{opt[8]*3:.1f}', code)
        code = re.sub(r'(if is_moving and ha > )[\d.]+:',
                       f'\\g<1>{opt[0]:.1f}:', code)
        code = re.sub(r'(elif ha <= )[\d.]+( and hr_iqr < )[\d.]+( and avg_mv < )[\d.]+:',
                       f'\\g<1>{opt[2]:.1f}\\g<2>{opt[3]:.1f}\\g<3>{opt[4]:.1f}:', code)
        code = re.sub(r'(elif hr_iqr > )[\d.]+( and avg_mv < )[\d.]+:',
                       f'\\g<1>{opt[5]:.1f}\\g<2>{opt[7]:.1f}:', code)
        code = re.sub(r'(elif local_hrv > )[\d.]+( and avg_mv < )[\d.]+:',
                       f'\\g<1>{opt[6]:.1f}\\g<2>{opt[7]:.1f}:', code)
        return code

    for name, path in [
        ("engine.py", Path(__file__).parent / "algo4_calibrated" / "engine.py"),
        ("analyze_all.py", Path(__file__).parent / "analyze_all.py"),
    ]:
        print(f"\n  Updating {name}...")
        code = path.read_text()
        new_code = replace_thresholds(code, opt_params)
        if new_code != code:
            path.write_text(new_code)
            print(f"    Done!")
        else:
            print(f"    WARNING: no pattern matched")

    return opt_params, opt_score


if __name__ == "__main__":
    params, score = main()
    if params is not None:
        print("\n  Re-running full analysis with optimized classification...")
        import subprocess
        subprocess.run([sys.executable, str(Path(__file__).parent / "analyze_all.py")])
