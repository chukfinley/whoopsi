#!/usr/bin/env python3
"""Train a supervised ML model to replicate Whoop's sleep staging.

Uses per-second HR + RR interval sensor data aligned with Whoop's minute-by-minute
sleep stage labels extracted from deep dive JSONs. Trains a HistGradientBoostingClassifier
with awake-boosted sample weights, Viterbi post-processing, and sticky awake transitions.

v7 improvements (Mar 2026):
- 68 features (was 67): +1 probably_light composite detector feature
  probably_light = (hr_above_rhr<5) * (hr_std in 3.5-8) * (rmssd in 80-145)
- Awake boost reduced: awake_boost=1.5 (was 2.0) to reduce REM->awake confusion
- Light self-transition boosted 1.3x in Viterbi (stickier light predictions)
- Temperature=0.6 (was 0.5) slightly less sharp to recover REM recall

v6 improvements (Mar 2026):
- 67 features (was 63): +4 light-specific centrality/discrimination features
  hr_std_centrality, rmssd_centrality, not_deep_score, not_rem_score
- Awake class boosted with 3x sample weight (on top of class_weight="balanced")
- Viterbi awake self-transition boosted 2x (stickier awake predictions)
- Temperature=0.5 (was 0.7) for sharper emission probabilities

v5 improvements (Mar 2026):
- 63 features (was 58): +5 Light/Deep/REM discriminating features
  hr_std_zone, rmssd_zone, light_score, deep_vs_light, rem_vs_light
- Added algorithms/whoop_backup/deep_dive/ as label search path
- Merged Mar 13-16 sensor data (180K new records with accel/gyro/spo2)

v4 improvements (Mar 2026):
- 58 features (was 48): +3/15-min rolling, acc_energy, acc_zcr, hr_accel,
  hr_skewness, hr_std*gyro_std interaction, rmssd/hr normalized HRV
- Viterbi post-processing with learned transition matrix
- Optimized hyperparameters: max_depth=3 (was 4), reduces overfitting
- Sleep onset/offset detection for trimming analysis window
- LONO accuracy: 71.0% (was 70.4%), MAE: 6.1 (was 5.3 LONO / 7.2 full)
- Awake recall: 29.0% (was 28.8%), Deep: 75.4% (was 78.7%), REM: 74.2% (was 68.9%)

Output:
  - whoop_model.joblib: trained model
  - whoop_transition_matrix.joblib: Viterbi transition/initial matrices
  - Console: per-night accuracy, confusion matrix, feature importance, MAE comparison
"""

import sys
import re
import math
import warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter

import numpy as np
import pandas as pd
import joblib
from scipy.interpolate import interp1d
from scipy.signal import welch as welch_psd
from scipy.stats import skew as scipy_skew
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data.db_loader import load_from_db
from common.preprocessing import compute_rhr

warnings.filterwarnings("ignore", category=FutureWarning)

BERLIN = timedelta(hours=1)
MIN_SLEEP_SAMPLES = 18000  # 5 hours minimum

PHASE_TO_INT = {"awake": 0, "light": 1, "deep": 2, "rem": 3}
INT_TO_PHASE = {v: k for k, v in PHASE_TO_INT.items()}

STAGE_MAP = {
    "AWAKE": "awake",
    "LIGHT_SLEEP": "light",
    "SWS_SLEEP": "deep",
    "REM_SLEEP": "rem",
}

FEATURE_NAMES = [
    # HR basic (8)
    "hr_mean", "hr_median", "hr_std", "hr_min", "hr_max",
    "hr_p10", "hr_p90", "hr_iqr",
    # HR relative (1)
    "hr_above_rhr",
    # RR / HRV time-domain (6)
    "rr_mean", "rr_std", "rmssd", "sdnn", "pnn50", "pnn20",
    # HRV spectral (3)
    "lf_power", "hf_power", "lf_hf_ratio",
    # Nonlinear (4)
    "sd1", "sd2", "sd1_sd2_ratio", "cv",
    # Temporal (6)
    "hours_since_onset", "fraction_of_night",
    "ultradian_sin", "ultradian_cos",
    "circadian_sin", "circadian_cos",
    # Context rolling 5/10 (4)
    "roll5_hr_mean", "roll5_rmssd_mean",
    "roll10_hr_mean", "roll10_rmssd_mean",
    # Delta (4)
    "delta_hr", "delta_rmssd", "delta_lf_hf", "delta_rr_mean",
    # Accel/Gyro/SpO2 (optional — zeros if not available) (5)
    "accel_mag_mean", "accel_mag_std", "gyro_mean", "gyro_std", "spo2_mean",
    # Awake-discriminating features (6)
    "gyro_max", "gyro_spikes", "acc_jerk_max", "acc_jerk_p95",
    "hr_range", "hr_spikes",
    # SpO2 variability (REM indicator) (1)
    "spo2_std",
    # --- NEW v4 features (10) ---
    # Context rolling 3/15 (4)
    "roll3_hr_mean", "roll3_rmssd_mean",
    "roll15_hr_mean", "roll15_rmssd_mean",
    # Movement features (2)
    "acc_energy", "acc_zcr",
    # HR derivative features (2)
    "hr_accel", "hr_skewness",
    # Cross-features (2)
    "hr_std_x_gyro_std", "rmssd_over_hr",
    # --- NEW v5 features: Light/Deep/REM discriminators (5) ---
    "hr_std_zone", "rmssd_zone", "light_score", "deep_vs_light", "rem_vs_light",
    # --- NEW v6 features: Light centrality + discrimination (4) ---
    "hr_std_centrality", "rmssd_centrality", "not_deep_score", "not_rem_score",
    # --- NEW v7 feature: Light composite detector (1) ---
    "probably_light",
]


