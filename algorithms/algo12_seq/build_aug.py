"""Augmented dataset: existing 115 features + new high-AUC features the
sub-agents found (autonomic/gyro for awake, atonia/breath-irregularity for REM).
Mirrors build_training_data windowing EXACTLY so labels/nights line up.
"""
import sys, time
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.db_loader import load_from_db
from common.preprocessing import compute_rhr
from algo5_ml.features import (
    extract_window_features, _parse_deep_dive_sleep_bounds, _parse_deep_dive_stress,
    FEATURE_NAMES, PHASE_TO_INT, WINDOW_SEC, STRIDE_SEC,
)

NEW = ["acc_z_var", "gyro_max", "hr_rise_after_burst", "atonia_score",
       "breath_irreg", "hr_accel_var", "hr_masd_std", "hr_var_x_level"]


def new_features(chunk):
    hr = chunk["hr"].values.astype(float)
    hrv = hr[(hr > 30) & (hr < 200)]
    gy = chunk["gyro"].values.astype(float) if "gyro" in chunk else np.zeros(len(chunk))
    mv = chunk["movement"].values.astype(float) if "movement" in chunk else np.zeros(len(chunk))
    azc = chunk["acc_z"].values.astype(float) if "acc_z" in chunk else np.zeros(len(chunk))
    f = {k: 0.0 for k in NEW}
    f["acc_z_var"] = float(np.var(azc)) if len(azc) > 2 else 0.0
    f["gyro_max"] = float(np.max(gy)) if len(gy) else 0.0
    if len(hrv) > 3:
        d1 = np.abs(np.diff(hrv))
        f["hr_masd_std"] = float(np.std(d1))
        d2 = np.diff(hrv, 2)
        f["hr_accel_var"] = float(np.var(d2)) if len(d2) else 0.0
        f["hr_var_x_level"] = float(np.std(hrv) * np.mean(hrv))
    # atonia: HR variable while body still -> high hr_std / low movement
    hr_std = float(np.std(hrv)) if len(hrv) > 3 else 0.0
    f["atonia_score"] = hr_std / (float(np.mean(mv)) + 0.05)
    # gyro burst -> HR rise coupling: max HR rise in 15s after a gyro peak
    if len(gy) > 10 and len(hr) == len(gy):
        thr = np.mean(gy) + 2 * np.std(gy)
        rises = []
        for i in np.where(gy > thr)[0]:
            a, b = i, min(len(hr), i + 15)
            seg = hr[a:b]
            seg = seg[seg > 30]
            if len(seg) > 2:
                rises.append(seg.max() - seg.min())
        f["hr_rise_after_burst"] = float(np.mean(rises)) if rises else 0.0
    # breathing irregularity from RR intervals
    rr = chunk["rr1_ms"].values.astype(float) if "rr1_ms" in chunk else np.array([])
    rr = rr[(rr > 300) & (rr < 2000)]
    if len(rr) > 4:
        f["breath_irreg"] = float(np.std(np.diff(rr)))
    return f


def main():
    t0 = time.time()
    print("loading DB...", flush=True)
    df = load_from_db()
    print(f"  rows={len(df)} [{time.time()-t0:.0f}s]", flush=True)
    sensor_dates = sorted(set(str(d) for d in df["date"].unique()
                              if hasattr(d, "year") and d.year >= 2025))
    allF, allY, allN, allT, dates = [], [], [], [], []
    nidx = 0; seen = set()
    stride = STRIDE_SEC
    for ds in sensor_dates:
        second_gt, start_ts, end_ts = _parse_deep_dive_sleep_bounds(ds)
        if second_gt is None or len(second_gt) < 600:
            continue
        if (start_ts, end_ts) in seen:
            continue
        seen.add((start_ts, end_ts))
        stress_ts = _parse_deep_dive_stress(ds)
        sl = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]
        if len(sl) < 30:
            continue
        nhr = sl["hr"].values; nhr = nhr[(nhr > 30) & (nhr < 200)]
        ns = None
        if len(nhr) >= 10:
            ns = {"p5": float(np.percentile(nhr, 5)), "p10": float(np.percentile(nhr, 10)),
                  "median": float(np.median(nhr)), "p95": float(np.percentile(nhr, 95)),
                  "hr_mean_night": float(np.mean(nhr)),
                  "hr_std_night": float(np.std(nhr)) if len(nhr) > 10 else 1.0}
        if "skin_temp" in sl.columns and ns is not None:
            nt = sl["skin_temp"].values; nt = nt[(nt > 25) & (nt < 42)]
            ns["temp_median"] = float(np.median(nt)) if len(nt) >= 10 else None
        ts_arr = sl["timestamp"].values
        if len(ts_arr) > 10 and float(np.median(np.diff(ts_arr[:200]))) < 1.5:
            sl = sl.iloc[::2].reset_index(drop=True)
        timestamps = sl["timestamp"].values
        mi = np.median(np.diff(timestamps[:100])) if len(timestamps) > 10 else 1.0
        rhr = compute_rhr(sl)
        prev = None; hist = []; n_win = 0
        ws = int(start_ts)
        while ws + WINDOW_SEC <= int(end_ts):
            we = ws + WINDOW_SEC
            ch = sl[(sl["timestamp"] >= ws) & (sl["timestamp"] < we)]
            if len(ch) < max(3, int(WINDOW_SEC / mi * 0.25)):
                ws += stride; continue
            feats = extract_window_features(ch, rhr, start_ts, end_ts, prev, hist, ns)
            if feats is None:
                ws += stride; continue
            gtp = [second_gt.get(s) for s in range(ws, we) if second_gt.get(s)]
            if not gtp:
                prev = feats; hist.append(feats); ws += stride; continue
            lbl = PHASE_TO_INT[Counter(gtp).most_common(1)[0][0]]
            if stress_ts:
                sv = [v for t, v in stress_ts.items() if ws <= t < we]
                feats["stress_mean"] = float(np.mean(sv)) if sv else 1.0
            else:
                feats["stress_mean"] = 1.0
            nf = new_features(ch)
            vec = [feats.get(n, 0.0) for n in FEATURE_NAMES] + [nf[n] for n in NEW]
            allF.append(vec); allY.append(lbl); allN.append(nidx); allT.append(ws)
            prev = feats; hist.append(feats); n_win += 1
            ws += stride
        if n_win >= 50:
            dates.append(ds); nidx += 1
        elif n_win > 0:
            allF = allF[:-n_win]; allY = allY[:-n_win]; allN = allN[:-n_win]; allT = allT[:-n_win]
    X = np.array(allF); y = np.array(allY); nid = np.array(allN); ts = np.array(allT)
    names = list(FEATURE_NAMES) + NEW
    print(f"X={X.shape} nights={len(dates)} dist={dict(Counter(y.tolist()))} [{time.time()-t0:.0f}s]")
    np.savez_compressed(Path(__file__).resolve().parent / "dataset_aug.npz",
                        X=X, y=y, night_ids=nid, timestamps=ts,
                        dates=np.array(dates), feature_names=np.array(names))
    print("saved dataset_aug.npz")


if __name__ == "__main__":
    main()
