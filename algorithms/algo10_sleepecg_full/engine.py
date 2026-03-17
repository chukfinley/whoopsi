"""Algo 10: Full SleepECG pipeline with proper 4-class staging.

Improves on algo2's basic SleepECG usage by:
1. Better RR interval preprocessing (artifact rejection, interpolation)
2. Using SleepECG's classifier with proper epoch handling
3. Splitting NREM into deep/light using HR-based heuristics from window analysis
4. Computing sleep cycle detection and duration statistics

This is the most "clinical" approach — closest to polysomnography-grade
sleep staging, adapted for wearable PPG/HR data.
"""

import math
from collections import Counter

import numpy as np
import pandas as pd


def _preprocess_rr(rr_raw):
    """Clean RR intervals: remove artifacts, interpolate gaps."""
    rr = rr_raw[(rr_raw > 200) & (rr_raw < 2500)].copy()
    if len(rr) < 30:
        return rr

    # Remove ectopic beats (>20% change from previous)
    clean = [rr[0]]
    for i in range(1, len(rr)):
        if abs(rr[i] - clean[-1]) / clean[-1] < 0.20:
            clean.append(rr[i])
        else:
            # Replace with interpolated value
            clean.append(clean[-1])
    return np.array(clean)


def _detect_sleep_cycles(phases, min_cycle_min=30):
    """Detect NREM-REM sleep cycles from phase sequence.

    A sleep cycle = NREM period followed by REM period.
    Returns list of cycle dicts with durations.
    """
    if not phases:
        return []

    cycles = []
    current_cycle = {"nrem_start": 0, "nrem_min": 0, "rem_min": 0, "total_min": 0}
    in_rem = False

    for i, p in enumerate(phases):
        phase = p["phase"]
        if phase in ("deep", "light"):
            if in_rem and current_cycle["rem_min"] > 0:
                # End of REM = end of cycle
                current_cycle["total_min"] = current_cycle["nrem_min"] + current_cycle["rem_min"]
                if current_cycle["total_min"] >= min_cycle_min:
                    cycles.append(current_cycle)
                current_cycle = {"nrem_start": i, "nrem_min": 0, "rem_min": 0, "total_min": 0}
                in_rem = False
            current_cycle["nrem_min"] += 1  # 1-min windows
        elif phase == "rem":
            in_rem = True
            current_cycle["rem_min"] += 1

    # Last cycle
    current_cycle["total_min"] = current_cycle["nrem_min"] + current_cycle["rem_min"]
    if current_cycle["total_min"] >= min_cycle_min:
        cycles.append(current_cycle)

    return cycles


