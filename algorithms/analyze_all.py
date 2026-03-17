#!/usr/bin/env python3
"""Analyze all days from the companion app DB with all 3 algorithms + Whoop ground truth.
Generates a multi-day comparison dashboard."""

import sys
import json
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from data.db_loader import load_from_db
from data.loader import load_ground_truth
from common.preprocessing import (
    compute_rhr,
    compute_hrv_rmssd,
    compute_hrv_sdnn,
    compute_pnn50,
    compute_respiratory_rate,
    compute_hr_zones,
)
from algo4_calibrated.engine import (
    compute_sleep_rhr,
    compute_sws_hrv,
    compute_whoop_strain,
    classify_sleep_phases,
    compute_sleep_score as a4_sleep_score,
    compute_recovery as a4_recovery,
    compute_respiratory_rate as a4_resp_rate,
)
from algo5_ml.engine import MLScoringEngine
from algo5_ml.features import extract_whoop_timeline as a5_extract_timeline
from algo6_sleep_android.engine import SleepAndroidEngine
from algo7_mihealth.engine import MiHealthEngine
from algo8_yasa.engine import YasaEngine
from algo9_neurokit.engine import NeuroKitEngine
from algo10_sleepecg_full.engine import SleepECGFullEngine

BERLIN = timedelta(hours=1)
DEEP_DIVE_DIR = (
    Path(__file__).resolve().parent.parent
    / "whoop-companion"
    / "data"
    / "whoop_backup"
    / "deep_dive"
)


def extract_whoop_sleep_stages(date_str):
    """Extract sleep stage percentages and durations from deep_dive JSON."""
    f = DEEP_DIVE_DIR / f"{date_str}.json"
    if not f.exists():
        return None
    try:
        data = json.load(f.open())
    except Exception:
        return None

    ln = data.get("last_night", {})
    for section in ln.get("sections", []):
        for item in section.get("items", []):
            content = item.get("content", {})
            for cc in content.get("card_content", []):
                cc_c = cc.get("content", {})
                zones = cc_c.get("heart_rate_zones", [])
                if len(zones) >= 4:
                    result = {}
                    for zone in zones:
                        zid = zone.get("id", "")
                        pct_str = zone.get("bar_graph_tile_percentage_display", "0%")
                        time_str = zone.get("bar_graph_tile_time_display", "0:00")
                        if zid == "AWAKE":
                            result["awake_pct"] = pct_str
                            result["awake_time"] = time_str
                        elif zid == "LIGHT_SLEEP":
                            result["light_pct"] = pct_str
                            result["light_time"] = time_str
                        elif zid == "SWS_SLEEP":
                            result["deep_pct"] = pct_str
                            result["deep_time"] = time_str
                        elif zid == "REM_SLEEP":
                            result["rem_pct"] = pct_str
                            result["rem_time"] = time_str
                    if "deep_time" in result:
                        return result
    return None


def extract_whoop_sleep_phases(date_str):
    """Extract official Whoop sleep stage timeline from deep_dive JSON.

    Uses the per-second ground truth from time_bound_ranges and converts
    to a continuous 2-minute block timeline, matching our prediction grid.
    """
    from algo5_ml.features import _parse_deep_dive_sleep_bounds
    from collections import Counter as _Counter
    from datetime import datetime as _dt

    try:
        second_gt, start_ts, end_ts = _parse_deep_dive_sleep_bounds(date_str)
    except Exception:
        return []
    if not second_gt or start_ts is None:
        return []

    # Build 2-minute blocks with majority-vote phase
    block_size = 120  # 2 minutes, matching WINDOW_SEC
    phases = []
    t = int(start_ts)
    t_end = int(end_ts)
    while t + block_size <= t_end:
        labels = [second_gt.get(s) for s in range(t, t + block_size) if s in second_gt]
        if labels:
            majority = _Counter(labels).most_common(1)[0][0]
        else:
            majority = "light"  # fallback
        time_str = _dt.fromtimestamp(t).strftime("%H:%M")
        phases.append({"time": time_str, "phase": majority})
        t += block_size

    return phases


def classify_phases(df, rhr, window_sec=600):
    """Rule-based sleep phase classification with robust sparse-data handling."""
    phases = []
    for i in range(0, len(df) - window_sec, window_sec):
        chunk = df.iloc[i : i + window_sec]
        t = chunk["datetime_local"].iloc[0]
        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            phases.append(
                {
                    "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
                    "phase": "unknown",
                    "hr": 0,
                }
            )
            continue

        avg_hr = float(np.median(hr_v))
        hr_iqr = (
            float(np.percentile(hr_v, 75) - np.percentile(hr_v, 25))
            if len(hr_v) > 10
            else float(hr_v.std())
            if len(hr_v) > 1
            else 0
        )
        avg_mv = float(mv.mean())
        max_mv = float(mv.max())
        ha = avg_hr - rhr

        local_hrv = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_hrv = float(np.sqrt(np.mean(diffs**2)))

        is_moving = avg_mv > 0.8 or max_mv > 2.4
        if is_moving and ha > 24.7:
            phase = "awake"
        elif ha <= 14.2 and hr_iqr < 5.7 and avg_mv < 2.8:
            phase = "deep"
        elif hr_iqr > 10.2 and avg_mv < 0.7:
            phase = "rem"
        elif local_hrv > 152.6 and avg_mv < 0.7:
            phase = "rem"
        else:
            phase = "light"

        phases.append(
            {
                "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
                "phase": phase,
                "hr": round(avg_hr, 1),
                "movement": round(avg_mv, 3),
                "hrv": round(local_hrv, 1),
            }
        )
    return phases


def ml_classify(df, rhr, window_sec=600):
    """ML-style scoring sleep classification with robust sparse-data handling."""
    phases = []
    for i in range(0, len(df) - window_sec, window_sec):
        chunk = df.iloc[i : i + window_sec]
        t = chunk["datetime_local"].iloc[0]
        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            phases.append(
                {
                    "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
                    "phase": "unknown",
                }
            )
            continue

        avg_hr = float(np.median(hr_v))
        hr_iqr = (
            float(np.percentile(hr_v, 75) - np.percentile(hr_v, 25))
            if len(hr_v) > 10
            else float(hr_v.std())
            if len(hr_v) > 1
            else 0
        )
        avg_mv, max_mv = float(mv.mean()), float(mv.max())
        ha = avg_hr - rhr
        local_hrv = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_hrv = float(np.sqrt(np.mean(diffs**2)))
        is_moving = avg_mv > 0.8 or max_mv > 2.4

        scores = {"deep": 0, "rem": 0, "awake": 0, "light": 0}
        if ha < 0:
            scores["deep"] += 3
        elif ha < 5:
            scores["deep"] += 2
        if hr_iqr < 5:
            scores["deep"] += 2
        if not is_moving and avg_mv < 0.2:
            scores["deep"] += 2
        if local_hrv > 0 and local_hrv < 30:
            scores["deep"] += 1

        if hr_iqr > 10:
            scores["rem"] += 3
        if local_hrv > 80:
            scores["rem"] += 2
        if not is_moving:
            scores["rem"] += 1

        if is_moving and ha > 24.7:
            scores["awake"] += 5
        elif is_moving:
            scores["awake"] += 2
        if ha > 20:
            scores["awake"] += 3

        if 5 <= ha < 15 and not is_moving:
            scores["light"] += 3
        if 5 <= hr_iqr <= 10:
            scores["light"] += 1

        phase = max(scores, key=scores.get)
        phases.append(
            {
                "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
                "phase": phase,
            }
        )
    return phases


def phase_summary(phases):
    total = len(phases)
    if total == 0:
        return {
            "total_min": 0,
            "sleep_min": 0,
            "efficiency": 0,
            "deep_min": 0,
            "light_min": 0,
            "rem_min": 0,
            "awake_min": 0,
            "deep_pct": 0,
            "light_pct": 0,
            "rem_pct": 0,
            "awake_pct": 0,
        }
    c = defaultdict(int)
    for p in phases:
        c[p["phase"]] += 1
    sl = total - c["awake"] - c["unknown"]
    win_min = 10  # 10-min windows for rule-based algos
    return {
        "total_min": total * win_min,
        "sleep_min": sl * win_min,
        "efficiency": round(sl / total * 100, 1),
        "deep_min": c["deep"] * win_min,
        "light_min": c["light"] * win_min,
        "rem_min": c["rem"] * win_min,
        "awake_min": c["awake"] * win_min,
        "deep_pct": round(c["deep"] / total * 100, 1),
        "light_pct": round(c["light"] / total * 100, 1),
        "rem_pct": round(c["rem"] / total * 100, 1),
        "awake_pct": round(c["awake"] / total * 100, 1),
    }


