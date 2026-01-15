#!/usr/bin/env python3
"""Dedicated optimizer for algo5 ML sleep phase classification.

v3: 2-min windows, per-second GT, sleep architecture post-processing.

TWO-PHASE OPTIMIZATION:
  Phase 1: Grid search over ML hyperparameters (max_depth, lr, max_iter)
           Pre-computes LONO raw predictions for each config
  Phase 2: Fast DE optimization of post-processing
           (awake thresholds + smoothing) on cached predictions

Usage:
    python3 optimize_algo5_phases.py              # Full optimization
    python3 optimize_algo5_phases.py --baseline    # Evaluate current params only
    python3 optimize_algo5_phases.py --phase2only  # Skip ML grid, optimize post-processing only
"""

import sys
import json
import time
import argparse
import itertools
import numpy as np
from pathlib import Path
from datetime import date as date_cls
from collections import Counter

from scipy.optimize import differential_evolution

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data.db_loader import load_from_db
from common.preprocessing import compute_rhr
from algo5_ml.features import (
    extract_window_features, _parse_deep_dive_sleep_bounds,
    FEATURE_NAMES, PHASE_TO_INT, INT_TO_PHASE, WINDOW_SEC, STRIDE_SEC,
)

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix, classification_report

# ── Constants ──────────────────────────────────────────────────────────────────

LOG_FILE = Path(__file__).parent / "optimize_algo5_log.txt"
BEST_FILE = Path(__file__).parent / "algo5_best_params.json"
PHASE_NAMES = ["awake", "light", "deep", "rem"]

_IDX = {name: i for i, name in enumerate(FEATURE_NAMES)}
IDX_MV_MEAN = _IDX["mv_mean"]
IDX_MV_MAX = _IDX["mv_max"]
IDX_MV_ENERGY = _IDX["mv_energy"]
IDX_HR_ABOVE_RHR = _IDX["hr_above_rhr"]
IDX_HR_STD = _IDX["hr_std"]
IDX_MV_ZCR = _IDX["mv_zcr"]
IDX_HOURS_SINCE = _IDX["hours_since_onset"]

# ── Data Loading ──────────────────────────────────────────────────────────────

_CACHE = {}


def load_data():
    """Load sensor DB + build per-night training/eval windows."""
    if "data" in _CACHE:
        return _CACHE["data"]

    print("Loading sensor DB...")
    df = load_from_db()
    if df.empty:
        print("ERROR: No data!")
        sys.exit(1)

    sensor_dates = sorted(set(
        str(d) for d in df["date"].unique()
        if hasattr(d, "year") and d.year >= 2025
    ))

    nights = []
    for date_str in sensor_dates:
        second_gt, start_ts, end_ts = _parse_deep_dive_sleep_bounds(date_str)
        if second_gt is None or len(second_gt) < 600:
            continue

        parts = date_str.split("-")
        day_date = date_cls(int(parts[0]), int(parts[1]), int(parts[2]))
        day_df = df[df["date"] == day_date]
        mask = (day_df["timestamp"] >= start_ts) & (day_df["timestamp"] <= end_ts)
        sleep_df = day_df[mask]

        if len(sleep_df) < WINDOW_SEC:
            continue

        rhr = compute_rhr(sleep_df)

        train_windows = _extract_windows(sleep_df, rhr, start_ts, end_ts, second_gt, STRIDE_SEC)
        eval_windows = _extract_windows(sleep_df, rhr, start_ts, end_ts, second_gt, WINDOW_SEC)

        # Pre-sleep awake augmentation (1h before sleep)
        pre_mask = (day_df["timestamp"] >= start_ts - 3600) & (day_df["timestamp"] < start_ts)
        pre_df = day_df[pre_mask]
        awake_aug = []
        if len(pre_df) >= WINDOW_SEC:
            prev_feats = None
            history = []
            for i in range(0, len(pre_df) - WINDOW_SEC, WINDOW_SEC):
                chunk = pre_df.iloc[i:i + WINDOW_SEC]
                feats = extract_window_features(
                    chunk, rhr, sleep_start_ts=start_ts, sleep_end_ts=end_ts,
                    prev_features=prev_feats, history=history,
                )
                if feats is not None:
                    vec = [feats.get(name, 0.0) for name in FEATURE_NAMES]
                    awake_aug.append((vec, 0))
                    prev_feats = feats
                    history.append(feats)

        if len(train_windows) < 20:
            continue

        n_awake = sum(1 for _, l in train_windows if l == 0)
        nights.append({
            "date": date_str,
            "train_windows": train_windows,
            "eval_windows": eval_windows,
            "awake_aug": awake_aug,
        })
        print(f"  {date_str}: {len(train_windows)} train, {len(eval_windows)} eval, "
              f"{n_awake} awake, {len(awake_aug)} aug")

    print(f"\nLoaded {len(nights)} nights")
    _CACHE["data"] = nights
    return nights


