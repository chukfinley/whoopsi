#!/usr/bin/env python3
"""Experiment runner for sleep staging model improvements.

Runs ablation studies and reports LONO accuracy for each configuration.
Loads data once, then iterates over experiments quickly.

Usage:
    python3 experiment_runner.py
"""

import sys
import re
import math
import warnings
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter
from itertools import product

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
from scipy.signal import welch as welch_psd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data.db_loader import load_from_db
from common.preprocessing import compute_rhr

warnings.filterwarnings("ignore")

BERLIN = timedelta(hours=1)
MIN_SLEEP_SAMPLES = 18000

PHASE_TO_INT = {"awake": 0, "light": 1, "deep": 2, "rem": 3}
INT_TO_PHASE = {v: k for k, v in PHASE_TO_INT.items()}

STAGE_MAP = {
    "AWAKE": "awake",
    "LIGHT_SLEEP": "light",
    "SWS_SLEEP": "deep",
    "REM_SLEEP": "rem",
}


# ---------------------------------------------------------------------------
# Viterbi decoder (from algo5_ml)
# ---------------------------------------------------------------------------

def viterbi_decode(log_probs, log_trans, log_init):
    """Viterbi algorithm for most likely state sequence."""
    T, K = log_probs.shape
    V = np.full((T, K), -np.inf)
    backptr = np.zeros((T, K), dtype=int)
    V[0] = log_init + log_probs[0]
    for t in range(1, T):
        for j in range(K):
            scores = V[t - 1] + log_trans[:, j]
            best_i = np.argmax(scores)
            V[t, j] = scores[best_i] + log_probs[t, j]
            backptr[t, j] = best_i
    path = np.zeros(T, dtype=int)
    path[-1] = np.argmax(V[-1])
    for t in range(T - 2, -1, -1):
        path[t] = backptr[t + 1, path[t + 1]]
    return path


def learn_transition_matrix(y_all, night_boundaries):
    """Learn transition matrix from training labels.

    night_boundaries: list of (start_idx, end_idx) for each night in y_all.
    """
    K = 4
    counts = np.ones((K, K)) * 0.1  # Laplace smoothing
    init_counts = np.ones(K) * 0.1

    for start, end in night_boundaries:
        night_labels = y_all[start:end]
        if len(night_labels) == 0:
            continue
        init_counts[night_labels[0]] += 1
        for i in range(len(night_labels) - 1):
            counts[night_labels[i], night_labels[i + 1]] += 1

    trans = counts / counts.sum(axis=1, keepdims=True)
    log_trans = np.log(np.clip(trans, 1e-10, 1.0))

    init_probs = init_counts / init_counts.sum()
    log_init = np.log(np.clip(init_probs, 1e-10, 1.0))

    return log_trans, log_init


def apply_viterbi(model, X_test, log_trans, log_init):
    """Apply Viterbi post-processing to model predictions."""
    proba = model.predict_proba(X_test)
    # Ensure all 4 classes are represented
    classes = list(model.classes_)
    if len(classes) < 4:
        full_proba = np.full((len(X_test), 4), 1e-10)
        for i, c in enumerate(classes):
            full_proba[:, c] = proba[:, i]
        proba = full_proba
    log_probs = np.log(np.clip(proba, 1e-10, 1.0))
    return viterbi_decode(log_probs, log_trans, log_init)


# ---------------------------------------------------------------------------
# Sleep onset/offset detection
# ---------------------------------------------------------------------------

