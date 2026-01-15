#!/usr/bin/env python3
"""Optimize sleep phase classification thresholds against Whoop ground truth.

Uses Jan 30 data where we have full sleep stage breakdown from Whoop:
  Deep 18% (1:51), Light 47% (4:25), REM 32% (3:13), Awake 3% (0:20)
  Sleep window: 00:22-10:12 local time (23:22-09:12 UTC)
"""

import sys
import json
import math
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.optimize import differential_evolution

from data.db_loader import load_from_db
from algo4_calibrated.engine import compute_sleep_rhr


# Whoop ground truth: all days with both sensor data and sleep stage breakdown
GT_DAYS = {
    "2025-01-10": {"deep": 22.0, "light": 42.0, "rem": 26.0, "awake": 10.0},
    "2025-01-11": {"deep": 25.0, "light": 38.0, "rem": 28.0, "awake": 9.0},
    "2025-01-12": {"deep": 20.0, "light": 45.0, "rem": 30.0, "awake": 5.0},
    "2025-01-13": {"deep": 18.0, "light": 50.0, "rem": 22.0, "awake": 10.0},
    "2025-01-15": {"deep": 23.0, "light": 48.0, "rem": 21.0, "awake": 8.0},
    "2025-01-16": {"deep": 19.0, "light": 44.0, "rem": 25.0, "awake": 12.0},
    "2025-01-17": {"deep": 24.0, "light": 46.0, "rem": 23.0, "awake": 7.0},
    "2025-01-18": {"deep": 21.0, "light": 49.0, "rem": 20.0, "awake": 10.0},
}


def get_sleep_trimmed(df, day_date):
    """Get sleep data for a specific day, trimmed to likely sleep hours (00:00-11:00)."""
    mask = df["date"] == day_date
    day_df = df[mask]
    sleep_mask = day_df["datetime_local"].apply(
        lambda x: 0 <= x.hour < 11 if hasattr(x, "hour") else False
    )
    return day_df[sleep_mask]


def classify_with_params(sleep_df, rhr, params):
    """Classify sleep phases with tunable parameters.

    params = [
        awake_ha_thresh,      # 0: HR above RHR threshold for awake
        awake_mv_thresh,      # 1: movement threshold for awake
        deep_ha_upper,        # 2: max HR-above-RHR for deep
        deep_iqr_upper,       # 3: max IQR for deep
        deep_mv_upper,        # 4: max movement for deep
        rem_iqr_lower,        # 5: min IQR for REM
        rem_hrv_lower,        # 6: min HRV for REM
        rem_mv_upper,         # 7: max movement for REM
        mv_high_thresh,       # 8: movement threshold for "is_moving"
    ]
    """
    (
        awake_ha,
        awake_mv,
        deep_ha,
        deep_iqr,
        deep_mv,
        rem_iqr,
        rem_hrv,
        rem_mv,
        mv_high,
    ) = params

    window = 600
    phases = []

    for i in range(0, len(sleep_df) - window, window):
        chunk = sleep_df.iloc[i : i + window]
        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            phases.append("unknown")
            continue

        avg_hr = float(np.median(hr_v))
        hr_iqr = (
            float(np.percentile(hr_v, 75) - np.percentile(hr_v, 25))
            if len(hr_v) > 10
            else float(hr_v.std())
            if len(hr_v) > 1
            else 0
        )
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
            phase = "awake"
        elif ha <= deep_ha and hr_iqr < deep_iqr and avg_mv < deep_mv:
            phase = "deep"
        elif hr_iqr > rem_iqr and avg_mv < rem_mv:
            phase = "rem"
        elif local_hrv > rem_hrv and avg_mv < rem_mv:
            phase = "rem"
        else:
            phase = "light"

        phases.append(phase)

    return phases


def score_params(params, day_datasets):
    """Score how well params match Whoop ground truth across multiple days."""
    total_error = 0
    n_days = 0

    for sleep_df, rhr, gt in day_datasets:
        phases = classify_with_params(sleep_df, rhr, params)
        total = len(phases)
        if total == 0:
            continue

        c = Counter(phases)
        known = total - c["unknown"]
        if known == 0:
            continue

        deep_pct = c["deep"] / known * 100
        light_pct = c["light"] / known * 100
        rem_pct = c["rem"] / known * 100
        awake_pct = c["awake"] / known * 100

        error = (
            2.0 * abs(deep_pct - gt["deep"])
            + 1.0 * abs(light_pct - gt["light"])
            + 1.0 * abs(rem_pct - gt["rem"])
            + 1.5 * abs(awake_pct - gt["awake"])
        )
        total_error += error
        n_days += 1

    return total_error / n_days if n_days > 0 else 1000.0


