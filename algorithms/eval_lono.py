#!/usr/bin/env python3
"""Leave-One-Night-Out cross-validation for sleep phase classification.

Evaluates per-2-minute-block accuracy matching Whoop's ground truth timeline.
Includes Viterbi post-processing with biologically-informed transition probs.

Usage:
    python eval_lono.py                    # Full LONO CV
    python eval_lono.py --quick            # 4-fold CV (faster)
    python eval_lono.py --no-viterbi       # Without Viterbi post-processing
    python eval_lono.py --model-params 500 5 0.05  # Custom max_iter max_depth lr
"""

import sys
import time
import numpy as np
from pathlib import Path
from collections import Counter
from datetime import date as date_cls

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data.db_loader import load_from_db as load_sensor_db
from algo5_ml.features import (
    build_training_data,
    FEATURE_NAMES,
    PHASE_TO_INT,
    INT_TO_PHASE,
    WINDOW_SEC,
)
from algo5_ml.engine import _smooth_isolated
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix


# ── Viterbi post-processing ──────────────────────────────────────────


def viterbi_decode(log_probs, log_trans, log_init):
    """Viterbi algorithm for most likely state sequence.

    Args:
        log_probs: (T, K) log emission probabilities per timestep
        log_trans: (K, K) log transition matrix [from][to]
        log_init: (K,) log initial state probabilities

    Returns:
        path: (T,) optimal state sequence
    """
    T, K = log_probs.shape
    V = np.full((T, K), -np.inf)
    backptr = np.zeros((T, K), dtype=int)

    V[0] = log_init + log_probs[0]

    for t in range(1, T):
        for j in range(K):
            scores = V[t - 1] + log_trans[:, j]
            best_i = np.argmax(scores)
            V[t, j] = scores[best_i] + log_probs[t, j]
            backptr[t, j] = best_i

    # Backtrace
    path = np.zeros(T, dtype=int)
    path[-1] = np.argmax(V[-1])
    for t in range(T - 2, -1, -1):
        path[t] = backptr[t + 1, path[t + 1]]

    return path


def learn_transition_matrix(y, night_ids):
    """Learn transition probabilities from ground truth labels.

    Counts transitions within each night (not across night boundaries).
    Uses Laplace smoothing to avoid zero probabilities.
    """
    K = 4  # number of states
    counts = np.ones((K, K)) * 0.1  # Laplace smoothing

    unique_nights = sorted(set(night_ids))
    for night in unique_nights:
        mask = night_ids == night
        night_labels = y[mask]
        for i in range(len(night_labels) - 1):
            counts[night_labels[i], night_labels[i + 1]] += 1

    # Normalize rows
    trans = counts / counts.sum(axis=1, keepdims=True)
    return trans


def learn_initial_probs(y, night_ids):
    """Learn initial state probabilities from ground truth."""
    K = 4
    counts = np.ones(K) * 0.1
    unique_nights = sorted(set(night_ids))
    for night in unique_nights:
        mask = night_ids == night
        night_labels = y[mask]
        if len(night_labels) > 0:
            counts[night_labels[0]] += 1
    return counts / counts.sum()


def get_transition_matrix():
    """Default biologically-informed transition probabilities (fallback)."""
    trans = np.array(
        [
            [0.80, 0.15, 0.02, 0.03],
            [0.05, 0.82, 0.08, 0.05],
            [0.01, 0.09, 0.89, 0.01],
            [0.04, 0.06, 0.01, 0.89],
        ]
    )
    return trans


def get_initial_probs():
    """Default initial state probabilities."""
    return np.array([0.30, 0.55, 0.10, 0.05])


def apply_viterbi(proba, night_ids_test=None):
    """Apply Viterbi decoding to probability matrix.

    If night_ids_test is provided, applies Viterbi per-night separately.
    Otherwise applies to the entire sequence.
    """
    trans = get_transition_matrix()
    init_probs = get_initial_probs()

    log_trans = np.log(np.clip(trans, 1e-10, 1.0))
    log_init = np.log(np.clip(init_probs, 1e-10, 1.0))

    if night_ids_test is None:
        # Single sequence
        log_probs = np.log(np.clip(proba, 1e-10, 1.0))
        return viterbi_decode(log_probs, log_trans, log_init)

    # Per-night decoding
    result = np.zeros(len(proba), dtype=int)
    unique_nights = sorted(set(night_ids_test))
    for night in unique_nights:
        mask = night_ids_test == night
        indices = np.where(mask)[0]
        night_proba = proba[indices]
        log_probs = np.log(np.clip(night_proba, 1e-10, 1.0))
        path = viterbi_decode(log_probs, log_trans, log_init)
        result[indices] = path

    return result


