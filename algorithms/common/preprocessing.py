"""Common preprocessing: HRV computation, HR zones, sleep features."""

import numpy as np
import pandas as pd
from datetime import date


def compute_rhr(df: pd.DataFrame, window_sec: int = 300) -> float:
    """Compute resting heart rate as lowest 5-minute average HR."""
    hrs = df["hr"].values
    valid = hrs > 0
    if valid.sum() < window_sec:
        return float(np.median(hrs[valid])) if valid.any() else 60.0

    best = 999.0
    step = 30
    for i in range(0, len(hrs) - window_sec, step):
        chunk = hrs[i:i + window_sec]
        chunk_valid = chunk[chunk > 0]
        if len(chunk_valid) >= window_sec * 0.5:
            avg = chunk_valid.mean()
            if avg < best:
                best = avg
    return best if best < 999 else 60.0


def compute_hrv_rmssd(df: pd.DataFrame, method: str = "sws") -> float:
    """Compute RMSSD from RR intervals.

    method='sws': Whoop-style — find the 5-min window with lowest mean HR during sleep,
                  compute RMSSD only from that window. This matches Whoop's approach of
                  measuring HRV during slow-wave sleep (SWS).
    method='all': Compute RMSSD from all valid RR intervals (naive, gives inflated values).
    """
    rr = df["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 10:
        return 0.0

    if method == "all" or len(rr) < 60:
        diffs = np.diff(rr)
        return float(np.sqrt(np.mean(diffs**2)))

    # SWS method: slide a 5-min window (~300 beats at ~60bpm, use 150-300 RR intervals)
    # Find the window with the lowest mean RR (highest HR = most likely not SWS)
    # Actually: find the window with lowest HR variability + lowest HR → SWS
    win = min(300, len(rr) // 2)
    if win < 30:
        win = len(rr)

    best_rmssd = None
    best_score = float("inf")

    step = max(1, win // 4)
    for i in range(0, len(rr) - win, step):
        chunk = rr[i:i + win]
        diffs = np.diff(chunk)
        # Filter out artifact diffs (>200ms jump = likely artifact)
        diffs = diffs[np.abs(diffs) < 200]
        if len(diffs) < 10:
            continue

        rmssd = float(np.sqrt(np.mean(diffs**2)))
        mean_rr = float(np.mean(chunk))
        std_rr = float(np.std(chunk))

        # Score: prefer windows with high mean_rr (low HR) and low std (stable)
        # SWS has: high RR (low HR ~45-55bpm → RR ~1100-1300ms), low variability
        score = -mean_rr + std_rr * 2  # lower score = more likely SWS

        if score < best_score:
            best_score = score
            best_rmssd = rmssd

    return best_rmssd if best_rmssd is not None else 0.0


def compute_hrv_sdnn(df: pd.DataFrame) -> float:
    """Compute SDNN (standard deviation of NN intervals)."""
    rr = df["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 10:
        return 0.0
    return float(np.std(rr))


def compute_pnn50(df: pd.DataFrame) -> float:
    """Compute pNN50 (percentage of successive RR diffs > 50ms)."""
    rr = df["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 10:
        return 0.0
    diffs = np.abs(np.diff(rr))
    return float(np.sum(diffs > 50) / len(diffs) * 100)


def compute_lf_hf_ratio(df: pd.DataFrame) -> float:
    """Compute LF/HF power ratio from RR intervals using Welch PSD.

    LF (0.04-0.15 Hz): sympathetic + parasympathetic activity
    HF (0.15-0.40 Hz): parasympathetic activity

    Key sleep discriminator:
      deep sleep: LF/HF < 1 (parasympathetic dominant)
      REM: LF/HF > 2 (sympathetic activation)
      awake: LF/HF > 3
    """
    rr = df["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 30:
        return 1.0

    from scipy.interpolate import interp1d
    from scipy.signal import welch

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


def compute_respiratory_rate(df: pd.DataFrame) -> float:
    """Estimate respiratory rate from RR interval modulation (RSA).

    Respiratory sinus arrhythmia causes ~0.15-0.4 Hz modulation in RR intervals.
    We find the dominant frequency in that band.
    """
    rr = df["rr1_ms"].dropna().values
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 60:
        return 14.0  # default

    # Interpolate to uniform sampling at 4Hz
    from scipy.interpolate import interp1d
    from scipy.signal import welch

    cumtime = np.cumsum(rr) / 1000.0  # seconds
    cumtime -= cumtime[0]
    if cumtime[-1] < 30:
        return 14.0

    fs = 4.0
    t_uniform = np.arange(0, cumtime[-1], 1.0 / fs)
    f_interp = interp1d(cumtime, rr, kind="linear", fill_value="extrapolate")
    rr_uniform = f_interp(t_uniform)

    # Detrend
    rr_uniform = rr_uniform - np.mean(rr_uniform)

    freqs, psd = welch(rr_uniform, fs=fs, nperseg=min(256, len(rr_uniform)))
    # Respiratory band: 0.15-0.4 Hz (9-24 breaths/min)
    mask = (freqs >= 0.15) & (freqs <= 0.5)
    if not mask.any():
        return 14.0

    peak_freq = freqs[mask][np.argmax(psd[mask])]
    return float(peak_freq * 60)  # breaths per minute


def compute_hr_zones(df: pd.DataFrame, max_hr: int = 200) -> dict:
    """Compute time in each HR zone (minutes).

    Zone 1: 50-60% MaxHR
    Zone 2: 60-70%
    Zone 3: 70-80%
    Zone 4: 80-90%
    Zone 5: 90-100%
    """
    hrs = df["hr"].values
    hrs = hrs[hrs > 0]

    boundaries = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    zones = {}
    for i in range(5):
        lo = max_hr * boundaries[i]
        hi = max_hr * boundaries[i + 1]
        count = np.sum((hrs >= lo) & (hrs < hi))
        zones[f"zone{i+1}_min"] = float(count / 60)  # seconds to minutes
    zones["zone5_min"] += float(np.sum(hrs >= max_hr) / 60)
    return zones


def compute_daily_features(df: pd.DataFrame, day: date, max_hr: int = 200) -> dict:
    """Compute all features for one day's sensor data."""
    day_df = df[df["date"] == day].copy()
    if day_df.empty:
        return {}

    hrs = day_df["hr"].values
    valid_hr = hrs[hrs > 0]

    features = {
        "date": str(day),
        "n_samples": len(day_df),
        "n_valid_hr": int((hrs > 0).sum()),
        "hr_mean": float(valid_hr.mean()) if len(valid_hr) > 0 else 0,
        "hr_std": float(valid_hr.std()) if len(valid_hr) > 1 else 0,
        "hr_min": int(valid_hr.min()) if len(valid_hr) > 0 else 0,
        "hr_max": int(valid_hr.max()) if len(valid_hr) > 0 else 0,
        "hr_p5": float(np.percentile(valid_hr, 5)) if len(valid_hr) > 0 else 0,
        "hr_p25": float(np.percentile(valid_hr, 25)) if len(valid_hr) > 0 else 0,
        "hr_p75": float(np.percentile(valid_hr, 75)) if len(valid_hr) > 0 else 0,
        "hr_p95": float(np.percentile(valid_hr, 95)) if len(valid_hr) > 0 else 0,
        "rhr": compute_rhr(day_df),
        "hrv_rmssd": compute_hrv_rmssd(day_df),
        "hrv_sdnn": compute_hrv_sdnn(day_df),
        "pnn50": compute_pnn50(day_df),
        "resp_rate": compute_respiratory_rate(day_df),
        "movement_mean": float(day_df["movement"].mean()),
        "movement_std": float(day_df["movement"].std()),
        "movement_max": float(day_df["movement"].max()),
        "movement_p90": float(np.percentile(day_df["movement"].values, 90)),
    }

    # HR zones
    zones = compute_hr_zones(day_df, max_hr=max_hr)
    features.update(zones)

    # SpO2
    spo2_valid = day_df["spo2"].dropna()
    if len(spo2_valid) > 0:
        features["spo2_mean"] = float(spo2_valid.mean())
        features["spo2_min"] = float(spo2_valid.min())
    else:
        features["spo2_mean"] = 97.0
        features["spo2_min"] = 95.0

    return features


def compute_sleep_features(df: pd.DataFrame, day: date) -> dict:
    """Compute sleep-specific features (night before the given day).

    Sleep window: previous day 20:00 to current day 12:00.
    """
    day_str = str(day)
    prev_day = day - pd.Timedelta(days=1)

    # Filter for sleep window (20:00 previous day to 12:00 current day)
    sleep_mask = (
        (df["datetime_local"].dt.date == prev_day.date() if hasattr(prev_day, 'date') else df["date"] == prev_day)
        & (df["datetime_local"].dt.hour >= 20)
    ) | (
        (df["date"] == day)
        & (df["datetime_local"].dt.hour < 12)
    )

    sleep_df = df[sleep_mask].copy()
    if sleep_df.empty:
        return {"sleep_samples": 0}

    hrs = sleep_df["hr"].values
    valid_hr = hrs[hrs > 0]
    rhr = compute_rhr(sleep_df)

    # Classify 5-minute windows
    window = 300
    phases = []
    for i in range(0, len(sleep_df) - window, window):
        chunk = sleep_df.iloc[i:i + window]
        w_hr = chunk["hr"].values
        w_hr_valid = w_hr[w_hr > 0]
        w_mv = chunk["movement"].values

        if len(w_hr_valid) < 10:
            phases.append("unknown")
            continue

        avg_hr = w_hr_valid.mean()
        std_hr = w_hr_valid.std()
        avg_mv = w_mv.mean()
        hr_above = avg_hr - rhr

        if avg_hr > rhr + 15 and avg_mv > 0.4:
            phases.append("awake")
        elif hr_above < 4 and std_hr < 3 and avg_mv < 0.4:
            phases.append("deep")
        elif std_hr > 5 and avg_mv < 0.5:
            phases.append("rem")
        elif hr_above < 12:
            phases.append("light")
        else:
            phases.append("awake")

    total = len(phases)
    if total == 0:
        return {"sleep_samples": len(sleep_df)}

    deep_pct = phases.count("deep") / total * 100
    light_pct = phases.count("light") / total * 100
    rem_pct = phases.count("rem") / total * 100
    awake_pct = phases.count("awake") / total * 100
    sleep_min = (total - phases.count("awake") - phases.count("unknown")) * 5
    efficiency = (total - phases.count("awake") - phases.count("unknown")) / total * 100 if total > 0 else 0

    return {
        "sleep_samples": len(sleep_df),
        "sleep_total_min": sleep_min,
        "sleep_efficiency": efficiency,
        "sleep_deep_pct": deep_pct,
        "sleep_light_pct": light_pct,
        "sleep_rem_pct": rem_pct,
        "sleep_awake_pct": awake_pct,
        "sleep_hr_mean": float(valid_hr.mean()) if len(valid_hr) > 0 else 0,
        "sleep_hr_min": float(valid_hr.min()) if len(valid_hr) > 0 else 0,
        "sleep_rhr": rhr,
        "sleep_hrv_rmssd": compute_hrv_rmssd(sleep_df),
        "sleep_movement_mean": float(sleep_df["movement"].mean()),
    }