def detect_sleep_onset_offset(sleep_df, hr_drop_threshold=5.0, gyro_threshold=0.1,
                               sustained_minutes=5):
    """Detect sleep onset and offset from HR and gyro data.

    Sleep onset: sustained HR drop (>hr_drop_threshold from pre-sleep baseline)
                 + gyro_max drops below gyro_threshold
    Sleep offset: HR rises + movement increases sustained for >sustained_minutes

    Returns: (onset_ts, offset_ts) or (None, None) if detection fails.
    """
    if sleep_df.empty or len(sleep_df) < 600:
        return None, None

    ts = sleep_df["timestamp"].values.astype(int)
    hr = sleep_df["hr"].values.astype(float)

    # Get gyro if available
    has_gyro = "gyro" in sleep_df.columns
    if has_gyro:
        gyro = sleep_df["gyro"].values.astype(float)

    # Compute 5-minute rolling HR averages
    window = 300  # 5 minutes in seconds
    n = len(hr)

    # Compute baseline from first 30 minutes (pre-sleep period)
    baseline_end = min(1800, n // 3)  # first 30 min or 1/3 of data
    hr_baseline_vals = hr[:baseline_end]
    hr_baseline_vals = hr_baseline_vals[hr_baseline_vals > 30]
    if len(hr_baseline_vals) < 60:
        return None, None
    hr_baseline = float(np.median(hr_baseline_vals))

    # Sliding window to find onset
    onset_ts_val = None
    for i in range(0, n - window, 60):  # step by 1 minute
        chunk_hr = hr[i:i+window]
        chunk_hr_valid = chunk_hr[chunk_hr > 30]
        if len(chunk_hr_valid) < 30:
            continue

        chunk_hr_mean = float(np.mean(chunk_hr_valid))
        hr_drop = hr_baseline - chunk_hr_mean

        if hr_drop >= hr_drop_threshold:
            # Check gyro if available
            if has_gyro:
                chunk_gyro = gyro[i:i+window]
                gyro_max = float(np.max(np.abs(chunk_gyro)))
                if gyro_max < gyro_threshold:
                    onset_ts_val = int(ts[i])
                    break
            else:
                # Without gyro, just use HR drop sustained for 5 min
                onset_ts_val = int(ts[i])
                break

    # Sliding window from end to find offset
    offset_ts_val = None
    for i in range(n - 1, window, -60):  # step backwards by 1 minute
        chunk_hr = hr[max(0, i-window):i]
        chunk_hr_valid = chunk_hr[chunk_hr > 30]
        if len(chunk_hr_valid) < 30:
            continue

        chunk_hr_mean = float(np.mean(chunk_hr_valid))
        hr_rise = chunk_hr_mean - hr_baseline + hr_drop_threshold  # relative to sleep level

        if has_gyro:
            chunk_gyro = gyro[max(0, i-window):i]
            gyro_max = float(np.max(np.abs(chunk_gyro)))
            if gyro_max > gyro_threshold and chunk_hr_mean > hr_baseline - hr_drop_threshold + 3:
                offset_ts_val = int(ts[i])
                break
        else:
            if chunk_hr_mean > hr_baseline - 2:  # HR nearly back to baseline
                offset_ts_val = int(ts[i])
                break

    return onset_ts_val, offset_ts_val


# ---------------------------------------------------------------------------
# Data loading (shared with train_whoop_model.py)
# ---------------------------------------------------------------------------

def get_sleep_window(df, day):
    prev = day - timedelta(days=1)
    mask = (
        (df["date"] == prev)
        & (df["datetime_local"].apply(lambda x: x.hour if hasattr(x, "hour") else 0) >= 20)
    ) | (
        (df["date"] == day)
        & (df["datetime_local"].apply(lambda x: x.hour if hasattr(x, "hour") else 12) < 12)
    )
    return df[mask]


def extract_whoop_labels(date_str):
    base = Path(__file__).resolve().parent.parent
    candidates = [
        base / "ble-sync" / "data" / "backup" / "api" / "deep_dive" / date_str / "sleep_lastnight.json",
        base / "ble-sync" / "data" / "whoop_backup" / "deep_dive" / f"{date_str}.json",
    ]
    text = None
    for sln in candidates:
        if sln.exists():
            try:
                text = sln.read_text(errors="replace")
                if "scrubber_style" in text:
                    break
            except Exception:
                continue
    if not text:
        return []
    pattern = (
        r'"secondary_contextual_display"\s*:\s*"([^"]+)"\s*,'
        r'\s*"scrubber_style"\s*:\s*"(AWAKE|LIGHT_SLEEP|SWS_SLEEP|REM_SLEEP)"'
    )
    matches = re.findall(pattern, text)
    if not matches:
        return []
    results = []
    seen = set()
    for time_str, stage in matches:
        phase = STAGE_MAP.get(stage, "light")
        try:
            t = datetime.strptime(time_str.strip(), "%I:%M %p")
            time_24 = t.strftime("%H:%M")
        except ValueError:
            time_24 = time_str
        if time_24 not in seen:
            seen.add(time_24)
            results.append((time_24, phase))
    def sort_key(item):
        h, m = item[0].split(":")
        mins = int(h) * 60 + int(m)
        if mins < 720:
            mins += 1440
        return mins
    results.sort(key=sort_key)
    return results


def align_labels_to_sensor(sleep_df, labels, day):
    if not labels or sleep_df.empty:
        return {}
    label_map = {}
    for time_24, phase in labels:
        label_map[time_24] = phase
    aligned = {}
    dt_series = sleep_df["datetime_local"]
    ts_series = sleep_df["timestamp"].values.astype(int)
    hours = dt_series.apply(lambda x: x.hour if hasattr(x, "hour") else 0).values
    minutes = dt_series.apply(lambda x: x.minute if hasattr(x, "minute") else 0).values
    minute_ts_arr = ts_series - (ts_series % 60)
    seen = set()
    for i in range(len(sleep_df)):
        mt = int(minute_ts_arr[i])
        if mt in seen:
            continue
        seen.add(mt)
        time_key = f"{hours[i]:02d}:{minutes[i]:02d}"
        if time_key in label_map:
            aligned[mt] = label_map[time_key]
    return aligned


# ---------------------------------------------------------------------------
# Feature extraction (extended version)
# ---------------------------------------------------------------------------

def _compute_spectral_features(rr_vals):
    lf_p, hf_p, lf_hf = 0.0, 0.0, 1.5
    if len(rr_vals) < 20:
        return lf_p, hf_p, lf_hf
    rr = rr_vals[(rr_vals > 200) & (rr_vals < 2500)]
    if len(rr) < 20:
        return lf_p, hf_p, lf_hf
    try:
        cumtime = np.cumsum(rr) / 1000.0
        cumtime -= cumtime[0]
        if cumtime[-1] < 15:
            return lf_p, hf_p, lf_hf
        fs = 4.0
        t_uni = np.arange(0, cumtime[-1], 1.0 / fs)
        if len(t_uni) < 32:
            return lf_p, hf_p, lf_hf
        f_int = interp1d(cumtime, rr, kind="linear", fill_value="extrapolate")
        rr_uni = f_int(t_uni) - np.mean(rr)
        nperseg = min(128, len(rr_uni))
        freqs, psd = welch_psd(rr_uni, fs=fs, nperseg=nperseg)
        lf_mask = (freqs >= 0.04) & (freqs <= 0.15)
        hf_mask = (freqs >= 0.15) & (freqs <= 0.40)
        lf_p = float(np.trapezoid(psd[lf_mask], freqs[lf_mask])) if lf_mask.any() else 0.0
        hf_p = float(np.trapezoid(psd[hf_mask], freqs[hf_mask])) if hf_mask.any() else 0.0
        lf_hf = lf_p / hf_p if hf_p > 1e-10 else 5.0
    except Exception:
        pass
    return lf_p, hf_p, lf_hf


def extract_minute_features_extended(chunk, rhr, hours_since_onset, fraction_of_night):
    """Extract extended features for a 1-minute window of sensor data.

    Returns feature dict or None if insufficient data.
    """
    hr = chunk["hr"].values
    hr_valid = hr[hr > 30]
    if len(hr_valid) < 5:
        return None

    rr_all = chunk["rr1_ms"].dropna().values
    rr_valid = rr_all[(rr_all > 200) & (rr_all < 2500)]

    # --- HR basic ---
    hr_mean = float(np.mean(hr_valid))
    hr_median = float(np.median(hr_valid))
    hr_std = float(np.std(hr_valid)) if len(hr_valid) > 1 else 0.0
    hr_min = float(np.min(hr_valid))
    hr_max = float(np.max(hr_valid))
    hr_p10 = float(np.percentile(hr_valid, 10))
    hr_p90 = float(np.percentile(hr_valid, 90))
    hr_iqr = float(np.percentile(hr_valid, 75) - np.percentile(hr_valid, 25))
    hr_above_rhr = hr_mean - rhr
    hr_range = float(hr_max - hr_min)
    hr_spikes_val = float(np.sum(np.abs(np.diff(hr_valid)) > 5)) if len(hr_valid) > 1 else 0.0

    # --- HR derivative features (NEW) ---
    hr_diff1 = np.diff(hr_valid) if len(hr_valid) > 1 else np.array([0.0])
    hr_diff2 = np.diff(hr_diff1) if len(hr_diff1) > 1 else np.array([0.0])
    hr_accel = float(np.mean(np.abs(hr_diff2))) if len(hr_diff2) > 0 else 0.0  # 2nd derivative
    from scipy.stats import skew as scipy_skew
    hr_skewness = float(scipy_skew(hr_valid)) if len(hr_valid) >= 3 else 0.0

    # --- RR / HRV time-domain ---
    if len(rr_valid) >= 5:
        rr_mean = float(np.mean(rr_valid))
        rr_std_val = float(np.std(rr_valid))
        diffs = np.diff(rr_valid)
        diffs_clean = diffs[np.abs(diffs) < 300]
        if len(diffs_clean) >= 3:
            rmssd = float(np.sqrt(np.mean(diffs_clean ** 2)))
            sdnn = float(np.std(rr_valid))
            pnn50 = float(np.sum(np.abs(diffs_clean) > 50) / len(diffs_clean) * 100)
            pnn20 = float(np.sum(np.abs(diffs_clean) > 20) / len(diffs_clean) * 100)
        else:
            rmssd = sdnn = 0.0
            pnn50 = pnn20 = 0.0
    else:
        rr_mean = 1000.0
        rr_std_val = 0.0
        rmssd = sdnn = 0.0
        pnn50 = pnn20 = 0.0

    # --- Spectral ---
    lf_power, hf_power, lf_hf_ratio = _compute_spectral_features(rr_valid)

    # --- Nonlinear ---
    if len(rr_valid) >= 5:
        diffs = np.diff(rr_valid)
        diffs_clean = diffs[np.abs(diffs) < 300]
        if len(diffs_clean) >= 3:
            sd1 = float(np.std(diffs_clean) / np.sqrt(2))
            total_var = float(np.std(rr_valid))
            sd2 = float(np.sqrt(max(2 * total_var ** 2 - sd1 ** 2, 0.01)))
            sd1_sd2_ratio = sd1 / sd2 if sd2 > 0.01 else 1.0
        else:
            sd1 = sd2 = 0.0
            sd1_sd2_ratio = 1.0
        cv = float(np.std(rr_valid) / np.mean(rr_valid)) if np.mean(rr_valid) > 0 else 0.0
    else:
        sd1 = sd2 = 0.0
        sd1_sd2_ratio = 1.0
        cv = 0.0

    # --- Temporal ---
    elapsed_sec = hours_since_onset * 3600
    ultradian_sin = math.sin(2 * math.pi * elapsed_sec / 5400)
    ultradian_cos = math.cos(2 * math.pi * elapsed_sec / 5400)
    circadian_sin = math.sin(2 * math.pi * hours_since_onset / 24)
    circadian_cos = math.cos(2 * math.pi * hours_since_onset / 24)

    # --- Accel / Gyro / SpO2 ---
    accel_mag_mean = accel_mag_std = 0.0
    gyro_mean_val = gyro_std_val = 0.0
    spo2_mean_val = spo2_std_val = 0.0
    gyro_max_val = gyro_spikes_val = 0.0
    acc_jerk_max_val = acc_jerk_p95_val = 0.0
    acc_energy = 0.0  # NEW: sum of squared acceleration
    acc_zcr = 0.0     # NEW: zero-crossing rate

    if "acc_x" in chunk.columns:
        ax = chunk["acc_x"].values
        ay = chunk["acc_y"].values
        az = chunk["acc_z"].values
        mag = np.sqrt(ax**2 + ay**2 + az**2)
        mag_nonzero = mag[mag > 0.01]
        if len(mag_nonzero) > 0:
            accel_mag_mean = float(np.mean(mag_nonzero))
            accel_mag_std = float(np.std(mag_nonzero))
            # Energy: sum of squared magnitudes (normalized by window size)
            acc_energy = float(np.sum(mag_nonzero**2) / len(mag_nonzero))
        if len(mag) > 1:
            jerk = np.abs(np.diff(mag))
            acc_jerk_max_val = float(np.max(jerk))
            acc_jerk_p95_val = float(np.percentile(jerk, 95))
            # Zero-crossing rate of acceleration changes
            mag_centered = mag - np.mean(mag)
            if len(mag_centered) > 1:
                crossings = np.sum(np.abs(np.diff(np.sign(mag_centered))) > 0)
                acc_zcr = float(crossings / len(mag_centered))

    if "gyro" in chunk.columns:
        gyro_vals = chunk["gyro"].values
        gyro_nz = gyro_vals[np.abs(gyro_vals) > 0.001]
        if len(gyro_nz) > 0:
            gyro_mean_val = float(np.mean(np.abs(gyro_nz)))
            gyro_std_val = float(np.std(gyro_nz))
        gyro_abs = np.abs(gyro_vals)
        gyro_max_val = float(np.max(gyro_abs)) if len(gyro_abs) > 0 else 0.0
        gyro_spikes_val = float(np.sum(gyro_abs > 0.05))

    if "spo2" in chunk.columns:
        spo2 = chunk["spo2"].dropna().values
        if len(spo2) > 0:
            spo2_mean_val = float(np.mean(spo2))
        if len(spo2) > 1:
            spo2_std_val = float(np.std(spo2))

    # --- Cross-features (NEW) ---
    hr_std_x_gyro_std = hr_std * gyro_std_val  # interaction
    rmssd_over_hr = rmssd / hr_mean if hr_mean > 0 else 0.0  # normalized HRV

    feats = {
        # HR basic (8)
        "hr_mean": hr_mean, "hr_median": hr_median, "hr_std": hr_std,
        "hr_min": hr_min, "hr_max": hr_max,
        "hr_p10": hr_p10, "hr_p90": hr_p90, "hr_iqr": hr_iqr,
        # HR relative (1)
        "hr_above_rhr": hr_above_rhr,
        # RR / HRV (6)
        "rr_mean": rr_mean, "rr_std": rr_std_val, "rmssd": rmssd,
        "sdnn": sdnn, "pnn50": pnn50, "pnn20": pnn20,
        # Spectral (3)
        "lf_power": lf_power, "hf_power": hf_power, "lf_hf_ratio": lf_hf_ratio,
        # Nonlinear (4)
        "sd1": sd1, "sd2": sd2, "sd1_sd2_ratio": sd1_sd2_ratio, "cv": cv,
        # Temporal (6)
        "hours_since_onset": hours_since_onset, "fraction_of_night": fraction_of_night,
        "ultradian_sin": ultradian_sin, "ultradian_cos": ultradian_cos,
        "circadian_sin": circadian_sin, "circadian_cos": circadian_cos,
        # Rolling (placeholders, filled later)
        "roll5_hr_mean": 0.0, "roll5_rmssd_mean": 0.0,
        "roll10_hr_mean": 0.0, "roll10_rmssd_mean": 0.0,
        # Delta (placeholders)
        "delta_hr": 0.0, "delta_rmssd": 0.0, "delta_lf_hf": 0.0, "delta_rr_mean": 0.0,
        # Accel/Gyro/SpO2 (5)
        "accel_mag_mean": accel_mag_mean, "accel_mag_std": accel_mag_std,
        "gyro_mean": gyro_mean_val, "gyro_std": gyro_std_val, "spo2_mean": spo2_mean_val,
        # Awake features (6)
        "gyro_max": gyro_max_val, "gyro_spikes": gyro_spikes_val,
        "acc_jerk_max": acc_jerk_max_val, "acc_jerk_p95": acc_jerk_p95_val,
        "hr_range": hr_range, "hr_spikes": hr_spikes_val,
        # SpO2 std (1)
        "spo2_std": spo2_std_val,
        # NEW features
        "roll3_hr_mean": 0.0, "roll3_rmssd_mean": 0.0,  # 3-min rolling
        "roll15_hr_mean": 0.0, "roll15_rmssd_mean": 0.0,  # 15-min rolling
        "acc_energy": acc_energy,
        "acc_zcr": acc_zcr,
        "hr_accel": hr_accel,  # 2nd derivative of HR
        "hr_skewness": hr_skewness,
        "hr_std_x_gyro_std": hr_std_x_gyro_std,  # interaction
        "rmssd_over_hr": rmssd_over_hr,  # normalized HRV
    }

    return feats


def add_rolling_and_delta_extended(features_list):
    """Add rolling averages and delta features to list of feature dicts."""
    n = len(features_list)
    for i in range(n):
        # Rolling 3-minute
        start3 = max(0, i - 2)
        w3 = features_list[start3:i+1]
        features_list[i]["roll3_hr_mean"] = np.mean([f["hr_mean"] for f in w3])
        features_list[i]["roll3_rmssd_mean"] = np.mean([f["rmssd"] for f in w3])

        # Rolling 5-minute
        start5 = max(0, i - 4)
        w5 = features_list[start5:i+1]
        features_list[i]["roll5_hr_mean"] = np.mean([f["hr_mean"] for f in w5])
        features_list[i]["roll5_rmssd_mean"] = np.mean([f["rmssd"] for f in w5])

        # Rolling 10-minute
        start10 = max(0, i - 9)
        w10 = features_list[start10:i+1]
        features_list[i]["roll10_hr_mean"] = np.mean([f["hr_mean"] for f in w10])
        features_list[i]["roll10_rmssd_mean"] = np.mean([f["rmssd"] for f in w10])

        # Rolling 15-minute
        start15 = max(0, i - 14)
        w15 = features_list[start15:i+1]
        features_list[i]["roll15_hr_mean"] = np.mean([f["hr_mean"] for f in w15])
        features_list[i]["roll15_rmssd_mean"] = np.mean([f["rmssd"] for f in w15])

        # Delta from previous
        if i > 0:
            features_list[i]["delta_hr"] = features_list[i]["hr_mean"] - features_list[i-1]["hr_mean"]
            features_list[i]["delta_rmssd"] = features_list[i]["rmssd"] - features_list[i-1]["rmssd"]
            features_list[i]["delta_lf_hf"] = features_list[i]["lf_hf_ratio"] - features_list[i-1]["lf_hf_ratio"]
            features_list[i]["delta_rr_mean"] = features_list[i]["rr_mean"] - features_list[i-1]["rr_mean"]


def build_night_data_extended(sleep_df, aligned_labels, rhr, feature_names):
    """Build feature matrix and labels for one night using extended features."""
    if not aligned_labels or sleep_df.empty:
        return None, None, None

    ts_arr = sleep_df["timestamp"].values
    sleep_start_ts = int(ts_arr[0])
    sleep_end_ts = int(ts_arr[-1])
    total_dur = max(sleep_end_ts - sleep_start_ts, 1)

    sorted_minutes = sorted(aligned_labels.keys())
    if not sorted_minutes:
        return None, None, None

    features_list = []
    labels_list = []
    times_list = []

    for minute_ts in sorted_minutes:
        phase = aligned_labels[minute_ts]
        label = PHASE_TO_INT.get(phase)
        if label is None:
            continue

        mask = (sleep_df["timestamp"] >= minute_ts) & (sleep_df["timestamp"] < minute_ts + 60)
        chunk = sleep_df[mask]
        if len(chunk) < 5:
            continue

        hours_since = (minute_ts - sleep_start_ts) / 3600.0
        fraction = (minute_ts - sleep_start_ts) / total_dur

        feat = extract_minute_features_extended(chunk, rhr, hours_since, fraction)
        if feat is None:
            continue

        features_list.append(feat)
        labels_list.append(label)

        dt = datetime.fromtimestamp(minute_ts, timezone.utc) + BERLIN
        times_list.append(dt.strftime("%H:%M"))

    if not features_list:
        return None, None, None

    add_rolling_and_delta_extended(features_list)

    # Convert to numpy array using feature_names ordering
    X = np.array([[f.get(name, 0.0) for name in feature_names] for f in features_list])
    y = np.array(labels_list)

    return X, y, times_list


def compute_stage_pcts(phases):
    if not phases:
        return {}
    total = len(phases)
    counts = Counter(p if isinstance(p, str) else p.get("phase", "light") for p in phases)
    return {
        "deep_pct": round(counts.get("deep", 0) / total * 100, 1),
        "light_pct": round(counts.get("light", 0) / total * 100, 1),
        "rem_pct": round(counts.get("rem", 0) / total * 100, 1),
        "awake_pct": round(counts.get("awake", 0) / total * 100, 1),
    }


def compute_mae(pcts_a, pcts_b):
    if not pcts_a or not pcts_b:
        return None
    errors = []
    for key in ("deep_pct", "light_pct", "rem_pct", "awake_pct"):
        va = pcts_a.get(key)
        vb = pcts_b.get(key)
        if va is not None and vb is not None:
            errors.append(abs(va - vb))
    return round(sum(errors) / len(errors), 1) if errors else None


# ---------------------------------------------------------------------------
# LONO evaluation
# ---------------------------------------------------------------------------

def run_lono(all_nights, feature_names, model_params, use_viterbi=False, verbose=False):
    """Run leave-one-night-out cross-validation.

    Returns dict with accuracy, mae, per-class recall, per-night results.
    """
    night_accuracies = []
    night_maes = []
    all_y_true = []
    all_y_pred = []

    for hold_idx in range(len(all_nights)):
        held = all_nights[hold_idx]
        train_nights = [n for i, n in enumerate(all_nights) if i != hold_idx]

        X_train = np.concatenate([n["X"] for n in train_nights])
        y_train = np.concatenate([n["y"] for n in train_nights])
        X_train = np.nan_to_num(X_train, nan=0.0, posinf=5.0, neginf=-5.0)

        X_test = np.nan_to_num(held["X"], nan=0.0, posinf=5.0, neginf=-5.0)
        y_test = held["y"]

        model = HistGradientBoostingClassifier(**model_params)
        model.fit(X_train, y_train)

        if use_viterbi:
            # Learn transition matrix from training data
            boundaries = []
            offset = 0
            for n in train_nights:
                boundaries.append((offset, offset + len(n["y"])))
                offset += len(n["y"])
            log_trans, log_init = learn_transition_matrix(y_train, boundaries)
            y_pred = apply_viterbi(model, X_test, log_trans, log_init)
        else:
            y_pred = model.predict(X_test)
            # Smooth isolated predictions
            for i in range(1, len(y_pred) - 1):
                if y_pred[i] != y_pred[i-1] and y_pred[i] != y_pred[i+1]:
                    y_pred[i] = y_pred[i-1]

        acc = accuracy_score(y_test, y_pred)
        night_accuracies.append(acc)
        all_y_true.extend(y_test)
        all_y_pred.extend(y_pred)

        whoop_pcts = compute_stage_pcts([INT_TO_PHASE[int(v)] for v in y_test])
        pred_pcts = compute_stage_pcts([INT_TO_PHASE[int(v)] for v in y_pred])
        mae = compute_mae(whoop_pcts, pred_pcts)
        night_maes.append(mae if mae else 0.0)

        if verbose:
            print(f"  Night {hold_idx+1}/{len(all_nights)}: {held['date']} "
                  f"acc={acc:.1%} MAE={mae}")

    all_y_true = np.array(all_y_true)
    all_y_pred = np.array(all_y_pred)
    cm = confusion_matrix(all_y_true, all_y_pred, labels=[0, 1, 2, 3])

    # Per-class recall
    recalls = {}
    for idx, name in INT_TO_PHASE.items():
        total = cm[idx].sum()
        recalls[name] = cm[idx][idx] / total * 100 if total > 0 else 0

    return {
        "accuracy": float(np.mean(night_accuracies)),
        "accuracy_std": float(np.std(night_accuracies)),
        "mae": float(np.mean(night_maes)),
        "mae_std": float(np.std(night_maes)),
        "recalls": recalls,
        "per_night_acc": night_accuracies,
        "per_night_mae": night_maes,
        "confusion_matrix": cm,
    }


# ---------------------------------------------------------------------------
# Load all nights data
# ---------------------------------------------------------------------------

def load_all_nights(feature_names):
    """Load and prepare all nights data."""
    print("Loading sensor data...")
    df = load_from_db()
    df = df[(df["date"].apply(lambda d: 2025 <= d.year <= 2026 if hasattr(d, "year") else False))]
    dates = sorted(df["date"].unique())
    print(f"  {len(df)} samples, {len(dates)} dates")

    print("Building night datasets...")
    all_nights = []
    for day in dates:
        date_str = str(day)
        sleep_df = get_sleep_window(df, day)
        if len(sleep_df) < MIN_SLEEP_SAMPLES:
            continue
        labels = extract_whoop_labels(date_str)
        if not labels:
            continue
        rhr = compute_rhr(sleep_df)
        aligned = align_labels_to_sensor(sleep_df, labels, day)
        if len(aligned) < 60:
            continue

        X, y, times = build_night_data_extended(sleep_df, aligned, rhr, feature_names)
        if X is None or len(X) < 60:
            continue

        all_nights.append({
            "date": date_str,
            "X": X,
            "y": y,
            "times": times,
            "sleep_df": sleep_df,
            "rhr": rhr,
        })
        counts = Counter(y)
        label_str = ", ".join(f"{INT_TO_PHASE[k]}:{v}" for k, v in sorted(counts.items()))
        print(f"  {date_str}: {len(X)} windows ({label_str})")

    print(f"\n  Total: {len(all_nights)} nights")
    return all_nights


# ---------------------------------------------------------------------------
# Feature name lists for different experiments
# ---------------------------------------------------------------------------

# Original 48 features
ORIGINAL_FEATURES = [
    "hr_mean", "hr_median", "hr_std", "hr_min", "hr_max",
    "hr_p10", "hr_p90", "hr_iqr",
    "hr_above_rhr",
    "rr_mean", "rr_std", "rmssd", "sdnn", "pnn50", "pnn20",
    "lf_power", "hf_power", "lf_hf_ratio",
    "sd1", "sd2", "sd1_sd2_ratio", "cv",
    "hours_since_onset", "fraction_of_night",
    "ultradian_sin", "ultradian_cos",
    "circadian_sin", "circadian_cos",
    "roll5_hr_mean", "roll5_rmssd_mean",
    "roll10_hr_mean", "roll10_rmssd_mean",
    "delta_hr", "delta_rmssd", "delta_lf_hf", "delta_rr_mean",
    "accel_mag_mean", "accel_mag_std", "gyro_mean", "gyro_std", "spo2_mean",
    "gyro_max", "gyro_spikes", "acc_jerk_max", "acc_jerk_p95",
    "hr_range", "hr_spikes",
    "spo2_std",
]

# Extended features (original + new)
EXTENDED_FEATURES = ORIGINAL_FEATURES + [
    "roll3_hr_mean", "roll3_rmssd_mean",
    "roll15_hr_mean", "roll15_rmssd_mean",
    "acc_energy", "acc_zcr",
    "hr_accel", "hr_skewness",
    "hr_std_x_gyro_std", "rmssd_over_hr",
]


# ---------------------------------------------------------------------------
# Main experiments
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("SLEEP STAGING EXPERIMENT RUNNER")
    print("=" * 70)

    # Use extended features for all experiments (superset)
    feature_names = EXTENDED_FEATURES
    all_nights = load_all_nights(feature_names)

    if len(all_nights) < 2:
        print("ERROR: Need at least 2 nights")
        return

    total_windows = sum(len(n["y"]) for n in all_nights)
    print(f"\n  Total windows: {total_windows}")

    results_log = []

    def log_result(name, result):
        recalls = result["recalls"]
        entry = {
            "name": name,
            "accuracy": result["accuracy"],
            "mae": result["mae"],
            "awake_recall": recalls["awake"],
            "light_recall": recalls["light"],
            "deep_recall": recalls["deep"],
            "rem_recall": recalls["rem"],
        }
        results_log.append(entry)
        print(f"\n  >>> {name}: LONO={result['accuracy']:.1%} MAE={result['mae']:.1f} "
              f"| Awake={recalls['awake']:.1f}% Light={recalls['light']:.1f}% "
              f"Deep={recalls['deep']:.1f}% REM={recalls['rem']:.1f}%")

    # -----------------------------------------------------------------------
    # Experiment 0: Baseline (original features, original params, no Viterbi)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("EXP 0: BASELINE (original features, original params)")
    print("=" * 70)

    # Build data with original features only
    baseline_nights = []
    for n in all_nights:
        # Select only original feature columns
        orig_indices = [feature_names.index(f) for f in ORIGINAL_FEATURES]
        X_orig = n["X"][:, orig_indices]
        baseline_nights.append({**n, "X": X_orig})

    baseline_params = {
        "max_iter": 500, "max_depth": 4, "learning_rate": 0.05,
        "min_samples_leaf": 10, "l2_regularization": 0.01,
        "max_bins": 128, "class_weight": "balanced", "random_state": 42,
    }
    result = run_lono(baseline_nights, ORIGINAL_FEATURES, baseline_params, use_viterbi=False)
    log_result("Baseline (48 feat, no Viterbi)", result)

    # -----------------------------------------------------------------------
    # Experiment 1: Baseline + Viterbi
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("EXP 1: BASELINE + VITERBI")
    print("=" * 70)

    result = run_lono(baseline_nights, ORIGINAL_FEATURES, baseline_params, use_viterbi=True)
    log_result("Baseline + Viterbi", result)

    # -----------------------------------------------------------------------
    # Experiment 2: Extended features (58 feat) + Viterbi
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("EXP 2: EXTENDED FEATURES (58) + VITERBI")
    print("=" * 70)

    result = run_lono(all_nights, EXTENDED_FEATURES, baseline_params, use_viterbi=True)
    log_result("Extended (58 feat) + Viterbi", result)

    # -----------------------------------------------------------------------
    # Experiment 3: Extended features + Viterbi + no class_weight
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("EXP 3: EXTENDED + VITERBI + NO CLASS WEIGHT")
    print("=" * 70)

    params_no_cw = {**baseline_params, "class_weight": None}
    result = run_lono(all_nights, EXTENDED_FEATURES, params_no_cw, use_viterbi=True)
    log_result("Extended + Viterbi + no class_weight", result)

    # -----------------------------------------------------------------------
    # Experiment 4: Hyperparameter grid search (reduced)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("EXP 4: HYPERPARAMETER TUNING")
    print("=" * 70)

    best_acc = 0
    best_config = None
    best_result = None

    hp_configs = [
        {"max_depth": 3, "learning_rate": 0.05, "max_iter": 500, "min_samples_leaf": 10},
        {"max_depth": 4, "learning_rate": 0.05, "max_iter": 500, "min_samples_leaf": 10},
        {"max_depth": 5, "learning_rate": 0.05, "max_iter": 500, "min_samples_leaf": 10},
        {"max_depth": 6, "learning_rate": 0.05, "max_iter": 500, "min_samples_leaf": 10},
        {"max_depth": 4, "learning_rate": 0.02, "max_iter": 800, "min_samples_leaf": 10},
        {"max_depth": 4, "learning_rate": 0.1, "max_iter": 300, "min_samples_leaf": 10},
        {"max_depth": 4, "learning_rate": 0.05, "max_iter": 500, "min_samples_leaf": 5},
        {"max_depth": 4, "learning_rate": 0.05, "max_iter": 500, "min_samples_leaf": 20},
        {"max_depth": 5, "learning_rate": 0.05, "max_iter": 800, "min_samples_leaf": 5},
        {"max_depth": 3, "learning_rate": 0.1, "max_iter": 500, "min_samples_leaf": 5},
        {"max_depth": 5, "learning_rate": 0.02, "max_iter": 800, "min_samples_leaf": 10},
        {"max_depth": 4, "learning_rate": 0.05, "max_iter": 800, "min_samples_leaf": 10},
    ]

    for i, hp in enumerate(hp_configs):
        params = {
            **hp,
            "l2_regularization": 0.01, "max_bins": 128,
            "class_weight": "balanced", "random_state": 42,
        }
        t0 = time.time()
        result = run_lono(all_nights, EXTENDED_FEATURES, params, use_viterbi=True)
        elapsed = time.time() - t0
        acc = result["accuracy"]
        mae = result["mae"]
        recalls = result["recalls"]
        tag = f"d{hp['max_depth']}_lr{hp['learning_rate']}_i{hp['max_iter']}_msl{hp['min_samples_leaf']}"
        print(f"  [{i+1}/{len(hp_configs)}] {tag}: "
              f"acc={acc:.1%} MAE={mae:.1f} "
              f"A={recalls['awake']:.0f}% L={recalls['light']:.0f}% "
              f"D={recalls['deep']:.0f}% R={recalls['rem']:.0f}% "
              f"({elapsed:.0f}s)")

        if acc > best_acc:
            best_acc = acc
            best_config = hp
            best_result = result

    print(f"\n  Best HP config: {best_config}")
    log_result(f"Best HP ({best_config})", best_result)

    # -----------------------------------------------------------------------
    # Experiment 5: Best HP + no class_weight
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("EXP 5: BEST HP + NO CLASS WEIGHT")
    print("=" * 70)

    params_best_no_cw = {
        **best_config,
        "l2_regularization": 0.01, "max_bins": 128,
        "class_weight": None, "random_state": 42,
    }
    result = run_lono(all_nights, EXTENDED_FEATURES, params_best_no_cw, use_viterbi=True)
    log_result("Best HP + no class_weight", result)

    # -----------------------------------------------------------------------
    # Experiment 6: Best HP + balanced + extended + Viterbi (verbose)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("EXP 6: BEST CONFIG DETAILED RESULTS")
    print("=" * 70)

    params_best = {
        **best_config,
        "l2_regularization": 0.01, "max_bins": 128,
        "class_weight": "balanced", "random_state": 42,
    }
    result = run_lono(all_nights, EXTENDED_FEATURES, params_best, use_viterbi=True, verbose=True)
    log_result("Best config (detailed)", result)

    # Print confusion matrix
    cm = result["confusion_matrix"]
    print(f"\n  Overall confusion matrix:")
    print(f"               awake  light  deep   rem")
    for row_idx, label in enumerate(["awake", "light", "deep", "rem"]):
        row = cm[row_idx]
        total = row.sum()
        recall = row[row_idx] / total * 100 if total > 0 else 0
        print(f"    {label:>6}  {row[0]:>5}  {row[1]:>5}  {row[2]:>5}  {row[3]:>5}  "
              f"(recall: {recall:.1f}%)")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"{'Experiment':<45} {'LONO':>6} {'MAE':>5} {'Awake':>6} {'Light':>6} "
          f"{'Deep':>6} {'REM':>5}")
    print("-" * 100)
    for r in results_log:
        print(f"{r['name']:<45} {r['accuracy']:>5.1%} {r['mae']:>5.1f} "
              f"{r['awake_recall']:>5.1f}% {r['light_recall']:>5.1f}% "
              f"{r['deep_recall']:>5.1f}% {r['rem_recall']:>4.1f}%")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
