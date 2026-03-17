"""Algo 8: YASA-based sleep staging from HR/HRV data.

YASA (Yet Another Spindle Algorithm) is designed for EEG data, but we can
leverage its sleep statistics and bandpower concepts. Since we only have
HR + accelerometer (no EEG), we build a YASA-inspired pipeline:

1. Convert RR intervals to HRV time series
2. Compute spectral features (LF/HF) in sliding windows
3. Use YASA's sleep statistics computation on our classified stages
4. Apply a multi-feature classifier inspired by YASA's approach

The key insight: YASA uses spectral power in EEG bands; we use spectral
power in HRV bands (VLF/LF/HF) which correlate with sleep stages.
"""

import math
from collections import defaultdict, Counter

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
from scipy.signal import welch


def _rr_to_uniform(rr_ms, fs=4.0):
    """Resample irregular RR intervals to uniform time series."""
    rr = rr_ms[(rr_ms > 200) & (rr_ms < 2500)]
    if len(rr) < 30:
        return None, None
    cumtime = np.cumsum(rr) / 1000.0
    cumtime -= cumtime[0]
    if cumtime[-1] < 30:
        return None, None
    t_uniform = np.arange(0, cumtime[-1], 1.0 / fs)
    if len(t_uniform) < 32:
        return None, None
    f_interp = interp1d(cumtime, rr, kind="linear", fill_value="extrapolate")
    return f_interp(t_uniform), t_uniform


def _compute_spectral_features(rr_window, fs=4.0):
    """Compute HRV spectral features for a window of RR intervals."""
    rr_uniform, _ = _rr_to_uniform(rr_window, fs)
    if rr_uniform is None or len(rr_uniform) < 32:
        return None

    rr_detrend = rr_uniform - np.mean(rr_uniform)
    nperseg = min(128, len(rr_detrend))
    if nperseg < 16:
        return None

    freqs, psd = welch(rr_detrend, fs=fs, nperseg=nperseg)

    # HRV frequency bands
    vlf_mask = (freqs >= 0.003) & (freqs < 0.04)
    lf_mask = (freqs >= 0.04) & (freqs <= 0.15)
    hf_mask = (freqs >= 0.15) & (freqs <= 0.40)

    vlf = np.trapezoid(psd[vlf_mask], freqs[vlf_mask]) if vlf_mask.any() else 0
    lf = np.trapezoid(psd[lf_mask], freqs[lf_mask]) if lf_mask.any() else 0
    hf = np.trapezoid(psd[hf_mask], freqs[hf_mask]) if hf_mask.any() else 0
    total = vlf + lf + hf

    return {
        "vlf": vlf,
        "lf": lf,
        "hf": hf,
        "total_power": total,
        "lf_hf": lf / hf if hf > 1e-10 else 5.0,
        "hf_pct": hf / total * 100 if total > 1e-10 else 33,
        "lf_pct": lf / total * 100 if total > 1e-10 else 33,
    }


