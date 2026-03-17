#!/usr/bin/env python3
"""Train a supervised ML model to replicate Whoop's sleep staging.

Uses per-second HR + RR interval sensor data aligned with Whoop's minute-by-minute
sleep stage labels extracted from deep dive JSONs. Trains a HistGradientBoostingClassifier
with leave-one-night-out cross-validation.

Output:
  - whoop_model.joblib: trained model
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
    # Context rolling (4)
    "roll5_hr_mean", "roll5_rmssd_mean",
    "roll10_hr_mean", "roll10_rmssd_mean",
    # Delta (4)
    "delta_hr", "delta_rmssd", "delta_lf_hf", "delta_rr_mean",
    # Accel/Gyro/SpO2 (optional — zeros if not available) (5)
    "accel_mag_mean", "accel_mag_std", "gyro_mean", "gyro_std", "spo2_mean",
]


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
    """Extract Whoop minute-by-minute sleep stage labels from deep dive JSONs.

    Returns list of (time_24h_str, phase_str) or empty list.
    """
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

    # Sort handling midnight crossing
    def sort_key(item):
        h, m = item[0].split(":")
        mins = int(h) * 60 + int(m)
        if mins < 720:  # before noon = after midnight
            mins += 1440
        return mins

    results.sort(key=sort_key)
    return results


def align_labels_to_sensor(sleep_df, labels, day):
    """Align Whoop minute labels to sensor data timestamps.

    Returns dict mapping minute_timestamp -> phase_str for minutes
    that have both sensor data and a Whoop label.
    """
    if not labels or sleep_df.empty:
        return {}

    # Build a mapping from HH:MM -> phase
    label_map = {}
    for time_24, phase in labels:
        label_map[time_24] = phase

    # Vectorized alignment: compute HH:MM from datetime_local, match against labels
    aligned = {}

    # Extract hour and minute from datetime_local (pandas Timestamps)
    dt_series = sleep_df["datetime_local"]
    ts_series = sleep_df["timestamp"].values.astype(int)

    # Compute HH:MM for each sensor sample efficiently
    hours = dt_series.apply(lambda x: x.hour if hasattr(x, "hour") else 0).values
    minutes = dt_series.apply(lambda x: x.minute if hasattr(x, "minute") else 0).values

    # Build minute_ts (start of each UTC minute)
    minute_ts_arr = ts_series - (ts_series % 60)

    # Process unique minutes only
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
    """Extract features for a 1-minute window of sensor data.

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

    # Circadian: approximate hour of night
    circadian_sin = math.sin(2 * math.pi * hours_since_onset / 24)
    circadian_cos = math.cos(2 * math.pi * hours_since_onset / 24)

    # --- Accel / Gyro / SpO2 (optional — zeros if columns missing or all zero) ---
    accel_mag_mean = 0.0
    accel_mag_std = 0.0
    gyro_mean_val = 0.0
    gyro_std_val = 0.0
    spo2_mean_val = 0.0

    if "acc_x" in chunk.columns:
        ax = chunk["acc_x"].values
        ay = chunk["acc_y"].values
        az = chunk["acc_z"].values
        mag = np.sqrt(ax**2 + ay**2 + az**2)
        mag_nonzero = mag[mag > 0.01]
        if len(mag_nonzero) > 0:
            accel_mag_mean = float(np.mean(mag_nonzero))
            accel_mag_std = float(np.std(mag_nonzero))

    if "gyro" in chunk.columns:
        gyro = chunk["gyro"].values
        gyro_nz = gyro[np.abs(gyro) > 0.001]
        if len(gyro_nz) > 0:
            gyro_mean_val = float(np.mean(np.abs(gyro_nz)))
            gyro_std_val = float(np.std(gyro_nz))

    if "spo2" in chunk.columns:
        spo2 = chunk["spo2"].dropna().values
        if len(spo2) > 0:
            spo2_mean_val = float(np.mean(spo2))

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
        # Accel/Gyro/SpO2 (optional — zeros if data not available)
        accel_mag_mean, accel_mag_std, gyro_mean_val, gyro_std_val, spo2_mean_val,
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
# Accel/Gyro/SpO2 at indices 36-40
IDX_ACCEL_MAG_MEAN = 36
IDX_ACCEL_MAG_STD = 37
IDX_GYRO_MEAN = 38
IDX_GYRO_STD = 39
IDX_SPO2_MEAN = 40


def add_rolling_and_delta(features_list):
    """Add rolling averages and delta features in-place."""
    n = len(features_list)
    for i in range(n):
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
    """Build feature matrix and labels for one night.

    Returns (X, y, times) where X is n_windows x n_features,
    y is n_windows, times is list of HH:MM strings.
    """
    if not aligned_labels or sleep_df.empty:
        return None, None, None

    ts_arr = sleep_df["timestamp"].values
    sleep_start_ts = int(ts_arr[0])
    sleep_end_ts = int(ts_arr[-1])
    total_dur = max(sleep_end_ts - sleep_start_ts, 1)

    # Sort aligned labels by timestamp
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

        # Get sensor data for this minute
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

        # Time string
        dt = datetime.fromtimestamp(minute_ts, timezone.utc) + BERLIN
        times_list.append(dt.strftime("%H:%M"))

    if not features_list:
        return None, None, None

    # Add rolling and delta features
    add_rolling_and_delta(features_list)

    X = np.array(features_list)
    y = np.array(labels_list)

    return X, y, times_list


