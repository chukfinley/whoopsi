"""Algorithm 4: Whoop-Calibrated Algorithm.

Reverse-engineered to match Whoop's actual scoring by using:
- Whoop's actual HR zones (from API: 0-108, 109-135, 136-149, 150-162, 163-176, 177-189)
- HRV computed during SWS (slow-wave sleep) only
- RHR as lowest 5-min mean during confirmed sleep
- Recovery formula calibrated against 4 days of matched GT
- Strain formula calibrated against actual Whoop strain with coverage adjustment
- Sleep score matching Whoop's contributor weights
"""

import math
import numpy as np
import pandas as pd
from datetime import timedelta


# Whoop's actual HR zones for this user (from hr-zones-service/v1/bff/zones)
WHOOP_ZONES = [
    (0, 108),    # Zone 0 (below zone 1)
    (109, 135),  # Zone 1
    (136, 149),  # Zone 2
    (150, 162),  # Zone 3
    (163, 176),  # Zone 4
    (177, 189),  # Zone 5
]
MAX_HR = 189


def compute_sleep_rhr(sleep_df):
    """Compute RHR the way Whoop does: lowest 5-min rolling average during sleep.

    Whoop RHR is consistently ~52-62bpm for this user. They compute it as
    the lowest 5-minute rolling average during the confirmed sleep period,
    excluding periods classified as awake.
    """
    if sleep_df.empty:
        return 55.0

    hr = sleep_df["hr"].values
    valid = hr[hr > 30]
    if len(valid) < 300:
        return float(np.median(valid)) if len(valid) > 0 else 55.0

    # Whoop RHR appears to be the average HR during the deepest sleep phase,
    # NOT the absolute lowest window. Comparing GT:
    # Jan 30: Whoop RHR=55, our P5=45, P25=51, median=56 → median is closest
    # Jan 31: Whoop RHR=62, our P5=49, P25=56, median=62 → median matches
    # So Whoop uses something like the average of the lowest quartile of sleep HR
    p25 = float(np.percentile(valid, 25))
    median_hr = float(np.median(valid))
    # Use average of P25 and median (gives ~53-59 range matching Whoop)
    return round((p25 + median_hr) / 2, 1)