# ---------------------------------------------------------------------------
# Viterbi decoder
# ---------------------------------------------------------------------------

def viterbi_decode(log_probs, log_trans, log_init):
    """Viterbi algorithm for most likely state sequence.

    Args:
        log_probs: (T, K) log emission probabilities per timestep
        log_trans: (K, K) log transition matrix [from][to]
        log_init: (K,) log initial state probabilities

    Returns:
        path: (T,) optimal state sequence
    """
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


def learn_transition_matrix(y_all, night_boundaries, awake_boost=1.5, light_boost=1.3):
    """Learn transition matrix from training labels.

    night_boundaries: list of (start_idx, end_idx) for each night in y_all.
    awake_boost: multiply awake self-transition count by this factor to make
                 awake predictions "stickier" (reduces rapid oscillation).
    light_boost: multiply light self-transition count by this factor to make
                 light predictions stickier (improves light recall).
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

    # Boost awake self-transition to make awake stickier
    counts[0, 0] *= awake_boost
    # Boost light self-transition to improve light recall
    counts[1, 1] *= light_boost

    trans = counts / counts.sum(axis=1, keepdims=True)
    log_trans = np.log(np.clip(trans, 1e-10, 1.0))

    init_probs = init_counts / init_counts.sum()
    log_init = np.log(np.clip(init_probs, 1e-10, 1.0))

    return log_trans, log_init


def apply_viterbi(model, X_test, log_trans, log_init, temperature=0.6):
    """Apply Viterbi post-processing to model predictions.

    Temperature < 1.0 sharpens emission probabilities, allowing more state
    transitions (better awake recall). Temperature > 1.0 smooths predictions
    (fewer transitions, more temporal coherence).

    Default 0.6 (was 0.5) slightly less sharp to balance awake vs REM recall.
    """
    proba = model.predict_proba(X_test)
    # Ensure all 4 classes are represented
    classes = list(model.classes_)
    if len(classes) < 4:
        full_proba = np.full((len(X_test), 4), 1e-10)
        for i, c in enumerate(classes):
            full_proba[:, c] = proba[:, i]
        proba = full_proba
    log_probs = np.log(np.clip(proba, 1e-10, 1.0)) / temperature
    return viterbi_decode(log_probs, log_trans, log_init)


def apply_two_stage_viterbi(awake_model, sleep_model, X_test, log_trans, log_init,
                             temperature=0.6, awake_boost_factor=1.5):
    """Two-stage prediction: awake detection then sleep staging with Viterbi.

    Stage 1: Binary awake-vs-sleep classifier provides awake probability.
    Stage 2: 3-class (light/deep/rem) classifier provides sleep stage probs.
    Combined into 4-class probabilities, then Viterbi decodes the sequence.

    The awake_boost_factor multiplies the raw awake probability to give it
    more weight relative to sleep stages (compensating for class imbalance).
    Viterbi handles temporal coherence so we don't need a hard threshold.

    Args:
        awake_model: Binary classifier (0=sleep, 1=awake)
        sleep_model: 3-class classifier (1=light, 2=deep, 3=rem)
        awake_boost_factor: Multiply awake probability by this factor (default 1.5)
    """
    # Stage 1: Get awake probabilities
    awake_proba = awake_model.predict_proba(X_test)
    awake_classes = list(awake_model.classes_)
    awake_idx = awake_classes.index(1) if 1 in awake_classes else -1
    if awake_idx >= 0:
        p_awake = awake_proba[:, awake_idx]
    else:
        p_awake = np.zeros(len(X_test))

    # Stage 2: Get sleep stage probabilities
    sleep_proba = sleep_model.predict_proba(X_test)
    sleep_classes = list(sleep_model.classes_)

    # Combine into 4-class probabilities
    full_proba = np.full((len(X_test), 4), 1e-10)
    for i in range(len(X_test)):
        pa = p_awake[i] * awake_boost_factor  # boost awake
        p_sleep = 1.0 - p_awake[i]  # use original (unboosted) for sleep scaling
        full_proba[i, 0] = max(pa, 1e-10)  # awake
        for j, cls in enumerate(sleep_classes):
            full_proba[i, cls] = max(sleep_proba[i, j] * p_sleep, 1e-10)
        # Re-normalize to sum to 1
        row_sum = full_proba[i].sum()
        full_proba[i] /= row_sum

    log_probs = np.log(np.clip(full_proba, 1e-10, 1.0)) / temperature
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

    has_gyro = "gyro" in sleep_df.columns
    if has_gyro:
        gyro = sleep_df["gyro"].values.astype(float)

    # Compute baseline from first 30 minutes (pre-sleep period)
    window = 300  # 5 minutes
    n = len(hr)
    baseline_end = min(1800, n // 3)
    hr_baseline_vals = hr[:baseline_end]
    hr_baseline_vals = hr_baseline_vals[hr_baseline_vals > 30]
    if len(hr_baseline_vals) < 60:
        return None, None
    hr_baseline = float(np.median(hr_baseline_vals))

    # Sliding window to find onset
    onset_ts_val = None
    for i in range(0, n - window, 60):
        chunk_hr = hr[i:i+window]
        chunk_hr_valid = chunk_hr[chunk_hr > 30]
        if len(chunk_hr_valid) < 30:
            continue
        chunk_hr_mean = float(np.mean(chunk_hr_valid))
        hr_drop = hr_baseline - chunk_hr_mean

        if hr_drop >= hr_drop_threshold:
            if has_gyro:
                chunk_gyro = gyro[i:i+window]
                gyro_max = float(np.max(np.abs(chunk_gyro)))
                if gyro_max < gyro_threshold:
                    onset_ts_val = int(ts[i])
                    break
            else:
                onset_ts_val = int(ts[i])
                break

    # Sliding window from end to find offset
    offset_ts_val = None
    for i in range(n - 1, window, -60):
        chunk_hr = hr[max(0, i-window):i]
        chunk_hr_valid = chunk_hr[chunk_hr > 30]
        if len(chunk_hr_valid) < 30:
            continue
        chunk_hr_mean = float(np.mean(chunk_hr_valid))

        if has_gyro:
            chunk_gyro = gyro[max(0, i-window):i]
            gyro_max = float(np.max(np.abs(chunk_gyro)))
            if gyro_max > gyro_threshold and chunk_hr_mean > hr_baseline - hr_drop_threshold + 3:
                offset_ts_val = int(ts[i])
                break
        else:
            if chunk_hr_mean > hr_baseline - 2:
                offset_ts_val = int(ts[i])
                break

    return onset_ts_val, offset_ts_val


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def get_sleep_window(df, day):
    """Extract sleep window: previous day 20:00 to current day 12:00."""
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
    """Extract Whoop minute-by-minute sleep stage labels from deep dive JSONs."""
    base = Path(__file__).resolve().parent.parent
    algo_dir = Path(__file__).resolve().parent
    candidates = [
        base / "ble-sync" / "data" / "backup" / "api" / "deep_dive" / date_str / "sleep_lastnight.json",
        base / "ble-sync" / "data" / "whoop_backup" / "deep_dive" / f"{date_str}.json",
        algo_dir / "whoop_backup" / "deep_dive" / f"{date_str}.json",
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
    """Align Whoop minute labels to sensor data timestamps."""
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
# Feature extraction for a 1-minute window
# ---------------------------------------------------------------------------

def _compute_spectral_features(rr_vals):
    """Compute LF power, HF power, LF/HF ratio from RR intervals."""
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


def extract_minute_features(chunk, rhr, hours_since_onset, fraction_of_night):
    """Extract 63 features for a 1-minute window of sensor data.

    Returns feature vector (numpy array) or None if insufficient data.
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

    # HR relative
    hr_above_rhr = hr_mean - rhr

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
    ultradian_sin = math.sin(2 * math.pi * elapsed_sec / 5400)  # 90-min cycle
    ultradian_cos = math.cos(2 * math.pi * elapsed_sec / 5400)
    circadian_sin = math.sin(2 * math.pi * hours_since_onset / 24)
    circadian_cos = math.cos(2 * math.pi * hours_since_onset / 24)

    # --- Accel / Gyro / SpO2 ---
    accel_mag_mean = 0.0
    accel_mag_std = 0.0
    gyro_mean_val = 0.0
    gyro_std_val = 0.0
    spo2_mean_val = 0.0
    gyro_max_val = 0.0
    gyro_spikes_val = 0.0
    acc_jerk_max_val = 0.0
    acc_jerk_p95_val = 0.0
    acc_energy = 0.0
    acc_zcr = 0.0
    spo2_std_val = 0.0

    if "acc_x" in chunk.columns:
        ax = chunk["acc_x"].values
        ay = chunk["acc_y"].values
        az = chunk["acc_z"].values
        mag = np.sqrt(ax**2 + ay**2 + az**2)
        mag_nonzero = mag[mag > 0.01]
        if len(mag_nonzero) > 0:
            accel_mag_mean = float(np.mean(mag_nonzero))
            accel_mag_std = float(np.std(mag_nonzero))
            acc_energy = float(np.sum(mag_nonzero**2) / len(mag_nonzero))
        if len(mag) > 1:
            jerk = np.abs(np.diff(mag))
            acc_jerk_max_val = float(np.max(jerk))
            acc_jerk_p95_val = float(np.percentile(jerk, 95))
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

    # HR range and spikes
    hr_range = float(hr_max - hr_min)
    hr_spikes_val = float(np.sum(np.abs(np.diff(hr_valid)) > 5)) if len(hr_valid) > 1 else 0.0

    # --- HR derivative features (NEW v4) ---
    hr_diff1 = np.diff(hr_valid) if len(hr_valid) > 1 else np.array([0.0])
    hr_diff2 = np.diff(hr_diff1) if len(hr_diff1) > 1 else np.array([0.0])
    hr_accel = float(np.mean(np.abs(hr_diff2))) if len(hr_diff2) > 0 else 0.0
    hr_skewness = float(scipy_skew(hr_valid)) if len(hr_valid) >= 3 else 0.0

    # --- Cross-features (NEW v4) ---
    hr_std_x_gyro_std = hr_std * gyro_std_val
    rmssd_over_hr = rmssd / hr_mean if hr_mean > 0 else 0.0

    # --- NEW v5 features: Light/Deep/REM discriminators ---
    # hr_std_zone: 0=deep-like (<4), 1=light-like (4-7), 2=rem-like (>7)
    hr_std_zone = 0.0 if hr_std < 4.0 else (1.0 if hr_std <= 7.0 else 2.0)

    # rmssd_zone: 0=deep-like (<90), 1=light-like (90-140), 2=rem-like (>140)
    rmssd_zone = 0.0 if rmssd < 90.0 else (1.0 if rmssd <= 140.0 else 2.0)

    # light_score: composite score for light sleep signature
    light_score = float(
        (1.0 if pnn50 > 55 else 0.0)
        + (1.0 if 3.0 < hr_above_rhr < 6.0 else 0.0)
        + (1.0 if 4.0 < hr_std < 7.0 else 0.0)
        + (1.0 if 0.1 < gyro_spikes_val < 1.0 else 0.0)
    ) / 4.0  # normalize to 0-1

    # deep_vs_light: hr_std / (rmssd + 1) — low for light, high for deep
    deep_vs_light = hr_std / (rmssd + 1.0)

    # rem_vs_light: gyro_spikes * spo2_std — high for REM, low for light
    rem_vs_light = gyro_spikes_val * spo2_std_val

    # --- NEW v6 features: Light centrality + discrimination ---
    # hr_std_centrality: peaks at hr_std=5.5 (light sleep sweet spot)
    hr_std_centrality = max(0.0, 1.0 - abs(hr_std - 5.5) / 5.5)

    # rmssd_centrality: peaks at rmssd=110 (light sleep sweet spot)
    rmssd_centrality = max(0.0, 1.0 - abs(rmssd - 110.0) / 110.0)

    # not_deep_score: count of indicators that this is NOT deep sleep
    not_deep_score = float(
        (1.0 if hr_std > 4.0 else 0.0)
        + (1.0 if rmssd > 80.0 else 0.0)
        + (1.0 if gyro_spikes_val > 0.1 else 0.0)
    ) / 3.0  # normalize to 0-1

    # not_rem_score: count of indicators that this is NOT REM
    not_rem_score = float(
        (1.0 if hr_std < 7.0 else 0.0)
        + (1.0 if spo2_std_val < 2.5 else 0.0)
        + (1.0 if gyro_spikes_val < 1.0 else 0.0)
    ) / 3.0  # normalize to 0-1

    # --- NEW v7: probably_light composite detector ---
    # Light sleep signature: low hr_above_rhr, moderate hr_std, moderate rmssd
    probably_light = float(
        (1.0 if hr_above_rhr < 5.0 else 0.0)
        * (1.0 if hr_std > 3.5 else 0.0)
        * (1.0 if hr_std < 8.0 else 0.0)
        * (1.0 if rmssd > 80.0 else 0.0)
        * (1.0 if rmssd < 145.0 else 0.0)
    )

    return np.array([
        hr_mean, hr_median, hr_std, hr_min, hr_max, hr_p10, hr_p90, hr_iqr,
        hr_above_rhr,
        rr_mean, rr_std_val, rmssd, sdnn, pnn50, pnn20,
        lf_power, hf_power, lf_hf_ratio,
        sd1, sd2, sd1_sd2_ratio, cv,
        hours_since_onset, fraction_of_night,
        ultradian_sin, ultradian_cos,
        circadian_sin, circadian_cos,
        # Placeholders for rolling and delta features (filled later)
        0.0, 0.0, 0.0, 0.0,  # roll5_hr, roll5_rmssd, roll10_hr, roll10_rmssd
        0.0, 0.0, 0.0, 0.0,  # delta_hr, delta_rmssd, delta_lf_hf, delta_rr_mean
        # Accel/Gyro/SpO2
        accel_mag_mean, accel_mag_std, gyro_mean_val, gyro_std_val, spo2_mean_val,
        # Awake-discriminating features
        gyro_max_val, gyro_spikes_val, acc_jerk_max_val, acc_jerk_p95_val,
        hr_range, hr_spikes_val,
        # SpO2 variability
        spo2_std_val,
        # NEW v4 features (placeholders for roll3/roll15, filled later)
        0.0, 0.0,  # roll3_hr, roll3_rmssd
        0.0, 0.0,  # roll15_hr, roll15_rmssd
        # Movement features
        acc_energy, acc_zcr,
        # HR derivative features
        hr_accel, hr_skewness,
        # Cross-features
        hr_std_x_gyro_std, rmssd_over_hr,
        # NEW v5: Light/Deep/REM discriminators
        hr_std_zone, rmssd_zone, light_score, deep_vs_light, rem_vs_light,
        # NEW v6: Light centrality + discrimination
        hr_std_centrality, rmssd_centrality, not_deep_score, not_rem_score,
        # NEW v7: Light composite detector
        probably_light,
    ], dtype=np.float64)