def _extract_windows(sleep_df, rhr, start_ts, end_ts, second_gt, stride):
    """Extract feature windows with per-second GT labels."""
    windows = []
    prev_feats = None
    history = []
    for i in range(0, len(sleep_df) - WINDOW_SEC, stride):
        chunk = sleep_df.iloc[i:i + WINDOW_SEC]
        feats = extract_window_features(
            chunk, rhr, sleep_start_ts=start_ts, sleep_end_ts=end_ts,
            prev_features=prev_feats, history=history,
        )
        if feats is None:
            prev_feats = feats
            continue

        # Per-second GT majority vote
        win_start_ts = chunk.iloc[0]["timestamp"]
        win_end_ts = chunk.iloc[-1]["timestamp"]
        gt_phases = []
        for s in range(int(win_start_ts), int(win_end_ts) + 1):
            p = second_gt.get(s)
            if p:
                gt_phases.append(p)

        if not gt_phases:
            prev_feats = feats
            history.append(feats)
            continue

        dominant = Counter(gt_phases).most_common(1)[0][0]
        vec = [feats.get(name, 0.0) for name in FEATURE_NAMES]
        windows.append((vec, PHASE_TO_INT[dominant]))
        prev_feats = feats
        history.append(feats)
    return windows


# ── Phase 1: Pre-compute LONO raw predictions ────────────────────────────────

def precompute_lono_predictions(nights, ml_params):
    """Train LONO models and cache raw ML predictions + feature vectors."""
    max_iter, max_depth, lr, msl, l2, mb = ml_params
    max_iter = max(50, int(max_iter))
    max_depth = max(1, int(max_depth))
    lr = max(0.005, float(lr))
    msl = max(1, int(msl))
    l2 = max(0.0001, float(l2))
    mb = max(32, min(255, int(mb)))

    results = []
    for hold_idx in range(len(nights)):
        train_X, train_y = [], []
        for i, n in enumerate(nights):
            if i == hold_idx:
                continue
            for vec, label in n["train_windows"]:
                train_X.append(vec)
                train_y.append(label)
            for vec, label in n["awake_aug"]:
                train_X.append(vec)
                train_y.append(label)

        if len(train_X) < 50:
            results.append(None)
            continue

        train_X = np.array(train_X)
        train_y = np.array(train_y)

        model = HistGradientBoostingClassifier(
            max_iter=max_iter, max_depth=max_depth, learning_rate=lr,
            min_samples_leaf=msl, l2_regularization=l2, max_bins=mb,
            max_features=0.8,
            class_weight="balanced", random_state=42,
        )
        model.fit(train_X, train_y)

        hold = nights[hold_idx]
        eval_X = np.array([w[0] for w in hold["eval_windows"]])
        eval_y = np.array([w[1] for w in hold["eval_windows"]])

        if len(eval_X) == 0:
            results.append(None)
            continue

        raw_preds = model.predict(eval_X).astype(int)
        proba = model.predict_proba(eval_X)

        results.append({
            "date": hold["date"],
            "eval_X": eval_X,
            "eval_y": eval_y,
            "raw_preds": raw_preds,
            "proba": proba,
            "classes": model.classes_,
        })

    return results


