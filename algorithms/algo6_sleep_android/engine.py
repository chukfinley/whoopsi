"""Algorithm 6: Sleep as Android — Reverse-Engineered Actigraphy Pipeline.

Uses the reverse-engineered Sleep as Android algorithm (from sleep_algorithm.py)
applied to Whoop sensor data. Since Whoop's raw accelerometer data isn't
available from the DB (rawHex decode fails for most records), we derive a
movement proxy from heart rate variability and heart rate dynamics.

Cardioballistic method:
  - HR elevation above RHR correlates with movement/arousal
  - Short-term HR variability (successive differences) maps to autonomic arousal
  - RR interval instability tracks restlessness
  - Combined into a single "movement equivalent" per 10-second epoch

The movement proxy is fed into the full Sleep as Android offline hypnogram
pipeline: adaptive normalization → segment classification → deep postprocessing
→ REM detection → awake overlay.
"""

import sys
from pathlib import Path
from datetime import timedelta
from collections import Counter

import numpy as np
import pandas as pd

# Add sleep_algorithm.py to path
SLEEP_ALGO_DIR = Path(__file__).resolve().parent.parent.parent.parent / "sleep"
sys.path.insert(0, str(SLEEP_ALGO_DIR))

from sleep_algorithm import (
    compute_hypnogram,
    SleepPhase,
    SleepResult,
    EPOCH_MS,
    VERSION_EXPERIMENT3,
    Sensitivity,
)

from common.metrics import BaseAlgorithm, WhoopScores
from common.preprocessing import (
    compute_rhr,
    compute_hrv_rmssd,
    compute_respiratory_rate,
)

# 10-second epoch for Sleep as Android
EPOCH_SEC = EPOCH_MS // 1000  # 10


def hr_to_movement_proxy(
    sleep_df: pd.DataFrame,
    rhr: float,
    epoch_sec: int = EPOCH_SEC,
) -> np.ndarray:
    """Convert Whoop HR/RR time series to movement-equivalent epochs.

    Strategy: We use a two-pass approach.

    Pass 1: Compute raw HR-based features per epoch:
      - HR deviation from a rolling 5-minute baseline (not RHR, which is too low)
      - Successive HR differences (beat-to-beat instability)
      - RR interval variability

    Pass 2: Normalize using the distribution of values so that:
      - Deep sleep epochs (low HR, stable) map to ~0.01-0.05
      - Light sleep epochs (moderate HR, some variability) map to ~0.05-0.3
      - Wake/arousal epochs (high HR, instability) map to ~1.0-10.0

    This mapping matches what an accelerometer would produce: near-zero during
    immobility, small values during restless sleep, large values during movement.

    Returns one value per epoch (10 seconds).
    """
    ts = sleep_df["timestamp"].values.astype(float)
    hr = sleep_df["hr"].values.astype(float)
    rr = sleep_df["rr1_ms"].values.astype(float)

    if len(ts) == 0:
        return np.array([])

    # Create epoch boundaries
    t_start = float(ts[0])
    t_end = float(ts[-1])
    n_epochs = max(1, int((t_end - t_start) / epoch_sec))

    # Pass 1: Compute raw features per epoch
    raw_features = np.zeros(n_epochs)
    epoch_hrs = np.zeros(n_epochs)  # mean HR per epoch for rolling baseline

    for e in range(n_epochs):
        e_start = t_start + e * epoch_sec
        e_end = e_start + epoch_sec
        mask = (ts >= e_start) & (ts < e_end)
        epoch_hr = hr[mask]
        epoch_rr = rr[mask]

        valid_hr = epoch_hr[(epoch_hr > 30) & (epoch_hr < 220)]
        valid_rr = epoch_rr[
            (epoch_rr > 200) & (epoch_rr < 2500) & np.isfinite(epoch_rr)
        ]

        if len(valid_hr) < 2:
            epoch_hrs[e] = np.nan
            raw_features[e] = np.nan
            continue

        mean_hr = float(np.mean(valid_hr))
        epoch_hrs[e] = mean_hr

        # Feature 1: Successive HR differences (instability)
        hr_diffs = np.abs(np.diff(valid_hr))
        hr_mad = float(np.mean(hr_diffs)) if len(hr_diffs) > 0 else 0.0

        # Feature 2: HR range (spike detection)
        hr_range = float(np.max(valid_hr) - np.min(valid_hr))

        # Feature 3: RR interval instability
        rr_instability = 0.0
        if len(valid_rr) >= 2:
            rr_diffs = np.abs(np.diff(valid_rr))
            rr_instability = float(np.std(rr_diffs))

        # Raw composite: emphasizes instability over absolute level
        raw_features[e] = hr_mad * 1.0 + hr_range * 0.3 + rr_instability * 0.01

    # Pass 2: Compute rolling baseline HR (30-epoch = 5-minute window)
    # and add HR deviation from local baseline
    baseline_window = 30  # 30 epochs = 5 minutes
    for e in range(n_epochs):
        if np.isnan(raw_features[e]):
            continue
        # Local baseline: median HR in surrounding window
        w_start = max(0, e - baseline_window)
        w_end = min(n_epochs, e + baseline_window)
        window_hrs = epoch_hrs[w_start:w_end]
        valid_window = window_hrs[~np.isnan(window_hrs)]
        if len(valid_window) > 0:
            baseline = float(
                np.percentile(valid_window, 25)
            )  # Use P25 as quiet baseline
            deviation = max(0.0, epoch_hrs[e] - baseline)
            raw_features[e] += deviation * 0.3

    # Pass 3: Normalize to actigraph-like range
    # Fill NaN with local median
    valid_mask = ~np.isnan(raw_features)
    if valid_mask.sum() == 0:
        return np.full(n_epochs, 0.01)

    valid_vals = raw_features[valid_mask]

    # Use percentile-based normalization:
    # P10 → 0.01 (deep sleep baseline)
    # P50 → 0.05 (typical light sleep)
    # P90 → 0.5  (light arousals)
    # P99 → 5.0  (clear wake/movement)
    p10 = float(np.percentile(valid_vals, 10))
    p50 = float(np.percentile(valid_vals, 50))
    p90 = float(np.percentile(valid_vals, 90))
    p99 = float(np.percentile(valid_vals, 99))

    movement = np.full(n_epochs, 0.01)
    for e in range(n_epochs):
        if np.isnan(raw_features[e]):
            # No data — very low (quiet)
            movement[e] = 0.01
            continue

        val = raw_features[e]
        if val <= p10:
            # Deep sleep range
            frac = val / max(p10, 0.001)
            movement[e] = frac * 0.01
        elif val <= p50:
            # Light sleep range
            frac = (val - p10) / max(p50 - p10, 0.001)
            movement[e] = 0.01 + frac * 0.04
        elif val <= p90:
            # Light sleep to arousal
            frac = (val - p50) / max(p90 - p50, 0.001)
            movement[e] = 0.05 + frac * 0.45
        elif val <= p99:
            # Arousals and restlessness
            frac = (val - p90) / max(p99 - p90, 0.001)
            movement[e] = 0.5 + frac * 4.5
        else:
            # Clear wake / high activity
            movement[e] = 5.0 + min(5.0, (val - p99) / max(p99, 0.001) * 5.0)

        movement[e] = max(0.001, min(10.0, movement[e]))

    return movement


