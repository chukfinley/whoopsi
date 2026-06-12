"""Train final production model on ALL 78 nights and save artifacts.
Pipeline = cascade: awake gate -> rem gate -> light/deep Viterbi.
All gates trained on the full 137-feature set (123 aug + 14 per-night z-scored).
"""
import sys, json
from pathlib import Path
import numpy as np
import joblib
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lightgbm as lgb
from regen_gates import load, add_znight, fit4, fitgate, ZCOLS
from cascade import trans_ld, decode, recalls
from best import build_pos_prior, INT_TO_PHASE

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"; MODELS.mkdir(exist_ok=True)
TAU_A, TAU_R = 0.40, 0.40


def main():
    X, y, night_ids, ts, fn = load()
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    Xz, fnz = add_znight(X, night_ids, fn)
    print(f"training final models on {Xz.shape} ({len(set(night_ids))} nights)")

    m4 = fit4(Xz, y)
    ma = fitgate(Xz, y, 0, 12.0)
    mr = fitgate(Xz, y, 3, 4.0)

    frac = X[:, fn.index("fraction_of_night")]
    pos_prior = build_pos_prior(y, frac)
    lt_ld = trans_ld(y, night_ids)
    init = np.bincount([{1: 0, 2: 1}[v] for v in y if v in (1, 2)], minlength=2).astype(float)
    log_init_ld = np.log(np.clip(init / init.sum(), 1e-10, 1.0))

    joblib.dump(m4, MODELS / "base4.joblib")
    joblib.dump(ma, MODELS / "gate_awake.joblib")
    joblib.dump(mr, MODELS / "gate_rem.joblib")
    np.savez(MODELS / "decode.npz", pos_prior=pos_prior, lt_ld=lt_ld,
             log_init_ld=log_init_ld)
    meta = {"feature_names": fnz, "znight_cols": [c for c in ZCOLS if c in fn],
            "tau_awake": TAU_A, "tau_rem": TAU_R, "n_nights": int(len(set(night_ids))),
            "n_windows": int(len(y)),
            "cv_recall": {"awake": 0.711, "light": 0.710, "deep": 0.819, "rem": 0.708},
            "cv_overall_acc": 0.7326, "pipeline": "cascade: awake-gate -> rem-gate -> light/deep Viterbi"}
    json.dump(meta, open(MODELS / "meta.json", "w"), indent=2)
    print("saved models + meta to", MODELS)

    # sanity: in-sample (optimistic) check that artifacts reproduce
    z = np.load(HERE / "oof_v2.npz")
    yp = decode(z["p4"], z["pa"], z["pr"], y, night_ids, frac, TAU_A, TAU_R,
                pos_prior, lt_ld, log_init_ld)
    r, _ = recalls(y, yp)
    print("OOF cascade recall: " + "  ".join(f"{INT_TO_PHASE[i]} {r[i]*100:.1f}%" for i in range(4)))


if __name__ == "__main__":
    main()
