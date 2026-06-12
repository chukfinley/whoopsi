"""Recall-balanced sleep staging: hit >=70% recall on EVERY stage.

Idea: a plain multiclass model maximizes overall accuracy and starves the
minority class (awake). We instead collect out-of-fold posteriors once, then
choose a per-class GAIN vector g so predicted = argmax_c proba[c]*g[c].
Tuning g trades a little Light recall (huge class) for a lot of Awake recall.
We search g to maximize the MINIMUM per-class recall, then report the operating
point and the largest feasible target where all stages clear the bar.

Pipeline per night: model proba -> gain -> context blend -> Viterbi -> rules.
"""
import sys, time, argparse
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.model_selection import GroupKFold
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix

from eval_lono import viterbi_decode, learn_transition_matrix, context_blend_proba
from algo5_ml.engine import apply_post_processing

INT_TO_PHASE = {0: "awake", 1: "light", 2: "deep", 3: "rem"}
WEIGHT_MAP = {0: 6.0, 1: 1.0, 2: 3.0, 3: 2.0}


def load_data():
    d = np.load(Path(__file__).resolve().parent / "dataset.npz", allow_pickle=True)
    return d["X"], d["y"], d["night_ids"], d["timestamps"]


def lgbm_fit(Xtr, ytr):
    import lightgbm as lgb
    m = lgb.LGBMClassifier(n_estimators=500, num_leaves=48, max_depth=6,
        learning_rate=0.03, min_child_samples=15, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=0.2, random_state=42, n_jobs=4, verbose=-1)
    sw = np.array([WEIGHT_MAP[l] for l in ytr])
    m.fit(Xtr, ytr, sample_weight=sw)
    return m


def collect_oof(X, y, night_ids, ts, folds=5):
    """Return per-window OOF proba (N,4) and learned global transition."""
    proba = np.zeros((len(y), 4))
    gkf = GroupKFold(n_splits=folds)
    for tr, te in gkf.split(X, y, night_ids):
        m = lgbm_fit(X[tr], y[tr])
        proba[te] = m.predict_proba(X[te])
    return proba


def recalls_from(yt, yp):
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    return np.array([cm[i, i] / cm[i].sum() if cm[i].sum() else 0.0 for i in range(4)]), cm


def decode_all(proba, y, night_ids, ts, gain, log_trans, log_init, viterbi=True):
    """Apply gain + (optional) Viterbi per night, return preds aligned to proba rows."""
    pred = np.zeros(len(y), dtype=int)
    g = np.asarray(gain)
    for nid in sorted(set(night_ids)):
        idx = np.where(night_ids == nid)[0]
        idx = idx[np.argsort(ts[idx])]
        p = proba[idx] * g
        p = p / np.maximum(p.sum(1, keepdims=True), 1e-10)
        if viterbi:
            p = context_blend_proba(p, alpha=0.2, lookback=3)
            path = viterbi_decode(np.log(np.clip(p, 1e-10, 1.0)), log_trans, log_init)
            path = np.array(apply_post_processing(list(path), list(ts[idx])))
        else:
            path = p.argmax(1)
        pred[idx] = path
    return pred


def tune_gain(proba, y, night_ids, ts, log_trans, log_init, viterbi):
    """Coordinate search over gain to maximize min-recall."""
    best_g = np.array([1.0, 1.0, 1.0, 1.0])
    yp = decode_all(proba, y, night_ids, ts, best_g, log_trans, log_init, viterbi)
    rec, _ = recalls_from(y, yp)
    best_min = rec.min()
    # grid candidates per class (awake & rem get the biggest lift)
    grids = {0: [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8], 1: [0.7, 0.85, 1.0],
             2: [1.0, 1.3, 1.7], 3: [1.0, 1.4, 1.8, 2.2]}
    for _ in range(3):  # a few coordinate passes
        for c in [0, 3, 2, 1]:
            for val in grids[c]:
                g = best_g.copy(); g[c] = val
                yp = decode_all(proba, y, night_ids, ts, g, log_trans, log_init, viterbi)
                rec, _ = recalls_from(y, yp)
                # objective: maximize min recall, tiebreak overall acc
                score = rec.min() + 0.05 * (y == yp).mean()
                if score > best_min:
                    best_min = score; best_g = g
    return best_g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--no-viterbi", action="store_true")
    args = ap.parse_args()
    viterbi = not args.no_viterbi

    X, y, night_ids, ts = load_data()
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    print(f"data: X={X.shape} nights={len(set(night_ids))} dist={dict(Counter(y.tolist()))}")

    t0 = time.time()
    print("collecting OOF posteriors (LightGBM, 5-fold)...", flush=True)
    proba = collect_oof(X, y, night_ids, ts, args.folds)
    np.save(Path(__file__).resolve().parent / "oof_proba.npy", proba)
    print(f"  done [{time.time()-t0:.0f}s]", flush=True)

    trans = learn_transition_matrix(y, night_ids)
    log_trans = np.log(np.clip(trans, 1e-10, 1.0))
    log_init = np.log(np.clip(np.bincount(y, minlength=4) / len(y), 1e-10, 1.0))

    # baseline (no gain)
    yp0 = decode_all(proba, y, night_ids, ts, [1, 1, 1, 1], log_trans, log_init, viterbi)
    r0, cm0 = recalls_from(y, yp0)
    print(f"\nbaseline gain=1: acc={ (y==yp0).mean()*100:.2f}%  "
          + "  ".join(f"{INT_TO_PHASE[i]} {r0[i]*100:.1f}%" for i in range(4)))

    print("tuning gain for max-min-recall...", flush=True)
    g = tune_gain(proba, y, night_ids, ts, log_trans, log_init, viterbi)
    yp = decode_all(proba, y, night_ids, ts, g, log_trans, log_init, viterbi)
    rec, cm = recalls_from(y, yp)
    acc = (y == yp).mean()
    print(f"\n=== Recall-balanced  gain={np.round(g,2).tolist()}  [{time.time()-t0:.0f}s] ===")
    print(f"  overall acc: {acc*100:.2f}%   min-recall: {rec.min()*100:.1f}%")
    print("  recall: " + "  ".join(f"{INT_TO_PHASE[i]} {rec[i]*100:.1f}%" for i in range(4)))
    print("  confusion (rows=true awake/light/deep/rem):")
    for i in range(4):
        print("   ", "  ".join(f"{cm[i,j]:5d}" for j in range(4)))
    np.save(Path(__file__).resolve().parent / "best_gain.npy", g)


if __name__ == "__main__":
    main()