# ---------------------------------------------------------------------------
# algo_f_trained: compatible prediction function for algo_compare.py
# ---------------------------------------------------------------------------

def algo_f_trained(sleep_df, rhr, window_sec=60):
    """Classify sleep phases using the trained Whoop model.

    Returns list of {"time": "HH:MM", "phase": "deep|light|rem|awake"}.
    Compatible with algo_compare.py format.
    """
    model_path = Path(__file__).resolve().parent / "whoop_model.joblib"
    if not model_path.exists():
        print("    [algo_f] Model not found, run train_whoop_model.py first")
        return []

    model = joblib.load(model_path)

    if sleep_df.empty or len(sleep_df) < 300:
        return []

    ts_arr = sleep_df["timestamp"].values
    sleep_start_ts = int(ts_arr[0])
    sleep_end_ts = int(ts_arr[-1])
    total_dur = max(sleep_end_ts - sleep_start_ts, 1)

    features_list = []
    times_list = []

    for i in range(0, len(sleep_df) - window_sec, window_sec):
        chunk = sleep_df.iloc[i:i + window_sec]
        t = chunk["datetime_local"].iloc[0]
        time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)

        ts_mid = int(chunk["timestamp"].iloc[len(chunk) // 2])
        hours_since = (ts_mid - sleep_start_ts) / 3600.0
        fraction = (ts_mid - sleep_start_ts) / total_dur

        feat = extract_minute_features(chunk, rhr, hours_since, fraction)
        if feat is None:
            # Pad with zeros
            feat = np.zeros(len(FEATURE_NAMES), dtype=np.float64)

        features_list.append(feat)
        times_list.append(time_str)

    if not features_list:
        return []

    # Add rolling and delta features
    add_rolling_and_delta(features_list)

    X = np.array(features_list)

    # Handle NaN
    X = np.nan_to_num(X, nan=0.0, posinf=5.0, neginf=-5.0)

    predictions = model.predict(X)

    phases = []
    for i, pred in enumerate(predictions):
        phases.append({
            "time": times_list[i],
            "phase": INT_TO_PHASE.get(int(pred), "light"),
        })

    # Smooth isolated phases
    for i in range(1, len(phases) - 1):
        if (phases[i]["phase"] != phases[i - 1]["phase"]
                and phases[i]["phase"] != phases[i + 1]["phase"]):
            phases[i] = {**phases[i], "phase": phases[i - 1]["phase"]}

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
    print("WHOOP SLEEP STAGING MODEL TRAINER")
    print("=" * 70)

    # 1. Load sensor data
    print("\n[1/5] Loading sensor data from DB...")
    df = load_from_db()
    # Filter to valid year range
    df = df[(df["date"].apply(lambda d: 2025 <= d.year <= 2026 if hasattr(d, "year") else False))]
    print(f"  {len(df)} samples after filtering to 2025-2026")

    dates = sorted(df["date"].unique())
    print(f"  {len(dates)} unique dates: {dates[0]} to {dates[-1]}")

    # 2. Find overlapping nights (sensor data + Whoop labels)
    print("\n[2/5] Finding nights with both sensor data and Whoop labels...")

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

        if len(aligned) < 60:  # Need at least 60 minutes of aligned data
            print(f"  {date_str}: only {len(aligned)} aligned minutes, skipping")
            continue

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
        })
        counts = Counter(y)
        label_str = ", ".join(f"{INT_TO_PHASE[k]}:{v}" for k, v in sorted(counts.items()))
        print(f"  {date_str}: {len(X)} windows ({label_str})")

    print(f"\n  Total: {len(all_nights)} nights with usable data")

    if len(all_nights) < 2:
        print("ERROR: Need at least 2 nights for LONO-CV")
        return

    # Combine all data
    X_all = np.concatenate([n["X"] for n in all_nights])
    y_all = np.concatenate([n["y"] for n in all_nights])

    # Handle NaN/inf
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=5.0, neginf=-5.0)

    print(f"\n  Total windows: {len(X_all)}")
    total_counts = Counter(y_all)
    for k in sorted(total_counts.keys()):
        print(f"    {INT_TO_PHASE[k]}: {total_counts[k]} ({total_counts[k] / len(y_all) * 100:.1f}%)")

    # 3. Leave-one-night-out cross-validation
    print("\n[3/5] Leave-one-night-out cross-validation...")
    print("-" * 70)

    night_accuracies = []
    night_maes = []
    all_y_true = []
    all_y_pred = []

    for hold_idx in range(len(all_nights)):
        held_night = all_nights[hold_idx]

        # Build training set (all nights except held-out)
        train_nights = [n for i, n in enumerate(all_nights) if i != hold_idx]
        X_train = np.concatenate([n["X"] for n in train_nights])
        y_train = np.concatenate([n["y"] for n in train_nights])
        X_train = np.nan_to_num(X_train, nan=0.0, posinf=5.0, neginf=-5.0)

        X_test = np.nan_to_num(held_night["X"], nan=0.0, posinf=5.0, neginf=-5.0)
        y_test = held_night["y"]

        # Train model
        model = HistGradientBoostingClassifier(
            max_iter=500,
            max_depth=4,
            learning_rate=0.05,
            min_samples_leaf=10,
            l2_regularization=0.01,
            max_bins=128,
            class_weight="balanced",
            random_state=42,
        )
        model.fit(X_train, y_train)

        # Predict
        y_pred = model.predict(X_test)

        # Smooth isolated predictions
        for i in range(1, len(y_pred) - 1):
            if y_pred[i] != y_pred[i - 1] and y_pred[i] != y_pred[i + 1]:
                y_pred[i] = y_pred[i - 1]

        acc = accuracy_score(y_test, y_pred)
        night_accuracies.append(acc)

        all_y_true.extend(y_test)
        all_y_pred.extend(y_pred)

        # Compute MAE (stage percentages)
        whoop_pcts = compute_stage_pcts([INT_TO_PHASE[int(v)] for v in y_test])
        pred_pcts = compute_stage_pcts([INT_TO_PHASE[int(v)] for v in y_pred])
        mae = compute_mae(whoop_pcts, pred_pcts)
        night_maes.append(mae if mae else 0.0)

        # Per-night confusion
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
    print("\n[4/5] Training final model on all nights...")
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=5.0, neginf=-5.0)

    final_model = HistGradientBoostingClassifier(
        max_iter=500,
        max_depth=4,
        learning_rate=0.05,
        min_samples_leaf=10,
        l2_regularization=0.01,
        max_bins=128,
        class_weight="balanced",
        random_state=42,
    )
    final_model.fit(X_all, y_all)

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

    # Save model
    model_path = Path(__file__).resolve().parent / "whoop_model.joblib"
    joblib.dump(final_model, model_path)
    print(f"\n  Model saved to {model_path}")

    # 5. Run on all nights and compare MAE vs Whoop
    print("\n[5/5] Running trained model on all nights (MAE comparison)...")
    print("-" * 70)

    maes_trained = []

    for night in all_nights:
        sleep_df = night["sleep_df"]
        rhr = night["rhr"]
        date_str = night["date"]

        # Run algo_f_trained
        pred_phases = algo_f_trained(sleep_df, rhr, window_sec=60)

        # Whoop ground truth percentages from aligned labels
        whoop_phase_list = [v for v in night["aligned"].values()]
        whoop_pcts = compute_stage_pcts(whoop_phase_list)
        pred_pcts = compute_stage_pcts([p["phase"] for p in pred_phases])

        mae = compute_mae(whoop_pcts, pred_pcts)
        maes_trained.append(mae if mae else 0.0)

        print(f"  {date_str}: MAE={mae}  "
              f"W[d={whoop_pcts.get('deep_pct', 0):>5.1f} l={whoop_pcts.get('light_pct', 0):>5.1f} "
              f"r={whoop_pcts.get('rem_pct', 0):>5.1f} a={whoop_pcts.get('awake_pct', 0):>5.1f}]  "
              f"P[d={pred_pcts.get('deep_pct', 0):>5.1f} l={pred_pcts.get('light_pct', 0):>5.1f} "
              f"r={pred_pcts.get('rem_pct', 0):>5.1f} a={pred_pcts.get('awake_pct', 0):>5.1f}]")

    print(f"\n  Average MAE (trained model): {np.mean(maes_trained):.1f}")
    print(f"  Median MAE (trained model):  {np.median(maes_trained):.1f}")

    # 6. Train Recovery & Sleep Score regressors (if whoop_official data available)
    print("\n[6/6] Training Recovery & Sleep Score models...")
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

                # Night-level features: HRV, RHR, sleep phase percentages, duration
                sleep_df = night["sleep_df"]
                rhr = night["rhr"]
                from common.preprocessing import compute_hrv_rmssd, compute_respiratory_rate
                hrv = compute_hrv_rmssd(sleep_df, method="sws")
                resp = compute_respiratory_rate(sleep_df) if len(sleep_df) > 60 else 14.0

                # Run trained model to get phase percentages
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

            # Recovery model
            rec_model = GradientBoostingRegressor(
                n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42
            )
            rec_model.fit(score_X, np.array(recovery_y))
            rec_pred = rec_model.predict(score_X)
            rec_mae = np.mean(np.abs(rec_pred - np.array(recovery_y)))
            print(f"  Recovery model: MAE={rec_mae:.1f} on {len(recovery_y)} nights (train set)")

            # Sleep score model
            slp_model = GradientBoostingRegressor(
                n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42
            )
            slp_model.fit(score_X, np.array(sleep_y))
            slp_pred = slp_model.predict(score_X)
            slp_mae = np.mean(np.abs(slp_pred - np.array(sleep_y)))
            print(f"  Sleep score model: MAE={slp_mae:.1f} on {len(sleep_y)} nights (train set)")

            # Save score models
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