def compute_sws_hrv(sleep_df):
    """Compute HRV during slow-wave sleep (SWS) window.

    Whoop HRV is measured during the deepest sleep phase. We find the 5-min
    window with the lowest HR + lowest HR variability = most likely SWS,
    then compute RMSSD from that window's RR intervals.
    """
    rr = sleep_df["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 30:
        return 0.0

    # Find the SWS window: lowest HR + most stable
    win = min(300, len(rr) // 2)
    if win < 30:
        # Not enough data for windowed analysis, use all
        diffs = np.diff(rr)
        diffs = diffs[np.abs(diffs) < 200]
        return float(np.sqrt(np.mean(diffs**2))) if len(diffs) > 5 else 0.0

    best_rmssd = None
    best_score = float("inf")

    for i in range(0, len(rr) - win, max(1, win // 4)):
        chunk = rr[i:i + win]
        diffs = np.diff(chunk)
        # Filter artifact diffs
        diffs = diffs[np.abs(diffs) < 200]
        if len(diffs) < 10:
            continue

        rmssd = float(np.sqrt(np.mean(diffs**2)))
        mean_rr = float(np.mean(chunk))
        std_rr = float(np.std(chunk))

        # SWS: high RR (low HR), low variability
        score = -mean_rr + std_rr * 2
        if score < best_score:
            best_score = score
            best_rmssd = rmssd

    return best_rmssd if best_rmssd is not None else 0.0


def compute_whoop_strain(day_df, total_samples):
    """Compute strain using Whoop's actual HR zone boundaries.

    Whoop strain is logarithmic accumulation of time in elevated HR zones.
    We use their actual zone boundaries and scale for HR coverage gaps.
    """
    hr = day_df["hr"].values
    valid_hr = hr[hr > 30]
    if len(valid_hr) == 0:
        return 0.0

    # Coverage adjustment
    coverage = len(valid_hr) / total_samples if total_samples > 0 else 1.0
    scale = min(3.0, 1.0 / coverage) if coverage > 0.1 else 1.0

    # Whoop zone weights: Zone 0 is sub-threshold (no strain contribution)
    # Only zones 1-5 contribute to strain load
    # Calibrated against GT: Jan 28 strain=8.6, Jan 30 strain=11.0, Jan 31 strain=4.9
    zone_weights = [0, 1.0, 2.5, 5.0, 10.0, 20.0]  # zones 0-5

    load = 0
    for zi, (lo, hi) in enumerate(WHOOP_ZONES):
        minutes_in_zone = np.sum((valid_hr >= lo) & (valid_hr <= hi)) / 60.0
        load += minutes_in_zone * zone_weights[zi]

    load *= scale

    # Whoop uses log scale: strain = k * ln(1 + load / c)
    # Calibrated: k=5.0, c=12 gives best match to GT
    if load <= 0:
        return 0.0
    strain = 2.32 * math.log(1 + load / 6.3)
    return min(21.0, round(strain, 1))


def classify_sleep_phases(sleep_df, rhr):
    """Classify sleep phases with more nuanced thresholds."""
    if sleep_df.empty:
        return [], {}

    window = 600  # 10 minutes for more robust stats with sparse HR data
    phases = []

    for i in range(0, len(sleep_df) - window, window):
        chunk = sleep_df.iloc[i:i + window]
        t = chunk["datetime_local"].iloc[0]
        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            phases.append({"time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
                          "phase": "unknown", "hr": 0})
            continue

        # Use median (robust to sparse sampling) instead of mean
        avg_hr = float(np.median(hr_v))
        # IQR as variability measure (robust to outliers from sparse data)
        hr_iqr = float(np.percentile(hr_v, 75) - np.percentile(hr_v, 25)) if len(hr_v) > 10 else float(hr_v.std()) if len(hr_v) > 1 else 0
        avg_mv = float(mv.mean())
        max_mv = float(mv.max())
        ha = avg_hr - rhr

        # Local HRV from RR (filter large artifact diffs)
        local_hrv = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_hrv = float(np.sqrt(np.mean(diffs**2)))

        # Classification using robust metrics
        # Whoop target: ~47% light, ~18% deep, ~32% REM, ~3% awake
        is_moving = avg_mv > 0.8 or max_mv > 2.4

        if is_moving and ha > 24.7:
            phase = "awake"
        elif ha <= 14.2 and hr_iqr < 5.7 and avg_mv < 2.8:
            # Low HR + stable + still = deep sleep
            phase = "deep"
        elif hr_iqr > 10.2 and avg_mv < 0.7:
            # High HR variability during stillness = REM
            phase = "rem"
        elif local_hrv > 152.6 and avg_mv < 0.7:
            phase = "rem"
        else:
            phase = "light"
        phases.append({"time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
                       "phase": phase, "hr": round(avg_hr, 1),
                       "movement": round(avg_mv, 3), "hrv": round(local_hrv, 1)})

    # Phase summary
    total = len(phases)
    if total == 0:
        return phases, {"total_min": 0, "sleep_min": 0, "efficiency": 0,
                       "deep_pct": 0, "light_pct": 0, "rem_pct": 0, "awake_pct": 0}

    from collections import Counter
    counts = Counter(p["phase"] for p in phases)
    sleep_count = total - counts["awake"] - counts["unknown"]

    summary = {
        "total_min": total * 10,
        "sleep_min": sleep_count * 10,
        "efficiency": round(sleep_count / total * 100, 1) if total > 0 else 0,
        "deep_min": counts["deep"] * 10,
        "light_min": counts["light"] * 10,
        "rem_min": counts["rem"] * 10,
        "awake_min": counts["awake"] * 10,
        "deep_pct": round(counts["deep"] / total * 100, 1),
        "light_pct": round(counts["light"] / total * 100, 1),
        "rem_pct": round(counts["rem"] / total * 100, 1),
        "awake_pct": round(counts["awake"] / total * 100, 1),
    }
    return phases, summary


def compute_sleep_score(summary, sleep_need_min=480):
    """Compute sleep score matching Whoop's contributor weights.

    Whoop sleep score contributors:
    - Hours vs Needed (40%): sleep_min / need
    - Sleep Consistency (15%): how close to regular schedule (we approximate)
    - Sleep Efficiency (30%): time asleep / time in bed
    - High Sleep Stress (15%): inverse of awake/restless %
    """
    total_min = summary.get("sleep_min", 0)
    efficiency = summary.get("efficiency", 0)
    awake_pct = summary.get("awake_pct", 0)

    hours_score = min(100, (total_min / sleep_need_min) * 100)
    eff_score = min(100, efficiency)
    # Sleep consistency: we don't have multi-day schedule data, use 62% (GT average)
    consistency_score = 41.5
    # Sleep stress: inverse of awake %
    stress_score = max(0, 100 - awake_pct * 5)

    # Whoop GT shows: Jan 30 sleep=83 (100% hours, 62% consistency, 97% efficiency, 1% stress)
    # Jan 31 sleep=78 (100% hours, 49% consistency, 89% efficiency, 4% stress)
    # This maps well to: 0.35 * hours + 0.15 * consistency + 0.35 * efficiency + 0.15 * (100-stress)
    score = (0.23 * hours_score + 0.34 * consistency_score +
             0.26 * eff_score + 0.18 * stress_score)
    return max(0, min(100, round(score)))


def compute_recovery(hrv, rhr, sleep_score, resp_rate, hrv_baseline, rhr_baseline):
    """Compute recovery matching Whoop's scoring.

    Whoop recovery is primarily HRV-driven. Calibrated against:
    - Jan 28: hrv=96, rhr=54, rec=72 (green)
    - Jan 29: hrv=102, rhr=52, rec=79 (green)
    - Jan 30: hrv=96, rhr=55, rec=71 (green)
    - Jan 31: hrv=80, rhr=62, rec=45 (yellow)

    The key insight: Whoop uses HRV relative to personal baseline as primary driver.
    When HRV drops from 96→80 (17% drop), recovery drops from 71→45 (37% drop).
    This suggests a steep sigmoid relationship.
    """
    if hrv_baseline <= 0:
        hrv_baseline = 90

    # HRV score: very steep sigmoid
    # Calibration points:
    #   HRV=102 (110% baseline) → rec=79 → hrv_score~85
    #   HRV=96  (104% baseline) → rec=71-72 → hrv_score~75
    #   HRV=80  (87% baseline)  → rec=45 → hrv_score~35
    hrv_ratio = hrv / hrv_baseline
    hrv_score = 100 / (1 + math.exp(-11.6 * (hrv_ratio - 1.107)))

    # RHR score: deviation from baseline
    # Lower RHR = better recovery
    # Jan 30: RHR=55, baseline~54 → modest impact
    # Jan 31: RHR=62, baseline~54 → big negative impact
    if rhr_baseline > 0:
        rhr_diff = rhr_baseline - rhr  # positive = better
        rhr_score = max(0, min(100, 50 + rhr_diff * 8.1))
    else:
        rhr_score = max(0, min(100, 100 - (rhr - 45) * 2))

    # Respiratory rate penalty
    resp_penalty = max(0, (resp_rate - 16) * 3) if resp_rate > 16 else 0

    # Sleep contribution
    sleep_contrib = min(100, sleep_score)

    # Weighted: HRV dominates
    recovery = (0.09 * hrv_score + 0.43 * rhr_score +
                0.33 * sleep_contrib + 0.14 * (100 - resp_penalty))

    return max(0, min(100, round(recovery)))


def compute_respiratory_rate(sleep_df):
    """Respiratory rate from RR interval RSA modulation."""
    rr = sleep_df["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 60:
        return 14.0

    from scipy.interpolate import interp1d
    from scipy.signal import welch

    cumtime = np.cumsum(rr) / 1000.0
    cumtime -= cumtime[0]
    if cumtime[-1] < 30:
        return 14.0

    fs = 4.0
    t_uniform = np.arange(0, cumtime[-1], 1.0 / fs)
    f_interp = interp1d(cumtime, rr, kind="linear", fill_value="extrapolate")
    rr_uniform = f_interp(t_uniform)
    rr_uniform = rr_uniform - np.mean(rr_uniform)

    freqs, psd = welch(rr_uniform, fs=fs, nperseg=min(256, len(rr_uniform)))
    mask = (freqs >= 0.15) & (freqs <= 0.5)
    if not mask.any():
        return 14.0

    peak_freq = freqs[mask][np.argmax(psd[mask])]
    return round(float(peak_freq * 60), 1)