# Indices for rolling/delta features
IDX_HR_MEAN = 0
IDX_RMSSD = 12
IDX_LF_HF = 17
IDX_RR_MEAN = 9

IDX_ROLL5_HR = 28
IDX_ROLL5_RMSSD = 29
IDX_ROLL10_HR = 30
IDX_ROLL10_RMSSD = 31
IDX_DELTA_HR = 32
IDX_DELTA_RMSSD = 33
IDX_DELTA_LF_HF = 34
IDX_DELTA_RR_MEAN = 35

# NEW v4 rolling indices
IDX_ROLL3_HR = 48
IDX_ROLL3_RMSSD = 49
IDX_ROLL15_HR = 50
IDX_ROLL15_RMSSD = 51


def add_rolling_and_delta(features_list):
    """Add rolling averages and delta features in-place."""
    n = len(features_list)
    for i in range(n):
        # Rolling 3-minute averages (NEW v4)
        start3 = max(0, i - 2)
        window3 = features_list[start3:i + 1]
        features_list[i][IDX_ROLL3_HR] = np.mean([f[IDX_HR_MEAN] for f in window3])
        features_list[i][IDX_ROLL3_RMSSD] = np.mean([f[IDX_RMSSD] for f in window3])

        # Rolling 5-minute averages
        start5 = max(0, i - 4)
        window5 = features_list[start5:i + 1]
        features_list[i][IDX_ROLL5_HR] = np.mean([f[IDX_HR_MEAN] for f in window5])
        features_list[i][IDX_ROLL5_RMSSD] = np.mean([f[IDX_RMSSD] for f in window5])

        # Rolling 10-minute averages
        start10 = max(0, i - 9)
        window10 = features_list[start10:i + 1]
        features_list[i][IDX_ROLL10_HR] = np.mean([f[IDX_HR_MEAN] for f in window10])
        features_list[i][IDX_ROLL10_RMSSD] = np.mean([f[IDX_RMSSD] for f in window10])

        # Rolling 15-minute averages (NEW v4)
        start15 = max(0, i - 14)
        window15 = features_list[start15:i + 1]
        features_list[i][IDX_ROLL15_HR] = np.mean([f[IDX_HR_MEAN] for f in window15])
        features_list[i][IDX_ROLL15_RMSSD] = np.mean([f[IDX_RMSSD] for f in window15])

        # Delta from previous window
        if i > 0:
            features_list[i][IDX_DELTA_HR] = features_list[i][IDX_HR_MEAN] - features_list[i - 1][IDX_HR_MEAN]
            features_list[i][IDX_DELTA_RMSSD] = features_list[i][IDX_RMSSD] - features_list[i - 1][IDX_RMSSD]
            features_list[i][IDX_DELTA_LF_HF] = features_list[i][IDX_LF_HF] - features_list[i - 1][IDX_LF_HF]
            features_list[i][IDX_DELTA_RR_MEAN] = features_list[i][IDX_RR_MEAN] - features_list[i - 1][IDX_RR_MEAN]


