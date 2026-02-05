"""Evaluate all algorithms against Whoop ground truth."""

import numpy as np
import pandas as pd
from common.metrics import WhoopScores


def evaluate(results: list[WhoopScores], gt_df: pd.DataFrame, algo_name: str) -> dict:
    """Compare algorithm results against ground truth.

    Returns dict with MAE and Pearson correlation for each metric.
    """
    if not results:
        return {"algo": algo_name, "n": 0}

    pred_df = pd.DataFrame([
        {"date": r.date, "pred_recovery": r.recovery, "pred_sleep": r.sleep,
         "pred_strain": r.strain, "pred_hrv": r.hrv_ms, "pred_rhr": r.rhr_bpm}
        for r in results
    ])

    merged = gt_df.merge(pred_df, on="date", how="inner")
    if merged.empty:
        return {"algo": algo_name, "n": 0}

    metrics = {}
    metrics["algo"] = algo_name
    metrics["n"] = len(merged)

    pairs = [
        ("recovery", "recovery_score", "pred_recovery"),
        ("sleep", "sleep_score", "pred_sleep"),
        ("strain", "strain_score", "pred_strain"),
    ]

    for name, gt_col, pred_col in pairs:
        if gt_col not in merged.columns or pred_col not in merged.columns:
            continue
        gt_vals = merged[gt_col].dropna()
        pred_vals = merged.loc[gt_vals.index, pred_col]

        if len(gt_vals) < 2:
            continue

        mae = float(np.mean(np.abs(gt_vals.values - pred_vals.values)))
        corr = float(np.corrcoef(gt_vals.values, pred_vals.values)[0, 1])
        rmse = float(np.sqrt(np.mean((gt_vals.values - pred_vals.values) ** 2)))

        metrics[f"{name}_mae"] = round(mae, 1)
        metrics[f"{name}_rmse"] = round(rmse, 1)
        metrics[f"{name}_corr"] = round(corr, 3)

    # Also evaluate HRV and RHR if ground truth available
    if "hrv_ms" in merged.columns and "pred_hrv" in merged.columns:
        gt_hrv = merged["hrv_ms"].dropna()
        if len(gt_hrv) >= 2:
            pred_hrv = merged.loc[gt_hrv.index, "pred_hrv"]
            metrics["hrv_mae"] = round(float(np.mean(np.abs(gt_hrv.values - pred_hrv.values))), 1)

    if "rhr_bpm" in merged.columns and "pred_rhr" in merged.columns:
        gt_rhr = merged["rhr_bpm"].dropna()
        if len(gt_rhr) >= 2:
            pred_rhr = merged.loc[gt_rhr.index, "pred_rhr"]
            metrics["rhr_mae"] = round(float(np.mean(np.abs(gt_rhr.values - pred_rhr.values))), 1)

    return metrics


def print_comparison(all_results: dict, gt_df: pd.DataFrame):
    """Print side-by-side comparison of all algorithms vs ground truth."""
    print("\n" + "=" * 90)
    print("  GROUND TRUTH vs PREDICTIONS")
    print("=" * 90)

    for date_str in sorted(gt_df["date"].unique()):
        gt_row = gt_df[gt_df["date"] == date_str].iloc[0]
        print(f"\n  {date_str}")
        print(f"  {'':15} {'Recovery':>10} {'Sleep':>10} {'Strain':>10} {'HRV':>8} {'RHR':>8}")
        print(f"  {'─' * 63}")

        # Ground truth
        rec = gt_row.get("recovery_score", "?")
        slp = gt_row.get("sleep_score", "?")
        str_ = gt_row.get("strain_score", gt_row.get("cycle_strain", "?"))
        hrv = gt_row.get("hrv_ms", "?")
        rhr = gt_row.get("rhr_bpm", "?")
        print(f"  {'WHOOP':15} {rec:>10} {slp:>10} {str_:>10} {hrv:>8} {rhr:>8}")

        # Each algorithm
        for algo_name, results in all_results.items():
            match = [r for r in results if r.date == date_str]
            if match:
                r = match[0]
                print(f"  {algo_name:15} {r.recovery:>10.0f} {r.sleep:>10.0f} "
                      f"{r.strain:>10.1f} {r.hrv_ms:>8.1f} {r.rhr_bpm:>8.1f}")


def print_summary(eval_results: list[dict]):
    """Print summary evaluation table."""
    print("\n" + "=" * 90)
    print("  EVALUATION SUMMARY")
    print("=" * 90)
    print(f"\n  {'Algorithm':20} {'N':>3} │ {'Rec MAE':>8} {'Rec r':>7} │ "
          f"{'Slp MAE':>8} {'Slp r':>7} │ {'Str MAE':>8} {'Str r':>7}")
    print(f"  {'─' * 84}")

    for ev in eval_results:
        name = ev.get("algo", "?")
        n = ev.get("n", 0)
        rec_mae = ev.get("recovery_mae", "-")
        rec_r = ev.get("recovery_corr", "-")
        slp_mae = ev.get("sleep_mae", "-")
        slp_r = ev.get("sleep_corr", "-")
        str_mae = ev.get("strain_mae", "-")
        str_r = ev.get("strain_corr", "-")

        def fmt(v):
            return f"{v:>8.1f}" if isinstance(v, (int, float)) else f"{v:>8}"

        def fmt_r(v):
            return f"{v:>7.3f}" if isinstance(v, (int, float)) else f"{v:>7}"

        print(f"  {name:20} {n:>3} │ {fmt(rec_mae)} {fmt_r(rec_r)} │ "
              f"{fmt(slp_mae)} {fmt_r(slp_r)} │ {fmt(str_mae)} {fmt_r(str_r)}")

    # Also print HRV/RHR accuracy if available
    has_hrv = any("hrv_mae" in ev for ev in eval_results)
    if has_hrv:
        print(f"\n  {'Algorithm':20} {'HRV MAE':>10} {'RHR MAE':>10}")
        print(f"  {'─' * 42}")
        for ev in eval_results:
            name = ev.get("algo", "?")
            hrv = ev.get("hrv_mae", "-")
            rhr = ev.get("rhr_mae", "-")
            print(f"  {name:20} {fmt(hrv)} {fmt(rhr)}")
