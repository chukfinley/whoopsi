"""Measure the TRUE awake-vs-light operating ceiling on all 78 nights.
Pure 2-class gate (awake=1 vs light=0), GroupKFold, OOF ROC. Reports awake
recall achievable while holding light recall at 70/75/80%. Also adds per-night
z-scored variability features (the lever the REM agent proved) to test if they
sharpen the awake gate beyond the absolute features.
"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sklearn.model_selection import GroupKFold
import lightgbm as lgb

HERE = Path(__file__).resolve().parent


def load(name):
    d = np.load(HERE / name, allow_pickle=True)
    return d["X"], d["y"], d["night_ids"], list(d["feature_names"])


def add_znight(X, night_ids, fn, cols):
    """Append per-night z-scored versions of `cols`."""
    newcols = []
    extra = np.zeros((len(X), len(cols)))
    for j, c in enumerate(cols):
        if c not in fn:
            continue
        v = X[:, fn.index(c)].astype(float)
        z = np.zeros(len(v))
        for nid in set(night_ids):
            m = night_ids == nid
            mu, sd = v[m].mean(), v[m].std()
            z[m] = (v[m] - mu) / (sd + 1e-6)
        extra[:, j] = z
        newcols.append(c + "_znight")
    return np.hstack([X, extra]), fn + newcols


def gate_oof(X, yb, night_ids, pos_w=12.0, folds=5):
    p = np.zeros(len(yb))
    for tr, te in GroupKFold(n_splits=folds).split(X, yb, night_ids):
        sw = np.where(yb[tr] == 1, pos_w, 1.0)
        m = lgb.LGBMClassifier(n_estimators=500, num_leaves=32, max_depth=5,
            learning_rate=0.03, min_child_samples=20, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, reg_lambda=0.3, random_state=42, n_jobs=4, verbose=-1)
        m.fit(X[tr], yb[tr], sample_weight=sw)
        p[te] = m.predict_proba(X[te])[:, 1]
    return p


def curve(pa, yb, name):
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(yb, pa)
    # light recall = TN rate (negatives correctly kept). sweep threshold.
    print(f"\n[{name}] awake-vs-light AUC = {auc:.3f}")
    pos = pa[yb == 1]; neg = pa[yb == 0]
    for light_rec in [0.70, 0.75, 0.80, 0.85]:
        thr = np.quantile(neg, light_rec)   # keep light_rec of negatives below thr
        awake_rec = (pos >= thr).mean()
        print(f"  light_rec {light_rec:.2f} -> awake_rec {awake_rec*100:.1f}%  (thr={thr:.3f})")
    return auc


def main():
    X, y, night_ids, fn = load("dataset_aug.npz")
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    # only awake (1) vs light (0) windows
    mask = (y == 0) | (y == 1)
    Xa, na = X[mask], night_ids[mask]
    yb = (y[mask] == 0).astype(int)  # awake=1
    print(f"awake={yb.sum()} light={(yb==0).sum()} nights={len(set(na))}")

    pa = gate_oof(Xa, yb, na)
    curve(pa, yb, "absolute features (123)")

    zcols = ["hr_std", "rr_std", "sdnn", "rmssd", "hr_range", "hr_delta",
             "gyro_std", "gyro_mean", "hr_max", "hr_mean", "rr_cv"]
    Xz, fnz = add_znight(Xa, na, fn, zcols)
    paz = gate_oof(Xz, yb, na)
    curve(paz, yb, "absolute + per-night z-scored")


if __name__ == "__main__":
    main()