def classify_sleep_yasa(sleep_df, rhr, window_sec=120, stride_sec=60):
    """YASA-inspired sleep staging using HRV spectral analysis.

    Uses a multi-feature approach combining:
    - HRV spectral power (LF/HF ratio) - key discriminator
    - Heart rate relative to RHR
    - Movement/accelerometer data
    - Temporal position in sleep cycle (ultradian rhythm ~90min)

    Returns (phases_list, summary_dict).
    """
    if sleep_df.empty or len(sleep_df) < 300:
        return [], _empty_summary()

    phases = []
    hr_all = sleep_df["hr"].values
    mv_all = sleep_df["movement"].values
    rr_all = sleep_df["rr1_ms"].values
    ts_all = sleep_df["timestamp"].values if "timestamp" in sleep_df.columns else np.arange(len(sleep_df))

    sleep_start = ts_all[0] if len(ts_all) > 0 else 0
    total_duration = (ts_all[-1] - ts_all[0]) if len(ts_all) > 1 else 1

    # Sliding window analysis
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

        # Basic features
        avg_hr = float(np.median(hr_v))
        hr_std = float(np.std(hr_v)) if len(hr_v) > 1 else 0
        hr_above_rhr = avg_hr - rhr
        avg_mv = float(np.mean(mv))
        max_mv = float(np.max(mv))

        # Time position for ultradian rhythm
        elapsed = (ts_all[min(i + window_sec // 2, len(ts_all) - 1)] - sleep_start)
        fraction = elapsed / total_duration if total_duration > 0 else 0.5
        # 90-minute cycle phase
        cycle_phase = math.sin(2 * math.pi * elapsed / 5400) if elapsed > 0 else 0

        # HRV spectral features
        spectral = _compute_spectral_features(rr) if len(rr) > 20 else None

        # RMSSD for this window
        local_rmssd = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_rmssd = float(np.sqrt(np.mean(diffs**2)))

        # --- YASA-inspired scoring ---
        # Score each stage based on multiple features
        scores = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}

        # 1. HR relative to RHR
        if hr_above_rhr < -2:
            scores["deep"] += 3.0
        elif hr_above_rhr < 3:
            scores["deep"] += 1.5
            scores["light"] += 1.0
        elif hr_above_rhr < 10:
            scores["light"] += 2.0
        elif hr_above_rhr < 18:
            scores["light"] += 1.0
            scores["rem"] += 1.5
        else:
            scores["awake"] += 3.0

        # 2. HR variability (std) — REM has irregular HR
        if hr_std < 2.5:
            scores["deep"] += 2.0
        elif hr_std < 5:
            scores["light"] += 1.5
        elif hr_std > 8:
            scores["rem"] += 2.5
        else:
            scores["rem"] += 1.0

        # 3. Movement
        if avg_mv > 1.0 or max_mv > 3.0:
            scores["awake"] += 4.0
        elif avg_mv > 0.3:
            scores["awake"] += 1.5
            scores["light"] += 0.5
        elif avg_mv < 0.05:
            scores["deep"] += 1.5
        else:
            scores["light"] += 0.5

        # 4. HRV spectral (the key YASA-like feature)
        if spectral:
            lf_hf = spectral["lf_hf"]
            hf_pct = spectral["hf_pct"]

            # Deep sleep: parasympathetic dominant → high HF, low LF/HF
            if lf_hf < 0.8:
                scores["deep"] += 3.0
            elif lf_hf < 1.5:
                scores["deep"] += 1.5
                scores["light"] += 1.0

            # REM: sympathetic activation → high LF/HF
            if lf_hf > 2.5:
                scores["rem"] += 2.5
            elif lf_hf > 1.8:
                scores["rem"] += 1.5

            # Light sleep: balanced
            if 1.0 <= lf_hf <= 2.0:
                scores["light"] += 1.5

            # HF percentage — high HF = deep sleep
            if hf_pct > 50:
                scores["deep"] += 1.5
            elif hf_pct < 20:
                scores["rem"] += 1.0
                scores["awake"] += 0.5

        # 5. RMSSD
        if local_rmssd > 0:
            if local_rmssd > 120:
                scores["deep"] += 1.5
            elif local_rmssd > 80:
                scores["light"] += 1.0
            elif local_rmssd < 30:
                scores["rem"] += 1.0
                scores["awake"] += 0.5

        # 6. Temporal context (ultradian rhythm)
        # Early night favors deep, late night favors REM
        if fraction < 0.35:
            scores["deep"] += 1.5
            scores["light"] += 0.5
        elif fraction > 0.65:
            scores["rem"] += 1.5
            scores["light"] += 0.5

        # Cycle phase — trough of sine = NREM, peak = REM
        if cycle_phase < -0.5:
            scores["deep"] += 1.0
        elif cycle_phase > 0.5:
            scores["rem"] += 1.0

        phase = max(scores, key=scores.get)
        phases.append({
            "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
            "phase": phase,
        })

    # Smooth isolated phases (remove single-window glitches)
    phases = _smooth_phases(phases)

    summary = _compute_summary(phases, window_sec=stride_sec)
    return phases, summary


def _smooth_phases(phases, min_run=2):
    """Remove isolated single-window phases."""
    if len(phases) < 3:
        return phases

    result = list(phases)
    for i in range(1, len(result) - 1):
        if result[i]["phase"] != result[i - 1]["phase"] and result[i]["phase"] != result[i + 1]["phase"]:
            result[i] = {**result[i], "phase": result[i - 1]["phase"]}
    return result


def _compute_summary(phases, window_sec=60):
    """Compute sleep stage summary from phase list."""
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


class YasaEngine:
    """YASA-inspired sleep analysis engine."""

    def classify_sleep(self, sleep_df, rhr, **kwargs):
        return classify_sleep_yasa(sleep_df, rhr)