# ── CV functions ─────────────────────────────────────────────────────


def run_lono_cv(
    X,
    y,
    night_ids,
    night_dates,
    max_iter=300,
    max_depth=3,
    learning_rate=0.1,
    min_samples_leaf=5,
    use_viterbi=True,
    verbose=True,
):
    """Run Leave-One-Night-Out cross-validation."""
    unique_nights = sorted(set(night_ids))
    n_nights = len(unique_nights)

    all_preds = []
    all_preds_no_viterbi = []
    all_true = []
    night_accs = {}
    night_accs_no_viterbi = {}

    t0 = time.time()

    for fold_i, test_night in enumerate(unique_nights):
        train_mask = night_ids != test_night
        test_mask = night_ids == test_night

        X_train, y_train = X[train_mask], y[train_mask]
        X_test, y_test = X[test_mask], y[test_mask]
        train_night_ids = night_ids[train_mask]

        model = HistGradientBoostingClassifier(
            max_iter=max_iter,
            max_depth=max_depth,
            learning_rate=learning_rate,
            min_samples_leaf=min_samples_leaf,
            l2_regularization=0.01,
            max_bins=128,
            max_features=0.8,
            class_weight="balanced",
            random_state=42,
        )
        model.fit(X_train, y_train)

        # Raw predictions
        raw_preds = model.predict(X_test)
        raw_preds = np.array(_smooth_isolated(list(raw_preds)))

        acc_raw = float(np.mean(raw_preds == y_test) * 100)
        night_accs_no_viterbi[night_dates[test_night]] = acc_raw
        all_preds_no_viterbi.extend(raw_preds)

        if use_viterbi:
            # Learn transition matrix from training data
            trans = learn_transition_matrix(y_train, train_night_ids)
            init_probs = learn_initial_probs(y_train, train_night_ids)
            log_trans = np.log(np.clip(trans, 1e-10, 1.0))
            log_init = np.log(np.clip(init_probs, 1e-10, 1.0))

            proba = model.predict_proba(X_test)
            log_probs = np.log(np.clip(proba, 1e-10, 1.0))
            preds = viterbi_decode(log_probs, log_trans, log_init)
        else:
            preds = raw_preds

        acc = float(np.mean(preds == y_test) * 100)
        night_accs[night_dates[test_night]] = acc

        all_preds.extend(preds)
        all_true.extend(y_test)

        if verbose:
            elapsed = time.time() - t0
            vit_str = (
                f" (raw={acc_raw:.1f}%)"
                if use_viterbi and abs(acc - acc_raw) > 0.1
                else ""
            )
            print(
                f"  [{fold_i + 1}/{n_nights}] {night_dates[test_night]}: "
                f"{acc:.1f}%{vit_str} ({len(y_test)} windows) [{elapsed:.1f}s]"
            )

    all_preds = np.array(all_preds)
    all_preds_no_viterbi = np.array(all_preds_no_viterbi)
    all_true = np.array(all_true)
    overall_acc = float(np.mean(all_preds == all_true) * 100)
    overall_acc_raw = float(np.mean(all_preds_no_viterbi == all_true) * 100)

    return night_accs, overall_acc, all_true, all_preds, overall_acc_raw