# ── Phase 2: Fast post-processing optimization ───────────────────────────────

def apply_postprocessing(lono_results, params):
    """Apply awake rules + smoothing + sleep architecture to cached LONO predictions.

    params: [smooth_kernel, awake_mv_thresh, awake_hr_thresh,
             awake_energy_thresh, awake_zcr_thresh, awake_prob_thresh]
    """
    smooth_k = max(1, int(params[0]))
    if smooth_k % 2 == 0:
        smooth_k += 1
    awake_mv = float(params[1])
    awake_hr = float(params[2])
    awake_energy = float(params[3])
    awake_zcr = float(params[4])
    awake_prob_thresh = float(params[5])

    all_true = []
    all_pred = []
    night_details = []

    for r in lono_results:
        if r is None:
            continue

        eval_X = r["eval_X"]
        eval_y = r["eval_y"]
        raw_preds = r["raw_preds"].copy()
        proba = r["proba"]
        classes = r["classes"]

        awake_cls_idx = None
        for ci, c in enumerate(classes):
            if c == 0:
                awake_cls_idx = ci
                break

        # Apply hybrid awake detection
        preds = []
        for i in range(len(eval_X)):
            mv_mean = eval_X[i, IDX_MV_MEAN]
            hr_above = eval_X[i, IDX_HR_ABOVE_RHR]
            mv_energy = eval_X[i, IDX_MV_ENERGY]
            zcr = eval_X[i, IDX_MV_ZCR]

            rule_mv_hr = mv_mean > awake_mv and hr_above > awake_hr
            rule_energy = mv_energy > awake_energy
            rule_zcr = zcr > awake_zcr and rule_mv_hr
            rule_ml_awake = False
            if awake_cls_idx is not None:
                rule_ml_awake = proba[i, awake_cls_idx] > awake_prob_thresh

            if rule_mv_hr or rule_energy or rule_zcr or rule_ml_awake:
                preds.append(0)
            else:
                preds.append(int(raw_preds[i]))

        # Smoothing
        preds = _smooth(preds, smooth_k)

        # Sleep architecture constraints
        hours_list = eval_X[:, IDX_HOURS_SINCE].tolist()
        preds = _apply_architecture(preds, hours_list)

        correct = sum(1 for p, t in zip(preds, eval_y) if p == t)
        acc = correct / len(eval_y) * 100

        all_true.extend(eval_y.tolist())
        all_pred.extend(preds)

        phase_acc = {}
        for pi, pn in INT_TO_PHASE.items():
            m = eval_y == pi
            if m.sum() > 0:
                pc = sum(1 for p, t in zip(preds, eval_y) if t == pi and p == t)
                phase_acc[pn] = round(pc / m.sum() * 100, 1)

        night_details.append({
            "date": r["date"], "accuracy": round(acc, 1),
            "n_windows": len(eval_y), "phase_acc": phase_acc,
        })

    if not all_true:
        return 0.0, []
    overall = sum(1 for p, t in zip(all_pred, all_true) if p == t) / len(all_true) * 100
    return overall, night_details


def _smooth(preds, kernel_size):
    if kernel_size <= 1 or len(preds) < kernel_size:
        return preds
    smoothed = preds.copy()
    half = kernel_size // 2
    for i in range(half, len(preds) - half):
        window = preds[i - half:i + half + 1]
        counts = Counter(window)
        smoothed[i] = counts.most_common(1)[0][0]
    return smoothed


def _apply_architecture(preds, hours_list):
    """Sleep architecture post-processing."""
    if len(preds) < 3:
        return preds

    result = list(preds)

    # Suppress REM in first 60 min
    for i in range(len(result)):
        if result[i] == 3 and i < len(hours_list) and hours_list[i] < 1.0:
            result[i] = 1  # light

    # Isolated single-window phases -> match neighbors
    for i in range(1, len(result) - 1):
        if result[i] != result[i - 1] and result[i] != result[i + 1]:
            if result[i - 1] == result[i + 1]:
                result[i] = result[i - 1]

    # Short awake bursts (<=2 windows) surrounded by sleep -> light
    i = 0
    while i < len(result):
        if result[i] == 0:  # awake
            j = i
            while j < len(result) and result[j] == 0:
                j += 1
            if j - i <= 2:
                before = (i > 0 and result[i - 1] != 0)
                after = (j < len(result) and result[j] != 0)
                if before and after:
                    for k in range(i, j):
                        result[k] = 1  # light
            i = j
        else:
            i += 1

    return result


