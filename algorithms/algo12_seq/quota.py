"""Per-night awake-quota decoding — the decisive lever for awake recall.

The awake gate ranks windows well (awake-vs-light AUC ~0.9) but a global
threshold over/under-fires per night. Fix: give each night an awake BUDGET
(target fraction) and adapt the awake gain per night so the decoded awake count
matches it. Viterbi keeps stage structure intact.

Budget sources compared:
  oracle  : true awake fraction per night        (ceiling)
  whoop   : 1 - efficiency from official deep_dive (architecture agent: corr 0.94)
  pred    : LightGBM regressor on night-level features (INDEPENDENT of Whoop)

Reuses cached OOF posteriors (oof_best.npz from best.py).
"""
import sys, time, json
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.model_selection import GroupKFold
from sklearn.metrics import confusion_matrix
import lightgbm as lgb

from best import (load_data, build_pos_prior, empirical_trans, viterbi,
                  soft_floor, context_blend, recalls, INT_TO_PHASE)

HERE = Path(__file__).resolve().parent


def decode_night(post_row, frac_n, gain0, pos_prior, log_trans, log_init,
                 gate_w, pa_n, pr_n, p4_n, prior_alpha=0.6, nbins=10):
    post = p4_n.copy()
    post[:, 0] = (1 - gate_w) * post[:, 0] + gate_w * pa_n
    post[:, 3] = (1 - gate_w) * post[:, 3] + gate_w * pr_n
    post = post / np.maximum(post.sum(1, keepdims=True), 1e-10)
    b = np.clip((frac_n * nbins).astype(int), 0, nbins - 1)
    post = post * (pos_prior[b] ** prior_alpha)
    g = np.array([gain0, 0.8, 1.0, 1.0])
    post = post * g
    post = post / np.maximum(post.sum(1, keepdims=True), 1e-10)
    post = context_blend(post, 0.2, 3)
    path = viterbi(np.log(np.clip(post, 1e-10, 1.0)), log_trans, log_init)
    return soft_floor(path)


def decode_quota(p4, pa, pr, y, night_ids, frac, target_frac, pos_prior,
                 log_trans, log_init, gate_w=0.6):
    """For each night, binary-search awake gain to hit target awake fraction."""
    pred = np.zeros(len(y), int)
    for nid in sorted(set(night_ids)):
        idx = np.where(night_ids == nid)[0]
        idx = idx[np.argsort(frac[idx])]
        tgt = float(target_frac[nid])
        tgt = min(max(tgt, 0.0), 0.6)
        lo, hi = 0.3, 40.0
        best_path = None
        for _ in range(14):  # binary search on awake gain
            g0 = (lo * hi) ** 0.5
            path = decode_night(None, frac[idx], g0, pos_prior, log_trans,
                                log_init, gate_w, pa[idx], pr[idx], p4[idx])
            aw = (path == 0).mean()
            best_path = path
            if abs(aw - tgt) < 0.01:
                break
            if aw < tgt:
                lo = g0
            else:
                hi = g0
        pred[idx] = best_path
    return pred


def night_target_oracle(y, night_ids):
    t = {}
    for nid in set(night_ids):
        s = y[night_ids == nid]
        t[nid] = (s == 0).mean()
    return t


def night_features(X, y, night_ids, fn):
    """Aggregate per-night features for awake-fraction regression (no labels)."""
    cols = [c for c in ["hr_std", "gyro_std", "rmssd", "hr_range", "hr_delta",
                        "rr_std", "gyro_mean", "hr_max", "mv_std", "spo2_min"] if c in fn]
    nids = sorted(set(night_ids))
    Xf, yf = [], []
    for nid in nids:
        idx = night_ids == nid
        row = []
        for c in cols:
            v = X[idx, fn.index(c)]
            row += [float(np.mean(v)), float(np.std(v)),
                    float(np.percentile(v, 90)), float(np.percentile(v, 10))]
        row.append(int(idx.sum()))
        Xf.append(row)
        yf.append((y[idx] == 0).mean())
    return np.array(Xf), np.array(yf), nids


def night_target_pred(X, y, night_ids, fn, folds=5):
    """OOF night-level awake-fraction prediction (independent of Whoop)."""
    Xf, yf, nids = night_features(X, y, night_ids, fn)
    pred = np.zeros(len(nids))
    gkf = GroupKFold(n_splits=folds)
    grp = np.arange(len(nids))
    for tr, te in gkf.split(Xf, yf, grp):
        m = lgb.LGBMRegressor(n_estimators=200, num_leaves=15, max_depth=4,
            learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            min_child_samples=5, random_state=42, n_jobs=4, verbose=-1)
        m.fit(Xf[tr], yf[tr])
        pred[te] = m.predict(Xf[te])
    return {nids[i]: max(0.0, pred[i]) for i in range(len(nids))}, \
           {nids[i]: yf[i] for i in range(len(nids))}