def run_kfold_cv(
    X,
    y,
    night_ids,
    night_dates,
    k=4,
    max_iter=300,
    max_depth=3,
    learning_rate=0.1,
    min_samples_leaf=5,
    use_viterbi=True,
    verbose=True,
):
    """Run k-fold CV (grouping nights into folds)."""
    unique_nights = sorted(set(night_ids))

    fold_assignment = {}
    for i, night in enumerate(unique_nights):
        fold_assignment[night] = i % k

    all_preds = []
    all_preds_no_viterbi = []
    all_true = []
    fold_accs = {}

    t0 = time.time()

    for fold in range(k):
        test_nights = [n for n, f in fold_assignment.items() if f == fold]
        train_nights = [n for n, f in fold_assignment.items() if f != fold]

        train_mask = np.isin(night_ids, train_nights)
        test_mask = np.isin(night_ids, test_nights)

        X_train, y_train = X[train_mask], y[train_mask]
        X_test, y_test = X[test_mask], y[test_mask]
        test_night_ids = night_ids[test_mask]
        train_night_ids = night_ids[train_mask]

        model = HistGradientBoostingClassifier(
            max_iter=max_iter,
            max_depth=max_depth,
            learning_rate=learning_rate,
            min_samples_leaf=min_samples_leaf,
            l2_regularization=0.01,
            max_bins=128,
            max_features=0.8,
            class_weight="balanced",
            random_state=42,
        )
        model.fit(X_train, y_train)

        raw_preds = model.predict(X_test)
        raw_preds = np.array(_smooth_isolated(list(raw_preds)))
        all_preds_no_viterbi.extend(raw_preds)

        if use_viterbi:
            # Learn transition matrix from training data
            trans = learn_transition_matrix(y_train, train_night_ids)
            init_probs = learn_initial_probs(y_train, train_night_ids)
            log_trans = np.log(np.clip(trans, 1e-10, 1.0))
            log_init = np.log(np.clip(init_probs, 1e-10, 1.0))

            proba = model.predict_proba(X_test)
            # Apply Viterbi per-night
            result = np.zeros(len(proba), dtype=int)
            unique_test_nights = sorted(set(test_night_ids))
            for night in unique_test_nights:
                mask = test_night_ids == night
                indices = np.where(mask)[0]
                night_proba = proba[indices]
                log_probs = np.log(np.clip(night_proba, 1e-10, 1.0))
                path = viterbi_decode(log_probs, log_trans, log_init)
                result[indices] = path
            preds = result
        else:
            preds = raw_preds

        acc = float(np.mean(preds == y_test) * 100)
        test_dates = [night_dates[n] for n in test_nights]
        fold_accs[f"fold{fold + 1} ({', '.join(test_dates)})"] = acc

        all_preds.extend(preds)
        all_true.extend(y_test)

        if verbose:
            acc_raw = float(np.mean(raw_preds == y_test) * 100)
            elapsed = time.time() - t0
            vit_str = f" (raw={acc_raw:.1f}%)" if use_viterbi else ""
            print(
                f"  [Fold {fold + 1}/{k}] {acc:.1f}%{vit_str} "
                f"({len(y_test)} windows, nights: {', '.join(test_dates)}) [{elapsed:.1f}s]"
            )

    all_preds = np.array(all_preds)
    all_preds_no_viterbi = np.array(all_preds_no_viterbi)
    all_true = np.array(all_true)
    overall_acc = float(np.mean(all_preds == all_true) * 100)
    overall_acc_raw = float(np.mean(all_preds_no_viterbi == all_true) * 100)

    return fold_accs, overall_acc, all_true, all_preds, overall_acc_raw


