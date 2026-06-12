"""Run the pretrained SleepECG (wrn-gru-mesa) on our 78 nights — a real external
HRV-based baseline. It is 3-class (Wake/REM/NREM); we evaluate it honestly on
the 3-class problem (merging our Light+Deep -> NREM) and also store a 4-class
display mapping (NREM->light colour) for the dashboard strip.
"""
import sys
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sleepecg
from data.db_loader import load_from_db
from algo5_ml.features import _parse_deep_dive_sleep_bounds
from sklearn.metrics import confusion_matrix

HERE = Path(__file__).resolve().parent
# our 4-class -> 3-class: 0 awake->WAKE(0), 1 light & 2 deep -> NREM(2), 3 rem->REM(1)
TO3 = {0: 0, 1: 2, 2: 2, 3: 1}
NAME3 = {0: "wake", 1: "rem", 2: "nrem"}


def main():
    d = np.load(HERE / "dataset.npz", allow_pickle=True)
    y, nid, ts = d["y"], d["night_ids"], d["timestamps"]
    dates = list(d["dates"])
    clf = sleepecg.load_classifier("wrn-gru-mesa", "SleepECG")
    df = load_from_db()

    pred3 = np.full(len(y), -1)   # sleepecg native 3-class per our window
    order = sorted(set(nid))
    for k, n in enumerate(order):
        date = dates[k]
        second_gt, start, end = _parse_deep_dive_sleep_bounds(date)
        if start is None:
            continue
        seg = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)].sort_values("timestamp")
        tcol = seg["timestamp"].values.astype(float)
        hrc = seg["hr"].values.astype(float)
        m = (hrc > 30) & (hrc < 200)
        tcol, hrc = tcol[m], hrc[m]
        if len(tcol) < 100:
            continue
        # reconstruct heartbeat times by integrating instantaneous HR (beats/sec=hr/60)
        t0 = tcol[0]
        trel = tcol - t0
        rate = hrc / 60.0
        cumbeats = np.concatenate([[0], np.cumsum((rate[:-1] + rate[1:]) / 2 * np.diff(trel))])
        total = int(cumbeats[-1])
        if total < 100:
            continue
        beats = np.interp(np.arange(1, total + 1), cumbeats, trel)  # times of each beat (s from t0)
        import datetime as _dt
        rst = _dt.datetime.utcfromtimestamp(int(t0) + 3600)  # naive local (UTC+1)
        rec = sleepecg.SleepRecord(heartbeat_times=beats,
                                   recording_start_time=rst,
                                   sleep_stage_duration=30)
        try:
            stages = sleepecg.stage(clf, rec, return_mode="int")  # 0 wake,1 rem,2 nrem (per 30s)
        except Exception as e:
            print("  skip", date, e); continue
        stages = np.asarray(stages)
        # map each of our 2-min windows to majority sleepecg epoch
        idx = np.where(nid == n)[0]
        for i in idx:
            e0 = int((ts[i] - t0) // 30)
            e1 = int((ts[i] + 120 - t0) // 30)
            ep = stages[max(0, e0):max(1, e1)]
            ep = ep[(ep >= 0)]
            if len(ep):
                pred3[i] = Counter(ep.tolist()).most_common(1)[0][0]

    cov = pred3 >= 0
    yt3 = np.array([TO3[v] for v in y])
    print(f"SleepECG coverage: {cov.sum()}/{len(y)} windows ({cov.mean()*100:.0f}%)")
    cm = confusion_matrix(yt3[cov], pred3[cov], labels=[0, 1, 2])
    print("3-class (rows=true wake/rem/nrem, cols=pred):")
    for i in range(3):
        rec = cm[i, i] / cm[i].sum() if cm[i].sum() else 0
        print(f"  {NAME3[i]:5s} recall {rec*100:5.1f}%   " + "  ".join(f"{cm[i,j]:5d}" for j in range(3)))
    acc = (yt3[cov] == pred3[cov]).mean()
    print(f"3-class overall acc: {acc*100:.1f}%")

    # store 4-class display coding (wake->0, rem->3, nrem->1 light colour); uncovered->1
    disp = np.where(pred3 == 0, 0, np.where(pred3 == 1, 3, 1))
    disp[~cov] = 1
    (HERE / "preds").mkdir(exist_ok=True)
    np.save(HERE / "preds" / "sleepecg.npy", disp.astype(int))
    np.save(HERE / "sleepecg_3c.npy", pred3)
    print("saved preds/sleepecg.npy (display) + sleepecg_3c.npy (native 3-class)")


if __name__ == "__main__":
    main()
