#!/usr/bin/env python3
"""Build algo12_seq/daily_scores.json — WHOOP official vs OUR computed daily scores.

For each of the ~78 nights in dataset.npz (where we have BOTH sensor data and a
Whoop deep_dive), emit:
  {date, whoop:{recovery,sleep_score,sleep_hours,strain}, ours:{...}}

Sources
-------
- WHOOP recovery/sleep_score/strain: data.loader.load_ground_truth (deep_dive + cycles)
- WHOOP sleep_hours: deep_dive last_night 'hours_of_sleep' card arrow_stat current_stat_text
  ("H:MM"), cross-checked against sum of light/deep/rem stage times.
- OUR recovery/sleep_score/strain: algo4_calibrated via analyze_all.analyze_day (algo id 'algo4')
- OUR sleep_hours: cached hybrid predictions (algo12_seq/preds/hybrid.npy) aligned to
  dataset.npz. Timestamps are 60s-spaced (1-min stride, 2-min overlapping windows),
  so unique time coverage = (#non-awake windows) * 60s. (×120s would double-count.)
"""

import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ALG = HERE.parent
sys.path.insert(0, str(ALG))

import analyze_all as AA
from data.db_loader import load_from_db
from data.loader import load_ground_truth

DEEP_DIVE = AA.DEEP_DIVE_DIR


def _hhmm_to_hours(s):
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"^\s*(\d+):(\d{1,2})\s*$", s)
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2)) / 60.0


def whoop_sleep_hours(date_str):
    """Whoop total hours of sleep (asleep duration) from deep_dive.

    Primary: 'hours_of_sleep' card -> arrow_stat[0].current_stat_text ("H:MM").
    Fallback: sum of light+deep+rem stage time displays.
    """
    f = DEEP_DIVE / f"{date_str}.json"
    if not f.exists():
        return None
    try:
        data = json.load(f.open())
    except Exception:
        return None

    # Primary: arrow_stat on the hours_of_sleep card
    for section in data.get("last_night", {}).get("sections", []):
        for item in section.get("items", []):
            c = item.get("content", {})
            if c.get("id") == "hours_of_sleep":
                for a in c.get("arrow_stat") or []:
                    h = _hhmm_to_hours(a.get("current_stat_text"))
                    if h is not None:
                        return round(h, 2)

    # Fallback: sum of non-awake stage times
    st = AA.extract_whoop_sleep_stages(date_str)
    if st:
        tot = 0.0
        ok = False
        for k in ("light_time", "deep_time", "rem_time"):
            h = _hhmm_to_hours(st.get(k))
            if h is not None:
                tot += h
                ok = True
        if ok:
            return round(tot, 2)
    return None