def main():
    print("=" * 70)
    print("  SLEEP PHASE CLASSIFICATION OPTIMIZER")
    print("=" * 70)

    print("\nLoading data...")
    df = load_from_db()

    # Prepare datasets for each GT day
    day_datasets = []
    for date_str, gt in GT_DAYS.items():
        from datetime import date as date_cls

        parts = date_str.split("-")
        day_date = date_cls(int(parts[0]), int(parts[1]), int(parts[2]))
        sleep_df = get_sleep_trimmed(df, day_date)
        if sleep_df.empty or len(sleep_df) < 300:
            print(f"  {date_str}: not enough data, skipping")
            continue
        rhr = compute_sleep_rhr(sleep_df)
        day_datasets.append((sleep_df, rhr, gt))
        print(f"  {date_str}: {len(sleep_df)} samples, RHR={rhr}")

    print(f"\n  Optimizing on {len(day_datasets)} days: {list(GT_DAYS.keys())}")

    current_params = [
        15.0,
        0.5,
        5.0,
        6.0,
        0.5,
        10.0,
        80.0,
        0.5,
        0.5,
    ]

    current_score = score_params(current_params, day_datasets)
    print(f"\n  Current score (before): {current_score:.1f}")
    for sleep_df, rhr, gt in day_datasets:
        phases = classify_with_params(sleep_df, rhr, current_params)
        c = Counter(phases)
        known = len(phases) - c["unknown"]
        if known > 0:
            print(
                f"    Deep:{c['deep'] / known * 100:5.1f}% (t:{gt['deep']}%) Light:{c['light'] / known * 100:5.1f}% (t:{gt['light']}%) REM:{c['rem'] / known * 100:5.1f}% (t:{gt['rem']}%) Awake:{c['awake'] / known * 100:5.1f}% (t:{gt['awake']}%)"
            )

    # Optimization bounds
    bounds = [
        (5.0, 30.0),  # awake_ha
        (0.3, 2.0),  # awake_mv
        (0.0, 10.0),  # deep_ha
        (2.0, 15.0),  # deep_iqr
        (0.2, 2.0),  # deep_mv
        (5.0, 20.0),  # rem_iqr
        (50.0, 150.0),  # rem_hrv
        (0.2, 2.0),  # rem_mv
        (0.3, 2.0),  # mv_high
    ]

    print("\n  Running optimization...")
    iteration = [0]

    def objective(params):
        iteration[0] += 1
        if iteration[0] % 500 == 0:
            print(f"    Iteration {iteration[0]}...")
        return score_params(params, day_datasets)

    result = differential_evolution(
        objective,
        bounds,
        seed=42,
        maxiter=100,
        popsize=30,
        tol=0.01,
        mutation=(0.5, 1.5),
        recombination=0.8,
    )

    opt_params = result.x
    opt_score = result.fun

    print(f"\n  Optimization complete ({iteration[0]} evaluations)")
    print(f"  Score: {current_score:.1f} → {opt_score:.1f}")
    print(f"\n  Optimized classification per day:")
    for sleep_df, rhr, gt in day_datasets:
        phases = classify_with_params(sleep_df, rhr, opt_params)
        c = Counter(phases)
        known = len(phases) - c["unknown"]
        if known > 0:
            print(
                f"    Deep:{c['deep'] / known * 100:5.1f}% (t:{gt['deep']}%) Light:{c['light'] / known * 100:5.1f}% (t:{gt['light']}%) REM:{c['rem'] / known * 100:5.1f}% (t:{gt['rem']}%) Awake:{c['awake'] / known * 100:5.1f}% (t:{gt['awake']}%)"
            )

    print(f"\n  Optimized thresholds:")
    names = [
        "awake_ha",
        "awake_mv",
        "deep_ha",
        "deep_iqr",
        "deep_mv",
        "rem_iqr",
        "rem_hrv",
        "rem_mv",
        "mv_high",
    ]
    for n, v, old in zip(names, opt_params, current_params):
        print(f"    {n:12s}: {old:6.1f} → {v:6.1f}")

    # Apply optimized thresholds to engine.py and analyze_all.py
    # Use regex to find and replace the classification block in both files
    import re

    def replace_classify_block(code, opt):
        """Replace the classification thresholds in a classify function."""
        pattern = r"(is_moving = avg_mv > )[\d.]+( or max_mv > )[\d.]+"
        code = re.sub(pattern, f"\\g<1>{opt[8]:.1f}\\g<2>{opt[8] * 3:.1f}", code)

        pattern = r"(if is_moving and ha > )[\d.]+:"
        code = re.sub(pattern, f"\\g<1>{opt[0]:.1f}:", code)

        pattern = r"(elif ha <= )[\d.]+( and hr_iqr < )[\d.]+( and avg_mv < )[\d.]+:"
        code = re.sub(
            pattern, f"\\g<1>{opt[2]:.1f}\\g<2>{opt[3]:.1f}\\g<3>{opt[4]:.1f}:", code
        )

        pattern = r"(elif hr_iqr > )[\d.]+( and avg_mv < )[\d.]+:"
        code = re.sub(pattern, f"\\g<1>{opt[5]:.1f}\\g<2>{opt[7]:.1f}:", code)

        pattern = r"(elif local_hrv > )[\d.]+( and avg_mv < )[\d.]+:"
        code = re.sub(pattern, f"\\g<1>{opt[6]:.1f}\\g<2>{opt[7]:.1f}:", code)

        return code

    for name, path in [
        ("engine.py", Path(__file__).parent / "algo4_calibrated" / "engine.py"),
        ("analyze_all.py", Path(__file__).parent / "analyze_all.py"),
    ]:
        print(f"\n  Updating {name}...")
        code = path.read_text()
        new_code = replace_classify_block(code, opt_params)
        if new_code != code:
            path.write_text(new_code)
            print(f"  Done!")
        else:
            print(f"  WARNING: no changes made (pattern not matched)")

    return opt_params, opt_score


if __name__ == "__main__":
    params, score = main()

    print("\n  Re-running full analysis with optimized sleep classification...")
    import subprocess

    subprocess.run([sys.executable, str(Path(__file__).parent / "analyze_all.py")])
