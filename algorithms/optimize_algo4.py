#!/usr/bin/env python3
"""Self-improving optimization loop for Algorithm 4 (Whoop-Calibrated).

Iteratively tunes parameters in the recovery, strain, and sleep formulas
to minimize MAE against Whoop ground truth across all available days.
"""

import sys
import math
import json
from pathlib import Path
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from scipy.optimize import differential_evolution

from data.db_loader import load_from_db
from data.loader import load_ground_truth
from common.preprocessing import compute_hrv_rmssd
from analyze_all import get_sleep_window
from algo4_calibrated.engine import (
    compute_sleep_rhr,
    compute_sws_hrv,
    compute_whoop_strain,
    classify_sleep_phases,
    compute_respiratory_rate,
    WHOOP_ZONES,
)
import pandas as pd


def load_data():
    """Load sensor data and ground truth."""
    df = load_from_db()
    gt_df = load_ground_truth()
    if "2025-01-15" not in gt_df["date"].values:
        gt_df = pd.concat(
            [
                gt_df,
                pd.DataFrame(
                    [
                        {
                            "date": "2025-01-15",
                            "recovery_score": 55,
                            "sleep_score": 75,
                            "strain_score": 8.2,
                            "hrv_ms": 65,
                            "rhr_bpm": 58,
                            "resp_rate": 14.5,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    # Load Whoop official
    official_file = Path(__file__).parent / "data" / "raw" / "whoop_official.json"
    whoop_official = json.load(official_file.open()) if official_file.exists() else {}

    return df, gt_df, whoop_official


def prepare_days(df):
    """Pre-compute per-day sleep data and baselines."""
    days = sorted(df["date"].unique())
    daily_hrvs, daily_rhrs = [], []
    day_data = {}

    for day in days:
        sw = get_sleep_window(df, day)
        day_df = df[df["date"] == day]
        if sw.empty or len(sw) < 300:
            continue
        hrv = compute_hrv_rmssd(sw, method="sws")
        if hrv > 10:
            daily_hrvs.append(hrv)
        sh = sw["hr"][sw["hr"] > 30].values
        if len(sh) > 100:
            daily_rhrs.append((float(np.percentile(sh, 25)) + float(np.median(sh))) / 2)

        rhr = compute_sleep_rhr(sw)
        sws_hrv = compute_sws_hrv(sw)
        resp = compute_respiratory_rate(sw) if len(sw) > 60 else 14.0
        phases, sleep_sum = classify_sleep_phases(sw, rhr)

        day_data[str(day)] = {
            "day_df": day_df,
            "sleep_df": sw,
            "rhr": rhr,
            "hrv": sws_hrv,
            "resp": resp,
            "phases": phases,
            "sleep_sum": sleep_sum,
            "n_samples": len(day_df),
        }

    hrv_base = float(np.median(daily_hrvs)) if daily_hrvs else 90
    rhr_base = float(np.median(daily_rhrs)) if daily_rhrs else 55
    return day_data, hrv_base, rhr_base


def compute_scores(params, day_data, gt_rows, hrv_base, rhr_base):
    """Compute Algo4 scores with given parameters, return errors vs GT.

    params = [
        rec_hrv_weight, rec_rhr_weight, rec_sleep_weight, rec_resp_weight,  # 0-3
        rec_sigmoid_slope, rec_sigmoid_center,                                # 4-5
        rec_rhr_scale,                                                        # 6
        strain_k, strain_c,                                                   # 7-8
        sleep_hours_w, sleep_consistency_w, sleep_eff_w, sleep_stress_w,     # 9-12
        sleep_consistency_default,                                            # 13
    ]
    """
    (
        rec_hrv_w,
        rec_rhr_w,
        rec_sleep_w,
        rec_resp_w,
        rec_sig_slope,
        rec_sig_center,
        rec_rhr_scale,
        strain_k,
        strain_c,
        sl_hours_w,
        sl_cons_w,
        sl_eff_w,
        sl_stress_w,
        sl_cons_default,
    ) = params

    # Normalize recovery weights
    rw_total = rec_hrv_w + rec_rhr_w + rec_sleep_w + rec_resp_w
    if rw_total <= 0:
        return 1000.0
    rec_hrv_w /= rw_total
    rec_rhr_w /= rw_total
    rec_sleep_w /= rw_total
    rec_resp_w /= rw_total

    # Normalize sleep weights
    sw_total = sl_hours_w + sl_cons_w + sl_eff_w + sl_stress_w
    if sw_total <= 0:
        return 1000.0
    sl_hours_w /= sw_total
    sl_cons_w /= sw_total
    sl_eff_w /= sw_total
    sl_stress_w /= sw_total

    errors = []

    for _, gt_row in gt_rows.iterrows():
        date = str(gt_row["date"])
        dd = day_data.get(date)
        if dd is None:
            continue

        gt_rec = gt_row.get("recovery_score")
        gt_sleep = gt_row.get("sleep_score")
        gt_strain = gt_row.get("strain_score", gt_row.get("cycle_strain"))

        hrv = dd["hrv"]
        rhr = dd["rhr"]
        resp = dd["resp"]
        sleep_sum = dd["sleep_sum"]

        # Sleep score
        total_min = sleep_sum.get("sleep_min", 0)
        efficiency = sleep_sum.get("efficiency", 0)
        awake_pct = sleep_sum.get("awake_pct", 0)
        hours_score = min(100, (total_min / 480) * 100)
        eff_score = min(100, efficiency)
        consistency_score = sl_cons_default
        stress_score = max(0, 100 - awake_pct * 5)
        sleep_score = (
            sl_hours_w * hours_score
            + sl_cons_w * consistency_score
            + sl_eff_w * eff_score
            + sl_stress_w * stress_score
        )
        if math.isnan(sleep_score) or math.isinf(sleep_score):
            return 1000.0
        sleep_score = max(0, min(100, round(sleep_score)))

        # Strain
        hr = dd["day_df"]["hr"].values
        valid_hr = hr[hr > 30]
        if len(valid_hr) > 0:
            coverage = len(valid_hr) / dd["n_samples"]
            scale = min(3.0, 1.0 / coverage) if coverage > 0.1 else 1.0
            zone_weights = [0, 1.0, 2.5, 5.0, 10.0, 20.0]
            load = 0
            for zi, (lo, hi) in enumerate(WHOOP_ZONES):
                minutes_in_zone = np.sum((valid_hr >= lo) & (valid_hr <= hi)) / 60.0
                load += minutes_in_zone * zone_weights[zi]
            load *= scale
            strain = strain_k * math.log(1 + load / strain_c) if load > 0 else 0
            if math.isnan(strain) or math.isinf(strain):
                return 1000.0
            strain = min(21.0, round(strain, 1))
        else:
            strain = 0

        # Recovery
        if hrv_base > 0:
            hrv_ratio = hrv / hrv_base
            # Guard against exp overflow
            exp_arg = -rec_sig_slope * (hrv_ratio - rec_sig_center)
            exp_arg = max(-500, min(500, exp_arg))
            hrv_score = 100 / (1 + math.exp(exp_arg))
        else:
            hrv_score = 50

        if rhr_base > 0:
            rhr_diff = rhr_base - rhr
            rhr_score = max(0, min(100, 50 + rhr_diff * rec_rhr_scale))
        else:
            rhr_score = 50

        resp_penalty = max(0, (resp - 16) * 3) if resp > 16 else 0
        sleep_contrib = min(100, sleep_score)
        recovery = (
            rec_hrv_w * hrv_score
            + rec_rhr_w * rhr_score
            + rec_sleep_w * sleep_contrib
            + rec_resp_w * (100 - resp_penalty)
        )
        if math.isnan(recovery) or math.isinf(recovery):
            return 1000.0
        recovery = max(0, min(100, round(recovery)))

        # Accumulate errors (guard against NaN/None/"--" in ground truth)
        def _valid_gt(v):
            if v is None:
                return False
            if isinstance(v, str):
                return v not in ("--", "")
            try:
                return not (math.isnan(float(v)) or math.isinf(float(v)))
            except (ValueError, TypeError):
                return False

        if _valid_gt(gt_rec):
            errors.append(abs(float(recovery) - float(gt_rec)))
        if _valid_gt(gt_sleep):
            errors.append(abs(float(sleep_score) - float(gt_sleep)))
        if _valid_gt(gt_strain):
            errors.append(abs(float(strain) - float(gt_strain)))

    return float(np.mean(errors)) if errors else 1000.0


def main():
    print("=" * 70)
    print("  ALGO 4 SELF-IMPROVING OPTIMIZER")
    print("=" * 70)

    print("\nLoading data...")
    df, gt_df, whoop_official = load_data()

    # Merge whoop_official into gt_df
    for date_str, wo in whoop_official.items():
        if wo.get("recovery") and wo["recovery"] != "--":
            mask = gt_df["date"] == date_str
            if mask.any():
                gt_df.loc[mask, "recovery_score"] = float(wo["recovery"])
            else:
                gt_df = pd.concat(
                    [
                        gt_df,
                        pd.DataFrame(
                            [
                                {
                                    "date": date_str,
                                    "recovery_score": float(wo["recovery"])
                                    if wo.get("recovery") and wo["recovery"] != "--"
                                    else None,
                                    "sleep_score": float(wo["sleep_score"])
                                    if wo.get("sleep_score")
                                    else None,
                                    "strain_score": float(wo["strain"])
                                    if wo.get("strain") and wo["strain"] != "--"
                                    else None,
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )

    print("Preparing day data...")
    day_data, hrv_base, rhr_base = prepare_days(df)
    print(f"  Baselines: HRV={hrv_base:.1f}ms, RHR={rhr_base:.1f}bpm")
    print(f"  Days with data: {list(day_data.keys())}")

    gt_rows = gt_df[gt_df["date"].isin(day_data.keys())].drop_duplicates(
        subset=["date"], keep="last"
    )
    print(f"  GT rows matched: {len(gt_rows)}")

    # Current parameters (from last optimization run, applied to engine.py)
    current_params = [
        0.3042,
        0.3764,
        0.2029,
        0.0405,  # recovery weights
        11.25,
        0.904,  # sigmoid slope, center
        9.8,  # rhr_scale
        5.04,
        10.32,  # strain k, c
        0.4892,
        0.0610,
        0.3006,
        0.0631,  # sleep weights
        74.9,  # consistency default
    ]

    # Evaluate current
    current_mae = compute_scores(current_params, day_data, gt_rows, hrv_base, rhr_base)
    print(f"\n  Current MAE: {current_mae:.2f}")

    # Define search bounds (widened to explore beyond previous optimum)
    bounds = [
        (0.1, 0.8),  # rec_hrv_w
        (0.05, 0.6),  # rec_rhr_w
        (0.05, 0.4),  # rec_sleep_w
        (0.0, 0.2),  # rec_resp_w
        (4.0, 20.0),  # sigmoid slope
        (0.7, 1.2),  # sigmoid center
        (2.0, 15.0),  # rhr_scale
        (2.0, 12.0),  # strain_k
        (3.0, 40.0),  # strain_c
        (0.15, 0.6),  # sleep hours_w
        (0.02, 0.3),  # sleep consistency_w
        (0.15, 0.6),  # sleep eff_w
        (0.02, 0.3),  # sleep stress_w
        (40.0, 90.0),  # consistency default
    ]

    print("\n  Running optimization (differential evolution)...")
    print("  This will iterate many times to find the best parameters.\n")

    best_mae = current_mae
    best_params = current_params[:]
    iteration = [0]

    def objective(params):
        mae = compute_scores(params, day_data, gt_rows, hrv_base, rhr_base)
        iteration[0] += 1
        if iteration[0] % 100 == 0:
            print(f"    Iteration {iteration[0]:>5d} | MAE: {mae:.2f}")
        return mae

    result = differential_evolution(
        objective,
        bounds,
        seed=42,
        maxiter=100,
        popsize=25,
        tol=0.001,
        mutation=(0.5, 1.5),
        recombination=0.8,
    )

    opt_mae = result.fun
    opt_params = result.x

    print(f"\n  Optimization complete after {iteration[0]} evaluations")
    print(f"  Before: MAE = {current_mae:.2f}")
    print(f"  After:  MAE = {opt_mae:.2f}")
    print(f"  Improvement: {current_mae - opt_mae:.2f} points")

    # Normalize weights for display
    rw = opt_params[:4]
    rw = rw / rw.sum()
    sw = opt_params[9:13]
    sw = sw / sw.sum()

    print(
        f"\n  Optimized Recovery: HRV={rw[0]:.2f} RHR={rw[1]:.2f} Sleep={rw[2]:.2f} Resp={rw[3]:.2f}"
    )
    print(f"  Sigmoid: slope={opt_params[4]:.1f} center={opt_params[5]:.3f}")
    print(f"  RHR scale: {opt_params[6]:.1f}")
    print(f"  Strain: k={opt_params[7]:.2f} c={opt_params[8]:.1f}")
    print(
        f"  Sleep: hours={sw[0]:.2f} consistency={sw[1]:.2f} efficiency={sw[2]:.2f} stress={sw[3]:.2f}"
    )
    print(f"  Consistency default: {opt_params[13]:.0f}%")

    # Show per-day breakdown with optimized params
    print(f"\n  Per-day results with optimized parameters:")
    print(
        f"  {'Date':>12} │ {'Metric':>8} │ {'WHOOP':>6} │ {'Before':>6} │ {'After':>6} │ {'Δ':>5}"
    )
    print("  " + "─" * 55)

    for _, gt_row in gt_rows.iterrows():
        date = str(gt_row["date"])
        dd = day_data.get(date)
        if dd is None:
            continue
        # We'd need to recompute individually, but for clarity let's just show the MAE
        # For detailed output, let's compute both
        for label, params in [("before", current_params), ("after", list(opt_params))]:
            pass  # Simplified - the per-day detail is in the dashboard

    # Apply optimized params to engine.py using regex for robustness
    if opt_mae < current_mae:
        print("\n  Applying optimized parameters to algo4_calibrated/engine.py...")
        engine_path = Path(__file__).parent / "algo4_calibrated" / "engine.py"
        engine_code = engine_path.read_text()
        import re

        # Update recovery weights (match any float pattern)
        engine_code = re.sub(
            r"recovery = \([\d.]+ \* hrv_score \+ [\d.]+ \* rhr_score \+\n\s+[\d.]+ \* sleep_contrib \+ [\d.]+ \* \(100 - resp_penalty\)\)",
            f"recovery = ({rw[0]:.2f} * hrv_score + {rw[1]:.2f} * rhr_score +\n                {rw[2]:.2f} * sleep_contrib + {rw[3]:.2f} * (100 - resp_penalty))",
            engine_code,
        )

        # Update sigmoid
        engine_code = re.sub(
            r"hrv_score = 100 / \(1 \+ math\.exp\(-[\d.]+ \* \(hrv_ratio - [\d.]+\)\)\)",
            f"hrv_score = 100 / (1 + math.exp(-{opt_params[4]:.1f} * (hrv_ratio - {opt_params[5]:.3f})))",
            engine_code,
        )

        # Update RHR scale
        engine_code = re.sub(
            r"rhr_score = max\(0, min\(100, 50 \+ rhr_diff \* [\d.]+\)\)",
            f"rhr_score = max(0, min(100, 50 + rhr_diff * {opt_params[6]:.1f}))",
            engine_code,
        )

        # Update strain
        engine_code = re.sub(
            r"strain = [\d.]+ \* math\.log\(1 \+ load / [\d.]+\)",
            f"strain = {opt_params[7]:.2f} * math.log(1 + load / {opt_params[8]:.1f})",
            engine_code,
        )

        # Update sleep weights
        engine_code = re.sub(
            r"score = \([\d.]+ \* hours_score \+ [\d.]+ \* consistency_score \+\n\s+[\d.]+ \* eff_score \+ [\d.]+ \* stress_score\)",
            f"score = ({sw[0]:.2f} * hours_score + {sw[1]:.2f} * consistency_score +\n             {sw[2]:.2f} * eff_score + {sw[3]:.2f} * stress_score)",
            engine_code,
        )

        # Update consistency default
        engine_code = re.sub(
            r"consistency_score = [\d.]+",
            f"consistency_score = {opt_params[13]:.1f}",
            engine_code,
        )

        engine_path.write_text(engine_code)
        print("  Parameters updated in engine.py!")
    else:
        print("\n  No improvement found. Keeping current parameters.")

    # Save optimization history
    history = {
        "before_mae": round(current_mae, 2),
        "after_mae": round(opt_mae, 2),
        "improvement": round(current_mae - opt_mae, 2),
        "iterations": iteration[0],
        "params_before": current_params,
        "params_after": list(opt_params),
        "param_names": [
            "rec_hrv_w",
            "rec_rhr_w",
            "rec_sleep_w",
            "rec_resp_w",
            "sigmoid_slope",
            "sigmoid_center",
            "rhr_scale",
            "strain_k",
            "strain_c",
            "sleep_hours_w",
            "sleep_consistency_w",
            "sleep_eff_w",
            "sleep_stress_w",
            "consistency_default",
        ],
    }
    hist_path = Path(__file__).parent / "optimization_history.json"
    with open(hist_path, "w") as f:
        json.dump(history, f, indent=2)
    print(f"\n  History saved to: {hist_path}")

    return opt_mae < current_mae


if __name__ == "__main__":
    improved = main()
    if improved:
        print("\n  Now re-running analyze_all.py with optimized parameters...")
        import subprocess

        subprocess.run([sys.executable, str(Path(__file__).parent / "analyze_all.py")])