def main():
    npz = np.load(HERE / "dataset.npz", allow_pickle=True)
    dates = [str(d) for d in npz["dates"]]          # 78 night dates
    night_ids = npz["night_ids"]
    hybrid = np.load(HERE / "preds" / "hybrid.npy")  # 0=awake,1=light,2=deep,3=rem
    assert len(hybrid) == len(night_ids), "hybrid/night_ids length mismatch"

    STRIDE_SEC = 60  # timestamps are 1-min spaced; unique coverage = windows*stride

    # OUR sleep hours per night via hybrid predictions
    our_sleep_hours = {}
    for ni, dstr in enumerate(dates):
        mask = night_ids == ni
        non_awake = int(np.sum(hybrid[mask] != 0))
        our_sleep_hours[dstr] = round(non_awake * STRIDE_SEC / 3600.0, 2)

    # OUR daily scores via algo4 (reuse analyze_all machinery)
    print("Loading sensor DB for algo4 daily scores...")
    df = load_from_db()

    # Baselines (same method analyze_all.main uses)
    from common.preprocessing import compute_hrv_rmssd
    daily_hrvs, daily_rhrs = [], []
    day_dates = set()
    for d in df["date"].unique():
        if hasattr(d, "year") and 2025 <= d.year <= 2026:
            day_dates.add(str(d))
    for dstr in dates:
        # map back to a date object present in df
        pass
    for day in sorted(d for d in df["date"].unique() if hasattr(d, "year") and 2025 <= d.year <= 2026):
        sw = AA.get_sleep_window(df, day)
        if sw.empty or len(sw) < 300:
            continue
        h = compute_hrv_rmssd(sw, method="sws")
        if h > 10:
            daily_hrvs.append(h)
        sh = sw["hr"][sw["hr"] > 30].values
        if len(sh) > 100:
            daily_rhrs.append((float(np.percentile(sh, 25)) + float(np.median(sh))) / 2)
    hrv_base = float(np.median(daily_hrvs)) if daily_hrvs else 90
    rhr_base = float(np.median(daily_rhrs)) if daily_rhrs else 55
    print(f"Baselines: HRV={hrv_base:.1f} RHR={rhr_base:.1f}")

    # Build a date-object lookup
    df_dates = {str(d): d for d in df["date"].unique() if hasattr(d, "year")}

    print("Computing algo4 daily scores per night...")
    our_scores = {}
    for dstr in dates:
        dobj = df_dates.get(dstr)
        if dobj is None:
            our_scores[dstr] = None
            continue
        res = AA.analyze_day(df, dobj, hrv_base, rhr_base)  # all extra engines None
        if not res:
            our_scores[dstr] = None
            continue
        a4 = next((a for a in res["algos"] if a["id"] == "algo4"), None)
        if a4 is None:
            our_scores[dstr] = None
            continue
        our_scores[dstr] = {
            "recovery": a4["recovery"],
            "sleep_score": a4["sleep_score"],
            "strain": a4["strain"],
        }

    # WHOOP ground truth daily scores
    print("Loading Whoop ground truth...")
    gt = load_ground_truth()
    gt_idx = {}
    if not gt.empty:
        for _, r in gt.iterrows():
            gt_idx[str(r["date"])] = r

    def _num(v):
        try:
            if v is None:
                return None
            f = float(v)
            return f if f == f else None  # filter NaN
        except (ValueError, TypeError):
            return None

    out = []
    for dstr in sorted(dates):
        gr = gt_idx.get(dstr)
        whoop = {
            "recovery": _num(gr.get("recovery_score")) if gr is not None else None,
            "sleep_score": _num(gr.get("sleep_score")) if gr is not None else None,
            "sleep_hours": whoop_sleep_hours(dstr),
            "strain": (_num(gr.get("strain_score")) or _num(gr.get("cycle_strain"))) if gr is not None else None,
        }
        ours = our_scores.get(dstr)
        if ours is None:
            ours = {"recovery": None, "sleep_score": None, "strain": None}
        ours = {
            "recovery": ours["recovery"],
            "sleep_score": ours["sleep_score"],
            "sleep_hours": our_sleep_hours.get(dstr),
            "strain": ours["strain"],
        }
        out.append({"date": dstr, "whoop": whoop, "ours": ours})

    out_path = HERE / "daily_scores.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path} ({len(out)} days)")

    # ---- Summary MAE (only where whoop present) ----
    def mae(metric):
        errs = []
        for e in out:
            w = e["whoop"].get(metric)
            o = e["ours"].get(metric)
            if w is not None and o is not None:
                errs.append(abs(w - o))
        return (sum(errs) / len(errs), len(errs)) if errs else (float("nan"), 0)

    print("\n=== MAE (ours vs whoop, where whoop present) ===")
    for m in ("recovery", "sleep_score", "strain", "sleep_hours"):
        v, n = mae(m)
        print(f"  {m:12s}: MAE={v:6.3f}  (n={n})")

    print("\nFirst 2 entries:")
    print(json.dumps(out[:2], indent=2))

    # verify reload
    json.loads(out_path.read_text())
    print("\nReload OK. Days:", len(out))


if __name__ == "__main__":
    main()