# ── Reporting ─────────────────────────────────────────────────────────────────

def report(lono_results, pp_params, title=""):
    """Detailed report for a set of post-processing params."""
    acc, details = apply_postprocessing(lono_results, pp_params)

    print(f"\n{'='*70}")
    print(f"  {title or 'REPORT'}  —  {acc:.2f}% overall")
    print(f"{'='*70}")

    names = ["smooth_k", "awake_mv", "awake_hr", "awake_energy",
             "awake_zcr", "awake_prob"]
    print("  Post-proc params:", ", ".join(
        f"{n}={v:.3f}" for n, v in zip(names, pp_params)))

    all_true_arr, all_pred_arr = _get_all_preds(lono_results, pp_params)

    if all_true_arr:
        print(f"\n  Confusion Matrix:")
        cm = confusion_matrix(all_true_arr, all_pred_arr, labels=[0, 1, 2, 3])
        header = "           " + "  ".join(f"{n:>7}" for n in PHASE_NAMES)
        print(f"  {header}")
        for i, row in enumerate(cm):
            vals = "  ".join(f"{v:>7}" for v in row)
            total = sum(row)
            pct = row[i] / total * 100 if total > 0 else 0
            print(f"  {PHASE_NAMES[i]:>9}  {vals}  ({pct:.1f}%)")

        print(f"\n  Classification Report:")
        rpt = classification_report(
            all_true_arr, all_pred_arr, labels=[0, 1, 2, 3],
            target_names=PHASE_NAMES, digits=3, zero_division=0,
        )
        for line in rpt.split("\n"):
            print(f"  {line}")

    print(f"\n  Per-Night:")
    for d in sorted(details, key=lambda x: x["accuracy"]):
        pa = " ".join(f"{k}={v}%" for k, v in sorted(d["phase_acc"].items()))
        print(f"    {d['date']}: {d['accuracy']:5.1f}% ({d['n_windows']:3d} win) [{pa}]")

    if details:
        accs = [d["accuracy"] for d in details]
        print(f"\n  Mean: {np.mean(accs):.1f}% +/- {np.std(accs):.1f}%  "
              f"|  Best: {max(accs):.1f}%  |  Worst: {min(accs):.1f}%")

    return acc


def _get_all_preds(lono_results, pp_params):
    """Get all true/pred for confusion matrix."""
    smooth_k = max(1, int(pp_params[0]))
    if smooth_k % 2 == 0:
        smooth_k += 1
    awake_mv = float(pp_params[1])
    awake_hr = float(pp_params[2])
    awake_energy = float(pp_params[3])
    awake_zcr = float(pp_params[4])
    awake_prob = float(pp_params[5])

    all_true, all_pred = [], []
    for r in lono_results:
        if r is None:
            continue
        eval_X = r["eval_X"]
        eval_y = r["eval_y"]
        raw_preds = r["raw_preds"].copy()
        proba = r["proba"]
        classes = r["classes"]

        awake_ci = None
        for ci, c in enumerate(classes):
            if c == 0:
                awake_ci = ci
                break

        preds = []
        for i in range(len(eval_X)):
            mv = eval_X[i, IDX_MV_MEAN]
            hr = eval_X[i, IDX_HR_ABOVE_RHR]
            en = eval_X[i, IDX_MV_ENERGY]
            zc = eval_X[i, IDX_MV_ZCR]

            rule1 = mv > awake_mv and hr > awake_hr
            rule2 = en > awake_energy
            rule3 = zc > awake_zcr and rule1
            rule4 = awake_ci is not None and proba[i, awake_ci] > awake_prob

            if rule1 or rule2 or rule3 or rule4:
                preds.append(0)
            else:
                preds.append(int(raw_preds[i]))

        preds = _smooth(preds, smooth_k)
        hours_list = eval_X[:, IDX_HOURS_SINCE].tolist()
        preds = _apply_architecture(preds, hours_list)

        all_true.extend(eval_y.tolist())
        all_pred.extend(preds)

    return all_true, all_pred


