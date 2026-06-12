"""Reproduce algo5's pipeline (HistGBT + Viterbi + post-processing) as OOF
predictions on the SAME 78 nights, for a fair dashboard comparison vs algo12.
Saves algo5_pred.npy aligned to dataset.npz / dataset_aug.npz row order."""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sklearn.model_selection import GroupKFold
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix
from eval_lono import (viterbi_decode, learn_transition_matrix, context_blend_proba)
from algo5_ml.engine import apply_post_processing

HERE = Path(__file__).resolve().parent
INT2 = {0: "awake", 1: "light", 2: "deep", 3: "rem"}
W = {0: 5.0, 1: 1.0, 2: 3.0, 3: 1.8}  # algo5 default LONO weights


def main():
    d = np.load(HERE / "dataset.npz", allow_pickle=True)
    X, y, nid, ts = d["X"], d["y"], d["night_ids"], d["timestamps"]
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    pred = np.zeros(len(y), int)
    for tr, te in GroupKFold(5).split(X, y, nid):
        m = HistGradientBoostingClassifier(max_iter=300, max_depth=3,
            learning_rate=0.05, l2_regularization=0.1, random_state=42)
        m.fit(X[tr], y[tr], sample_weight=np.array([W[l] for l in y[tr]]))
        trans = learn_transition_matrix(y[tr], nid[tr])
        lt = np.log(np.clip(trans, 1e-10, 1.0))
        li = np.log(np.clip(np.bincount(y[tr], minlength=4) / len(tr), 1e-10, 1.0))
        for n in sorted(set(nid[te])):
            idx = np.where(nid == n)[0]; idx = idx[np.argsort(ts[idx])]
            proba = context_blend_proba(m.predict_proba(X[idx]), 0.2, 3)
            path = viterbi_decode(np.log(np.clip(proba, 1e-10, 1.0)), lt, li)
            path = np.array(apply_post_processing(list(path), list(ts[idx])))
            pred[idx] = path
    np.save(HERE / "algo5_pred.npy", pred)
    cm = confusion_matrix(y, pred, labels=[0, 1, 2, 3])
    r = [cm[i, i] / cm[i].sum() if cm[i].sum() else 0 for i in range(4)]
    print(f"algo5 OOF acc={(y==pred).mean()*100:.2f}%  " +
          "  ".join(f"{INT2[i]} {r[i]*100:.1f}%" for i in range(4)))
    print("saved algo5_pred.npy")


if __name__ == "__main__":
    main()
