"""Model bake-off for sleep-stage classification on the full 78-night dataset.

Shared eval recipe (matches algo5 champion):
  per-fold fit with sample weights -> predict_proba
  -> per-night context blend (alpha=0.2) -> Viterbi (transitions learned from
  train labels) -> physiological post-processing.

Compares: HistGBT (baseline), LightGBM, XGBoost, soft-vote ensemble.
GroupKFold by night (no window leakage). Use --full for leave-one-night-out.
"""
import sys, time, argparse
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.model_selection import GroupKFold, LeaveOneGroupOut
from sklearn.metrics import confusion_matrix
from sklearn.ensemble import HistGradientBoostingClassifier

from eval_lono import (
    viterbi_decode, learn_transition_matrix, context_blend_proba,
)
from algo5_ml.engine import apply_post_processing

INT_TO_PHASE = {0: "awake", 1: "light", 2: "deep", 3: "rem"}
WEIGHT_MAP = {0: 5.0, 1: 1.0, 2: 3.0, 3: 1.8}


def load_data():
    d = np.load(Path(__file__).resolve().parent / "dataset.npz", allow_pickle=True)
    return d["X"], d["y"], d["night_ids"], d["timestamps"], list(d["feature_names"])


# ── model factories: each returns (fit_fn(Xtr,ytr)->fitted, name) ──
def make_histgbt():
    def fit(Xtr, ytr):
        m = HistGradientBoostingClassifier(
            max_iter=300, max_depth=4, learning_rate=0.05,
            min_samples_leaf=20, l2_regularization=0.1, max_bins=255,
            random_state=42)
        sw = np.array([WEIGHT_MAP[l] for l in ytr])
        m.fit(Xtr, ytr, sample_weight=sw)
        return m
    return fit, "HistGBT"


def make_lgbm():
    import lightgbm as lgb
    def fit(Xtr, ytr):
        m = lgb.LGBMClassifier(
            n_estimators=400, num_leaves=31, max_depth=5, learning_rate=0.04,
            min_child_samples=20, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, reg_lambda=0.1, class_weight=None,
            random_state=42, n_jobs=4, verbose=-1)
        sw = np.array([WEIGHT_MAP[l] for l in ytr])
        m.fit(Xtr, ytr, sample_weight=sw)
        return m
    return fit, "LightGBM"


def make_xgb():
    import xgboost as xgb
    def fit(Xtr, ytr):
        m = xgb.XGBClassifier(
            n_estimators=400, max_depth=5, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0,
            min_child_weight=5, objective="multi:softprob", num_class=4,
            tree_method="hist", random_state=42, n_jobs=4, verbosity=0)
        sw = np.array([WEIGHT_MAP[l] for l in ytr])
        m.fit(Xtr, ytr, sample_weight=sw)
        return m
    return fit, "XGBoost"


def make_ensemble():
    fh, _ = make_histgbt(); fl, _ = make_lgbm(); fx, _ = make_xgb()
    def fit(Xtr, ytr):
        models = [fh(Xtr, ytr), fl(Xtr, ytr), fx(Xtr, ytr)]
        class Ens:
            def predict_proba(self, X):
                return np.mean([mm.predict_proba(X) for mm in models], axis=0)
        return Ens()
    return fit, "Ensemble(H+L+X)"


def decode_night(proba, ts, log_trans, log_init):
    proba = context_blend_proba(proba, alpha=0.2, lookback=3)
    lp = np.log(np.clip(proba, 1e-10, 1.0))
    path = viterbi_decode(lp, log_trans, log_init)
    path = np.array(apply_post_processing(list(path), list(ts)))
    return path


def evaluate(fit_fn, X, y, night_ids, ts, splits):
    y_true_all, y_pred_all = [], []
    for tr, te in splits:
        Xtr, ytr = X[tr], y[tr]
        model = fit_fn(Xtr, ytr)
        trans = learn_transition_matrix(ytr, night_ids[tr])
        log_trans = np.log(np.clip(trans, 1e-10, 1.0))
        log_init = np.log(np.clip(np.bincount(ytr, minlength=4) / len(ytr), 1e-10, 1.0))
        # decode per test night
        for nid in sorted(set(night_ids[te])):
            nmask = night_ids[te] == nid
            idx = te[nmask]
            order = np.argsort(ts[idx])
            idx = idx[order]
            proba = model.predict_proba(X[idx])
            pred = decode_night(proba, ts[idx], log_trans, log_init)
            y_true_all.extend(y[idx].tolist())
            y_pred_all.extend(pred.tolist())
    return np.array(y_true_all), np.array(y_pred_all)


def report(name, yt, yp, elapsed):
    acc = (yt == yp).mean()
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    recalls = {INT_TO_PHASE[i]: (cm[i, i] / cm[i].sum() if cm[i].sum() else 0.0)
               for i in range(4)}
    print(f"\n=== {name}  [{elapsed:.0f}s] ===")
    print(f"  overall acc: {acc*100:.2f}%")
    print("  recall: " + "  ".join(f"{k} {v*100:.1f}%" for k, v in recalls.items()))
    print("  confusion (rows=true awake/light/deep/rem):")
    for i in range(4):
        print("   ", "  ".join(f"{cm[i,j]:5d}" for j in range(4)))
    return acc, recalls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="leave-one-night-out")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--models", default="histgbt,lgbm,xgb,ensemble")
    args = ap.parse_args()

    X, y, night_ids, ts, feat = load_data()
    print(f"data: X={X.shape}  nights={len(set(night_ids))}  dist={dict(Counter(y.tolist()))}")

    if args.full:
        logo = LeaveOneGroupOut()
        splits = list(logo.split(X, y, night_ids))
    else:
        gkf = GroupKFold(n_splits=args.folds)
        splits = list(gkf.split(X, y, night_ids))
    print(f"CV: {'LONO' if args.full else f'{args.folds}-fold GroupKFold'} ({len(splits)} folds)")

    factories = {"histgbt": make_histgbt, "lgbm": make_lgbm,
                 "xgb": make_xgb, "ensemble": make_ensemble}
    results = {}
    for key in args.models.split(","):
        key = key.strip()
        if key not in factories:
            continue
        fit_fn, name = factories[key]()
        t0 = time.time()
        yt, yp = evaluate(fit_fn, X, y, night_ids, ts, splits)
        results[name] = report(name, yt, yp, time.time() - t0)

    print("\n========== SUMMARY ==========")
    for name, (acc, rec) in sorted(results.items(), key=lambda x: -x[1][0]):
        print(f"  {name:18s} acc={acc*100:.2f}%  awake_rec={rec['awake']*100:.1f}%")


if __name__ == "__main__":
    main()
