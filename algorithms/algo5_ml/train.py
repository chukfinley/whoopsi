#!/usr/bin/env python3
"""Train algo5_ml sleep phase classifier with Leave-One-Night-Out CV.

v3: 2-min windows, 60 features, per-second GT, sleep architecture post-processing,
    deeper trees, no double class weighting.

Usage:
    cd algorithms
    .venv/bin/python3 algo5_ml/train.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from collections import Counter

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix, classification_report

from data.db_loader import load_from_db
from data.loader import load_ground_truth
from algo5_ml.features import (
    build_training_data,
    extract_deep_dive_metrics,
    FEATURE_NAMES,
    INT_TO_PHASE,
    PHASE_TO_INT,
    WINDOW_SEC,
)
from algo5_ml.engine import MLScoringEngine


def train_and_evaluate():
    print("=" * 60)
    print("  algo5_ml v3 — ML Sleep Phase Classifier Training")
    print(
        f"  HistGradientBoosting + {len(FEATURE_NAMES)} features + {WINDOW_SEC}s windows"
    )
    print("=" * 60)

    print("\nLoading sensor data...")
    df = load_from_db()
    if df.empty:
        print("No sensor data!")
        return

    print(f"\nBuilding training data ({WINDOW_SEC}s windows, 50% overlap)...")
    X, y, night_ids, night_dates = build_training_data(df, overlap=True)

    if len(X) == 0:
        print("No training data! Check deep_dive directory for GT.")
        return

    n_nights = len(night_dates)
    print(f"  {len(X)} windows from {n_nights} nights")
    print(f"  Nights: {', '.join(night_dates)}")
    print(f"  Features: {len(FEATURE_NAMES)}")
    print(f"  Class distribution:")
    for cls in sorted(Counter(y).keys()):
        count = Counter(y)[cls]
        pct = count / len(y) * 100
        print(f"    {INT_TO_PHASE[cls]:>6}: {count:>4} ({pct:.1f}%)")

    # --- Leave-One-Night-Out Cross-Validation ---
    print(f"\nLeave-One-Night-Out CV ({n_nights} folds)...")
    all_preds = np.zeros_like(y)
    per_night_accuracy = []

    for fold_night in range(n_nights):
        test_mask = night_ids == fold_night
        train_mask = ~test_mask

        X_train, y_train = X[train_mask], y[train_mask]
        X_test, y_test = X[test_mask], y[test_mask]

        if len(X_train) < 10 or len(X_test) < 3:
            continue

        model = HistGradientBoostingClassifier(
            max_iter=500,
            max_depth=4,
            learning_rate=0.1,
            min_samples_leaf=5,
            l2_regularization=0.05,
            max_bins=128,
            max_features=0.8,
            class_weight="balanced",
            random_state=42,
        )
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        all_preds[test_mask] = preds

        night_acc = np.mean(preds == y_test) * 100
        per_night_accuracy.append((night_dates[fold_night], night_acc, len(y_test)))

        phase_detail = []
        for cls in sorted(set(y_test)):
            mask = y_test == cls
            cls_acc = np.mean(preds[mask] == y_test[mask]) * 100
            phase_detail.append(f"{INT_TO_PHASE[cls]}={cls_acc:.0f}%")

        print(
            f"  Night {night_dates[fold_night]}: {night_acc:.1f}% "
            f"({int(np.sum(preds == y_test))}/{len(y_test)}) "
            f"[{', '.join(phase_detail)}]"
        )

    overall_acc = np.mean(all_preds == y) * 100
    print(f"\n  Overall LONO-CV accuracy: {overall_acc:.1f}%")

    if per_night_accuracy:
        accs = [a for _, a, _ in per_night_accuracy]
        print(f"  Night accuracy range: {min(accs):.1f}% - {max(accs):.1f}%")
        print(f"  Night accuracy mean:  {np.mean(accs):.1f}% +/- {np.std(accs):.1f}%")

    # Confusion matrix
    labels = sorted(PHASE_TO_INT.values())
    label_names = [INT_TO_PHASE[l] for l in labels]
    print(f"\nConfusion Matrix (rows=true, cols=predicted):")
    cm = confusion_matrix(y, all_preds, labels=labels)
    header = "         " + "  ".join(f"{n:>6}" for n in label_names)
    print(header)
    for i, row in enumerate(cm):
        total_row = sum(row)
        acc = row[i] / total_row * 100 if total_row > 0 else 0
        print(
            f"  {label_names[i]:>6} "
            + "  ".join(f"{v:>6}" for v in row)
            + f"  ({acc:.0f}%)"
        )

    print(f"\nClassification Report:")
    print(classification_report(y, all_preds, labels=labels, target_names=label_names))

    # Train final model on all data
    print("Training final model on all data...")
    engine = MLScoringEngine()
    engine.train_phase_model(X, y)

    try:
        model = engine.phase_model
        if hasattr(model, "feature_importances_"):
            importances = model.feature_importances_
            top_idx = np.argsort(importances)[-15:][::-1]
            print(f"\nTop 15 feature importances:")
            for idx in top_idx:
                print(f"  {FEATURE_NAMES[idx]:>25}: {importances[idx]:.4f}")
    except Exception:
        pass

    # Train daily score models
    print("\nTraining daily score models...")
    gt_df = load_ground_truth()

    extra_rows = []
    for date_str in night_dates:
        metrics = extract_deep_dive_metrics(date_str)
        if (
            metrics.get("recovery_score")
            and metrics.get("sleep_score")
            and metrics.get("strain_score")
        ):
            extra_rows.append(
                {
                    "date": date_str,
                    "recovery_score": metrics["recovery_score"],
                    "sleep_score": metrics["sleep_score"],
                    "strain_score": metrics["strain_score"],
                    "hrv_ms": metrics.get("hrv_ms"),
                    "rhr_bpm": metrics.get("rhr_bpm"),
                    "resp_rate": metrics.get("resp_rate"),
                }
            )
    if extra_rows:
        import pandas as pd

        extra_df = pd.DataFrame(extra_rows)
        existing_dates = set(gt_df["date"].values)
        new_rows = extra_df[~extra_df["date"].isin(existing_dates)]
        if not new_rows.empty:
            gt_df = pd.concat([gt_df, new_rows], ignore_index=True)
            print(f"  Added {len(new_rows)} extra GT days from deep_dive")

    engine.train_score_models(df, gt_df)

    # Stress + VO2max analysis
    print("\nStress & VO2max analysis:")
    for date_str in night_dates[-3:]:
        import pandas as pd

        day = pd.Timestamp(date_str).date()
        result = engine.full_analysis(df, day)
        print(
            f"  {date_str}: stress_avg={result.get('stress_avg', '?')}, "
            f"stress_high={result.get('stress_high_pct', '?')}%, "
            f"VO2max={result.get('vo2max', '?')}"
        )

    # Save models
    engine.save()
    print(f"\nModels saved to algo5_ml/models/")

    # Print GT summary
    print("\nGT Metrics from deep_dive (all available days):")
    for date_str in night_dates:
        metrics = extract_deep_dive_metrics(date_str)
        if metrics:
            print(
                f"  {date_str}: recovery={metrics.get('recovery_score', '?')}, "
                f"sleep={metrics.get('sleep_score', '?')}, "
                f"strain={metrics.get('strain_score', '?')}, "
                f"stress={metrics.get('stress_pct', '?')}%, "
                f"efficiency={metrics.get('sleep_efficiency', '?')}%, "
                f"consistency={metrics.get('sleep_consistency', '?')}%"
            )

    return overall_acc


if __name__ == "__main__":
    train_and_evaluate()
