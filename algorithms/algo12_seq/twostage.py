"""Two-stage (Wake-vs-Sleep gate -> Light/Deep/REM) on cached 78-night data.

Stage 1 lifts Awake recall (the chronic weakness) via heavy awake sample weight;
Stage 2 discriminates the three sleep stages. Same Viterbi+postproc recipe.
"""
import sys, time, argparse
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.model_selection import GroupKFold, LeaveOneGroupOut
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix

from eval_lono import viterbi_decode, learn_transition_matrix, context_blend_proba
from algo5_ml.engine import apply_post_processing

INT_TO_PHASE = {0: "awake", 1: "light", 2: "deep", 3: "rem"}


def load_data():
    d = np.load(Path(__file__).resolve().parent / "dataset.npz", allow_pickle=True)
    return d["X"], d["y"], d["night_ids"], d["timestamps"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--awake-weight", type=float, default=5.0)
    args = ap.parse_args()

    X, y, night_ids, ts = load_data()
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    print(f"data: X={X.shape} nights={len(set(night_ids))} dist={dict(Counter(y.tolist()))}")

    splits = (list(LeaveOneGroupOut().split(X, y, night_ids)) if args.full
              else list(GroupKFold(n_splits=args.folds).split(X, y, night_ids)))

    yt_all, yp_all = [], []
    t0 = time.time()
    for tr, te in splits:
        Xtr = X[tr]
        ytr = y[tr]
        # stage 1: wake (1) vs sleep (0)
        y_bin = (ytr != 0).astype(int)
        sw1 = np.where(ytr == 0, args.awake_weight, 1.0)
        m1 = HistGradientBoostingClassifier(max_iter=200, max_depth=3,
              learning_rate=0.05, l2_regularization=0.1, random_state=42)
        m1.fit(Xtr, y_bin, sample_weight=sw1)
        # stage 2: light/deep/rem on sleep windows only
        sm = ytr != 0
        Xs, ys = Xtr[sm], ytr[sm]
        sw2 = np.array([{1: 1.0, 2: 2.5, 3: 2.0}[v] for v in ys])
        m2 = HistGradientBoostingClassifier(max_iter=250, max_depth=3,
              learning_rate=0.05, l2_regularization=0.1, random_state=42)
        m2.fit(Xs, ys, sample_weight=sw2)
        s2_classes = list(m2.classes_)

        trans = learn_transition_matrix(ytr, night_ids[tr])
        log_trans = np.log(np.clip(trans, 1e-10, 1.0))
        log_init = np.log(np.clip(np.bincount(ytr, minlength=4) / len(ytr), 1e-10, 1.0))

        for nid in sorted(set(night_ids[te])):
            idx = np.where(night_ids == nid)[0]
            idx = idx[np.argsort(ts[idx])]
            p1 = m1.predict_proba(X[idx])            # [:,0]=awake, [:,1]=sleep
            p2 = m2.predict_proba(X[idx])            # over sleep classes
            combined = np.zeros((len(idx), 4))
            combined[:, 0] = p1[:, 0]
            for ci, cls in enumerate(s2_classes):
                combined[:, cls] = p1[:, 1] * p2[:, ci]
            combined /= np.maximum(combined.sum(1, keepdims=True), 1e-10)
            combined = context_blend_proba(combined, alpha=0.2, lookback=3)
            path = viterbi_decode(np.log(np.clip(combined, 1e-10, 1.0)), log_trans, log_init)
            path = np.array(apply_post_processing(list(path), list(ts[idx])))
            yt_all.extend(y[idx].tolist())
            yp_all.extend(path.tolist())

    yt, yp = np.array(yt_all), np.array(yp_all)
    acc = (yt == yp).mean()
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    rec = {INT_TO_PHASE[i]: (cm[i, i] / cm[i].sum() if cm[i].sum() else 0) for i in range(4)}
    print(f"\n=== Two-stage (awake_w={args.awake_weight})  [{time.time()-t0:.0f}s] ===")
    print(f"  overall acc: {acc*100:.2f}%")
    print("  recall: " + "  ".join(f"{k} {v*100:.1f}%" for k, v in rec.items()))
    for i in range(4):
        print("   ", "  ".join(f"{cm[i,j]:5d}" for j in range(4)))


if __name__ == "__main__":
    main()