# ── Main ──────────────────────────────────────────────────────────────────────

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--phase2only", action="store_true", help="Skip ML grid search")
    parser.add_argument("--maxiter", type=int, default=200, help="DE iterations for Phase 2")
    args = parser.parse_args()

    nights = load_data()
    if len(nights) < 3:
        print("Need >= 3 nights!")
        sys.exit(1)

    total_train = sum(len(n["train_windows"]) for n in nights)
    total_eval = sum(len(n["eval_windows"]) for n in nights)
    total_aug = sum(len(n["awake_aug"]) for n in nights)
    all_labels = [l for n in nights for _, l in n["train_windows"]]
    counts = Counter(all_labels)
    print(f"\n{total_train} train + {total_aug} aug, {total_eval} eval across {len(nights)} nights")
    print(f"Distribution: {', '.join(f'{INT_TO_PHASE[k]}={v}' for k, v in sorted(counts.items()))}")

    # Default ML + post-processing params
    default_ml = (500, 5, 0.05, 10, 0.01, 128)  # updated for v3
    default_pp = [3, 0.15, 8.0, 5.0, 0.3, 0.3]

    if args.baseline:
        print("\nPre-computing LONO predictions (baseline ML)...")
        t0 = time.time()
        lono = precompute_lono_predictions(nights, default_ml)
        print(f"  Done in {time.time()-t0:.1f}s")
        report(lono, default_pp, "BASELINE")
        return

    # ═══ Phase 1: ML Hyperparameter Grid Search ═══════════════════════════════

    best_ml = default_ml
    best_acc = 0.0
    best_pp = default_pp.copy()

    if not args.phase2only:
        print("\n" + "=" * 70)
        print("  PHASE 1: ML Hyperparameter Grid Search")
        print("=" * 70)

        ml_grid = {
            "max_iter": [300, 500, 800],
            "max_depth": [3, 4, 5, 6],
            "lr": [0.02, 0.05, 0.1],
            "msl": [5, 10, 15],
            "l2": [0.005, 0.01, 0.05],
            "bins": [128],
        }

        configs = list(itertools.product(
            ml_grid["max_iter"], ml_grid["max_depth"], ml_grid["lr"],
            ml_grid["msl"], ml_grid["l2"], ml_grid["bins"],
        ))
        print(f"\n  {len(configs)} ML configurations to evaluate")

        for ci, cfg in enumerate(configs):
            t0 = time.time()
            lono = precompute_lono_predictions(nights, cfg)
            acc, details = apply_postprocessing(lono, default_pp)
            dt = time.time() - t0

            if acc > best_acc:
                best_acc = acc
                best_ml = cfg
                log(f"  [{ci+1}/{len(configs)}] {acc:.2f}% *** NEW BEST *** "
                    f"depth={cfg[1]} lr={cfg[2]} msl={cfg[3]} l2={cfg[4]} "
                    f"iter={cfg[0]} ({dt:.1f}s)")
            elif ci % 20 == 0:
                log(f"  [{ci+1}/{len(configs)}] {acc:.2f}%  "
                    f"depth={cfg[1]} lr={cfg[2]} iter={cfg[0]} ({dt:.1f}s)")

        log(f"\n  Phase 1 best: {best_acc:.2f}% with {best_ml}")

    # ═══ Phase 2: Post-Processing Optimization (DE) ═══════════════════════════

    print(f"\n{'='*70}")
    print(f"  PHASE 2: Post-Processing Optimization (DE)")
    print(f"{'='*70}")

    log(f"\n  Pre-computing LONO predictions with best ML params: {best_ml}")
    t0 = time.time()
    lono = precompute_lono_predictions(nights, best_ml)
    log(f"  Done in {time.time()-t0:.1f}s")

    valid = sum(1 for r in lono if r is not None)
    log(f"  {valid}/{len(nights)} nights have predictions")

    base_acc, _ = apply_postprocessing(lono, default_pp)
    log(f"  Baseline post-proc: {base_acc:.2f}%")

    if base_acc > best_acc:
        best_acc = base_acc

    eval_count = [0]
    start_time = time.time()

    def pp_objective(params):
        acc, _ = apply_postprocessing(lono, params)
        return 100.0 - acc

    def pp_callback(xk, convergence=None):
        nonlocal best_acc, best_pp
        eval_count[0] += 1
        acc = 100.0 - pp_objective(xk)
        if acc > best_acc:
            best_acc = acc
            best_pp = xk.tolist()
            log(f"  eval #{eval_count[0]}: {acc:.2f}% *** NEW BEST ***")
            json.dump(
                {"accuracy": best_acc, "ml_params": list(best_ml),
                 "pp_params": best_pp,
                 "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")},
                open(BEST_FILE, "w"), indent=2,
            )
        elif eval_count[0] % 50 == 0:
            elapsed = time.time() - start_time
            log(f"  eval #{eval_count[0]}: {acc:.2f}% (best={best_acc:.2f}%, {elapsed:.0f}s)")

    pp_bounds = [
        (1, 9),          # smooth_kernel
        (0.01, 1.0),     # awake_mv_thresh
        (1.0, 30.0),     # awake_hr_thresh
        (0.5, 50.0),     # awake_energy_thresh
        (0.05, 0.8),     # awake_zcr_thresh
        (0.1, 0.9),      # awake_prob_thresh
    ]

    log(f"\n  Starting DE optimization (maxiter={args.maxiter})...")

    result = differential_evolution(
        pp_objective, pp_bounds,
        seed=42,
        maxiter=args.maxiter,
        popsize=15,
        tol=0.0005,
        mutation=(0.5, 1.5),
        recombination=0.8,
        callback=pp_callback,
        x0=best_pp,
    )

    final_acc = 100.0 - result.fun
    if final_acc > best_acc:
        best_acc = final_acc
        best_pp = result.x.tolist()

    elapsed = time.time() - start_time
    log(f"\n  Phase 2 done: {best_acc:.2f}% ({eval_count[0]} evals, {elapsed:.0f}s)")

    json.dump(
        {"accuracy": best_acc, "ml_params": list(best_ml),
         "pp_params": best_pp,
         "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")},
        open(BEST_FILE, "w"), indent=2,
    )

    # ═══ Final Report ═════════════════════════════════════════════════════════

    report(lono, best_pp, "FINAL BEST")

    ml = best_ml
    pp = best_pp
    print(f"\n{'─'*60}")
    print(f"  UPDATE engine.py:")
    print(f"{'─'*60}")
    print(f"# train_phase_model():")
    print(f"model = HistGradientBoostingClassifier(")
    print(f"    max_iter={int(ml[0])},")
    print(f"    max_depth={int(ml[1])},")
    print(f"    learning_rate={ml[2]:.6f},")
    print(f"    min_samples_leaf={int(ml[3])},")
    print(f"    l2_regularization={ml[4]:.6f},")
    print(f"    max_bins={int(ml[5])},")
    print(f"    class_weight='balanced', random_state=42,")
    print(f")")
    print(f"\n# Hybrid awake thresholds:")
    print(f"AWAKE_MV_THRESH = {pp[1]:.4f}")
    print(f"AWAKE_HR_THRESH = {pp[2]:.4f}")
    print(f"AWAKE_ENERGY_THRESH = {pp[3]:.4f}")
    print(f"AWAKE_ZCR_THRESH = {pp[4]:.4f}")
    print(f"AWAKE_PROB_THRESH = {pp[5]:.4f}")


if __name__ == "__main__":
    main()