def sleepecg_classify(sleep_df):
    """Run SleepECG on sleep window data, return phase summary dict."""
    default = {
        "total_min": 0,
        "sleep_min": 0,
        "efficiency": 0,
        "deep_min": 0,
        "light_min": 0,
        "rem_min": 0,
        "awake_min": 0,
        "deep_pct": 0,
        "light_pct": 0,
        "rem_pct": 0,
        "awake_pct": 0,
        "raw_stages": None,
    }
    if sleep_df.empty:
        return default

    rr = sleep_df["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 100:
        # Not enough RR data, fall back to rule-based
        rhr = compute_rhr(sleep_df)
        phases = classify_phases(sleep_df, rhr)
        result = phase_summary(phases)
        result["raw_stages"] = None
        return result

    try:
        import sleepecg

        clf = sleepecg.load_classifier("wrn-gru-mesa", classifiers_dir="SleepECG")
        beat_times = np.cumsum(rr) / 1000.0
        record = sleepecg.SleepRecord(heartbeat_times=beat_times)
        stages = sleepecg.stage(clf, record, return_mode="int")

        n = len(stages)
        if n == 0:
            raise RuntimeError("No epochs")

        # wrn-gru-mesa is 3-class: WAKE=0, REM=1, NREM=2
        # Map: NREM includes both deep and light; we split using HR heuristic later
        wake = int(np.sum(stages == 0))
        rem = int(np.sum(stages == 1))
        nrem = int(np.sum(stages == 2))

        # Split NREM into deep/light using a simple ratio (typical: 25% deep, 75% light of NREM)
        deep = int(nrem * 0.25)
        light = nrem - deep
        sleep_n = rem + nrem

        return {
            "total_min": n * 0.5,
            "sleep_min": sleep_n * 0.5,
            "efficiency": round(sleep_n / n * 100, 1) if n > 0 else 0,
            "deep_min": deep * 0.5,
            "light_min": light * 0.5,
            "rem_min": rem * 0.5,
            "awake_min": wake * 0.5,
            "deep_pct": round(deep / n * 100, 1),
            "light_pct": round(light / n * 100, 1),
            "rem_pct": round(rem / n * 100, 1),
            "awake_pct": round(wake / n * 100, 1),
            "raw_stages": stages,
        }
    except Exception as e:
        print(f"    SleepECG failed: {e}, using rule-based fallback")
        rhr = compute_rhr(sleep_df)
        phases = classify_phases(sleep_df, rhr)
        result = phase_summary(phases)
        result["raw_stages"] = None
        return result


def recovery_score(hrv, rhr, sleep_eff, resp, hrv_base=90, rhr_base=55):
    if hrv_base > 0:
        ratio = hrv / hrv_base
        hs = 100 / (1 + math.exp(-4 * (ratio - 0.85)))
    else:
        hs = 50
    rs = max(0, min(100, 50 + (rhr_base - rhr) * 8))
    rp = max(0, (resp - 16) * 5) if resp > 16 else 0
    return max(0, min(100, round(0.60 * hs + 0.25 * rs + 0.15 * sleep_eff - rp)))


def strain_score(hr_series, max_hr=200):
    """Compute strain 0-21 with coverage-adjusted HR zone accumulation.

    Whoop captures HR continuously (~86400 samples/day). Our DB only has ~35-65%
    valid HR. We scale the zone load by (total_samples / valid_samples) to compensate.
    """
    total = len(hr_series)
    hrs = hr_series[hr_series > 30].values
    if len(hrs) == 0:
        return 0

    # Coverage factor: if only 35% of samples have HR, scale load by ~2.8x
    coverage = len(hrs) / total if total > 0 else 1.0
    scale = min(3.0, 1.0 / coverage) if coverage > 0.1 else 1.0

    weights = [1, 2, 4, 8, 16]
    bounds = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    load = 0
    for i in range(5):
        lo, hi = max_hr * bounds[i], max_hr * bounds[i + 1]
        load += np.sum((hrs >= lo) & (hrs < hi)) / 60.0 * weights[i]
    load += np.sum(hrs >= max_hr) / 60.0 * 16

    load *= scale
    return min(21, round(5.5 * math.log(1 + load / 15), 1)) if load > 0 else 0


def get_sleep_window(df, day):
    """Get data for the night before `day` (20:00 prev day to 12:00 day)."""
    prev = day - timedelta(days=1)
    mask = (
        (df["date"] == prev)
        & (
            df["datetime_local"].apply(lambda x: x.hour if hasattr(x, "hour") else 0)
            >= 20
        )
    ) | (
        (df["date"] == day)
        & (
            df["datetime_local"].apply(lambda x: x.hour if hasattr(x, "hour") else 12)
            < 12
        )
    )
    return df[mask]


def analyze_day(
    df, day, hrv_base, rhr_base, a5_engine=None, a6_engine=None, a7_engine=None,
    a8_engine=None, a9_engine=None, a10_engine=None,
):
    """Analyze one day with all algorithms."""
    day_df = df[df["date"] == day]
    sleep_df = get_sleep_window(df, day)

    if day_df.empty:
        return None

    # Physiology: RHR from sleep window (Whoop measures RHR during sleep)
    if not sleep_df.empty:
        sleep_hr = sleep_df["hr"][sleep_df["hr"] > 30]
        if len(sleep_hr) > 100:
            # Use lowest 5-minute average during sleep (Whoop methodology)
            # This is more robust than raw percentiles
            sh = sleep_hr.values
            best = 999.0
            win = 300  # 5 minutes
            for i in range(0, len(sh) - win, 30):
                avg = sh[i : i + win].mean()
                if avg < best:
                    best = avg
            rhr = best if best < 999 else float(np.percentile(sleep_hr, 10))
        else:
            rhr = compute_rhr(day_df)
    else:
        rhr = compute_rhr(day_df)
    valid_hr = day_df["hr"][day_df["hr"] > 30]

    # HRV from sleep window using SWS method (matches Whoop methodology)
    hrv = (
        compute_hrv_rmssd(sleep_df, method="sws")
        if not sleep_df.empty
        else compute_hrv_rmssd(day_df, method="sws")
    )
    sdnn = compute_hrv_sdnn(sleep_df) if not sleep_df.empty else 0
    pnn = compute_pnn50(sleep_df) if not sleep_df.empty else 0
    resp = (
        compute_respiratory_rate(sleep_df)
        if not sleep_df.empty and len(sleep_df) > 60
        else 14.0
    )

    physio = {
        "rhr": round(rhr, 1),
        "hrv_rmssd": round(hrv, 1),
        "sdnn": round(sdnn, 1),
        "pnn50": round(pnn, 1),
        "resp_rate": round(resp, 1),
        "hr_mean": round(float(valid_hr.mean()), 1) if len(valid_hr) > 0 else 0,
        "hr_min": int(valid_hr.min()) if len(valid_hr) > 0 else 0,
        "hr_max": int(valid_hr.max()) if len(valid_hr) > 0 else 0,
        "samples": len(day_df),
        "valid_hr": len(valid_hr),
    }

    # Algo 1: Custom rule-based
    a1_phases = classify_phases(sleep_df, rhr) if not sleep_df.empty else []
    a1_sum = phase_summary(a1_phases)
    a1_rec = recovery_score(hrv, rhr, a1_sum["efficiency"], resp, hrv_base, rhr_base)
    a1_str = strain_score(day_df["hr"])

    # Algo 2: SleepECG-based sleep staging
    a2_result = sleepecg_classify(sleep_df)
    a2_sum = a2_result
    a2_rec = recovery_score(hrv, rhr, a2_sum["efficiency"], resp, hrv_base, rhr_base)
    a2_str = strain_score(day_df["hr"])
    # Build phases list for hypnogram from sleepecg raw stages
    a2_phases = []
    if "raw_stages" in a2_result and a2_result["raw_stages"] is not None:
        # wrn-gru-mesa 3-class: 0=WAKE, 1=REM, 2=NREM
        stage_map = {0: "awake", 1: "rem", 2: "light"}
        stages = a2_result["raw_stages"]
        # Each epoch is 30s; group into 5-min blocks (10 epochs)
        for i in range(0, len(stages), 10):
            block = stages[i : i + 10]
            # Majority vote
            counts = {}
            for s in block:
                p = stage_map.get(int(s), "unknown")
                counts[p] = counts.get(p, 0) + 1
            phase = max(counts, key=counts.get)
            # Estimate time from sleep_df if available
            epoch_min = i * 0.5  # minutes from start
            h = int(epoch_min // 60)
            m = int(epoch_min % 60)
            a2_phases.append({"time": f"{h:02d}:{m:02d}", "phase": phase})
    elif not sleep_df.empty:
        # Fallback: use same rule-based phases
        a2_phases = classify_phases(sleep_df, rhr)

    # Algo 3: ML feature-based
    a3_phases = ml_classify(sleep_df, rhr) if not sleep_df.empty else []
    a3_sum = phase_summary(a3_phases)
    a3_rec = recovery_score(hrv, rhr, a3_sum["efficiency"], resp, hrv_base, rhr_base)
    a3_str = strain_score(day_df["hr"])

    # Algo 4: Whoop-Calibrated
    a4_rhr = compute_sleep_rhr(sleep_df) if not sleep_df.empty else rhr
    a4_hrv = compute_sws_hrv(sleep_df) if not sleep_df.empty else hrv
    a4_resp = (
        a4_resp_rate(sleep_df) if not sleep_df.empty and len(sleep_df) > 60 else 14.0
    )
    a4_phases, a4_sum = (
        classify_sleep_phases(sleep_df, a4_rhr)
        if not sleep_df.empty
        else (
            [],
            {
                "total_min": 0,
                "sleep_min": 0,
                "efficiency": 0,
                "deep_pct": 0,
                "light_pct": 0,
                "rem_pct": 0,
                "awake_pct": 0,
            },
        )
    )
    a4_sleep = a4_sleep_score(a4_sum)
    a4_str = compute_whoop_strain(day_df, len(day_df))
    a4_rec = a4_recovery(a4_hrv, a4_rhr, a4_sleep, a4_resp, hrv_base, rhr_base)

    # Algo 5: ML GradientBoosting (sleep phases + daily scores)
    a5_algo = {
        "name": "ML GBoosting",
        "id": "algo5",
        "recovery": 50,
        "sleep_score": 50,
        "strain": 0,
        "sleep": {
            "total_min": 0,
            "sleep_min": 0,
            "efficiency": 0,
            "deep_pct": 0,
            "light_pct": 0,
            "rem_pct": 0,
            "awake_pct": 0,
            "deep_min": 0,
            "light_min": 0,
            "rem_min": 0,
            "awake_min": 0,
        },
        "phases": [],
    }
    if a5_engine and a5_engine._is_trained and not sleep_df.empty:
        # Get sleep start/end timestamps for temporal features
        _, a5_start, a5_end = a5_extract_timeline(str(day))
        a5_phases, a5_sum = a5_engine.classify_sleep(
            sleep_df, rhr, sleep_start_ts=a5_start, sleep_end_ts=a5_end
        )
        a5_scores = a5_engine.compute(df, day)
        a5_algo = {
            "name": "ML GBoosting",
            "id": "algo5",
            "recovery": a5_scores.recovery,
            "sleep_score": round(a5_sum["efficiency"]),
            "strain": a5_scores.strain,
            "sleep": a5_sum,
            "phases": a5_phases,
            "hrv": a5_scores.hrv_ms,
            "rhr": a5_scores.rhr_bpm,
            "resp": a5_scores.resp_rate,
        }

    # Algo 6: Sleep as Android (reverse-engineered actigraphy via HR proxy)
    a6_algo = {
        "name": "Sleep as Android",
        "id": "algo6",
        "recovery": 50,
        "sleep_score": 0,
        "strain": 0,
        "sleep": {
            "total_min": 0,
            "sleep_min": 0,
            "efficiency": 0,
            "deep_pct": 0,
            "light_pct": 0,
            "rem_pct": 0,
            "awake_pct": 0,
            "deep_min": 0,
            "light_min": 0,
            "rem_min": 0,
            "awake_min": 0,
        },
        "phases": [],
    }
    if a6_engine is not None and not sleep_df.empty:
        try:
            _, a6_start, a6_end = a5_extract_timeline(str(day))
            a6_phases, a6_sum = a6_engine.classify_sleep(
                sleep_df,
                rhr,
                sleep_start_ts=a6_start,
                sleep_end_ts=a6_end,
            )
            a6_algo = {
                "name": "Sleep as Android",
                "id": "algo6",
                "recovery": 50,
                "sleep_score": round(a6_sum["efficiency"]),
                "strain": 0,
                "sleep": a6_sum,
                "phases": a6_phases,
            }
        except Exception as e:
            print(f"    algo6 failed: {e}")

    # Algo 7: Mi Health (reverse-engineered Xiaomi phone sleep trace via HR proxy)
    a7_algo = {
        "name": "Mi Health (Xiaomi)",
        "id": "algo7",
        "recovery": 50,
        "sleep_score": 0,
        "strain": 0,
        "sleep": {
            "total_min": 0,
            "sleep_min": 0,
            "efficiency": 0,
            "deep_pct": 0,
            "light_pct": 0,
            "rem_pct": 0,
            "awake_pct": 0,
            "deep_min": 0,
            "light_min": 0,
            "rem_min": 0,
            "awake_min": 0,
        },
        "phases": [],
    }
    if a7_engine is not None and not sleep_df.empty:
        try:
            a7_phases, a7_sum = a7_engine.classify_sleep(sleep_df, rhr)
            a7_algo = {
                "name": "Mi Health (Xiaomi)",
                "id": "algo7",
                "recovery": 50,
                "sleep_score": round(a7_sum["efficiency"]),
                "strain": 0,
                "sleep": a7_sum,
                "phases": a7_phases,
            }
        except Exception as e:
            print(f"    algo7 failed: {e}")

    # Algo 8: YASA-inspired (HRV spectral analysis)
    a8_algo = {
        "name": "YASA Spectral",
        "id": "algo8",
        "recovery": 50,
        "sleep_score": 0,
        "strain": 0,
        "sleep": {
            "total_min": 0, "sleep_min": 0, "efficiency": 0,
            "deep_pct": 0, "light_pct": 0, "rem_pct": 0, "awake_pct": 0,
            "deep_min": 0, "light_min": 0, "rem_min": 0, "awake_min": 0,
        },
        "phases": [],
    }
    if a8_engine is not None and not sleep_df.empty:
        try:
            a8_phases, a8_sum = a8_engine.classify_sleep(sleep_df, rhr)
            a8_rec = recovery_score(hrv, rhr, a8_sum["efficiency"], resp, hrv_base, rhr_base)
            a8_algo = {
                "name": "YASA Spectral",
                "id": "algo8",
                "recovery": a8_rec,
                "sleep_score": round(a8_sum["efficiency"]),
                "strain": strain_score(day_df["hr"]),
                "sleep": a8_sum,
                "phases": a8_phases,
            }
        except Exception as e:
            print(f"    algo8 failed: {e}")

    # Algo 9: NeuroKit2 (advanced HRV nonlinear features)
    a9_algo = {
        "name": "NeuroKit2 HRV",
        "id": "algo9",
        "recovery": 50,
        "sleep_score": 0,
        "strain": 0,
        "sleep": {
            "total_min": 0, "sleep_min": 0, "efficiency": 0,
            "deep_pct": 0, "light_pct": 0, "rem_pct": 0, "awake_pct": 0,
            "deep_min": 0, "light_min": 0, "rem_min": 0, "awake_min": 0,
        },
        "phases": [],
    }
    if a9_engine is not None and not sleep_df.empty:
        try:
            a9_phases, a9_sum = a9_engine.classify_sleep(sleep_df, rhr)
            a9_rec = recovery_score(hrv, rhr, a9_sum["efficiency"], resp, hrv_base, rhr_base)
            a9_algo = {
                "name": "NeuroKit2 HRV",
                "id": "algo9",
                "recovery": a9_rec,
                "sleep_score": round(a9_sum["efficiency"]),
                "strain": strain_score(day_df["hr"]),
                "sleep": a9_sum,
                "phases": a9_phases,
            }
        except Exception as e:
            print(f"    algo9 failed: {e}")

    # Algo 10: Full SleepECG (ML + HR-based NREM splitting + cycle detection)
    a10_algo = {
        "name": "SleepECG Full",
        "id": "algo10",
        "recovery": 50,
        "sleep_score": 0,
        "strain": 0,
        "sleep": {
            "total_min": 0, "sleep_min": 0, "efficiency": 0,
            "deep_pct": 0, "light_pct": 0, "rem_pct": 0, "awake_pct": 0,
            "deep_min": 0, "light_min": 0, "rem_min": 0, "awake_min": 0,
        },
        "phases": [],
    }
    if a10_engine is not None and not sleep_df.empty:
        try:
            a10_phases, a10_sum = a10_engine.classify_sleep(sleep_df, rhr)
            a10_rec = recovery_score(hrv, rhr, a10_sum["efficiency"], resp, hrv_base, rhr_base)
            a10_algo = {
                "name": "SleepECG Full",
                "id": "algo10",
                "recovery": a10_rec,
                "sleep_score": round(a10_sum["efficiency"]),
                "strain": strain_score(day_df["hr"]),
                "sleep": a10_sum,
                "phases": a10_phases,
            }
            # Add cycle info
            if a10_sum.get("sleep_cycles"):
                a10_algo["sleep_cycles"] = a10_sum["sleep_cycles"]
                a10_algo["avg_cycle_min"] = a10_sum.get("avg_cycle_min", 0)
        except Exception as e:
            print(f"    algo10 failed: {e}")

    # HR timeseries (30s resolution for chart)
    ts_chart = []
    for i in range(0, len(day_df), 30):
        chunk = day_df.iloc[i : min(i + 30, len(day_df))]
        hr_c = chunk["hr"].values
        hr_v = hr_c[hr_c > 30]
        t = chunk["datetime_local"].iloc[0]
        entry = {
            "t": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
            "hr": round(float(hr_v.mean()), 1) if len(hr_v) > 0 else 0,
            "mv": round(float(chunk["movement"].mean()), 3),
        }
        ts_chart.append(entry)

    return {
        "date": str(day),
        "physio": physio,
        "algos": [
            {
                "name": "Custom Rule-Based",
                "id": "algo1",
                "recovery": a1_rec,
                "sleep_score": round(a1_sum["efficiency"]),
                "strain": a1_str,
                "sleep": a1_sum,
                "phases": a1_phases,
            },
            {
                "name": "SleepECG Hybrid",
                "id": "algo2",
                "recovery": a2_rec,
                "sleep_score": round(a2_sum["efficiency"]),
                "strain": a2_str,
                "sleep": a2_sum,
                "phases": a2_phases,
            },
            {
                "name": "ML Feature-Based",
                "id": "algo3",
                "recovery": a3_rec,
                "sleep_score": round(a3_sum["efficiency"]),
                "strain": a3_str,
                "sleep": a3_sum,
                "phases": a3_phases,
            },
            {
                "name": "Whoop-Calibrated",
                "id": "algo4",
                "recovery": a4_rec,
                "sleep_score": a4_sleep,
                "strain": a4_str,
                "sleep": a4_sum,
                "phases": a4_phases,
                "hrv": round(a4_hrv, 1),
                "rhr": round(a4_rhr, 1),
                "resp": round(a4_resp, 1),
            },
            a5_algo,
            a6_algo,
            a7_algo,
            a8_algo,
            a9_algo,
            a10_algo,
        ],
        "timeseries": ts_chart,
    }


def main():
    print("=" * 70)
    print("  WHOOP FULL ANALYSIS — All Days, All Algorithms")
    print("=" * 70)

    print("\nLoading DB...")
    df = load_from_db()
    if df.empty:
        print("No data!")
        return

    print("Loading ground truth...")
    gt_df = load_ground_truth()
    if gt_df.empty:
        gt_df = pd.DataFrame(columns=["date", "recovery_score", "sleep_score", "strain_score", "hrv_ms", "rhr_bpm", "resp_rate"])
    # Add sample day from Flutter app API cache
    if "date" not in gt_df.columns or "2025-01-15" not in gt_df["date"].values:
        extra_gt = pd.DataFrame(
            [
                {
                    "date": "2025-01-15",
                    "recovery_score": 45,
                    "sleep_score": 78,
                    "strain_score": 4.9,
                    "hrv_ms": 80,
                    "rhr_bpm": 62,
                    "resp_rate": 13.4,
                }
            ]
        )
        gt_df = pd.concat([gt_df, extra_gt], ignore_index=True)

    # Load detailed Whoop official data (sleep stages, etc.)
    whoop_official = {}
    official_file = Path(__file__).parent / "data" / "raw" / "whoop_official.json"
    if official_file.exists():
        whoop_official = json.load(official_file.open())
        print(f"Loaded Whoop official data for: {list(whoop_official.keys())}")

    days = sorted(
        d for d in df["date"].unique() if hasattr(d, "year") and 2025 <= d.year <= 2026
    )
    print(f"\nDays available: {days}")

    # Compute baselines: use per-day SWS HRV, then take median as baseline
    daily_hrvs = []
    daily_rhrs = []
    for day in days:
        sw = get_sleep_window(df, day)
        if sw.empty or len(sw) < 300:
            continue
        day_hrv = compute_hrv_rmssd(sw, method="sws")
        if day_hrv > 10:
            daily_hrvs.append(day_hrv)
        # RHR: use algo4 method (mean of P25 and median of sleep HR)
        sh = sw["hr"][sw["hr"] > 30].values
        if len(sh) > 100:
            daily_rhrs.append((float(np.percentile(sh, 25)) + float(np.median(sh))) / 2)
    hrv_base = float(np.median(daily_hrvs)) if daily_hrvs else 90
    rhr_base = float(np.median(daily_rhrs)) if daily_rhrs else 55
    print(f"Baselines: HRV={hrv_base:.1f}ms (median daily SWS), RHR={rhr_base:.1f}bpm")

    # Train algo5 ML model
    print("\nTraining algo5 ML sleep phase classifier...")
    a5_engine = MLScoringEngine()
    try:
        from algo5_ml.features import build_training_data

        X, y, night_ids, night_dates = build_training_data(df)
        if len(X) > 0:
            a5_engine.train_phase_model(X, y, night_ids=night_ids)
            a5_engine.train_score_models(df, gt_df)
            print(
                f"  algo5: trained on {len(X)} windows from {len(night_dates)} nights"
            )
        else:
            print("  algo5: no training data, skipping")
    except Exception as e:
        print(f"  algo5 training failed: {e}")

    # Initialize algo6 (Sleep as Android)
    print("\nInitializing algo6 (Sleep as Android reverse-engineered)...")
    a6_engine = SleepAndroidEngine()
    print("  algo6: ready (no training needed — rule-based actigraphy)")

    # Initialize algo7 (Mi Health)
    print("\nInitializing algo7 (Mi Health / Xiaomi reverse-engineered)...")
    a7_engine = MiHealthEngine()
    print("  algo7: ready (no training needed — rule-based phone sleep trace)")

    # Initialize algo8 (YASA Spectral)
    print("\nInitializing algo8 (YASA Spectral HRV analysis)...")
    a8_engine = YasaEngine()
    print("  algo8: ready (YASA-inspired spectral HRV sleep staging)")

    # Initialize algo9 (NeuroKit2)
    print("\nInitializing algo9 (NeuroKit2 advanced HRV)...")
    a9_engine = NeuroKitEngine()
    print("  algo9: ready (NeuroKit2 nonlinear HRV features)")

    # Initialize algo10 (Full SleepECG)
    print("\nInitializing algo10 (Full SleepECG pipeline)...")
    a10_engine = SleepECGFullEngine()
    print("  algo10: ready (SleepECG ML + HR-based NREM split + cycle detection)")

    # Analyze each day
    day_results = []
    for day in days:
        print(f"\nAnalyzing {day}...")
        result = analyze_day(
            df,
            day,
            hrv_base,
            rhr_base,
            a5_engine=a5_engine,
            a6_engine=a6_engine,
            a7_engine=a7_engine,
            a8_engine=a8_engine,
            a9_engine=a9_engine,
            a10_engine=a10_engine,
        )
        if result:
            # Add ground truth
            gt_row = gt_df[gt_df["date"] == str(day)]
            if not gt_row.empty:
                r = gt_row.iloc[0]
                result["whoop"] = {
                    "recovery": r.get("recovery_score"),
                    "sleep": r.get("sleep_score"),
                    "strain": r.get("strain_score", r.get("cycle_strain")),
                    "hrv_ms": r.get("hrv_ms"),
                    "rhr_bpm": r.get("rhr_bpm"),
                    "resp_rate": r.get("resp_rate"),
                }
            else:
                result["whoop"] = None

            # Merge detailed Whoop official data (sleep stages etc.)
            wo = whoop_official.get(str(day), {})
            if wo:
                if result["whoop"] is None:
                    result["whoop"] = {}

                def _to_num(v):
                    """Convert string values to float, return None for '--' or invalid."""
                    if v is None or v == "--" or v == "":
                        return None
                    try:
                        return float(str(v).replace("%", ""))
                    except (ValueError, TypeError):
                        return None

                # Override with more detailed data if available
                if wo.get("recovery") and wo["recovery"] != "--":
                    result["whoop"]["recovery"] = _to_num(wo["recovery"])
                if wo.get("sleep_score"):
                    result["whoop"]["sleep"] = _to_num(wo["sleep_score"])
                if wo.get("strain") and wo["strain"] != "--":
                    result["whoop"]["strain"] = _to_num(wo["strain"])
                if wo.get("hrv_ms") and wo["hrv_ms"] != "--":
                    result["whoop"]["hrv_ms"] = _to_num(wo["hrv_ms"])
                if wo.get("rhr_bpm") and wo["rhr_bpm"] != "--":
                    result["whoop"]["rhr_bpm"] = _to_num(wo["rhr_bpm"])
                if wo.get("resp_rate") and wo["resp_rate"] != "--":
                    result["whoop"]["resp_rate"] = _to_num(wo["resp_rate"])
                # Sleep stages
                result["whoop"]["sleep_stages"] = {
                    "duration": wo.get("sleep_duration"),
                    "awake_pct": wo.get("sleep_awake_pct"),
                    "awake_time": wo.get("sleep_awake_time"),
                    "light_pct": wo.get("sleep_light_sleep_pct"),
                    "light_time": wo.get("sleep_light_sleep_time"),
                    "deep_pct": wo.get("sleep_sws_sleep_pct"),
                    "deep_time": wo.get("sleep_sws_sleep_time"),
                    "rem_pct": wo.get("sleep_rem_sleep_pct"),
                    "rem_time": wo.get("sleep_rem_sleep_time"),
                    "restorative": wo.get("restorative_sleep"),
                    "efficiency": wo.get("efficiency"),
                    "consistency": wo.get("consistency"),
                    "stress": wo.get("sleep_stress"),
                }
            # Extract Whoop sleep stages from deep_dive (fallback if whoop_official.json missing)
            if result["whoop"] is not None:
                if not result["whoop"].get("sleep_stages", {}).get("deep_time"):
                    dd_stages = extract_whoop_sleep_stages(str(day))
                    if dd_stages:
                        result["whoop"]["sleep_stages"] = dd_stages

                # Extract Whoop official sleep phase timeline from deep_dive
                whoop_phases = extract_whoop_sleep_phases(str(day))
                if whoop_phases:
                    result["whoop"]["phases"] = whoop_phases
            day_results.append(result)

    # Filter out days with no useful data (no sleep phases from any algo)
    day_results = [
        dr for dr in day_results
        if any(len(a.get("phases", [])) > 0 for a in dr["algos"])
        or dr.get("whoop")
    ]

    # Print comparison table
    print("\n" + "=" * 100)
    print(
        f"  {'Date':>12} │ {'Source':>16} │ {'Recovery':>8} │ {'Sleep':>8} │ {'Strain':>8} │ {'HRV':>6} │ {'RHR':>5}"
    )
    print("  " + "─" * 90)

    for dr in day_results:
        d = dr["date"]
        if dr["whoop"]:
            w = dr["whoop"]
            print(
                f"  {d:>12} │ {'WHOOP':>16} │ {w.get('recovery') or '-':>8} │ {w.get('sleep') or '-':>8} │ {w.get('strain') or '-':>8} │ {w.get('hrv_ms') or '-':>6} │ {w.get('rhr_bpm') or '-':>5}"
            )

        for a in dr["algos"]:
            h = a.get("hrv", dr["physio"]["hrv_rmssd"])
            r = a.get("rhr", dr["physio"]["rhr"])
            print(
                f"  {'':>12} │ {a['name'][:16]:>16} │ {a['recovery']:>8} │ {a['sleep_score']:>8} │ {a['strain']:>8} │ {h:>6} │ {r:>5}"
            )
        print("  " + "─" * 90)

    # Generate HTML dashboard
    dashboard = {
        "days": day_results,
        "baselines": {"hrv": round(hrv_base, 1), "rhr": round(rhr_base, 1)},
        "total_records": len(df),
    }

    html = generate_html(dashboard)
    out = Path(__file__).parent / "full_dashboard.html"
    out.write_text(html)
    print(f"\nDashboard: file://{out.resolve()}")


def generate_html(data):
    dj = json.dumps(data, default=str, separators=(",", ":"))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Open Whoop — Dashboard</title>
<style>
:root{{--bg:#0a0a0a;--card:#141414;--card2:#1a1a1a;--border:#222;--border2:#2a2a2a;--text:#e0e0e0;--dim:#777;--dim2:#555;--green:#44cf6c;--green2:#44cf6c22;--pink:#e91e63;--pink2:#e91e6322;--yellow:#f5c542;--red:#e74c3c;--blue:#3498db;--deep:#1a237e;--light-s:#42a5f5;--rem:#ab47bc;--awake:#ff7043}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased}}
.container{{max-width:1400px;margin:0 auto;padding:20px 24px}}
.header{{text-align:center;padding:28px 0 20px;margin-bottom:24px}}
.header h1{{font-size:20px;font-weight:700;letter-spacing:4px;text-transform:uppercase}}
.header h1 .g{{color:var(--green)}}.header h1 .p{{color:var(--pink)}}
.header .meta{{color:var(--dim);font-size:11px;margin-top:8px;letter-spacing:0.5px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px;margin-bottom:16px}}
.card h2{{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:14px}}
.day-nav{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:20px;justify-content:center}}
.day-btn{{background:var(--card);border:1px solid var(--border);color:var(--dim);padding:8px 18px;border-radius:10px;cursor:pointer;font-size:12px;font-weight:500;transition:all .2s}}
.day-btn:hover{{border-color:var(--dim2);color:var(--text)}}
.day-btn.active{{border-color:var(--green);color:var(--green);background:var(--green2)}}

/* Recovery rings */
.ring-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:500px){{.ring-grid{{grid-template-columns:1fr}}}}
.ring-card{{background:var(--card2);border:1px solid var(--border);border-radius:14px;padding:24px 16px;text-align:center}}
.ring-card .label{{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:16px;font-weight:600}}
.ring-wrap{{position:relative;width:120px;height:120px;margin:0 auto 12px}}
.ring-wrap svg{{width:120px;height:120px;transform:rotate(-90deg)}}
.ring-wrap .val{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:36px;font-weight:700}}
.ring-wrap .pct{{font-size:14px;font-weight:400;color:var(--dim)}}

/* Score row */
.score-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:2px}}
@media(max-width:600px){{.score-grid{{grid-template-columns:repeat(3,1fr)}}}}
.score-cell{{text-align:center;padding:14px 4px;background:var(--card2);border-radius:10px}}
.score-cell:first-child{{border-radius:10px 4px 4px 10px}}
.score-cell:last-child{{border-radius:4px 10px 10px 4px}}
.score-cell .lbl{{font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:6px}}
.score-cell .w{{font-size:22px;font-weight:700;color:var(--green)}}
.score-cell .o{{font-size:22px;font-weight:700;color:var(--pink)}}
.score-cell .vs{{font-size:9px;color:var(--dim);margin-top:2px}}
.score-cell .diff{{font-size:10px;margin-top:2px}}
.diff.pos{{color:var(--green)}}.diff.neg{{color:var(--red)}}.diff.neutral{{color:var(--dim)}}