def night_target_whoop(night_ids, ts, X, fn):
    """1 - efficiency per night from official deep_dive JSON (architecture agent)."""
    from algo5_ml.features import _parse_deep_dive_sleep_bounds
    import glob, datetime
    # map night_id -> a representative date via timestamps
    tcol = ts
    out = {}
    # build date for each night from median timestamp
    eff_by_date = {}
    for f in glob.glob(str(HERE.parent.parent / "whoop_backup/deep_dive/*.json")):
        try:
            j = json.load(open(f))
        except Exception:
            continue
        date = Path(f).stem
        e = _find_efficiency(j)
        if e is not None:
            eff_by_date[date] = e
    for nid in sorted(set(night_ids)):
        idx = night_ids == nid
        med = int(np.median(tcol[idx]))
        d = datetime.datetime.utcfromtimestamp(med + 3600).strftime("%Y-%m-%d")
        # try this date and neighbors
        eff = None
        for dd in [d]:
            if dd in eff_by_date:
                eff = eff_by_date[dd]; break
        out[nid] = (1.0 - eff / 100.0) if eff is not None else None
    return out


def _find_efficiency(obj):
    """Recursively search deep_dive JSON for a sleep-efficiency percentage."""
    found = []
    def rec(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and "efficien" in k.lower() and isinstance(v, (int, float)):
                    found.append(float(v))
                rec(v)
        elif isinstance(o, list):
            for x in o:
                rec(x)
    rec(obj)
    vals = [v for v in found if 0 < v <= 100]
    return vals[0] if vals else None


def report(name, yt, yp, t0):
    r, cm = recalls(yt, yp)
    print(f"\n=== {name}  [{time.time()-t0:.0f}s] ===")
    print(f"  acc={ (yt==yp).mean()*100:.2f}%  MIN-recall={r.min()*100:.1f}%")
    print("  " + "  ".join(f"{INT_TO_PHASE[i]} {r[i]*100:.1f}%" for i in range(4)))
    for i in range(4):
        print("   ", "  ".join(f"{cm[i,j]:5d}" for j in range(4)))
    return r


def main():
    X, y, night_ids, ts, fn = load_data()
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    frac = X[:, fn.index("fraction_of_night")]
    print(f"data X={X.shape} nights={len(set(night_ids))} dist={dict(Counter(y.tolist()))}")

    z = np.load(HERE / "oof_best.npz")
    p4, pa, pr = z["p4"], z["pa"], z["pr"]
    pos_prior = build_pos_prior(y, frac)
    log_trans = empirical_trans(y, night_ids)
    log_init = np.log(np.clip(np.bincount(y, minlength=4) / len(y), 1e-10, 1.0))

    t0 = time.time()
    # oracle ceiling
    tgt_o = night_target_oracle(y, night_ids)
    yp = decode_quota(p4, pa, pr, y, night_ids, frac, tgt_o, pos_prior, log_trans, log_init)
    report("ORACLE quota (ceiling)", y, yp, t0)

    # independent night regressor
    tgt_p, tgt_true = night_target_pred(X, y, night_ids, fn)
    mae = np.mean([abs(tgt_p[k] - tgt_true[k]) for k in tgt_p])
    print(f"\n[night awake-frac regressor OOF MAE = {mae:.3f}]")
    yp = decode_quota(p4, pa, pr, y, night_ids, frac, tgt_p, pos_prior, log_trans, log_init)
    report("PREDICTED quota (independent)", y, yp, t0)

    # whoop-efficiency assisted
    try:
        tgt_w = night_target_whoop(night_ids, ts, X, fn)
        cov = sum(v is not None for v in tgt_w.values())
        print(f"\n[whoop efficiency covered {cov}/{len(tgt_w)} nights]")
        if cov > 0:
            tgt_wf = {k: (v if v is not None else tgt_p[k]) for k, v in tgt_w.items()}
            yp = decode_quota(p4, pa, pr, y, night_ids, frac, tgt_wf, pos_prior, log_trans, log_init)
            report("WHOOP-efficiency quota", y, yp, t0)
    except Exception as e:
        print("whoop quota skipped:", e)


if __name__ == "__main__":
    main()