def classify_sleep_saa(
    sleep_df: pd.DataFrame,
    rhr: float,
    sleep_start_ts: int = None,
    sleep_end_ts: int = None,
) -> tuple:
    """Classify sleep phases using Sleep as Android algorithm on HR-derived movement.

    Returns (phases_list, summary_dict) matching the format of other algos.
    phases_list: [{"time": "HH:MM", "phase": str, "hr": float}, ...]
    """
    if sleep_df.empty or len(sleep_df) < 60:
        empty_summary = {
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
        }
        return [], empty_summary

    # Step 1: Convert HR to movement proxy
    movement = hr_to_movement_proxy(sleep_df, rhr)

    if len(movement) < 6:
        empty_summary = {
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
        }
        return [], empty_summary

    # Step 2: Determine time bounds
    ts = sleep_df["timestamp"].values.astype(float)
    from_ms = int(ts[0]) * 1000
    to_ms = int(ts[-1]) * 1000

    if sleep_start_ts:
        from_ms = int(sleep_start_ts) * 1000
    if sleep_end_ts:
        to_ms = int(sleep_end_ts) * 1000

    # Step 3: Run Sleep as Android hypnogram
    result = compute_hypnogram(
        movement,
        from_time_ms=from_ms,
        to_time_ms=to_ms,
        version=VERSION_EXPERIMENT3,
        is_smartwatch=False,
        sensitivity=Sensitivity.MEDIUM,
    )

    # Step 4: Convert to per-window output format (2-min blocks to match algo5)
    window_sec = 120  # 2-min blocks
    epochs_per_window = window_sec // EPOCH_SEC  # 12 epochs per 2-min block

    phase_map = {
        SleepPhase.DEEP: "deep",
        SleepPhase.LIGHT: "light",
        SleepPhase.REM: "rem",
        SleepPhase.AWAKE: "awake",
    }

    phases_list = []
    hr_vals = sleep_df["hr"].values.astype(float)
    hr_ts = ts

    n_phases = len(result.phases)
    n_windows = n_phases // epochs_per_window

    for w in range(n_windows):
        start_epoch = w * epochs_per_window
        end_epoch = min(start_epoch + epochs_per_window, n_phases)

        # Majority vote for this window
        window_phases = result.phases[start_epoch:end_epoch]
        counts = Counter(window_phases)
        majority = counts.most_common(1)[0][0]

        # Time label
        epoch_ts = ts[0] + w * window_sec if len(ts) > 0 else 0
        from datetime import datetime

        time_str = datetime.fromtimestamp(epoch_ts).strftime("%H:%M")

        # Average HR in this window
        win_start = ts[0] + w * window_sec
        win_end = win_start + window_sec
        hr_mask = (hr_ts >= win_start) & (hr_ts < win_end)
        win_hr = hr_vals[hr_mask]
        win_hr = win_hr[(win_hr > 30) & (win_hr < 220)]
        avg_hr = float(np.median(win_hr)) if len(win_hr) > 0 else 0.0

        phases_list.append(
            {
                "time": time_str,
                "phase": phase_map.get(majority, "light"),
                "hr": round(avg_hr, 1),
            }
        )

    # Summary
    total = len(phases_list)
    if total == 0:
        return [], {
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
        }

    win_min = window_sec / 60.0
    counts = Counter(p["phase"] for p in phases_list)
    sleep_count = total - counts.get("awake", 0)

    summary = {
        "total_min": round(total * win_min, 1),
        "sleep_min": round(sleep_count * win_min, 1),
        "efficiency": round(sleep_count / total * 100, 1) if total > 0 else 0,
        "deep_min": round(counts.get("deep", 0) * win_min, 1),
        "light_min": round(counts.get("light", 0) * win_min, 1),
        "rem_min": round(counts.get("rem", 0) * win_min, 1),
        "awake_min": round(counts.get("awake", 0) * win_min, 1),
        "deep_pct": round(counts.get("deep", 0) / total * 100, 1),
        "light_pct": round(counts.get("light", 0) / total * 100, 1),
        "rem_pct": round(counts.get("rem", 0) / total * 100, 1),
        "awake_pct": round(counts.get("awake", 0) / total * 100, 1),
    }
    return phases_list, summary