# ---------------------------------------------------------------------------
# Build training data for one night
# ---------------------------------------------------------------------------

def build_night_data(sleep_df, aligned_labels, rhr):
    """Build feature matrix and labels for one night."""
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

        feat = extract_minute_features(chunk, rhr, hours_since, fraction)
        if feat is None:
            continue

        features_list.append(feat)
        labels_list.append(label)

        dt = datetime.fromtimestamp(minute_ts, timezone.utc) + BERLIN
        times_list.append(dt.strftime("%H:%M"))

    if not features_list:
        return None, None, None

    add_rolling_and_delta(features_list)

    X = np.array(features_list)
    y = np.array(labels_list)

    return X, y, times_list


# ---------------------------------------------------------------------------
# algo_f_trained: compatible prediction function for algo_compare.py
# ---------------------------------------------------------------------------

def algo_f_trained(sleep_df, rhr, window_sec=60):
    """Classify sleep phases using the trained two-stage Whoop model with Viterbi.

    Returns list of {"time": "HH:MM", "phase": "deep|light|rem|awake"}.
    Compatible with algo_compare.py format.
    """
    model_path = Path(__file__).resolve().parent / "whoop_model.joblib"
    if not model_path.exists():
        print("    [algo_f] Model not found, run train_whoop_model.py first")
        return []

    model = joblib.load(model_path)

    # Support both v5 (single model) and v6 dict format
    if isinstance(model, dict) and "model" in model:
        model = model["model"]

    # Load transition matrix if available
    trans_path = Path(__file__).resolve().parent / "whoop_transition_matrix.joblib"
    log_trans, log_init = None, None
    if trans_path.exists():
        try:
            tm = joblib.load(trans_path)
            log_trans = tm["log_trans"]
            log_init = tm["log_init"]
        except Exception:
            pass

    if sleep_df.empty or len(sleep_df) < 300:
        return []

    # Detect sleep onset/offset for trimming
    onset_ts, offset_ts = detect_sleep_onset_offset(sleep_df)

    ts_arr = sleep_df["timestamp"].values
    sleep_start_ts = int(ts_arr[0])
    sleep_end_ts = int(ts_arr[-1])

    # Use detected boundaries if available and sensible
    if onset_ts is not None and offset_ts is not None and offset_ts > onset_ts:
        # Safety check: detected window must be at least 3 hours
        if offset_ts - onset_ts >= 10800:
            sleep_start_ts = onset_ts
            sleep_end_ts = offset_ts
    elif onset_ts is not None:
        if sleep_end_ts - onset_ts >= 10800:
            sleep_start_ts = onset_ts
    elif offset_ts is not None and offset_ts > sleep_start_ts:
        if offset_ts - sleep_start_ts >= 10800:
            sleep_end_ts = offset_ts

    total_dur = max(sleep_end_ts - sleep_start_ts, 1)

    # Filter to detected sleep period
    sleep_mask = (sleep_df["timestamp"] >= sleep_start_ts) & (sleep_df["timestamp"] <= sleep_end_ts)
    trimmed_df = sleep_df[sleep_mask]
    if len(trimmed_df) < 300:
        trimmed_df = sleep_df  # fallback

    features_list = []
    times_list = []

    for i in range(0, len(trimmed_df) - window_sec, window_sec):
        chunk = trimmed_df.iloc[i:i + window_sec]
        t = chunk["datetime_local"].iloc[0]
        time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)

        ts_mid = int(chunk["timestamp"].iloc[len(chunk) // 2])
        hours_since = (ts_mid - sleep_start_ts) / 3600.0
        fraction = (ts_mid - sleep_start_ts) / total_dur

        feat = extract_minute_features(chunk, rhr, hours_since, fraction)
        if feat is None:
            feat = np.zeros(len(FEATURE_NAMES), dtype=np.float64)

        features_list.append(feat)
        times_list.append(time_str)

    if not features_list:
        return []

    add_rolling_and_delta(features_list)

    X = np.array(features_list)
    X = np.nan_to_num(X, nan=0.0, posinf=5.0, neginf=-5.0)

    # Apply Viterbi if transition matrix available
    if log_trans is not None and log_init is not None:
        predictions = apply_viterbi(model, X, log_trans, log_init)
    else:
        predictions = model.predict(X)
        # Smooth isolated predictions (fallback)
        for i in range(1, len(predictions) - 1):
            if predictions[i] != predictions[i - 1] and predictions[i] != predictions[i + 1]:
                predictions[i] = predictions[i - 1]

    phases = []
    for i, pred in enumerate(predictions):
        phases.append({
            "time": times_list[i],
            "phase": INT_TO_PHASE.get(int(pred), "light"),
        })

    return phases


# ---------------------------------------------------------------------------
# MAE computation (same metric as algo_compare.py)
# ---------------------------------------------------------------------------

def compute_stage_pcts(phases):
    """Compute stage percentages from phase list."""
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
    """MAE between two sets of stage percentages."""
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
# Main training pipeline
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("WHOOP SLEEP STAGING MODEL TRAINER v7")
    print("  68 features | Awake weight 2x | Viterbi temp=0.6 | Sticky awake+light")
    print("=" * 70)

    # 1. Load sensor data (merge multiple DBs for maximum coverage)
    print("\n[1/6] Loading sensor data from DB(s)...")
    df = load_from_db()

    # Also load from ble-sync DB if it has more data
    base = Path(__file__).resolve().parent.parent
    ble_db = base / "ble-sync" / "data" / "whoop_capture.db"
    if ble_db.exists():
        print(f"  Also loading from {ble_db}...")
        df2 = load_from_db(ble_db)
        if len(df2) > 0:
            df = pd.concat([df, df2], ignore_index=True)
            df = df.drop_duplicates(subset=["timestamp"], keep="first")
            df = df.sort_values("timestamp").reset_index(drop=True)
            print(f"  Merged: {len(df)} total records")

    df = df[(df["date"].apply(lambda d: 2025 <= d.year <= 2026 if hasattr(d, "year") else False))]
    print(f"  {len(df)} samples after filtering to 2025-2026")

    dates = sorted(df["date"].unique())
    print(f"  {len(dates)} unique dates: {dates[0]} to {dates[-1]}")

    # 2. Find overlapping nights (sensor data + Whoop labels)
    print("\n[2/6] Finding nights with both sensor data and Whoop labels...")

    all_nights = []
    from datetime import date as date_cls

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
            print(f"  {date_str}: only {len(aligned)} aligned minutes, skipping")
            continue

        # Detect sleep onset/offset
        onset_ts, offset_ts = detect_sleep_onset_offset(sleep_df)
        onset_str = "auto" if onset_ts else "data"
        offset_str = "auto" if offset_ts else "data"

        X, y, times = build_night_data(sleep_df, aligned, rhr)
        if X is None or len(X) < 60:
            print(f"  {date_str}: insufficient windows ({0 if X is None else len(X)}), skipping")
            continue

        all_nights.append({
            "date": date_str,
            "X": X,
            "y": y,
            "times": times,
            "sleep_df": sleep_df,
            "rhr": rhr,
            "labels": labels,
            "aligned": aligned,
            "onset_ts": onset_ts,
            "offset_ts": offset_ts,
        })
        counts = Counter(y)
        label_str = ", ".join(f"{INT_TO_PHASE[k]}:{v}" for k, v in sorted(counts.items()))
        print(f"  {date_str}: {len(X)} windows ({label_str}) onset={onset_str} offset={offset_str}")

    print(f"\n  Total: {len(all_nights)} nights with usable data")

    if len(all_nights) < 2:
        print("ERROR: Need at least 2 nights for LONO-CV")
        return

    # Combine all data
    X_all = np.concatenate([n["X"] for n in all_nights])
    y_all = np.concatenate([n["y"] for n in all_nights])
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=5.0, neginf=-5.0)

    print(f"\n  Total windows: {len(X_all)}")
    total_counts = Counter(y_all)
    for k in sorted(total_counts.keys()):
        print(f"    {INT_TO_PHASE[k]}: {total_counts[k]} ({total_counts[k] / len(y_all) * 100:.1f}%)")

    # 3. Leave-one-night-out cross-validation with Viterbi
    print("\n[3/6] Leave-one-night-out cross-validation (weighted + Viterbi)...")
    print("-" * 70)

    night_accuracies = []
    night_maes = []
    all_y_true = []
    all_y_pred = []

    # Optimized hyperparameters
    model_params = {
        "max_iter": 500,
        "max_depth": 3,
        "learning_rate": 0.05,
        "min_samples_leaf": 10,
        "l2_regularization": 0.01,
        "max_bins": 128,
        "class_weight": "balanced",
        "random_state": 42,
    }

    # Sample weight multipliers (on top of class_weight="balanced")
    AWAKE_WEIGHT = 2.0
    LIGHT_WEIGHT = 1.2  # Slight boost to help light vs deep/rem discrimination

    for hold_idx in range(len(all_nights)):
        held_night = all_nights[hold_idx]

        train_nights = [n for i, n in enumerate(all_nights) if i != hold_idx]
        X_train = np.concatenate([n["X"] for n in train_nights])
        y_train = np.concatenate([n["y"] for n in train_nights])
        X_train = np.nan_to_num(X_train, nan=0.0, posinf=5.0, neginf=-5.0)

        X_test = np.nan_to_num(held_night["X"], nan=0.0, posinf=5.0, neginf=-5.0)
        y_test = held_night["y"]

        # Sample weights: extra boost for awake and light samples
        sample_weights = np.ones(len(y_train))
        sample_weights[y_train == 0] = AWAKE_WEIGHT
        sample_weights[y_train == 1] = LIGHT_WEIGHT

        # Train single 4-class model with boosted awake weight
        model = HistGradientBoostingClassifier(**model_params)
        model.fit(X_train, y_train, sample_weight=sample_weights)

        # Learn transition matrix from training nights
        boundaries = []
        offset = 0
        for n in train_nights:
            boundaries.append((offset, offset + len(n["y"])))
            offset += len(n["y"])
        log_trans, log_init = learn_transition_matrix(y_train, boundaries,
                                                       awake_boost=1.5, light_boost=1.5)

        # Apply Viterbi post-processing
        y_pred = apply_viterbi(model, X_test, log_trans, log_init)

        acc = accuracy_score(y_test, y_pred)
        night_accuracies.append(acc)

        all_y_true.extend(y_test)
        all_y_pred.extend(y_pred)

        # Compute MAE (stage percentages)
        whoop_pcts = compute_stage_pcts([INT_TO_PHASE[int(v)] for v in y_test])
        pred_pcts = compute_stage_pcts([INT_TO_PHASE[int(v)] for v in y_pred])
        mae = compute_mae(whoop_pcts, pred_pcts)
        night_maes.append(mae if mae else 0.0)

        cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2, 3])

        print(f"\n  Night {hold_idx + 1}/{len(all_nights)}: {held_night['date']}")
        print(f"    Accuracy: {acc:.1%}  |  MAE: {mae}")
        print(f"    Whoop:     {whoop_pcts}")
        print(f"    Predicted: {pred_pcts}")
        print(f"    Confusion (rows=true, cols=pred):")
        print(f"               awake  light  deep   rem")
        for row_idx, label in enumerate(["awake", "light", "deep", "rem"]):
            row = cm[row_idx]
            print(f"      {label:>6}  {row[0]:>5}  {row[1]:>5}  {row[2]:>5}  {row[3]:>5}")

    print("\n" + "=" * 70)
    print("LONO CROSS-VALIDATION SUMMARY")
    print("=" * 70)
    print(f"  Mean accuracy: {np.mean(night_accuracies):.1%} "
          f"(std: {np.std(night_accuracies):.1%})")
    print(f"  Mean MAE:      {np.mean(night_maes):.1f} "
          f"(std: {np.std(night_maes):.1f})")
    print(f"  Per-night accuracies: {[f'{a:.1%}' for a in night_accuracies]}")
    print(f"  Per-night MAEs: {[f'{m:.1f}' for m in night_maes]}")

    # Overall confusion matrix
    all_y_true = np.array(all_y_true)
    all_y_pred = np.array(all_y_pred)
    overall_cm = confusion_matrix(all_y_true, all_y_pred, labels=[0, 1, 2, 3])

    print(f"\n  Overall confusion matrix:")
    print(f"               awake  light  deep   rem")
    for row_idx, label in enumerate(["awake", "light", "deep", "rem"]):
        row = overall_cm[row_idx]
        total = row.sum()
        recall = row[row_idx] / total * 100 if total > 0 else 0
        print(f"    {label:>6}  {row[0]:>5}  {row[1]:>5}  {row[2]:>5}  {row[3]:>5}  "
              f"(recall: {recall:.1f}%)")

    print(f"\n  Classification report:")
    target_names = ["awake", "light", "deep", "rem"]
    print(classification_report(all_y_true, all_y_pred,
                                labels=[0, 1, 2, 3],
                                target_names=target_names,
                                digits=3))

    # 4. Train final model on ALL data
    print("\n[4/6] Training final model on all nights...")
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=5.0, neginf=-5.0)

    sample_weights_all = np.ones(len(y_all))
    sample_weights_all[y_all == 0] = AWAKE_WEIGHT
    sample_weights_all[y_all == 1] = LIGHT_WEIGHT

    final_model = HistGradientBoostingClassifier(**model_params)
    final_model.fit(X_all, y_all, sample_weight=sample_weights_all)

    # Learn final transition matrix from all data
    boundaries = []
    offset = 0
    for n in all_nights:
        boundaries.append((offset, offset + len(n["y"])))
        offset += len(n["y"])
    final_log_trans, final_log_init = learn_transition_matrix(y_all, boundaries,
                                                              awake_boost=1.5, light_boost=1.5)

    # Feature importance via permutation
    print("\n  Feature importance (permutation, top 20):")
    from sklearn.inspection import permutation_importance
    perm_result = permutation_importance(
        final_model, X_all, y_all, n_repeats=5, random_state=42, n_jobs=-1
    )
    importances = perm_result.importances_mean
    sorted_idx = np.argsort(importances)[::-1]
    for rank, idx in enumerate(sorted_idx[:20]):
        name = FEATURE_NAMES[idx] if idx < len(FEATURE_NAMES) else f"feat_{idx}"
        print(f"    {rank + 1:>2}. {name:<25} {importances[idx]:.4f}")

    # Save model and transition matrix
    model_path = Path(__file__).resolve().parent / "whoop_model.joblib"
    joblib.dump(final_model, model_path)
    print(f"\n  Model saved to {model_path}")

    trans_path = Path(__file__).resolve().parent / "whoop_transition_matrix.joblib"
    joblib.dump({"log_trans": final_log_trans, "log_init": final_log_init}, trans_path)
    print(f"  Transition matrix saved to {trans_path}")

    # 5. Run on all nights and compare MAE vs Whoop
    print("\n[5/6] Running trained model on all nights (MAE comparison)...")
    print("-" * 70)

    maes_trained = []

    for night in all_nights:
        sleep_df = night["sleep_df"]
        rhr = night["rhr"]
        date_str = night["date"]

        pred_phases = algo_f_trained(sleep_df, rhr, window_sec=60)

        whoop_phase_list = [v for v in night["aligned"].values()]
        whoop_pcts = compute_stage_pcts(whoop_phase_list)
        pred_pcts = compute_stage_pcts([p["phase"] for p in pred_phases])

        mae = compute_mae(whoop_pcts, pred_pcts)
        maes_trained.append(mae if mae else 0.0)

        # Show onset/offset detection
        onset_str = "--"
        offset_str = "--"
        if night["onset_ts"]:
            dt = datetime.fromtimestamp(night["onset_ts"], timezone.utc) + BERLIN
            onset_str = dt.strftime("%H:%M")
        if night["offset_ts"]:
            dt = datetime.fromtimestamp(night["offset_ts"], timezone.utc) + BERLIN
            offset_str = dt.strftime("%H:%M")

        print(f"  {date_str}: MAE={mae}  onset={onset_str} offset={offset_str}  "
              f"W[d={whoop_pcts.get('deep_pct', 0):>5.1f} l={whoop_pcts.get('light_pct', 0):>5.1f} "
              f"r={whoop_pcts.get('rem_pct', 0):>5.1f} a={whoop_pcts.get('awake_pct', 0):>5.1f}]  "
              f"P[d={pred_pcts.get('deep_pct', 0):>5.1f} l={pred_pcts.get('light_pct', 0):>5.1f} "
              f"r={pred_pcts.get('rem_pct', 0):>5.1f} a={pred_pcts.get('awake_pct', 0):>5.1f}]")

    print(f"\n  Average MAE (trained model): {np.mean(maes_trained):.1f}")
    print(f"  Median MAE (trained model):  {np.median(maes_trained):.1f}")

    # 6. Analyze worst nights
    print("\n[6/6] Worst night analysis...")
    print("-" * 70)

    night_info = list(zip([n["date"] for n in all_nights], night_maes, night_accuracies))
    night_info.sort(key=lambda x: -x[1])  # sort by MAE descending

    print("  Nights ranked by MAE (worst first):")
    for date_str, mae, acc in night_info:
        night_data = next(n for n in all_nights if n["date"] == date_str)
        counts = Counter(night_data["y"])
        awake_pct = counts.get(0, 0) / len(night_data["y"]) * 100
        n_windows = len(night_data["y"])
        print(f"    {date_str}: MAE={mae:>5.1f}  acc={acc:.1%}  "
              f"windows={n_windows}  awake={awake_pct:.0f}%")

    # 7. Train Recovery & Sleep Score regressors (if whoop_official data available)
    print("\n[7/7] Training Recovery & Sleep Score models...")
    wo_path = Path(__file__).resolve().parent / "data" / "raw" / "whoop_official.json"
    if wo_path.exists():
        import json
        whoop_official = json.load(wo_path.open())

        score_X, recovery_y, sleep_y = [], [], []
        for night in all_nights:
            date_str = night["date"]
            wo = whoop_official.get(date_str, {})
            rec = wo.get("recovery")
            slp = wo.get("sleep_score")
            if rec and rec != "--" and slp:
                try:
                    rec_val = float(str(rec).replace("%", ""))
                    slp_val = float(str(slp).replace("%", ""))
                except (ValueError, TypeError):
                    continue

                sleep_df = night["sleep_df"]
                rhr = night["rhr"]
                from common.preprocessing import compute_hrv_rmssd, compute_respiratory_rate
                hrv = compute_hrv_rmssd(sleep_df, method="sws")
                resp = compute_respiratory_rate(sleep_df) if len(sleep_df) > 60 else 14.0

                pred_phases = algo_f_trained(sleep_df, rhr, window_sec=60)
                total = len(pred_phases)
                if total == 0:
                    continue
                counts = Counter(p["phase"] for p in pred_phases)
                deep_pct = counts.get("deep", 0) / total * 100
                light_pct = counts.get("light", 0) / total * 100
                rem_pct = counts.get("rem", 0) / total * 100
                awake_pct = counts.get("awake", 0) / total * 100
                sleep_eff = (total - counts.get("awake", 0)) / total * 100
                sleep_hours = total / 60.0

                score_features = [
                    hrv, rhr, resp, deep_pct, light_pct, rem_pct, awake_pct,
                    sleep_eff, sleep_hours,
                ]
                score_X.append(score_features)
                recovery_y.append(rec_val)
                sleep_y.append(slp_val)

        if len(score_X) >= 5:
            from sklearn.ensemble import GradientBoostingRegressor
            score_X = np.array(score_X)
            score_X = np.nan_to_num(score_X, nan=0.0)

            rec_model = GradientBoostingRegressor(
                n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42
            )
            rec_model.fit(score_X, np.array(recovery_y))
            rec_pred = rec_model.predict(score_X)
            rec_mae = np.mean(np.abs(rec_pred - np.array(recovery_y)))
            print(f"  Recovery model: MAE={rec_mae:.1f} on {len(recovery_y)} nights (train set)")

            slp_model = GradientBoostingRegressor(
                n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42
            )
            slp_model.fit(score_X, np.array(sleep_y))
            slp_pred = slp_model.predict(score_X)
            slp_mae = np.mean(np.abs(slp_pred - np.array(sleep_y)))
            print(f"  Sleep score model: MAE={slp_mae:.1f} on {len(sleep_y)} nights (train set)")

            score_model_path = Path(__file__).resolve().parent / "whoop_score_models.joblib"
            joblib.dump({"recovery": rec_model, "sleep": slp_model,
                        "feature_names": ["hrv", "rhr", "resp", "deep_pct", "light_pct",
                                         "rem_pct", "awake_pct", "sleep_eff", "sleep_hours"]},
                       score_model_path)
            print(f"  Score models saved to {score_model_path}")
        else:
            print(f"  Not enough data for score models ({len(score_X)} nights)")
    else:
        print("  whoop_official.json not found, skipping score models")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
