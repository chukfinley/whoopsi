"""Regenerate OOF gates with per-night z-scored features (REM agent's proven
lever) and a 4-class base, save to oof_v2.npz. Then cascade can push all stages
over 70%."""
import sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sklearn.model_selection import GroupKFold
import lightgbm as lgb

HERE = Path(__file__).resolve().parent
ZCOLS = ["hr_std", "rr_std", "sdnn", "rmssd", "hr_range", "hr_delta", "gyro_std",
         "gyro_mean", "hr_max", "hr_mean", "rr_cv", "hr_masd_std", "breath_irreg",
         "hr_accel_var", "atonia_score"]


def load():
    d = np.load(HERE / "dataset_aug.npz", allow_pickle=True)
    return d["X"], d["y"], d["night_ids"], d["timestamps"], list(d["feature_names"])


def add_znight(X, night_ids, fn):
    cols = [c for c in ZCOLS if c in fn]
    extra = np.zeros((len(X), len(cols)))
    for j, c in enumerate(cols):
        v = X[:, fn.index(c)].astype(float)
        for nid in set(night_ids):
            m = night_ids == nid
            extra[m, j] = (v[m] - v[m].mean()) / (v[m].std() + 1e-6)
    return np.hstack([X, extra]), fn + [c + "_zn" for c in cols]


def fit4(Xtr, ytr):
    w = {0: 6.0, 1: 1.0, 2: 3.0, 3: 2.0}
    m = lgb.LGBMClassifier(n_estimators=600, num_leaves=48, max_depth=6,
        learning_rate=0.03, min_child_samples=15, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=0.2, random_state=42, n_jobs=4, verbose=-1)
    m.fit(Xtr, ytr, sample_weight=np.array([w[l] for l in ytr]))
    return m


def fitgate(Xtr, ytr, tgt, pw):
    yb = (ytr == tgt).astype(int)
    m = lgb.LGBMClassifier(n_estimators=600, num_leaves=40, max_depth=6,
        learning_rate=0.025, min_child_samples=18, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=0.3, random_state=42, n_jobs=4, verbose=-1)
    m.fit(Xtr, yb, sample_weight=np.where(yb == 1, pw, 1.0))
    return m


def main():
    X, y, night_ids, ts, fn = load()
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    Xz, fnz = add_znight(X, night_ids, fn)
    print(f"X={Xz.shape} (added {Xz.shape[1]-X.shape[1]} znight feats)", flush=True)
    t0 = time.time()
    p4 = np.zeros((len(y), 4)); pa = np.zeros(len(y)); pr = np.zeros(len(y))
    for k, (tr, te) in enumerate(GroupKFold(5).split(Xz, y, night_ids)):
        p4[te] = fit4(Xz[tr], y[tr]).predict_proba(Xz[te])
        pa[te] = fitgate(Xz[tr], y[tr], 0, 12.0).predict_proba(Xz[te])[:, 1]
        pr[te] = fitgate(Xz[tr], y[tr], 3, 4.0).predict_proba(Xz[te])[:, 1]
        print(f"  fold {k+1}/5 [{time.time()-t0:.0f}s]", flush=True)
    from sklearn.metrics import roc_auc_score
    mL = (y == 0) | (y == 1)
    print(f"awake-vs-light AUC={roc_auc_score((y[mL]==0).astype(int), pa[mL]):.3f}")
    mR = (y == 3) | (y == 1)
    print(f"rem-vs-light   AUC={roc_auc_score((y[mR]==3).astype(int), pr[mR]):.3f}")
    np.savez_compressed(HERE / "oof_v2.npz", p4=p4, pa=pa, pr=pr)
    print("saved oof_v2.npz")


if __name__ == "__main__":
    main()