class SleepAndroidEngine(BaseAlgorithm):
    """Sleep as Android reverse-engineered algorithm applied to Whoop HR data.

    Uses HR-derived movement proxy fed through the full SAA offline pipeline:
    adaptive normalization → deep/light classification → REM detection → awake overlay.
    """

    name = "algo6_sleep_android"

    def __init__(self, max_hr=200):
        self.max_hr = max_hr

    def _get_sleep_window(self, df, day):
        """Get sleep window: 20:00 prev day to 12:00 current day."""
        prev = day - timedelta(days=1)
        mask = (
            (df["date"] == prev)
            & (
                df["datetime_local"].apply(
                    lambda x: x.hour if hasattr(x, "hour") else 0
                )
                >= 20
            )
        ) | (
            (df["date"] == day)
            & (
                df["datetime_local"].apply(
                    lambda x: x.hour if hasattr(x, "hour") else 12
                )
                < 12
            )
        )
        return df[mask]

    def compute(self, sensor_df: pd.DataFrame, day) -> WhoopScores:
        """Compute Whoop-compatible scores for a single day."""
        sleep_df = self._get_sleep_window(sensor_df, day)
        day_df = sensor_df[sensor_df["date"] == day]

        if day_df.empty:
            return WhoopScores(
                date=str(day),
                recovery=50,
                sleep=50,
                strain=0,
                hrv_ms=0,
                rhr_bpm=60,
                resp_rate=14,
            )

        rhr = compute_rhr(sleep_df) if not sleep_df.empty else compute_rhr(day_df)
        hrv = compute_hrv_rmssd(sleep_df, method="sws") if not sleep_df.empty else 0
        resp = (
            compute_respiratory_rate(sleep_df)
            if not sleep_df.empty and len(sleep_df) > 60
            else 14.0
        )

        _, summary = self.classify_sleep(sleep_df, rhr)

        return WhoopScores(
            date=str(day),
            recovery=50,  # Not computed by SAA
            sleep=round(summary["efficiency"]),
            strain=0,  # Not computed by SAA
            hrv_ms=round(hrv, 1),
            rhr_bpm=round(rhr, 1),
            resp_rate=round(resp, 1),
        )

    def classify_sleep(self, sleep_df, rhr, sleep_start_ts=None, sleep_end_ts=None):
        """Classify sleep phases. Returns (phases_list, summary_dict)."""
        return classify_sleep_saa(sleep_df, rhr, sleep_start_ts, sleep_end_ts)
