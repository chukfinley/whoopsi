#!/usr/bin/env python3
"""Continuous sleep phase optimizer — runs indefinitely on low CPU.

Run with: nice -n 19 taskset -c 0 python3 optimize_continuous.py

Saves best result to optimize_best.json and updates engine.py/analyze_all.py
whenever a new best is found. Logs progress to optimize_log.txt.
"""

import sys
import os
import json
import re
import time
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone, date as date_cls

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.optimize import differential_evolution

from data.db_loader import load_from_db

DEEP_DIVE_DIR = Path(__file__).resolve().parent.parent / "whoop-companion" / "data" / "whoop_backup" / "deep_dive"
PHASE_MAP = {"AWAKE": "awake", "LIGHT_SLEEP": "light", "SWS_SLEEP": "deep", "REM_SLEEP": "rem"}
BEST_FILE = Path(__file__).parent / "optimize_best.json"
LOG_FILE = Path(__file__).parent / "optimize_log.txt"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def extract_whoop_timeline(date_str):
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
            t = int(seg_start_ts) // 60
            end_min = int(seg_end_ts) // 60
            while t <= end_min:
                minute_gt[t] = phase
                t += 1
    return minute_gt, int(start_ts), int(end_ts)


def compute_rhr(sleep_df):
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
    (awake_ha, awake_mv, deep_ha, deep_iqr, deep_mv,
     rem_iqr, rem_hrv, rem_mv, mv_high) = params
    window = 600
    correct = 0
    total = 0
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
        mid_ts = chunk.iloc[len(chunk) // 2]["timestamp"]
        mid_min = int(mid_ts) // 60
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
    return correct, total


def score_params(params, day_datasets):
    total_correct = 0
    total_windows = 0
    for sleep_df, rhr, minute_gt in day_datasets:
        c, t = classify_and_compare(sleep_df, rhr, params, minute_gt)
        total_correct += c
        total_windows += t
    if total_windows == 0:
        return 1000.0
    return (1.0 - total_correct / total_windows) * 100


def apply_thresholds(opt_params):
    """Write optimized thresholds to engine.py and analyze_all.py."""
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
        code = path.read_text()
        new_code = replace_thresholds(code, opt_params)
        if new_code != code:
            path.write_text(new_code)


def save_best(params, accuracy, round_num, evals):
    names = ["awake_ha", "awake_mv", "deep_ha", "deep_iqr", "deep_mv",
             "rem_iqr", "rem_hrv", "rem_mv", "mv_high"]
    data = {
        "accuracy": round(accuracy, 2),
        "round": round_num,
        "total_evals": evals,
        "timestamp": datetime.now().isoformat(),
        "thresholds": {n: round(float(v), 2) for n, v in zip(names, params)},
        "params": [round(float(v), 2) for v in params],
    }
    with open(BEST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def main():
    log("=" * 60)
    log("CONTINUOUS SLEEP PHASE OPTIMIZER")
    log("1 core, low priority, saves best automatically")
    log("Kill with: kill $(cat /tmp/whoop_optimizer.pid)")
    log("=" * 60)

    # Write PID for easy killing
    with open("/tmp/whoop_optimizer.pid", "w") as f:
        f.write(str(os.getpid()))

    log("Loading sensor data...")
    df = load_from_db()

    sensor_dates = sorted(set(
        str(d) for d in df["date"].unique()
        if hasattr(d, "year") and d.year >= 2025
    ))

    day_datasets = []
    for date_str in sensor_dates:
        minute_gt, start_ts, end_ts = extract_whoop_timeline(date_str)
        if minute_gt is None or len(minute_gt) < 100:
            continue
        parts = date_str.split("-")
        day_date = date_cls(int(parts[0]), int(parts[1]), int(parts[2]))
        day_df = df[df["date"] == day_date]
        mask = (day_df["timestamp"] >= start_ts) & (day_df["timestamp"] <= end_ts)
        sleep_df = day_df[mask]
        if len(sleep_df) < 300:
            continue
        rhr = compute_rhr(sleep_df)
        day_datasets.append((sleep_df, rhr, minute_gt))
        log(f"  {date_str}: {len(sleep_df)} records, RHR={rhr:.0f}")

    log(f"  {len(day_datasets)} days loaded")

    # Load previous best or start from current
    if BEST_FILE.exists():
        prev = json.load(open(BEST_FILE))
        best_params = np.array(prev["params"])
        best_accuracy = prev["accuracy"]
        total_evals = prev.get("total_evals", 0)
        log(f"  Resuming from previous best: {best_accuracy:.1f}%")
    else:
        best_params = np.array([24.7, 2.5, 14.2, 5.7, 2.8, 10.2, 152.6, 0.7, 0.8])
        best_accuracy = 100 - score_params(best_params, day_datasets)
        total_evals = 0
        log(f"  Starting accuracy: {best_accuracy:.1f}%")

    bounds = [
        (3.0, 30.0),    # awake_ha
        (0.1, 3.0),     # awake_mv
        (-5.0, 15.0),   # deep_ha
        (2.0, 20.0),    # deep_iqr
        (0.1, 3.0),     # deep_mv
        (3.0, 25.0),    # rem_iqr
        (20.0, 200.0),  # rem_hrv
        (0.1, 3.0),     # rem_mv
        (0.1, 3.0),     # mv_high
    ]

    round_num = 0
    while True:
        round_num += 1
        seed = int(time.time()) % 100000 + round_num
        log(f"Round {round_num} (seed={seed}, best={best_accuracy:.1f}%)...")

        iteration = [0]

        def objective(params):
            iteration[0] += 1
            return score_params(params, day_datasets)

        # Use current best as one of the initial population members
        result = differential_evolution(
            objective, bounds,
            seed=seed, maxiter=300, popsize=30, tol=0.001,
            mutation=(0.4, 1.6), recombination=0.85,
            x0=best_params,
        )

        total_evals += iteration[0]
        new_accuracy = 100 - result.fun

        if new_accuracy > best_accuracy:
            improvement = new_accuracy - best_accuracy
            best_accuracy = new_accuracy
            best_params = result.x
            save_best(best_params, best_accuracy, round_num, total_evals)
            apply_thresholds(best_params)
            log(f"  NEW BEST: {best_accuracy:.1f}% (+{improvement:.1f}%) "
                f"[{iteration[0]} evals, {total_evals} total]")
        else:
            log(f"  No improvement ({new_accuracy:.1f}% vs {best_accuracy:.1f}%) "
                f"[{iteration[0]} evals, {total_evals} total]")

        # Brief pause between rounds to be nice
        time.sleep(1)


if __name__ == "__main__":
    main()
