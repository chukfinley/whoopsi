"""Inference for the production HYBRID sleep-stage model.

predict_hybrid(night_df, start_ts, end_ts) -> (timestamps, phases)
where phases are ints 0=awake,1=light,2=deep,3=rem per 2-min window.

night_df: raw sensor DataFrame (db_loader columns) sliced to the sleep window.
"""
import sys, json
from pathlib import Path
import numpy as np
import joblib
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.preprocessing import compute_rhr
from algo5_ml.features import extract_window_features, FEATURE_NAMES, WINDOW_SEC, STRIDE_SEC
from algo5_ml.engine import apply_post_processing
from eval_lono import viterbi_decode, context_blend_proba
from build_aug import new_features, NEW
from hybrid import awake_bridge

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"


class HybridModel:
    def __init__(self):
        self.a5 = joblib.load(MODELS / "hybrid_algo5.joblib")
        self.gate = joblib.load(MODELS / "hybrid_gate_awake.joblib")
        self.trans = np.load(MODELS / "hybrid_a5_trans.npy")
        self.meta = json.load(open(MODELS / "hybrid_meta.json"))
        self.gate_feats = self.meta["gate_features"]
        self.zcols = self.meta["znight_cols"]
        self.p = self.meta["params"]

    def _windows(self, df, start_ts, end_ts):
        ts_arr = df["timestamp"].values
        if len(ts_arr) > 10 and float(np.median(np.diff(ts_arr[:200]))) < 1.5:
            df = df.iloc[::2].reset_index(drop=True)
        mi = np.median(np.diff(df["timestamp"].values[:100])) if len(df) > 10 else 1.0
        nhr = df["hr"].values; nhr = nhr[(nhr > 30) & (nhr < 200)]
        ns = None
        if len(nhr) >= 10:
            ns = {"p5": float(np.percentile(nhr, 5)), "p10": float(np.percentile(nhr, 10)),
                  "median": float(np.median(nhr)), "p95": float(np.percentile(nhr, 95)),
                  "hr_mean_night": float(np.mean(nhr)), "hr_std_night": float(np.std(nhr))}
        rhr = compute_rhr(df)
        prev = None; hist = []
        rows115, rowsaug, tss = [], [], []
        ws = int(start_ts)
        while ws + WINDOW_SEC <= int(end_ts):
            we = ws + WINDOW_SEC
            ch = df[(df["timestamp"] >= ws) & (df["timestamp"] < we)]
            if len(ch) < max(3, int(WINDOW_SEC / mi * 0.25)):
                ws += STRIDE_SEC; continue
            f = extract_window_features(ch, rhr, start_ts, end_ts, prev, hist, ns)
            if f is None:
                ws += STRIDE_SEC; continue
            f["stress_mean"] = 1.0
            nf = new_features(ch)
            rows115.append([f.get(n, 0.0) for n in FEATURE_NAMES])
            rowsaug.append([f.get(n, 0.0) for n in FEATURE_NAMES] + [nf[n] for n in NEW])
            tss.append(ws)
            prev = f; hist.append(f); ws += STRIDE_SEC
        return np.array(rows115), np.array(rowsaug), np.array(tss)

    def _add_znight(self, Xaug):
        base = list(FEATURE_NAMES) + NEW
        extra = []
        for c in self.zcols:
            v = Xaug[:, base.index(c)].astype(float)
            extra.append((v - v.mean()) / (v.std() + 1e-6))
        return np.hstack([Xaug, np.array(extra).T]) if extra else Xaug

    def predict(self, night_df, start_ts, end_ts):
        X115, Xaug, tss = self._windows(night_df, start_ts, end_ts)
        if len(tss) == 0:
            return np.array([]), np.array([])
        X115 = np.nan_to_num(X115, nan=0.0, posinf=0.0, neginf=0.0)
        Xz = np.nan_to_num(self._add_znight(Xaug), nan=0.0, posinf=0.0, neginf=0.0)
        # algo5 path
        proba = context_blend_proba(self.a5.predict_proba(X115), 0.2, 3)
        li = np.log(np.clip(proba.mean(0), 1e-10, 1.0))
        path = viterbi_decode(np.log(np.clip(proba, 1e-10, 1.0)),
                              np.log(np.clip(self.trans, 1e-10, 1.0)), li)
        path = np.array(apply_post_processing(list(path), list(tss)))
        # awake-gate override (REM-protected) + bridge
        pa = self.gate.predict_proba(Xz)[:, 1]
        is_rem = path == 3
        awake = (pa >= self.p["tau_awake"]) & (~is_rem | (pa >= self.p["rem_protect"]))
        path[awake] = 0
        path = awake_bridge(path, self.p["bridge_L"])
        return tss, path


if __name__ == "__main__":
    # demo: predict one night from the unified DB using deep_dive bounds
    from data.db_loader import load_from_db
    from algo5_ml.features import _parse_deep_dive_sleep_bounds
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-01-28"
    second_gt, s, e = _parse_deep_dive_sleep_bounds(date)
    df = load_from_db()
    night = df[(df["timestamp"] >= s) & (df["timestamp"] <= e)]
    m = HybridModel()
    tss, ph = m.predict(night, s, e)
    from collections import Counter
    print(date, "windows:", len(ph), "dist:", dict(Counter(ph.tolist())))
    if second_gt:
        gt = []
        for t in tss:
            seg = [second_gt.get(x) for x in range(int(t), int(t)+120) if second_gt.get(x)]
            from collections import Counter as C
            gt.append({"awake":0,"light":1,"deep":2,"rem":3}[C(seg).most_common(1)[0][0]] if seg else -1)
        gt = np.array(gt); mask = gt >= 0
        print("acc vs Whoop:", round(float((ph[mask]==gt[mask]).mean())*100,1), "%")
