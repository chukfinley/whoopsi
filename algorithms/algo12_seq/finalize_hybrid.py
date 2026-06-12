"""Train the PRODUCTION hybrid on ALL 78 nights and save a self-contained model.

Hybrid = algo5 (HistGBT 115 feats + Viterbi + post-proc) for sleep structure,
overridden with AWAKE from the algo12 awake-gate (LightGBM 137 feats incl gyro
+ per-night z-scored), REM-protected, + awake-bridge L=1.
"""
import sys, json
from pathlib import Path
import numpy as np
import joblib
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix
import lightgbm as lgb
from eval_lono import learn_transition_matrix
from regen_gates import add_znight, fitgate, ZCOLS
from hybrid import hybrid_pred, recalls

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"; MODELS.mkdir(exist_ok=True)
INT2 = {0: "awake", 1: "light", 2: "deep", 3: "rem"}
A5_W = {0: 5.0, 1: 1.0, 2: 3.0, 3: 1.8}
PARAMS = {"tau_awake": 0.32, "rem_protect": 0.55, "bridge_L": 1}


def main():
    d115 = np.load(HERE / "dataset.npz", allow_pickle=True)
    d137 = np.load(HERE / "dataset_aug.npz", allow_pickle=True)
    X115 = np.nan_to_num(d115["X"], nan=0.0, posinf=0.0, neginf=0.0)
    y, nid, ts = d115["y"], d115["night_ids"], d115["timestamps"]
    fn115 = list(d115["feature_names"])
    Xaug = np.nan_to_num(d137["X"], nan=0.0, posinf=0.0, neginf=0.0)
    fnaug = list(d137["feature_names"])
    Xz, fnz = add_znight(Xaug, nid, fnaug)
    print(f"algo5 feats={X115.shape[1]}  gate feats={Xz.shape[1]} (gyro: "
          + ",".join(c for c in fnz if 'gyro' in c) + ")")

    # algo5 model + transition (trained on ALL data)
    a5 = HistGradientBoostingClassifier(max_iter=300, max_depth=3, learning_rate=0.05,
        l2_regularization=0.1, random_state=42)
    a5.fit(X115, y, sample_weight=np.array([A5_W[l] for l in y]))
    a5_trans = learn_transition_matrix(y, nid)

    # awake gate (trained on ALL data, 137 feats incl gyro + znight)
    gate = fitgate(Xz, y, 0, 12.0)

    joblib.dump(a5, MODELS / "hybrid_algo5.joblib")
    joblib.dump(gate, MODELS / "hybrid_gate_awake.joblib")
    np.save(MODELS / "hybrid_a5_trans.npy", a5_trans)
    meta = {"algo5_features": fn115, "gate_features": fnz, "znight_cols": [c for c in ZCOLS if c in fnaug],
            "params": PARAMS, "n_nights": int(len(set(nid))), "n_windows": int(len(y)),
            "cv_recall": {"awake": 74.8, "light": 70.1, "deep": 86.8, "rem": 71.5},
            "cv_overall_acc": 74.2,
            "pipeline": "algo5(HistGBT115+Viterbi) overridden by awake-gate(LGBM137), REM-protected, awake-bridge L=1"}
    json.dump(meta, open(MODELS / "hybrid_meta.json", "w"), indent=2)
    print("saved hybrid_* artifacts to", MODELS)

    # sanity (OOF for honest numbers)
    z = np.load(HERE / "oof_v2.npz"); a5oof = np.load(HERE / "algo5_pred.npy")
    p = hybrid_pred(a5oof, z["pa"], z["pr"], y, nid, ts,
                    PARAMS["tau_awake"], 1, PARAMS["rem_protect"], PARAMS["bridge_L"])
    r, cm = recalls(y, p)
    print(f"OOF hybrid acc={(y==p).mean()*100:.2f}%  "
          + "  ".join(f"{INT2[i]} {r[i]*100:.1f}%" for i in range(4)))


if __name__ == "__main__":
    main()