def classify_sleep_sleepecg_full(sleep_df, rhr, window_sec=120, stride_sec=60):
    """Full SleepECG-based sleep staging with enhanced NREM splitting.

    Strategy:
    1. Try SleepECG's ML classifier on RR intervals (3-class: Wake/REM/NREM)
    2. Split NREM into Deep/Light using HR-based analysis:
       - Deep: HR well below RHR, low HR variability, low movement
       - Light: HR near RHR, moderate variability
    3. Detect sleep cycles and compute cycle statistics

    Returns (phases_list, summary_dict).
    """
    if sleep_df.empty or len(sleep_df) < 300:
        return [], _empty_summary()

    rr_raw = sleep_df["rr1_ms"].dropna().values
    rr_clean = _preprocess_rr(rr_raw)

    # Try SleepECG classifier
    sleepecg_stages = None
    try:
        import sleepecg
        if len(rr_clean) >= 100:
            beat_times = np.cumsum(rr_clean) / 1000.0
            record = sleepecg.SleepRecord(heartbeat_times=beat_times)
            clf = sleepecg.load_classifier("wrn-gru-mesa", classifiers_dir="SleepECG")
            sleepecg_stages = sleepecg.stage(clf, record, return_mode="int")
    except Exception:
        pass

    # Build phases from windowed analysis
    phases = []
    ts_all = sleep_df["timestamp"].values if "timestamp" in sleep_df.columns else np.arange(len(sleep_df))
    sleep_start = ts_all[0]
    total_duration = max(ts_all[-1] - ts_all[0], 1)

    # Map SleepECG epochs (30s each) to our windows
    def _get_sleepecg_stage(window_start_idx):
        """Get SleepECG stage for a window by mapping time."""
        if sleepecg_stages is None:
            return None
        # Approximate: each SleepECG epoch is 30s
        elapsed = ts_all[min(window_start_idx, len(ts_all) - 1)] - sleep_start
        epoch_idx = int(elapsed / 30)
        if 0 <= epoch_idx < len(sleepecg_stages):
            return int(sleepecg_stages[epoch_idx])
        return None

    for i in range(0, len(sleep_df) - window_sec, stride_sec):
        chunk = sleep_df.iloc[i:i + window_sec]
        t = chunk["datetime_local"].iloc[0]

        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 10:
            phases.append({
                "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
                "phase": "light",
            })
            continue

        avg_hr = float(np.median(hr_v))
        hr_std = float(np.std(hr_v))
        hr_above = avg_hr - rhr
        avg_mv = float(np.mean(mv))
        max_mv = float(np.max(mv))

        elapsed = ts_all[min(i + window_sec // 2, len(ts_all) - 1)] - sleep_start
        fraction = elapsed / total_duration

        # Get SleepECG classification for this window
        sec_stage = _get_sleepecg_stage(i)

        # RMSSD
        local_rmssd = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_rmssd = float(np.sqrt(np.mean(diffs**2)))

        # Classification strategy: combine SleepECG with HR-based analysis
        if sec_stage is not None:
            # SleepECG: 0=WAKE, 1=REM, 2=NREM
            if sec_stage == 0:
                phase = "awake"
            elif sec_stage == 1:
                phase = "rem"
            else:
                # NREM: split into deep vs light using HR analysis
                if hr_above < 2 and hr_std < 3.0 and avg_mv < 0.15:
                    phase = "deep"
                elif hr_above < 0 and local_rmssd > 80:
                    phase = "deep"
                elif fraction < 0.4 and hr_above < 5 and hr_std < 4:
                    phase = "deep"
                else:
                    phase = "light"

            # Override: high movement always = awake
            if avg_mv > 1.0 or max_mv > 3.0:
                phase = "awake"
        else:
            # No SleepECG: pure HR-based fallback
            if avg_mv > 1.0 or max_mv > 3.0:
                phase = "awake"
            elif hr_above > 20:
                phase = "awake"
            elif hr_above < 2 and hr_std < 3.0 and avg_mv < 0.15:
                phase = "deep"
            elif hr_std > 8 and avg_mv < 0.5:
                phase = "rem"
            elif local_rmssd > 100 and avg_mv < 0.5:
                phase = "rem"
            else:
                phase = "light"

        phases.append({
            "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
            "phase": phase,
        })

    # Smooth
    phases = _smooth_phases(phases)

    # Detect sleep cycles
    cycles = _detect_sleep_cycles(phases)

    summary = _compute_summary(phases, window_sec=stride_sec)
    summary["sleep_cycles"] = len(cycles)
    if cycles:
        summary["avg_cycle_min"] = round(np.mean([c["total_min"] for c in cycles]), 1)
        summary["cycle_details"] = cycles
    else:
        summary["avg_cycle_min"] = 0
        summary["cycle_details"] = []

    return phases, summary


def _smooth_phases(phases):
    if len(phases) < 3:
        return phases
    result = list(phases)
    for i in range(1, len(result) - 1):
        if result[i]["phase"] != result[i - 1]["phase"] and result[i]["phase"] != result[i + 1]["phase"]:
            result[i] = {**result[i], "phase": result[i - 1]["phase"]}
    return result


def _compute_summary(phases, window_sec=60):
    if not phases:
        return _empty_summary()
    total = len(phases)
    c = Counter(p["phase"] for p in phases)
    win_min = window_sec / 60.0
    sleep_count = total - c.get("awake", 0) - c.get("unknown", 0)
    return {
        "total_min": round(total * win_min, 1),
        "sleep_min": round(sleep_count * win_min, 1),
        "efficiency": round(sleep_count / total * 100, 1) if total > 0 else 0,
        "deep_min": round(c.get("deep", 0) * win_min, 1),
        "light_min": round(c.get("light", 0) * win_min, 1),
        "rem_min": round(c.get("rem", 0) * win_min, 1),
        "awake_min": round(c.get("awake", 0) * win_min, 1),
        "deep_pct": round(c.get("deep", 0) / total * 100, 1) if total > 0 else 0,
        "light_pct": round(c.get("light", 0) / total * 100, 1) if total > 0 else 0,
        "rem_pct": round(c.get("rem", 0) / total * 100, 1) if total > 0 else 0,
        "awake_pct": round(c.get("awake", 0) / total * 100, 1) if total > 0 else 0,
    }


def _empty_summary():
    return {
        "total_min": 0, "sleep_min": 0, "efficiency": 0,
        "deep_min": 0, "light_min": 0, "rem_min": 0, "awake_min": 0,
        "deep_pct": 0, "light_pct": 0, "rem_pct": 0, "awake_pct": 0,
    }


class SleepECGFullEngine:
    """Full SleepECG pipeline with cycle detection."""

    def classify_sleep(self, sleep_df, rhr, **kwargs):
        return classify_sleep_sleepecg_full(sleep_df, rhr)