def print_results(overall_acc, all_true, all_preds, night_accs=None, raw_acc=None):
    """Print detailed results."""
    phase_names = ["awake", "light", "deep", "rem"]

    print(f"\n{'=' * 60}")
    if raw_acc is not None and abs(raw_acc - overall_acc) > 0.1:
        print(
            f"OVERALL ACCURACY: {overall_acc:.1f}%  (raw ML: {raw_acc:.1f}%, Viterbi: +{overall_acc - raw_acc:.1f}%)"
        )
    else:
        print(f"OVERALL ACCURACY: {overall_acc:.1f}%")
    print(f"{'=' * 60}")

    if night_accs:
        print(f"\nPer-night accuracy:")
        for date, acc in sorted(night_accs.items()):
            print(f"  {date}: {acc:.1f}%")
        accs = list(night_accs.values())
        print(
            f"\n  Mean: {np.mean(accs):.1f}%  Std: {np.std(accs):.1f}%  "
            f"Min: {np.min(accs):.1f}%  Max: {np.max(accs):.1f}%"
        )

    cm = confusion_matrix(all_true, all_preds, labels=[0, 1, 2, 3])
    print(f"\nConfusion Matrix (rows=true, cols=pred):")
    print(
        f"{'':>8} {'awake':>8} {'light':>8} {'deep':>8} {'rem':>8}  | {'total':>6} {'recall':>7}"
    )
    for i, name in enumerate(phase_names):
        row = cm[i]
        total = row.sum()
        recall = row[i] / total * 100 if total > 0 else 0
        print(
            f"{name:>8} {row[0]:>8d} {row[1]:>8d} {row[2]:>8d} {row[3]:>8d}  | {total:>6d} {recall:>6.1f}%"
        )

    print(f"\n{'':>8} {'precision':>10}")
    for i, name in enumerate(phase_names):
        col_sum = cm[:, i].sum()
        prec = cm[i, i] / col_sum * 100 if col_sum > 0 else 0
        print(f"{name:>8} {prec:>9.1f}%")

    print(f"\nLabel distribution:")
    for i, name in enumerate(phase_names):
        true_count = np.sum(all_true == i)
        pred_count = np.sum(all_preds == i)
        print(
            f"  {name}: true={true_count} pred={pred_count} "
            f"(true%={true_count / len(all_true) * 100:.1f}% pred%={pred_count / len(all_preds) * 100:.1f}%)"
        )

    print(f"\nTotal windows: {len(all_true)}")
    return overall_acc


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="LONO CV for sleep phase classification"
    )
    parser.add_argument(
        "--quick", action="store_true", help="Use 4-fold CV instead of full LONO"
    )
    parser.add_argument(
        "--no-viterbi", action="store_true", help="Disable Viterbi post-processing"
    )
    parser.add_argument(
        "--model-params",
        nargs=3,
        type=float,
        metavar=("ITER", "DEPTH", "LR"),
        help="max_iter max_depth learning_rate",
    )
    args = parser.parse_args()

    max_iter = 300
    max_depth = 3
    learning_rate = 0.1
    if args.model_params:
        max_iter = int(args.model_params[0])
        max_depth = int(args.model_params[1])
        learning_rate = args.model_params[2]

    use_viterbi = not args.no_viterbi

    print(f"Loading sensor database...")
    t0 = time.time()
    df = load_sensor_db()
    print(f"  Loaded {len(df)} records in {time.time() - t0:.1f}s")

    print(f"\nBuilding training data ({len(FEATURE_NAMES)} features)...")
    t0 = time.time()
    X, y, night_ids, night_dates = build_training_data(df)
    print(
        f"  Built {len(y)} windows from {len(night_dates)} nights in {time.time() - t0:.1f}s"
    )

    if len(y) == 0:
        print("ERROR: No training data built!")
        return

    for i in range(4):
        name = INT_TO_PHASE[i]
        count = np.sum(y == i)
        print(f"  {name}: {count} ({count / len(y) * 100:.1f}%)")

    print(
        f"\nModel: HistGBT(max_iter={max_iter}, max_depth={max_depth}, lr={learning_rate})"
    )
    print(f"Viterbi: {'ON' if use_viterbi else 'OFF'}")

    if args.quick:
        print(f"\nRunning 4-fold CV...")
        night_accs, overall_acc, all_true, all_preds, raw_acc = run_kfold_cv(
            X,
            y,
            night_ids,
            night_dates,
            k=4,
            max_iter=max_iter,
            max_depth=max_depth,
            learning_rate=learning_rate,
            use_viterbi=use_viterbi,
        )
    else:
        print(f"\nRunning Leave-One-Night-Out CV ({len(night_dates)} folds)...")
        night_accs, overall_acc, all_true, all_preds, raw_acc = run_lono_cv(
            X,
            y,
            night_ids,
            night_dates,
            max_iter=max_iter,
            max_depth=max_depth,
            learning_rate=learning_rate,
            use_viterbi=use_viterbi,
        )

    print_results(
        overall_acc, all_true, all_preds, night_accs, raw_acc if use_viterbi else None
    )

    # Feature importance
    print(f"\nTop 20 features by importance (trained on all data):")
    model = HistGradientBoostingClassifier(
        max_iter=max_iter,
        max_depth=max_depth,
        learning_rate=learning_rate,
        min_samples_leaf=5,
        l2_regularization=0.01,
        max_bins=128,
        max_features=0.8,
        class_weight="balanced",
        random_state=42,
    )
    model.fit(X, y)

    from sklearn.inspection import permutation_importance

    perm = permutation_importance(model, X, y, n_repeats=5, random_state=42, n_jobs=-1)
    sorted_idx = perm.importances_mean.argsort()[::-1]
    for rank, idx in enumerate(sorted_idx[:20]):
        name = FEATURE_NAMES[idx]
        imp = perm.importances_mean[idx]
        std = perm.importances_std[idx]
        print(f"  {rank + 1:>2}. {name:<25} {imp:.4f} +/- {std:.4f}")


if __name__ == "__main__":
    main()
