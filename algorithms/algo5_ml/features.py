"""Feature extraction for ML sleep phase classification.

v3 improvements:
- 2-min windows (120s) with 1-min stride (60s) — 8x more training data
- Per-second ground truth from ISO timestamps (not minute-level)
- Sequence features: previous 3 windows HR/movement/HRV as context
- Sleep architecture features: cycle phase, cumulative deep/rem
- HRV availability flag for sparse 2-min windows
- HR trend over 5-window horizon
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, date as date_cls
from collections import Counter

from common.preprocessing import compute_rhr

DEEP_DIVE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "whoop-companion"
    / "data"
    / "whoop_backup"
    / "deep_dive"
)
PHASE_MAP = {
    "AWAKE": "awake",
    "LIGHT_SLEEP": "light",
    "SWS_SLEEP": "deep",
    "REM_SLEEP": "rem",
}
PHASE_TO_INT = {"awake": 0, "light": 1, "deep": 2, "rem": 3}
INT_TO_PHASE = {v: k for k, v in PHASE_TO_INT.items()}

WINDOW_SEC = 120  # 2-minute windows (was 300)
STRIDE_SEC = 60  # 1-minute stride (was 150)


def compute_lf_hf_ratio(rr_values):
    """Compute LF/HF ratio from RR intervals using Welch PSD."""
    if len(rr_values) < 30:
        return 1.0

    from scipy.interpolate import interp1d
    from scipy.signal import welch

    rr = np.array(rr_values, dtype=float)
    cumtime = np.cumsum(rr) / 1000.0
    cumtime -= cumtime[0]
    if cumtime[-1] < 15:
        return 1.0

    fs = 4.0
    t_uniform = np.arange(0, cumtime[-1], 1.0 / fs)
    if len(t_uniform) < 32:
        return 1.0

    f_interp = interp1d(cumtime, rr, kind="linear", fill_value="extrapolate")
    rr_uniform = f_interp(t_uniform)
    rr_uniform = rr_uniform - np.mean(rr_uniform)

    nperseg = min(128, len(rr_uniform))
    if nperseg < 16:
        return 1.0

    freqs, psd = welch(rr_uniform, fs=fs, nperseg=nperseg)

    lf_mask = (freqs >= 0.04) & (freqs <= 0.15)
    hf_mask = (freqs >= 0.15) & (freqs <= 0.40)

    lf_power = np.trapezoid(psd[lf_mask], freqs[lf_mask]) if lf_mask.any() else 0
    hf_power = np.trapezoid(psd[hf_mask], freqs[hf_mask]) if hf_mask.any() else 0

    if hf_power < 1e-10:
        return 5.0
    return float(lf_power / hf_power)


def _parse_deep_dive_sleep_bounds(date_str):
    """Parse sleep start/end and per-second labels from deep_dive JSON.

    Returns (second_gt, start_ts, end_ts) where second_gt maps unix-second -> phase string.
    Uses exact ISO timestamps from header for start/end, and fractional
    time_bound_ranges mapped to second-level precision.
    """
    f = DEEP_DIVE_DIR / f"{date_str}.json"
    if not f.exists():
        return None, None, None

    data = json.load(open(f))
    last = data.get("last_night", {})
    header = last.get("header_section")
    if not header:
        return None, None, None

    dest = header.get("destination")
    if not dest:
        return None, None, None

    params = dest.get("parameters", {})
    start_str = params.get("start_time")
    end_str = params.get("end_time")
    if not start_str or not end_str:
        return None, None, None

    start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    total_secs = end_ts - start_ts
    if total_secs <= 0:
        return None, None, None

    try:
        zones = last["sections"][0]["items"][0]["content"]["card_content"][2][
            "content"
        ]["heart_rate_zones"]
    except (KeyError, IndexError):
        return None, None, None

    # Build per-second label array for precise ground truth
    second_gt = {}
    for z in zones:
        phase = PHASE_MAP.get(z["id"])
        if not phase:
            continue
        tbr = z.get("bar_graph", {}).get("time_bound_ranges", [])
        for r in tbr:
            lo = r["lower_endpoint"]
            hi = r["upper_endpoint"]
            seg_start_ts = start_ts + lo * total_secs
            seg_end_ts = start_ts + hi * total_secs
            # Fill every second in this range
            for s in range(int(seg_start_ts), int(seg_end_ts) + 1):
                second_gt[s] = phase

    return second_gt, int(start_ts), int(end_ts)


def extract_whoop_timeline(date_str):
    """Extract per-minute ground truth sleep phases from deep_dive JSON.

    Backward-compatible wrapper. Returns (minute_gt, start_ts, end_ts).
    """
    second_gt, start_ts, end_ts = _parse_deep_dive_sleep_bounds(date_str)
    if second_gt is None:
        return None, None, None

    # Convert to minute-level for backward compat
    minute_gt = {}
    for sec, phase in second_gt.items():
        minute_gt[sec // 60] = phase

    return minute_gt, start_ts, end_ts


def extract_stress_timeline(date_str):
    """Extract per-minute stress level from deep_dive sleep_stress graph."""
    f = DEEP_DIVE_DIR / f"{date_str}.json"
    if not f.exists():
        return None

    data = json.load(open(f))
    last = data.get("last_night", {})
    header = last.get("header_section")
    if not header:
        return None
    dest = header.get("destination")
    if not dest:
        return None
    params = dest.get("parameters", {})
    start_str = params.get("start_time")
    end_str = params.get("end_time")
    if not start_str or not end_str:
        return None

    start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    total_secs = end_ts - start_ts
    if total_secs <= 0:
        return None

    for s in last.get("sections", []):
        for item in s.get("items", []):
            c = item.get("content", {})
            if c.get("id") != "sleep_stress":
                continue
            for cc in c.get("card_content", []):
                inner = cc.get("content", {})
                plots = inner.get("plots", [])
                if not plots:
                    continue
                segments = plots[0].get("plot", {}).get("segments", [])
                if not segments:
                    continue
                points = segments[0].get("points", [])
                if not points:
                    continue

                minute_stress = {}
                for pt in points:
                    x = pt.get("position_x", 0)
                    details = pt.get("data_scrubber_details", {})
                    val_str = details.get("value_display", "0")
                    try:
                        stress_val = float(val_str)
                    except (ValueError, TypeError):
                        continue
                    ts = start_ts + x * total_secs
                    minute_stress[int(ts) // 60] = stress_val
                return minute_stress

    return None


def extract_deep_dive_metrics(date_str):
    """Extract all available GT metrics from a deep_dive JSON."""
    f = DEEP_DIVE_DIR / f"{date_str}.json"
    if not f.exists():
        return {}

    data = json.load(open(f))
    metrics = {"date": date_str}

    for section in data.get("recovery", {}).get("sections", []):
        for item in section.get("items", []):
            c = item.get("content", {})
            if item["type"] == "SCORE_GAUGE" and c.get("id") == "RECOVERY_SCORE_GAUGE":
                try:
                    metrics["recovery_score"] = float(c["score_display"])
                except (ValueError, TypeError):
                    pass
            if item["type"] == "CONTRIBUTORS_TILE":
                for m in c.get("metrics", []):
                    mid = m.get("id", "")
                    val = m.get("status", "")
                    try:
                        val_f = float(val.replace("%", ""))
                    except (ValueError, TypeError):
                        continue
                    if "HRV" in mid:
                        metrics["hrv_ms"] = val_f
                    elif "RHR" in mid:
                        metrics["rhr_bpm"] = val_f
                    elif "RESPIRATORY" in mid:
                        metrics["resp_rate"] = val_f
                    elif "SLEEP_PERFORMANCE" in mid:
                        metrics["sleep_performance"] = val_f

    for section in data.get("sleep", {}).get("sections", []):
        for item in section.get("items", []):
            c = item.get("content", {})
            if item["type"] == "SCORE_GAUGE" and c.get("id") == "SLEEP_SCORE_GAUGE":
                try:
                    metrics["sleep_score"] = float(c["score_display"])
                except (ValueError, TypeError):
                    pass
            if item["type"] == "CONTRIBUTORS_TILE":
                for m in c.get("metrics", []):
                    mid = m.get("id", "")
                    val = m.get("status", "")
                    try:
                        val_f = float(val.replace("%", ""))
                    except (ValueError, TypeError):
                        continue
                    if "STRESS" in mid:
                        metrics["stress_pct"] = val_f
                    elif "EFFICIENCY" in mid:
                        metrics["sleep_efficiency"] = val_f
                    elif "CONSISTENCY" in mid:
                        metrics["sleep_consistency"] = val_f
                    elif "HOURS" in mid:
                        metrics["hours_vs_needed"] = val_f

    for section in data.get("strain", {}).get("sections", []):
        for item in section.get("items", []):
            c = item.get("content", {})
            if item["type"] == "SCORE_GAUGE" and c.get("id") == "STRAIN_SCORE_GAUGE":
                try:
                    metrics["strain_score"] = float(c["score_display"])
                except (ValueError, TypeError):
                    pass
            if item["type"] == "CONTRIBUTORS_TILE":
                for m in c.get("metrics", []):
                    mid = m.get("id", "")
                    val = m.get("status", "")
                    if "STEPS" in mid:
                        try:
                            metrics["steps"] = int(val.replace(",", ""))
                        except (ValueError, TypeError):
                            pass

    return metrics


def extract_window_features(
    chunk, rhr, sleep_start_ts=None, sleep_end_ts=None, prev_features=None, history=None
):
    """Extract features from a single time window of sensor data.

    Handles 2-min windows gracefully:
    - HR features: reliable with ~120 samples
    - Movement: reliable with ~120 samples
    - HRV: may be sparse, flagged via hrv_available
    """
    hr = chunk["hr"].values
    hr_v = hr[hr > 30]
    mv = chunk["movement"].values
    rr = chunk["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]

    if len(hr_v) < 3:
        return None

    # --- HR features ---
    hr_mean = float(np.mean(hr_v))
    hr_median = float(np.median(hr_v))
    hr_std = float(np.std(hr_v)) if len(hr_v) > 1 else 0
    hr_iqr = (
        float(np.percentile(hr_v, 75) - np.percentile(hr_v, 25))
        if len(hr_v) > 4
        else hr_std
    )
    hr_min = float(np.min(hr_v))
    hr_max = float(np.max(hr_v))
    hr_p10 = float(np.percentile(hr_v, 10)) if len(hr_v) > 4 else hr_min
    hr_p90 = float(np.percentile(hr_v, 90)) if len(hr_v) > 4 else hr_max
    hr_above_rhr = hr_mean - rhr
    hr_range = hr_max - hr_min

    if len(hr_v) > 10:
        x = np.arange(len(hr_v))
        hr_trend = float(np.polyfit(x, hr_v, 1)[0]) * len(hr_v)
    else:
        hr_trend = 0.0

    # --- NEW: HR dynamics features (Light vs REM separation) ---
    # Mean absolute successive difference — REM has irregular HR, Deep has stable
    hr_masd = 0.0
    hr_skewness = 0.0
    hr_kurtosis = 0.0
    hr_entropy = 0.0
    hr_accel = 0.0
    hr_cv = 0.0

    if len(hr_v) > 5:
        hr_diffs = np.abs(np.diff(hr_v.astype(float)))
        hr_masd = float(np.mean(hr_diffs))

        # Skewness and kurtosis of HR — REM has right-skewed bursts
        from scipy.stats import skew, kurtosis as kurt_fn

        hr_skewness = float(skew(hr_v))
        hr_kurtosis = float(kurt_fn(hr_v))

        # HR "acceleration" — 2nd derivative, captures abrupt swings in REM
        if len(hr_v) > 10:
            hr_2nd_diff = np.diff(hr_v.astype(float), n=2)
            hr_accel = float(np.mean(np.abs(hr_2nd_diff)))

        # Coefficient of variation — normalized variability
        hr_cv = float(hr_std / hr_mean) if hr_mean > 0 else 0.0

        # Sample entropy approximation (regularity measure)
        # Low entropy = regular (Deep), High = irregular (REM)
        if len(hr_v) >= 20:
            # Binned entropy: bin HR into 5-BPM buckets, compute Shannon entropy
            bins = np.arange(hr_min - 2.5, hr_max + 7.5, 5)
            if len(bins) > 1:
                counts, _ = np.histogram(hr_v, bins=bins)
                probs = counts[counts > 0] / counts.sum()
                hr_entropy = float(-np.sum(probs * np.log2(probs)))

    # Normalized HR range (range / rhr)
    hr_range_norm = hr_range / rhr if rhr > 30 else 0.0

    # --- HRV / RR features ---
    rmssd = 0.0
    sdnn = 0.0
    pnn50 = 0.0
    rr_mean = 0.0
    rr_std = 0.0
    rr_trend = 0.0
    lf_hf = 1.0
    hrv_available = 0.0

    rr_cv = 0.0
    rr_entropy = 0.0
    pnn20 = 0.0

    if len(rr) > 3:
        hrv_available = 1.0
        diffs = np.diff(rr)
        clean_diffs = diffs[np.abs(diffs) < 300]
        if len(clean_diffs) > 2:
            rmssd = float(np.sqrt(np.mean(clean_diffs**2)))
            pnn50 = float(np.sum(np.abs(clean_diffs) > 50) / len(clean_diffs) * 100)
            pnn20 = float(np.sum(np.abs(clean_diffs) > 20) / len(clean_diffs) * 100)
        rr_mean = float(np.mean(rr))
        rr_std = float(np.std(rr))
        sdnn = rr_std
        if len(rr) > 10:
            x = np.arange(len(rr))
            rr_trend = float(np.polyfit(x, rr, 1)[0]) * len(rr)
        lf_hf = compute_lf_hf_ratio(rr)

        # RR coefficient of variation — high in REM, low in Deep
        rr_cv = float(rr_std / rr_mean) if rr_mean > 0 else 0.0

        # RR entropy — irregularity of beat-to-beat intervals
        if len(rr) >= 10:
            rr_bins = np.arange(np.min(rr) - 25, np.max(rr) + 75, 50)
            if len(rr_bins) > 1:
                rr_counts, _ = np.histogram(rr, bins=rr_bins)
                rr_probs = rr_counts[rr_counts > 0] / rr_counts.sum()
                rr_entropy = float(-np.sum(rr_probs * np.log2(rr_probs)))

    # --- Movement features ---
    mv_mean = float(np.mean(mv))
    mv_std = float(np.std(mv)) if len(mv) > 1 else 0
    mv_max = float(np.max(mv))
    mv_p90 = float(np.percentile(mv, 90)) if len(mv) > 4 else mv_max
    mv_energy = float(np.sum(mv**2))

    if len(mv) > 2:
        threshold = 0.1
        above = mv > threshold
        zcr = float(np.sum(np.diff(above.astype(int)) != 0) / len(mv))
    else:
        zcr = 0.0

    # --- NEW: Movement features for awake detection ---
    mv_p95 = float(np.percentile(mv, 95)) if len(mv) > 4 else mv_max
    # Fraction of window with movement above threshold (movement duration)
    mv_active_frac = float(np.mean(mv > 0.05)) if len(mv) > 0 else 0.0
    # Number of distinct movement bursts (contiguous above-threshold segments)
    mv_burst_count = 0
    if len(mv) > 2:
        above_thresh = mv > 0.08
        burst_starts = np.diff(above_thresh.astype(int))
        mv_burst_count = int(np.sum(burst_starts == 1))

    acc_x_std = float(np.std(chunk["acc_x"].values)) if "acc_x" in chunk.columns else 0
    acc_y_std = float(np.std(chunk["acc_y"].values)) if "acc_y" in chunk.columns else 0
    # Combined accelerometer magnitude variability
    acc_mag_std = 0.0
    if (
        "acc_x" in chunk.columns
        and "acc_y" in chunk.columns
        and "acc_z" in chunk.columns
    ):
        ax = chunk["acc_x"].values
        ay = chunk["acc_y"].values
        az = chunk["acc_z"].values
        acc_mag = np.sqrt(ax**2 + ay**2 + az**2)
        acc_mag_std = float(np.std(acc_mag))

    gyro_mean = (
        float(np.mean(np.abs(chunk["gyro"].values))) if "gyro" in chunk.columns else 0
    )
    gyro_std = float(np.std(chunk["gyro"].values)) if "gyro" in chunk.columns else 0

    spo2_vals = (
        chunk["spo2"].dropna().values if "spo2" in chunk.columns else np.array([])
    )
    spo2_mean = float(np.mean(spo2_vals)) if len(spo2_vals) > 0 else 97.0
    spo2_min = float(np.min(spo2_vals)) if len(spo2_vals) > 0 else 95.0

    # --- Temporal features ---
    mid_ts = chunk.iloc[len(chunk) // 2]["timestamp"]

    hours_since_onset = 0.0
    fraction_of_night = 0.5
    if sleep_start_ts and sleep_end_ts and sleep_end_ts > sleep_start_ts:
        hours_since_onset = (mid_ts - sleep_start_ts) / 3600.0
        fraction_of_night = (mid_ts - sleep_start_ts) / (sleep_end_ts - sleep_start_ts)
        fraction_of_night = max(0, min(1, fraction_of_night))

    hour_of_day = (mid_ts % 86400) / 3600.0
    circadian_sin = float(np.sin(2 * np.pi * hour_of_day / 24))
    circadian_cos = float(np.cos(2 * np.pi * hour_of_day / 24))

    if sleep_start_ts:
        mins_since = (mid_ts - sleep_start_ts) / 60.0
        ultradian_sin = float(np.sin(2 * np.pi * mins_since / 90))
        ultradian_cos = float(np.cos(2 * np.pi * mins_since / 90))
    else:
        ultradian_sin = 0.0
        ultradian_cos = 0.0

    # --- Cross features ---
    hr_mv_interaction = hr_above_rhr * mv_mean
    hrv_hr_ratio = rmssd / hr_mean if hr_mean > 0 else 0
    autonomic_balance = lf_hf * mv_mean

    # --- Delta from previous window ---
    delta_hr = 0.0
    delta_mv = 0.0
    delta_hrv = 0.0
    delta_lf_hf = 0.0
    if prev_features:
        delta_hr = hr_mean - prev_features.get("hr_mean", hr_mean)
        delta_mv = mv_mean - prev_features.get("mv_mean", mv_mean)
        delta_hrv = rmssd - prev_features.get("rmssd", rmssd)
        delta_lf_hf = lf_hf - prev_features.get("lf_hf", lf_hf)

    # --- 3-window rolling statistics ---
    roll_hr_mean = hr_mean
    roll_hr_std = 0.0
    roll_mv_mean = mv_mean
    roll_mv_std = 0.0
    roll_hrv_mean = rmssd
    if history and len(history) >= 2:
        recent_hr = [h.get("hr_mean", hr_mean) for h in history[-2:]] + [hr_mean]
        recent_mv = [h.get("mv_mean", mv_mean) for h in history[-2:]] + [mv_mean]
        recent_hrv = [h.get("rmssd", rmssd) for h in history[-2:]] + [rmssd]
        roll_hr_mean = float(np.mean(recent_hr))
        roll_hr_std = float(np.std(recent_hr))
        roll_mv_mean = float(np.mean(recent_mv))
        roll_mv_std = float(np.std(recent_mv))
        roll_hrv_mean = float(np.mean(recent_hrv))

    # --- NEW: 10-window rolling statistics (wider context ~20 min) ---
    roll10_hr_mean = hr_mean
    roll10_hr_std = 0.0
    roll10_lf_hf_mean = lf_hf
    roll10_mv_mean = mv_mean
    if history and len(history) >= 9:
        recent10_hr = [h.get("hr_mean", hr_mean) for h in history[-9:]] + [hr_mean]
        recent10_mv = [h.get("mv_mean", mv_mean) for h in history[-9:]] + [mv_mean]
        recent10_lf = [h.get("lf_hf", lf_hf) for h in history[-9:]] + [lf_hf]
        roll10_hr_mean = float(np.mean(recent10_hr))
        roll10_hr_std = float(np.std(recent10_hr))
        roll10_lf_hf_mean = float(np.mean(recent10_lf))
        roll10_mv_mean = float(np.mean(recent10_mv))

    # --- NEW: HR deviation from rolling mean (local anomaly detection) ---
    hr_dev_from_roll = hr_mean - roll_hr_mean
    hr_dev_from_roll10 = hr_mean - roll10_hr_mean

    # --- Sequence context: values from prev 1-3 windows ---
    prev1_hr = prev_features.get("hr_mean", hr_mean) if prev_features else hr_mean
    prev1_mv = prev_features.get("mv_mean", mv_mean) if prev_features else mv_mean
    prev1_hrv = prev_features.get("rmssd", rmssd) if prev_features else rmssd

    prev2_hr = hr_mean
    prev2_mv = mv_mean
    prev3_hr = hr_mean
    if history and len(history) >= 1:
        prev2_hr = history[-1].get("hr_mean", hr_mean)
        prev2_mv = history[-1].get("mv_mean", mv_mean)
    if history and len(history) >= 2:
        prev3_hr = history[-2].get("hr_mean", hr_mean)

    # HR delta from 2 windows ago to 1 window ago
    prev1_hr_delta = prev1_hr - prev2_hr

    # HR trend over 5 windows (slope)
    hr_trend_5win = 0.0
    mv_trend_5win = 0.0
    if history and len(history) >= 4:
        hr_vals = [h.get("hr_mean", hr_mean) for h in history[-4:]] + [hr_mean]
        mv_vals = [h.get("mv_mean", mv_mean) for h in history[-4:]] + [mv_mean]
        x5 = np.arange(5)
        hr_trend_5win = float(np.polyfit(x5, hr_vals, 1)[0])
        mv_trend_5win = float(np.polyfit(x5, mv_vals, 1)[0])

    # --- Sleep architecture features ---
    # Expected 90-min cycle phase (0-1 within the cycle)
    expected_cycle_phase = 0.5
    sleep_cycle_number = 0.0
    if sleep_start_ts:
        mins_since_onset = (mid_ts - sleep_start_ts) / 60.0
        expected_cycle_phase = (mins_since_onset % 90) / 90.0
        sleep_cycle_number = float(mins_since_onset // 90)  # 0, 1, 2, 3...

    # Cumulative sleep time in hours
    cumulative_sleep_hrs = 0.0
    if history:
        cumulative_sleep_hrs = len(history) * (WINDOW_SEC / 3600.0)

    # Cumulative deep/rem so far (from history of predictions — approximate via low HR/high HRV)
    cumulative_deep_proxy = 0.0
    cumulative_rem_proxy = 0.0
    cumulative_awake_proxy = 0.0
    if history and len(history) > 0:
        n_hist = len(history)
        # Deep proxy: windows with very low HR above RHR and low movement
        deep_count = sum(
            1
            for h in history
            if h.get("hr_above_rhr", 10) < 5 and h.get("mv_mean", 1) < 0.3
        )
        cumulative_deep_proxy = deep_count / n_hist if n_hist > 0 else 0
        # REM proxy: windows with high HR variability and low movement
        rem_count = sum(
            1 for h in history if h.get("hr_iqr", 0) > 8 and h.get("mv_mean", 1) < 0.5
        )
        cumulative_rem_proxy = rem_count / n_hist if n_hist > 0 else 0
        # Awake proxy: high movement windows
        awake_count = sum(
            1
            for h in history
            if h.get("mv_mean", 0) > 0.3 or h.get("hr_above_rhr", 0) > 15
        )
        cumulative_awake_proxy = awake_count / n_hist if n_hist > 0 else 0

    features = {
        # HR (11)
        "hr_mean": hr_mean,
        "hr_median": hr_median,
        "hr_std": hr_std,
        "hr_iqr": hr_iqr,
        "hr_min": hr_min,
        "hr_max": hr_max,
        "hr_p10": hr_p10,
        "hr_p90": hr_p90,
        "hr_above_rhr": hr_above_rhr,
        "hr_trend": hr_trend,
        "hr_range": hr_range,
        # NEW HR dynamics (7)
        "hr_masd": hr_masd,
        "hr_skewness": hr_skewness,
        "hr_kurtosis": hr_kurtosis,
        "hr_entropy": hr_entropy,
        "hr_accel": hr_accel,
        "hr_cv": hr_cv,
        "hr_range_norm": hr_range_norm,
        # HRV / RR (8 + 3 new = 11)
        "rmssd": rmssd,
        "sdnn": sdnn,
        "pnn50": pnn50,
        "pnn20": pnn20,
        "rr_mean": rr_mean,
        "rr_std": rr_std,
        "rr_trend": rr_trend,
        "lf_hf": lf_hf,
        "hrv_available": hrv_available,
        "rr_cv": rr_cv,
        "rr_entropy": rr_entropy,
        # Movement (8 + 4 new = 12)
        "mv_mean": mv_mean,
        "mv_std": mv_std,
        "mv_max": mv_max,
        "mv_p90": mv_p90,
        "mv_p95": mv_p95,
        "mv_energy": mv_energy,
        "mv_zcr": zcr,
        "mv_active_frac": mv_active_frac,
        "mv_burst_count": float(mv_burst_count),
        "acc_x_std": acc_x_std,
        "acc_y_std": acc_y_std,
        "acc_mag_std": acc_mag_std,
        # Gyro + SpO2 (4)
        "gyro_mean": gyro_mean,
        "gyro_std": gyro_std,
        "spo2_mean": spo2_mean,
        "spo2_min": spo2_min,
        # Temporal (6)
        "hours_since_onset": hours_since_onset,
        "fraction_of_night": fraction_of_night,
        "circadian_sin": circadian_sin,
        "circadian_cos": circadian_cos,
        "ultradian_sin": ultradian_sin,
        "ultradian_cos": ultradian_cos,
        # Cross (3)
        "hr_mv_interaction": hr_mv_interaction,
        "hrv_hr_ratio": hrv_hr_ratio,
        "autonomic_balance": autonomic_balance,
        # Delta / rolling context (4)
        "delta_hr": delta_hr,
        "delta_mv": delta_mv,
        "delta_hrv": delta_hrv,
        "delta_lf_hf": delta_lf_hf,
        # 3-window rolling (5)
        "roll_hr_mean": roll_hr_mean,
        "roll_hr_std": roll_hr_std,
        "roll_mv_mean": roll_mv_mean,
        "roll_mv_std": roll_mv_std,
        "roll_hrv_mean": roll_hrv_mean,
        # NEW 10-window rolling (6)
        "roll10_hr_mean": roll10_hr_mean,
        "roll10_hr_std": roll10_hr_std,
        "roll10_lf_hf_mean": roll10_lf_hf_mean,
        "roll10_mv_mean": roll10_mv_mean,
        "hr_dev_from_roll": hr_dev_from_roll,
        "hr_dev_from_roll10": hr_dev_from_roll10,
        # Sequence context (9)
        "prev1_hr": prev1_hr,
        "prev1_mv": prev1_mv,
        "prev1_hrv": prev1_hrv,
        "prev2_hr": prev2_hr,
        "prev2_mv": prev2_mv,
        "prev3_hr": prev3_hr,
        "prev1_hr_delta": prev1_hr_delta,
        "hr_trend_5win": hr_trend_5win,
        "mv_trend_5win": mv_trend_5win,
        # Sleep architecture (3 + 3 new = 6)
        "expected_cycle_phase": expected_cycle_phase,
        "sleep_cycle_number": sleep_cycle_number,
        "cumulative_sleep_hrs": cumulative_sleep_hrs,
        "cumulative_deep_proxy": cumulative_deep_proxy,
        "cumulative_rem_proxy": cumulative_rem_proxy,
        "cumulative_awake_proxy": cumulative_awake_proxy,
    }
    return features


FEATURE_NAMES = [
    # HR (11)
    "hr_mean",
    "hr_median",
    "hr_std",
    "hr_iqr",
    "hr_min",
    "hr_max",
    "hr_p10",
    "hr_p90",
    "hr_above_rhr",
    "hr_trend",
    "hr_range",
    # HR dynamics (7)
    "hr_masd",
    "hr_skewness",
    "hr_kurtosis",
    "hr_entropy",
    "hr_accel",
    "hr_cv",
    "hr_range_norm",
    # HRV / RR (11)
    "rmssd",
    "sdnn",
    "pnn50",
    "pnn20",
    "rr_mean",
    "rr_std",
    "rr_trend",
    "lf_hf",
    "hrv_available",
    "rr_cv",
    "rr_entropy",
    # Movement (12)
    "mv_mean",
    "mv_std",
    "mv_max",
    "mv_p90",
    "mv_p95",
    "mv_energy",
    "mv_zcr",
    "mv_active_frac",
    "mv_burst_count",
    "acc_x_std",
    "acc_y_std",
    "acc_mag_std",
    # Gyro + SpO2 (4)
    "gyro_mean",
    "gyro_std",
    "spo2_mean",
    "spo2_min",
    # Temporal (6)
    "hours_since_onset",
    "fraction_of_night",
    "circadian_sin",
    "circadian_cos",
    "ultradian_sin",
    "ultradian_cos",
    # Cross (3)
    "hr_mv_interaction",
    "hrv_hr_ratio",
    "autonomic_balance",
    # Delta (4)
    "delta_hr",
    "delta_mv",
    "delta_hrv",
    "delta_lf_hf",
    # 3-window rolling (5)
    "roll_hr_mean",
    "roll_hr_std",
    "roll_mv_mean",
    "roll_mv_std",
    "roll_hrv_mean",
    # 10-window rolling (6)
    "roll10_hr_mean",
    "roll10_hr_std",
    "roll10_lf_hf_mean",
    "roll10_mv_mean",
    "hr_dev_from_roll",
    "hr_dev_from_roll10",
    # Sequence context (9)
    "prev1_hr",
    "prev1_mv",
    "prev1_hrv",
    "prev2_hr",
    "prev2_mv",
    "prev3_hr",
    "prev1_hr_delta",
    "hr_trend_5win",
    "mv_trend_5win",
    # Sleep architecture (6)
    "expected_cycle_phase",
    "sleep_cycle_number",
    "cumulative_sleep_hrs",
    "cumulative_deep_proxy",
    "cumulative_rem_proxy",
    "cumulative_awake_proxy",
]


def build_training_data(df, overlap=True):
    """Build feature matrix + labels + night groups from sensor DB + deep_dive GT.

    Uses per-second ground truth labels for precise window labeling.

    Returns:
        X: np.ndarray (n_windows, n_features)
        y: np.ndarray (n_windows,) integer labels (0=awake, 1=light, 2=deep, 3=rem)
        night_ids: np.ndarray (n_windows,) integer night index (for LONO CV)
        night_dates: list of date strings
    """
    sensor_dates = sorted(
        set(
            str(d) for d in df["date"].unique() if hasattr(d, "year") and d.year >= 2025
        )
    )

    stride = STRIDE_SEC if overlap else WINDOW_SEC

    all_features = []
    all_labels = []
    all_night_ids = []
    night_dates = []
    night_idx = 0

    for date_str in sensor_dates:
        # Use per-second GT for precise labeling
        second_gt, start_ts, end_ts = _parse_deep_dive_sleep_bounds(date_str)
        if second_gt is None or len(second_gt) < 600:
            continue

        parts = date_str.split("-")
        day_date = date_cls(int(parts[0]), int(parts[1]), int(parts[2]))
        day_df = df[df["date"] == day_date]
        mask = (day_df["timestamp"] >= start_ts) & (day_df["timestamp"] <= end_ts)
        sleep_df = day_df[mask]

        if len(sleep_df) < WINDOW_SEC:
            continue

        rhr = compute_rhr(sleep_df)
        prev_feats = None
        history = []

        for i in range(0, len(sleep_df) - WINDOW_SEC, stride):
            chunk = sleep_df.iloc[i : i + WINDOW_SEC]
            feats = extract_window_features(
                chunk,
                rhr,
                sleep_start_ts=start_ts,
                sleep_end_ts=end_ts,
                prev_features=prev_feats,
                history=history,
            )
            if feats is None:
                continue

            # Get ground truth: majority vote from per-second labels within this window
            win_start_ts = chunk.iloc[0]["timestamp"]
            win_end_ts = chunk.iloc[-1]["timestamp"]
            gt_phases = []
            for s in range(int(win_start_ts), int(win_end_ts) + 1):
                p = second_gt.get(s)
                if p:
                    gt_phases.append(p)

            if not gt_phases:
                prev_feats = feats
                history.append(feats)
                continue

            dominant = Counter(gt_phases).most_common(1)[0][0]
            label = PHASE_TO_INT[dominant]

            vec = [feats.get(name, 0.0) for name in FEATURE_NAMES]
            all_features.append(vec)
            all_labels.append(label)
            all_night_ids.append(night_idx)
            prev_feats = feats
            history.append(feats)

        night_dates.append(date_str)
        night_idx += 1

    if not all_features:
        return np.array([]), np.array([]), np.array([]), []

    X = np.array(all_features)
    y = np.array(all_labels)
    night_ids = np.array(all_night_ids)
    return X, y, night_ids, night_dates