/* Sleep bars */
.sleep-compare{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
.sleep-col .src{{font-size:10px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;font-weight:600}}
.sleep-bar{{display:flex;height:20px;border-radius:10px;overflow:hidden;margin-bottom:6px}}
.sleep-bar .s{{transition:width .4s ease}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;font-size:10px}}
.legend .d{{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:3px;vertical-align:middle}}
.legend .dur{{color:var(--dim);margin-left:2px}}

/* Timeline */
.tl-row{{margin-bottom:18px}}
.tl-label{{font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;font-weight:600}}
.tl-track{{height:40px;background:var(--card2);border-radius:8px;position:relative;overflow:hidden}}
.tl-seg{{position:absolute;height:100%;border-radius:3px}}
.tl-detail{{display:flex;flex-wrap:wrap;gap:4px 10px;margin-top:5px}}
.tl-detail span{{font-size:11px;color:var(--dim)}}
.tl-axis{{display:flex;justify-content:space-between;font-size:11px;color:var(--dim2);margin-top:10px}}

/* Other algos collapsible */
.other-algos{{margin-top:4px}}
.other-algos summary{{cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);padding:10px 0;list-style:none;display:flex;align-items:center;gap:6px}}
.other-algos summary::-webkit-details-marker{{display:none}}
.other-algos summary::before{{content:'\\25B6';font-size:8px;transition:transform .2s}}
.other-algos[open] summary::before{{transform:rotate(90deg)}}
.other-algos .inner{{padding-top:12px}}
.compare-row{{display:grid;grid-template-columns:130px repeat(7,1fr);gap:4px;align-items:center;padding:6px 0;border-bottom:1px solid #1a1a1a;font-size:12px}}
.compare-row.hdr{{font-weight:600;color:var(--dim);font-size:9px;text-transform:uppercase}}
.tag{{display:inline-block;padding:2px 8px;border-radius:5px;font-size:9px;font-weight:600}}
</style></head><body>
<div class="container">
<div class="header">
  <h1><span class="g">OPEN</span> <span class="p">WHOOP</span></h1>
  <div class="meta" id="meta"></div>
</div>
<div class="day-nav" id="nav"></div>
<div id="content"></div>
</div>
<script>
const D={dj};
const recCol=v=>v>=67?'#44cf6c':v>=34?'#f5c542':'#e74c3c';
const pC={{deep:'#1a237e',light:'#42a5f5',rem:'#ab47bc',awake:'#ff7043',unknown:'#333'}};
const aC=['#3498db','#9b59b6','#ff9800','#2ecc71','#e91e63'];

document.getElementById('meta').textContent=D.total_records.toLocaleString()+' sensor records  ·  Baselines: HRV '+D.baselines.hrv+'ms, RHR '+D.baselines.rhr+'bpm';

const nav=document.getElementById('nav');
const content=document.getElementById('content');

function fmtMin(m){{if(!m&&m!==0)return'-';const h=Math.floor(m/60),mm=Math.round(m%60);return h>0?h+'h'+String(mm).padStart(2,'0')+'m':mm+'m';}}
function pT(s){{if(!s)return 0;const p=s.split(':');return parseInt(p[0])*60+parseInt(p[1]);}}
function diffBadge(ours,theirs){{
  if(ours==null||theirs==null)return'';
  const d=ours-theirs;if(Math.abs(d)<0.5)return'<div class="diff neutral">=</div>';
  return d>0?`<div class="diff pos">+${{Math.abs(d).toFixed(1)}}</div>`:`<div class="diff neg">${{d.toFixed(1)}}</div>`;
}}
function ring(val,color,size){{
  size=size||120;const r=50;const c=2*Math.PI*r;const pct=Math.min(Math.max(val||0,0),100);
  const dash=c*pct/100;const gap=c-dash;
  return `<div class="ring-wrap" style="width:${{size}}px;height:${{size}}px">
    <svg viewBox="0 0 120 120" style="width:${{size}}px;height:${{size}}px">
      <circle cx="60" cy="60" r="${{r}}" fill="none" stroke="#222" stroke-width="8"/>
      <circle cx="60" cy="60" r="${{r}}" fill="none" stroke="${{color}}" stroke-width="8"
        stroke-dasharray="${{dash}} ${{gap}}" stroke-linecap="round"/>
    </svg>
    <div class="val" style="color:${{color}}">${{val||'-'}}<span class="pct">%</span></div>
  </div>`;
}}

function render(idx){{
  const d=D.days[idx];
  nav.innerHTML=D.days.map((dy,i)=>`<button class="day-btn ${{i===idx?'active':''}}" onclick="render(${{i}})">${{dy.date}}</button>`).join('');

  // Find named algorithms
  const a5=d.algos.find(a=>a.id==='algo5')||d.algos[d.algos.length-1];
  const a6=d.algos.find(a=>a.id==='algo6');
  const a8=d.algos.find(a=>a.id==='algo8');
  const a9=d.algos.find(a=>a.id==='algo9');
  const a10=d.algos.find(a=>a.id==='algo10');
  // Primary algo = YASA Spectral
  const primary=a8||a5;
  const others=d.algos.filter(a=>a!==primary);
  const w=d.whoop||{{}};
  const wRec=parseFloat(w.recovery)||0;
  const pRec=parseFloat(primary.recovery)||0;
  const ss=w.sleep_stages||{{}};

  let h='';

  function sleepBarHtml(label,color,deep,light,rem,awake){{
    const tp=(deep||0)+(light||0)+(rem||0)+(awake||0)||1;
    return `<div class="sleep-col"><div class="src" style="color:${{color}}">${{label}}</div>
      <div class="sleep-bar">
        <div class="s" style="width:${{(deep||0)/tp*100}}%;background:var(--deep)"></div>
        <div class="s" style="width:${{(light||0)/tp*100}}%;background:var(--light-s)"></div>
        <div class="s" style="width:${{(rem||0)/tp*100}}%;background:var(--rem)"></div>
        <div class="s" style="width:${{(awake||0)/tp*100}}%;background:var(--awake)"></div>
      </div>
      <div class="legend">
        <span><span class="d" style="background:var(--deep)"></span>Deep<span class="dur">${{fmtMin(deep)}}</span></span>
        <span><span class="d" style="background:var(--light-s)"></span>Light<span class="dur">${{fmtMin(light)}}</span></span>
        <span><span class="d" style="background:var(--rem)"></span>REM<span class="dur">${{fmtMin(rem)}}</span></span>
        <span><span class="d" style="background:var(--awake)"></span>Awake<span class="dur">${{fmtMin(awake)}}</span></span>
      </div></div>`;
  }}

  // === 1. Recovery Rings ===
  h+=`<details class="other-algos" open><summary>Recovery — ${{d.date}}</summary><div class="inner"><div class="card">
    <div class="ring-grid">
      <div class="ring-card" style="border-color:var(--green2)">
        <div class="label" style="color:var(--green)">Whoop Official</div>
        ${{ring(wRec,recCol(wRec))}}
      </div>
      <div class="ring-card" style="border-color:#06b6d422">
        <div class="label" style="color:#06b6d4">YASA Spectral (Ours)</div>
        ${{ring(pRec,recCol(pRec))}}
      </div>
    </div>
  </div></div></details>`;

  // === 2. Score Comparison Row ===
  const phrv=primary.hrv||d.physio.hrv_rmssd;
  const prhr=primary.rhr||d.physio.rhr;
  h+=`<details class="other-algos" open><summary>Scores — Whoop vs YASA</summary><div class="inner"><div class="card">
    <div class="score-grid">
      <div class="score-cell">
        <div class="lbl">Recovery</div>
        <div class="w">${{wRec||'-'}}</div>
        <div class="vs">vs</div>
        <div class="o">${{pRec||'-'}}</div>
        ${{diffBadge(pRec,wRec)}}
      </div>
      <div class="score-cell">
        <div class="lbl">Sleep</div>
        <div class="w">${{w.sleep||'-'}}</div>
        <div class="vs">vs</div>
        <div class="o">${{primary.sleep_score||'-'}}</div>
        ${{diffBadge(primary.sleep_score,w.sleep)}}
      </div>
      <div class="score-cell">
        <div class="lbl">Strain</div>
        <div class="w">${{w.strain||'-'}}</div>
        <div class="vs">vs</div>
        <div class="o">${{primary.strain||'-'}}</div>
        ${{diffBadge(primary.strain,w.strain)}}
      </div>
      <div class="score-cell">
        <div class="lbl">HRV</div>
        <div class="w">${{w.hrv_ms||'-'}}<span style="font-size:11px;color:var(--dim)">ms</span></div>
        <div class="vs">vs</div>
        <div class="o">${{phrv||'-'}}<span style="font-size:11px;color:var(--dim)">ms</span></div>
      </div>
      <div class="score-cell">
        <div class="lbl">RHR</div>
        <div class="w">${{w.rhr_bpm||'-'}}<span style="font-size:11px;color:var(--dim)">bpm</span></div>
        <div class="vs">vs</div>
        <div class="o">${{prhr||'-'}}<span style="font-size:11px;color:var(--dim)">bpm</span></div>
      </div>
    </div>
  </div></div></details>`;

  // === 3. Sleep Stages Bars — Whoop vs YASA (always open) ===
  h+=`<details class="other-algos" open><summary>Sleep Stages — Whoop vs YASA</summary><div class="inner"><div class="card"><div class="sleep-compare">`;

  // Whoop side
  if(ss.deep_time){{
    h+=sleepBarHtml('Whoop Official','var(--green)',pT(ss.deep_time),pT(ss.light_time),pT(ss.rem_time),pT(ss.awake_time));
  }}else{{
    h+=`<div class="sleep-col"><div class="src" style="color:var(--green)">Whoop Official</div><div style="color:var(--dim);font-size:11px;padding:8px 0">No sleep stage data</div></div>`;
  }}

  // YASA side
  if(primary.sleep&&primary.sleep.total_min>0){{
    const ps=primary.sleep;
    h+=sleepBarHtml('YASA Spectral (Ours)','#06b6d4',ps.deep_min,ps.light_min,ps.rem_min,ps.awake_min);
  }}

  h+=`</div></div></div></details>`;

  // === 3b. Other Algo Sleep Bars (collapsed) ===
  h+=`<details class="other-algos"><summary>Sleep Stages — Other Algorithms</summary><div class="inner"><div class="card"><div class="sleep-compare">`;
  const barAlgos=[
    [a9,'NeuroKit2 HRV','#8b5cf6'],
    [a10,'SleepECG Full','#14b8a6'],
    [a6,'Sleep as Android','#f59e0b'],
    [d.algos.find(a=>a.id==='algo4'),'Whoop-Calibrated','#2ecc71'],
  ];
  barAlgos.forEach(([algo,name,color])=>{{
    if(algo&&algo.sleep&&algo.sleep.total_min>0){{
      const s=algo.sleep;
      let lbl=name;
      if(algo.sleep_cycles)lbl+=` (${{algo.sleep_cycles}} cycles)`;
      h+=sleepBarHtml(lbl,color,s.deep_min,s.light_min,s.rem_min,s.awake_min);
    }}
  }});
  h+=`</div></div></div></details>`;

  // === 4. Sleep Phase Timeline — Whoop vs YASA ===
  h+=`<details class="other-algos" open><summary>Sleep Timeline — Whoop vs YASA</summary><div class="inner"><div class="card">`;

  function mergePhases(phases){{
    if(!phases||!phases.length) return [];
    let segs=[];let cur={{phase:phases[0].phase,from:phases[0].time,to:phases[0].time,count:1}};
    for(let j=1;j<phases.length;j++){{
      if(phases[j].phase===cur.phase){{cur.to=phases[j].time;cur.count++;}}
      else{{segs.push(cur);cur={{phase:phases[j].phase,from:phases[j].time,to:phases[j].time,count:1}};}}
    }}
    segs.push(cur);return segs;
  }}

  const tlSources=[];
  if(w.phases&&w.phases.length>0)tlSources.push({{name:'Whoop Official',color:'var(--green)',phases:w.phases,winMin:2}});
  if(a8&&a8.phases&&a8.phases.length>0)tlSources.push({{name:'YASA Spectral (Ours)',color:'#06b6d4',phases:a8.phases,winMin:1}});

  let allTimes=[];
  tlSources.forEach(s=>s.phases.forEach(p=>allTimes.push(p.time)));
  if(allTimes.length>0){{
    allTimes.sort();
    const toMin=t=>{{const[hh,mm]=t.split(':').map(Number);return hh*60+mm;}};
    let minT=toMin(allTimes[0]),maxT=toMin(allTimes[allTimes.length-1]);
    if(maxT<minT) maxT+=24*60;
    const span=maxT-minT||1;

    tlSources.forEach(s=>{{
      const wm=s.winMin||2;
      const segs=mergePhases(s.phases);
      h+=`<div class="tl-row">`;
      h+=`<div class="tl-label" style="color:${{s.color}}">${{s.name}}</div>`;
      h+=`<div class="tl-track">`;
      segs.forEach(seg=>{{
        let sMin=toMin(seg.from);if(sMin<minT)sMin+=24*60;
        let eMin=toMin(seg.to);if(eMin<minT)eMin+=24*60;
        eMin+=wm;
        const left=(sMin-minT)/span*100;
        const width=(eMin-sMin)/span*100;
        h+=`<div class="tl-seg" title="${{seg.phase}} ${{seg.from}}-${{seg.to}}" style="left:${{left}}%;width:${{Math.max(width,0.5)}}%;background:${{pC[seg.phase]||'#333'}}"></div>`;
      }});
      h+=`</div>`;
      h+=`<div class="tl-detail">`;
      segs.forEach(seg=>{{
        h+=`<span><span class="d" style="background:${{pC[seg.phase]||'#333'}}"></span>${{seg.from}}-${{seg.to}} ${{seg.phase}} (${{seg.count*wm}}m)</span>`;
      }});
      h+=`</div></div>`;
    }});

    // Time axis
    h+=`<div class="tl-axis">`;
    for(let t=minT;t<=maxT;t+=60){{
      const hh=Math.floor((t%1440)/60),mm=t%60;
      h+=`<span>${{String(hh).padStart(2,'0')}}:${{String(mm).padStart(2,'0')}}</span>`;
    }}
    h+=`</div>`;
  }}else{{
    h+=`<div style="color:var(--dim);font-size:11px;padding:8px 0">No timeline data available</div>`;
  }}
  h+=`<div class="legend" style="margin-top:10px">
    <span><span class="d" style="background:var(--awake)"></span>Awake</span>
    <span><span class="d" style="background:var(--rem)"></span>REM</span>
    <span><span class="d" style="background:var(--light-s)"></span>Light</span>
    <span><span class="d" style="background:var(--deep)"></span>Deep</span>
  </div></div></div></details>`;

  // === 4b. Other Timelines (collapsed) ===
  const otherTlSrc=[];
  if(a9&&a9.phases&&a9.phases.length>0)otherTlSrc.push({{name:'NeuroKit2 HRV',color:'#8b5cf6',phases:a9.phases,winMin:1}});
  if(a10&&a10.phases&&a10.phases.length>0)otherTlSrc.push({{name:'SleepECG Full',color:'#14b8a6',phases:a10.phases,winMin:1}});
  if(a6&&a6.phases&&a6.phases.length>0)otherTlSrc.push({{name:'Sleep as Android',color:'#f59e0b',phases:a6.phases,winMin:2}});
  if(a5&&a5.phases&&a5.phases.length>0)otherTlSrc.push({{name:'ML GBoosting',color:'var(--pink)',phases:a5.phases,winMin:2}});
  if(otherTlSrc.length>0){{
    h+=`<details class="other-algos"><summary>Timelines — Other Algorithms (${{otherTlSrc.length}})</summary><div class="inner"><div class="card">`;
    let otherTimes2=[];
    otherTlSrc.forEach(s=>s.phases.forEach(p=>otherTimes2.push(p.time)));
    if(otherTimes2.length>0){{
      otherTimes2.sort();
      const toM2=t=>{{const[hh,mm]=t.split(':').map(Number);return hh*60+mm;}};
      let mn2=toM2(otherTimes2[0]),mx2=toM2(otherTimes2[otherTimes2.length-1]);
      if(mx2<mn2)mx2+=24*60;
      const sp2=mx2-mn2||1;
      otherTlSrc.forEach(s=>{{
        const wm=s.winMin||2;
        const segs=mergePhases(s.phases);
        h+=`<div class="tl-row"><div class="tl-label" style="color:${{s.color}}">${{s.name}}</div><div class="tl-track">`;
        segs.forEach(seg=>{{
          let sMin=toM2(seg.from);if(sMin<mn2)sMin+=24*60;
          let eMin=toM2(seg.to);if(eMin<mn2)eMin+=24*60;eMin+=wm;
          h+=`<div class="tl-seg" title="${{seg.phase}} ${{seg.from}}-${{seg.to}}" style="left:${{(sMin-mn2)/sp2*100}}%;width:${{Math.max((eMin-sMin)/sp2*100,0.5)}}%;background:${{pC[seg.phase]||'#333'}}"></div>`;
        }});
        h+=`</div></div>`;
      }});
    }}
    h+=`</div></div></details>`;
  }}

  // === 5. All Algorithm Scores Table (collapsed) ===
  if(others.length>0){{
    h+=`<details class="other-algos"><summary>All Algorithm Scores (${{d.algos.length}} algorithms)</summary><div class="inner">`;

    // Score comparison table
    h+=`<div class="card"><h2>All Algorithm Scores</h2>`;
    h+=`<div class="compare-row hdr"><div>Source</div><div>Recovery</div><div>Sleep</div><div>Strain</div><div>HRV / RHR</div><div>Deep</div><div>Light</div><div>REM</div></div>`;
    if(d.whoop){{
      h+=`<div class="compare-row"><div><span class="tag" style="background:var(--green2);color:var(--green)">WHOOP</span></div>
        <div style="color:${{recCol(w.recovery)}};font-weight:700">${{w.recovery||'-'}}%</div>
        <div style="font-weight:700">${{w.sleep||'-'}}%</div><div style="font-weight:700">${{w.strain||'-'}}</div>
        <div>${{w.hrv_ms||'-'}}ms / ${{w.rhr_bpm||'-'}}bpm</div>
        <div style="color:#7986cb">${{ss.deep_time||'-'}}</div>
        <div style="color:#64b5f6">${{ss.light_time||'-'}}</div>
        <div style="color:#ba68c8">${{ss.rem_time||'-'}}</div>
      </div>`;
    }}
    // YASA row
    const psl=primary.sleep||{{}};
    h+=`<div class="compare-row"><div><span class="tag" style="background:#06b6d422;color:#06b6d4">YASA Spectral</span></div>
      <div style="color:${{recCol(pRec)}};font-weight:700">${{pRec}}%</div>
      <div style="font-weight:700">${{primary.sleep_score}}%</div><div style="font-weight:700">${{primary.strain}}</div>
      <div>${{phrv}}ms / ${{prhr}}bpm</div>
      <div style="color:#7986cb">${{fmtMin(psl.deep_min)}}</div>
      <div style="color:#64b5f6">${{fmtMin(psl.light_min)}}</div>
      <div style="color:#ba68c8">${{fmtMin(psl.rem_min)}}</div>
    </div>`;
    // other algos
    others.forEach((a,i)=>{{
      const ahrv=a.hrv||d.physio.hrv_rmssd;const arhr=a.rhr||d.physio.rhr;const asl=a.sleep;
      h+=`<div class="compare-row"><div><span class="tag" style="background:${{aC[i]}}22;color:${{aC[i]}}">${{a.name}}</span></div>
        <div style="color:${{recCol(a.recovery)}};font-weight:700">${{a.recovery}}%</div>
        <div style="font-weight:700">${{a.sleep_score}}%</div><div style="font-weight:700">${{a.strain}}</div>
        <div>${{ahrv}}ms / ${{arhr}}bpm</div>
        <div style="color:#7986cb">${{fmtMin(asl.deep_min)}}</div>
        <div style="color:#64b5f6">${{fmtMin(asl.light_min)}}</div>
        <div style="color:#ba68c8">${{fmtMin(asl.rem_min)}}</div>
      </div>`;
    }});
    h+=`</div>`;

    // Other algos sleep bars
    h+=`<div class="card"><h2>Sleep Stages — All Algorithms</h2>`;
    function sleepBar2(name,color,deep,light,rem,awake){{
      const tp=(deep||0)+(light||0)+(rem||0)+(awake||0)||1;
      return `<div style="margin-bottom:10px">
        <div style="font-size:10px;color:${{color}};font-weight:600;letter-spacing:0.5px;margin-bottom:4px">${{name}}</div>
        <div class="sleep-bar">
          <div class="s" style="width:${{(deep||0)/tp*100}}%;background:var(--deep)"></div>
          <div class="s" style="width:${{(light||0)/tp*100}}%;background:var(--light-s)"></div>
          <div class="s" style="width:${{(rem||0)/tp*100}}%;background:var(--rem)"></div>
          <div class="s" style="width:${{(awake||0)/tp*100}}%;background:var(--awake)"></div>
        </div>
        <div class="legend" style="margin-top:2px">
          <span><span class="d" style="background:var(--deep)"></span>Deep ${{fmtMin(deep)}}</span>
          <span><span class="d" style="background:var(--light-s)"></span>Light ${{fmtMin(light)}}</span>
          <span><span class="d" style="background:var(--rem)"></span>REM ${{fmtMin(rem)}}</span>
          <span><span class="d" style="background:var(--awake)"></span>Awake ${{fmtMin(awake)}}</span>
        </div></div>`;
    }}
    others.forEach((a,i)=>{{
      const asl=a.sleep;
      h+=sleepBar2(a.name,aC[i],asl.deep_min,asl.light_min,asl.rem_min,asl.awake_min);
    }});
    h+=`</div>`;

    // Other algos timelines
    h+=`<div class="card"><h2>Sleep Timelines — All Algorithms</h2>`;
    const otherTlSources=[];
    others.forEach((a,i)=>{{
      if(a.phases&&a.phases.length>0) otherTlSources.push({{name:a.name,color:aC[i],phases:a.phases,winMin:10}});
    }});
    let otherTimes=[];
    otherTlSources.forEach(s=>s.phases.forEach(p=>otherTimes.push(p.time)));
    if(w.phases)w.phases.forEach(p=>otherTimes.push(p.time));
    if(otherTimes.length>0){{
      otherTimes.sort();
      const toMin2=t=>{{const[hh,mm]=t.split(':').map(Number);return hh*60+mm;}};
      let minT2=toMin2(otherTimes[0]),maxT2=toMin2(otherTimes[otherTimes.length-1]);
      if(maxT2<minT2) maxT2+=24*60;
      const span2=maxT2-minT2||1;

      otherTlSources.forEach(s=>{{
        const wm2=s.winMin||10;
        const segs=mergePhases(s.phases);
        h+=`<div class="tl-row"><div class="tl-label" style="color:${{s.color}}">${{s.name}}</div>`;
        h+=`<div class="tl-track">`;
        segs.forEach(seg=>{{
          let sMin=toMin2(seg.from);if(sMin<minT2)sMin+=24*60;
          let eMin=toMin2(seg.to);if(eMin<minT2)eMin+=24*60;
          eMin+=wm2;
          const left=(sMin-minT2)/span2*100;
          const width=(eMin-sMin)/span2*100;
          h+=`<div class="tl-seg" title="${{seg.phase}} ${{seg.from}}-${{seg.to}}" style="left:${{left}}%;width:${{Math.max(width,0.5)}}%;background:${{pC[seg.phase]||'#333'}}"></div>`;
        }});
        h+=`</div></div>`;
      }});
    }}
    h+=`</div>`;

    h+=`</div></details>`;
  }}

  content.innerHTML=h;
}}
// Find best default day: last day with whoop data, or last day with phases
let defIdx=D.days.length-1;
for(let i=D.days.length-1;i>=0;i--){{
  const dy=D.days[i];
  if(dy.whoop&&dy.whoop.recovery){{defIdx=i;break;}}
  if(dy.algos.some(a=>a.phases&&a.phases.length>5))defIdx=i;
}}
render(defIdx);
</script></body></html>"""


if __name__ == "__main__":
    main()
