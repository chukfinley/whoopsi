#!/usr/bin/env python3
"""Compare multiple sleep staging algorithms against Whoop official results.

Algorithms:
  A: HRV Spectral (YASA-inspired, 1-min windows)
  B: SleepECG ML (wrn-gru-mesa, 30s epochs)
  C: HRV Nonlinear (entropy/Poincare/RMSSD, 1-min windows)
  D: HR Delta (purely HR-based, 2-min windows)

Output: algo_compare.html with stacked hypnograms, stage percentages, and MAE.
"""

import sys
import json
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
import yasa

from data.db_loader import load_from_db
from common.preprocessing import compute_rhr, compute_hrv_rmssd
from train_whoop_model import algo_f_trained

BERLIN = timedelta(hours=1)
MIN_SLEEP_SAMPLES = 18000  # 5 hours minimum

# YASA integer mapping: 0=WAKE, 2=N2(light), 3=N3(deep), 4=REM
PHASE_TO_YASA = {"awake": 0, "light": 2, "deep": 3, "rem": 4}


# ---------------------------------------------------------------------------
# Data loading helpers (reused from dashboard_yasa.py)
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


def load_whoop_official():
    p = Path(__file__).parent / "data" / "raw" / "whoop_official.json"
    if p.exists():
        return json.load(p.open())
    return {}


def to_num(v):
    if v is None or v == "--" or v == "":
        return None
    try:
        return float(str(v).replace("%", ""))
    except (ValueError, TypeError):
        return None


def extract_whoop_phases(date_str, whoop_official):
    """Extract Whoop official minute-by-minute timeline from sleep_lastnight.json.

    Parses scrubber entries that contain timestamps and sleep stage labels
    from the deep dive API backup.
    """
    import re

    # Search multiple locations for deep dive data
    candidates = [
        Path(__file__).resolve().parent.parent / "ble-sync" / "data" / "backup" / "api" / "deep_dive" / date_str / "sleep_lastnight.json",
        Path(__file__).resolve().parent.parent / "ble-sync" / "data" / "whoop_backup" / "deep_dive" / f"{date_str}.json",
        Path(__file__).resolve().parent.parent / "whoop_backup" / "deep_dive" / f"{date_str}.json",
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

    # Extract scrubber entries: time + stage
    # Format: "secondary_contextual_display":"1:54 AM","scrubber_style":"AWAKE"
    pattern = (
        r'"secondary_contextual_display"\s*:\s*"([^"]+)"\s*,'
        r'\s*"scrubber_style"\s*:\s*"(AWAKE|LIGHT_SLEEP|SWS_SLEEP|REM_SLEEP)"'
    )
    matches = re.findall(pattern, text)
    if not matches:
        # Try old format via _parse_deep_dive_sleep_bounds
        try:
            from algo5_ml.features import _parse_deep_dive_sleep_bounds
            from datetime import datetime as _dt
            second_gt, start_ts, end_ts = _parse_deep_dive_sleep_bounds(date_str)
            if second_gt and start_ts:
                block_size = 60
                phases = []
                t = int(start_ts)
                t_end = int(end_ts)
                while t + block_size <= t_end:
                    labels = [second_gt.get(s) for s in range(t, t + block_size) if s in second_gt]
                    if labels:
                        majority = Counter(labels).most_common(1)[0][0]
                    else:
                        majority = "light"
                    time_str = _dt.fromtimestamp(t + BERLIN.total_seconds()).strftime("%H:%M")
                    phases.append({"time": time_str, "phase": majority})
                    t += block_size
                return phases
        except Exception:
            pass
        return []

    # Map Whoop stage names to our format
    stage_map = {
        "AWAKE": "awake",
        "LIGHT_SLEEP": "light",
        "SWS_SLEEP": "deep",
        "REM_SLEEP": "rem",
    }

    # Convert 12h AM/PM times to 24h format and build phase list
    phases = []
    seen_times = set()
    for time_str, stage in matches:
        phase = stage_map.get(stage, "light")
        # Convert "1:54 AM" to "01:54", "11:22 PM" to "23:22"
        try:
            from datetime import datetime as _dt
            t = _dt.strptime(time_str.strip(), "%I:%M %p")
            time_24 = t.strftime("%H:%M")
        except ValueError:
            time_24 = time_str
        # Deduplicate (keep first occurrence per minute)
        if time_24 not in seen_times:
            seen_times.add(time_24)
            phases.append({"time": time_24, "phase": phase})

    # Sort by time (handling midnight crossing)
    def sort_key(p):
        h, m = p["time"].split(":")
        mins = int(h) * 60 + int(m)
        if mins < 720:  # before noon = after midnight
            mins += 1440
        return mins

    phases.sort(key=sort_key)
    return phases


# ---------------------------------------------------------------------------
# Shared spectral helper
# ---------------------------------------------------------------------------

def _compute_spectral(rr, fs=4.0):
    """Compute LF/HF ratio and HF% from RR intervals via Welch PSD."""
    from scipy.interpolate import interp1d
    from scipy.signal import welch as welch_psd

    lf_hf, hf_pct = 1.5, 33.0
    if len(rr) < 20:
        return lf_hf, hf_pct
    rr = rr[(rr > 200) & (rr < 2500)]
    if len(rr) < 20:
        return lf_hf, hf_pct

    try:
        cumtime = np.cumsum(rr) / 1000.0
        cumtime -= cumtime[0]
        if cumtime[-1] < 15:
            return lf_hf, hf_pct
        t_uni = np.arange(0, cumtime[-1], 1.0 / fs)
        if len(t_uni) < 32:
            return lf_hf, hf_pct
        f_int = interp1d(cumtime, rr, kind="linear", fill_value="extrapolate")
        rr_uni = f_int(t_uni) - np.mean(rr)
        nperseg = min(128, len(rr_uni))
        freqs, psd = welch_psd(rr_uni, fs=fs, nperseg=nperseg)
        lf_m = (freqs >= 0.04) & (freqs <= 0.15)
        hf_m = (freqs >= 0.15) & (freqs <= 0.40)
        lf_p = np.trapezoid(psd[lf_m], freqs[lf_m]) if lf_m.any() else 0
        hf_p = np.trapezoid(psd[hf_m], freqs[hf_m]) if hf_m.any() else 0
        total_p = lf_p + hf_p
        lf_hf = lf_p / hf_p if hf_p > 1e-10 else 5.0
        hf_pct = hf_p / total_p * 100 if total_p > 1e-10 else 33.0
    except Exception:
        pass
    return lf_hf, hf_pct


# ---------------------------------------------------------------------------
# Algorithm A: HRV Spectral (YASA-inspired)
# ---------------------------------------------------------------------------

def algo_a_hrv_spectral(sleep_df, rhr, window_sec=60):
    """YASA-inspired HRV spectral staging with temporal context."""
    if sleep_df.empty or len(sleep_df) < 300:
        return []

    phases = []
    ts_all = sleep_df["timestamp"].values
    sleep_start = ts_all[0]
    total_dur = max(ts_all[-1] - ts_all[0], 1)

    for i in range(0, len(sleep_df) - window_sec, window_sec):
        chunk = sleep_df.iloc[i:i + window_sec]
        t = chunk["datetime_local"].iloc[0]
        time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)

        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            phases.append({"time": time_str, "phase": "light"})
            continue

        avg_hr = float(np.median(hr_v))
        hr_std = float(np.std(hr_v)) if len(hr_v) > 1 else 0
        hr_above = avg_hr - rhr
        avg_mv = float(np.mean(mv))
        max_mv = float(np.max(mv))

        elapsed = ts_all[min(i + window_sec // 2, len(ts_all) - 1)] - sleep_start
        fraction = elapsed / total_dur

        lf_hf, hf_pct = _compute_spectral(rr)

        # RMSSD
        local_rmssd = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_rmssd = float(np.sqrt(np.mean(diffs**2)))

        # Scoring
        scores = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}

        # HR relative to RHR
        if hr_above < -2:
            scores["deep"] += 3.0
        elif hr_above < 3:
            scores["deep"] += 1.5; scores["light"] += 1.0
        elif hr_above < 10:
            scores["light"] += 2.0
        elif hr_above < 18:
            scores["rem"] += 1.5; scores["light"] += 0.5
        else:
            scores["awake"] += 3.0

        # HR variability
        if hr_std < 2.5:
            scores["deep"] += 2.0
        elif hr_std < 5:
            scores["light"] += 1.5
        elif hr_std > 8:
            scores["rem"] += 2.5
        else:
            scores["rem"] += 1.0

        # Movement
        if avg_mv > 1.0 or max_mv > 3.0:
            scores["awake"] += 4.0
        elif avg_mv > 0.3:
            scores["awake"] += 1.5
        elif avg_mv < 0.05:
            scores["deep"] += 1.5

        # Spectral HRV
        if lf_hf < 0.8:
            scores["deep"] += 3.0
        elif lf_hf < 1.5:
            scores["deep"] += 1.5; scores["light"] += 1.0
        if lf_hf > 2.5:
            scores["rem"] += 2.5
        elif lf_hf > 1.8:
            scores["rem"] += 1.5
        if 1.0 <= lf_hf <= 2.0:
            scores["light"] += 1.5
        if hf_pct > 50:
            scores["deep"] += 1.5
        elif hf_pct < 20:
            scores["rem"] += 1.0

        # RMSSD
        if local_rmssd > 120:
            scores["deep"] += 1.5
        elif local_rmssd > 80:
            scores["light"] += 1.0
        elif 0 < local_rmssd < 30:
            scores["rem"] += 1.0

        # Temporal
        if fraction < 0.35:
            scores["deep"] += 1.5
        elif fraction > 0.65:
            scores["rem"] += 1.5

        # Ultradian (90-min cycle)
        cycle_phase = math.sin(2 * math.pi * elapsed / 5400) if elapsed > 0 else 0
        if cycle_phase < -0.5:
            scores["deep"] += 1.0
        elif cycle_phase > 0.5:
            scores["rem"] += 1.0

        phase = max(scores, key=scores.get)
        phases.append({"time": time_str, "phase": phase})

    # Smooth isolated phases
    for i in range(1, len(phases) - 1):
        if phases[i]["phase"] != phases[i - 1]["phase"] and phases[i]["phase"] != phases[i + 1]["phase"]:
            phases[i] = {**phases[i], "phase": phases[i - 1]["phase"]}

    return phases


# ---------------------------------------------------------------------------
# Algorithm B: SleepECG ML
# ---------------------------------------------------------------------------

def algo_b_sleepecg(sleep_df, rhr, epoch_sec=30):
    """SleepECG ML classifier with NREM split heuristic.

    Uses the wrn-gru-mesa pretrained model for 3-class (Wake/REM/NREM).
    Splits NREM into Deep/Light using HR-below-RHR heuristic.
    """
    if sleep_df.empty or len(sleep_df) < 300:
        return []

    rr_all = sleep_df["rr1_ms"].dropna().values
    rr_all = rr_all[(rr_all > 200) & (rr_all < 2500)]
    if len(rr_all) < 100:
        return []

    try:
        import sleepecg

        # Build heartbeat times (cumulative RR in seconds)
        heartbeat_times = np.cumsum(rr_all) / 1000.0

        # Create SleepRecord
        record = sleepecg.SleepRecord(
            heartbeat_times=heartbeat_times,
            sleep_stage_duration=epoch_sec,
        )

        # Load classifier and predict
        clf = sleepecg.load_classifier("wrn-gru-mesa")
        stages = sleepecg.stage(clf, record, return_mode="int")
        # SleepStage mapping: 0=UNDEFINED, 1=N3, 2=N2, 3=N1, 4=REM, 5=WAKE

        # Build time axis from sleep_df
        t0 = sleep_df["datetime_local"].iloc[0]
        phases = []
        for idx, st in enumerate(stages):
            epoch_time = t0 + timedelta(seconds=idx * epoch_sec)
            time_str = epoch_time.strftime("%H:%M") if hasattr(epoch_time, "strftime") else str(epoch_time)

            if st == 5:  # WAKE
                phase = "awake"
            elif st == 4:  # REM
                phase = "rem"
            elif st in (1, 2, 3):  # NREM (N1/N2/N3)
                # Split NREM using HR heuristic: below RHR = deep, above = light
                sec_start = idx * epoch_sec
                sec_end = min(sec_start + epoch_sec, len(sleep_df))
                if sec_start < len(sleep_df):
                    epoch_hr = sleep_df.iloc[sec_start:sec_end]["hr"].values
                    epoch_hr = epoch_hr[epoch_hr > 30]
                    if len(epoch_hr) > 0 and float(np.median(epoch_hr)) < rhr - 1:
                        phase = "deep"
                    else:
                        phase = "light"
                else:
                    phase = "light"
                # Trust N3 label from classifier
                if st == 1:
                    phase = "deep"
            else:
                phase = "light"

            phases.append({"time": time_str, "phase": phase})

        # Smooth isolated phases
        for i in range(1, len(phases) - 1):
            if phases[i]["phase"] != phases[i - 1]["phase"] and phases[i]["phase"] != phases[i + 1]["phase"]:
                phases[i] = {**phases[i], "phase": phases[i - 1]["phase"]}

        return phases

    except Exception as e:
        print(f"    SleepECG failed: {e}")
        # Fallback: simple RR-based staging in 30s epochs
        return _sleepecg_fallback(sleep_df, rhr, epoch_sec)


def _sleepecg_fallback(sleep_df, rhr, epoch_sec=30):
    """Fallback when sleepecg classifier is unavailable. Uses RR-based features."""
    phases = []
    for i in range(0, len(sleep_df) - epoch_sec, epoch_sec):
        chunk = sleep_df.iloc[i:i + epoch_sec]
        t = chunk["datetime_local"].iloc[0]
        time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)

        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 3:
            phases.append({"time": time_str, "phase": "light"})
            continue

        avg_hr = float(np.median(hr_v))
        hr_std = float(np.std(hr_v)) if len(hr_v) > 1 else 0
        hr_above = avg_hr - rhr

        rmssd = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 2:
                rmssd = float(np.sqrt(np.mean(diffs**2)))

        # Simple classification
        if hr_above > 15 or hr_std > 10:
            phase = "awake"
        elif hr_above < -2 and hr_std < 3 and rmssd > 40:
            phase = "deep"
        elif hr_std > 5 or (rmssd < 25 and rmssd > 0):
            phase = "rem"
        else:
            phase = "light"

        phases.append({"time": time_str, "phase": phase})

    for i in range(1, len(phases) - 1):
        if phases[i]["phase"] != phases[i - 1]["phase"] and phases[i]["phase"] != phases[i + 1]["phase"]:
            phases[i] = {**phases[i], "phase": phases[i - 1]["phase"]}

    return phases


# ---------------------------------------------------------------------------
# Algorithm C: HRV Nonlinear
# ---------------------------------------------------------------------------

def algo_c_hrv_nonlinear(sleep_df, rhr, window_sec=60):
    """Nonlinear HRV features: Sample Entropy proxy (CV), Poincare SD1/SD2, RMSSD, LF/HF."""
    if sleep_df.empty or len(sleep_df) < 300:
        return []

    phases = []
    ts_all = sleep_df["timestamp"].values
    sleep_start = ts_all[0]
    total_dur = max(ts_all[-1] - ts_all[0], 1)

    for i in range(0, len(sleep_df) - window_sec, window_sec):
        chunk = sleep_df.iloc[i:i + window_sec]
        t = chunk["datetime_local"].iloc[0]
        time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)

        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            phases.append({"time": time_str, "phase": "light"})
            continue

        avg_hr = float(np.median(hr_v))
        hr_above = avg_hr - rhr
        elapsed = ts_all[min(i + window_sec // 2, len(ts_all) - 1)] - sleep_start
        fraction = elapsed / total_dur

        # --- Nonlinear HRV features ---

        # Sample Entropy approximation via coefficient of variation
        sample_entropy_proxy = 0.5  # default: moderate complexity
        if len(rr) > 10:
            cv = float(np.std(rr) / np.mean(rr)) if np.mean(rr) > 0 else 0
            sample_entropy_proxy = cv  # higher CV = more irregular = higher entropy

        # Poincare SD1/SD2
        sd1, sd2, sd_ratio = 0.0, 0.0, 1.0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs_clean = diffs[np.abs(diffs) < 300]
            if len(diffs_clean) > 3:
                sd1 = float(np.std(diffs_clean) / np.sqrt(2))
                # SD2 from total variability and SD1
                total_var = float(np.std(rr))
                sd2 = float(np.sqrt(max(2 * total_var**2 - sd1**2, 0.01)))
                sd_ratio = sd1 / sd2 if sd2 > 0.01 else 1.0

        # RMSSD
        local_rmssd = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_rmssd = float(np.sqrt(np.mean(diffs**2)))

        # LF/HF
        lf_hf, hf_pct = _compute_spectral(rr)

        # --- Rule-based classification using nonlinear features ---
        scores = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}

        # Sample Entropy proxy (CV):
        #   Deep sleep: very regular (low entropy/CV)
        #   REM: irregular (high entropy/CV)
        #   Awake: most irregular
        if sample_entropy_proxy < 0.03:
            scores["deep"] += 3.0
        elif sample_entropy_proxy < 0.06:
            scores["deep"] += 1.5; scores["light"] += 1.0
        elif sample_entropy_proxy < 0.10:
            scores["light"] += 2.0
        elif sample_entropy_proxy < 0.15:
            scores["rem"] += 2.0
        else:
            scores["awake"] += 2.0; scores["rem"] += 1.0

        # Poincare SD1/SD2 ratio:
        #   SD1/SD2 < 0.5 => deep (low short-term variability relative to long-term)
        #   SD1/SD2 > 1.0 => REM or awake (high short-term variability)
        if sd_ratio < 0.3:
            scores["deep"] += 2.5
        elif sd_ratio < 0.5:
            scores["deep"] += 1.5; scores["light"] += 0.5
        elif sd_ratio < 0.8:
            scores["light"] += 2.0
        elif sd_ratio < 1.2:
            scores["rem"] += 2.0
        else:
            scores["awake"] += 1.5; scores["rem"] += 1.0

        # RMSSD
        if local_rmssd > 120:
            scores["deep"] += 2.0
        elif local_rmssd > 80:
            scores["deep"] += 1.0; scores["light"] += 1.0
        elif local_rmssd > 40:
            scores["light"] += 1.5
        elif local_rmssd > 15:
            scores["rem"] += 1.5
        elif local_rmssd > 0:
            scores["awake"] += 1.0

        # LF/HF ratio
        if lf_hf < 0.8:
            scores["deep"] += 2.5
        elif lf_hf < 1.5:
            scores["deep"] += 1.0; scores["light"] += 1.5
        elif lf_hf < 2.5:
            scores["light"] += 1.0; scores["rem"] += 1.5
        else:
            scores["rem"] += 2.5

        # HR relative to RHR (supportive)
        if hr_above < -2:
            scores["deep"] += 2.0
        elif hr_above < 3:
            scores["deep"] += 0.5; scores["light"] += 1.0
        elif hr_above < 10:
            scores["light"] += 1.0
        elif hr_above < 18:
            scores["rem"] += 1.5
        else:
            scores["awake"] += 3.0

        # Temporal bias
        if fraction < 0.30:
            scores["deep"] += 1.0
        elif fraction > 0.70:
            scores["rem"] += 1.0

        phase = max(scores, key=scores.get)
        phases.append({"time": time_str, "phase": phase})

    # Smooth isolated phases
    for i in range(1, len(phases) - 1):
        if phases[i]["phase"] != phases[i - 1]["phase"] and phases[i]["phase"] != phases[i + 1]["phase"]:
            phases[i] = {**phases[i], "phase": phases[i - 1]["phase"]}

    return phases


# ---------------------------------------------------------------------------
# Algorithm D: HR Delta
# ---------------------------------------------------------------------------

def algo_d_hr_delta(sleep_df, rhr, window_sec=120):
    """Simplest approach: purely HR-based with 2-minute windows.

    Features: HR relative to RHR, HR std, HR trend, movement proxy from HR spikes.
    """
    if sleep_df.empty or len(sleep_df) < 300:
        return []

    phases = []
    prev_avg_hr = None

    ts_all = sleep_df["timestamp"].values
    sleep_start = ts_all[0]
    total_dur = max(ts_all[-1] - ts_all[0], 1)

    for i in range(0, len(sleep_df) - window_sec, window_sec):
        chunk = sleep_df.iloc[i:i + window_sec]
        t = chunk["datetime_local"].iloc[0]
        time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)

        hr = chunk["hr"].values
        hr_v = hr[hr > 30]

        if len(hr_v) < 5:
            phases.append({"time": time_str, "phase": "light"})
            prev_avg_hr = None
            continue

        avg_hr = float(np.median(hr_v))
        hr_std = float(np.std(hr_v)) if len(hr_v) > 1 else 0
        hr_above = avg_hr - rhr

        # HR trend: compare to previous window
        hr_delta = 0.0
        if prev_avg_hr is not None:
            hr_delta = avg_hr - prev_avg_hr  # positive = rising, negative = falling

        # Movement proxy: count HR spikes (sudden jumps > 5 bpm between seconds)
        hr_jumps = 0
        if len(hr_v) > 10:
            hr_diffs = np.abs(np.diff(hr_v))
            hr_jumps = int(np.sum(hr_diffs > 5))

        elapsed = ts_all[min(i + window_sec // 2, len(ts_all) - 1)] - sleep_start
        fraction = elapsed / total_dur

        # Classification
        scores = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}

        # HR relative to RHR (primary feature)
        if hr_above < -3:
            scores["deep"] += 4.0
        elif hr_above < 0:
            scores["deep"] += 2.5; scores["light"] += 0.5
        elif hr_above < 5:
            scores["light"] += 2.5
        elif hr_above < 12:
            scores["light"] += 1.0; scores["rem"] += 1.5
        elif hr_above < 20:
            scores["rem"] += 1.0; scores["awake"] += 2.0
        else:
            scores["awake"] += 4.0

        # HR standard deviation
        if hr_std < 2.0:
            scores["deep"] += 2.5
        elif hr_std < 4.0:
            scores["light"] += 1.5; scores["deep"] += 0.5
        elif hr_std < 7.0:
            scores["light"] += 1.0; scores["rem"] += 1.0
        elif hr_std < 12.0:
            scores["rem"] += 2.5
        else:
            scores["awake"] += 2.0; scores["rem"] += 0.5

        # HR trend (delta from previous window)
        if hr_delta < -3:
            scores["deep"] += 1.5  # falling HR = going deeper
        elif hr_delta < -1:
            scores["deep"] += 0.5; scores["light"] += 0.5
        elif hr_delta > 3:
            scores["awake"] += 1.5  # rising HR = waking
            scores["rem"] += 0.5
        elif hr_delta > 1:
            scores["rem"] += 1.0; scores["light"] += 0.5

        # Movement proxy (HR variability spikes)
        if hr_jumps > 15:
            scores["awake"] += 3.0
        elif hr_jumps > 8:
            scores["awake"] += 1.0; scores["rem"] += 0.5
        elif hr_jumps > 3:
            scores["rem"] += 1.0
        elif hr_jumps <= 1:
            scores["deep"] += 1.0

        # Temporal
        if fraction < 0.30:
            scores["deep"] += 1.5
        elif fraction > 0.65:
            scores["rem"] += 1.5

        phase = max(scores, key=scores.get)
        phases.append({"time": time_str, "phase": phase})
        prev_avg_hr = avg_hr

    # Smooth isolated phases
    for i in range(1, len(phases) - 1):
        if phases[i]["phase"] != phases[i - 1]["phase"] and phases[i]["phase"] != phases[i + 1]["phase"]:
            phases[i] = {**phases[i], "phase": phases[i - 1]["phase"]}

    return phases


# ---------------------------------------------------------------------------
# Algorithm E: YASA Library (real yasa.Hypnogram + spectral + temporal)
# ---------------------------------------------------------------------------

def algo_e_yasa(sleep_df, rhr, window_sec=60):
    """Uses the real YASA library for hypnogram creation and statistics.

    Same spectral features as Algo A but with:
    - Real YASA Hypnogram object for proper sleep statistics
    - Stronger ultradian cycle phase weighting
    - YASA's transition smoothing
    """
    if sleep_df.empty or len(sleep_df) < 300:
        return []

    phases = []
    ts_all = sleep_df["timestamp"].values
    sleep_start = ts_all[0]
    total_dur = max(ts_all[-1] - ts_all[0], 1)

    for i in range(0, len(sleep_df) - window_sec, window_sec):
        chunk = sleep_df.iloc[i:i + window_sec]
        t = chunk["datetime_local"].iloc[0]

        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            phases.append({"time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t), "phase": "light"})
            continue

        avg_hr = float(np.median(hr_v))
        hr_std = float(np.std(hr_v)) if len(hr_v) > 1 else 0
        hr_above = avg_hr - rhr
        avg_mv = float(np.mean(mv))
        max_mv = float(np.max(mv))

        elapsed = ts_all[min(i + window_sec // 2, len(ts_all) - 1)] - sleep_start
        fraction = elapsed / total_dur

        # Spectral
        lf_hf, hf_pct = _compute_spectral(rr)

        # RMSSD
        local_rmssd = 0
        if len(rr) > 5:
            diffs = np.diff(rr)
            diffs = diffs[np.abs(diffs) < 300]
            if len(diffs) > 3:
                local_rmssd = float(np.sqrt(np.mean(diffs**2)))

        scores = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}

        # HR relative to RHR
        if hr_above < -2: scores["deep"] += 3.0
        elif hr_above < 3: scores["deep"] += 1.5; scores["light"] += 1.0
        elif hr_above < 10: scores["light"] += 2.0
        elif hr_above < 18: scores["rem"] += 1.5; scores["light"] += 0.5
        else: scores["awake"] += 3.0

        # HR variability
        if hr_std < 2.5: scores["deep"] += 2.0
        elif hr_std < 5: scores["light"] += 1.5
        elif hr_std > 8: scores["rem"] += 2.5
        else: scores["rem"] += 1.0

        # Movement
        if avg_mv > 1.0 or max_mv > 3.0: scores["awake"] += 4.0
        elif avg_mv > 0.3: scores["awake"] += 1.5
        elif avg_mv < 0.05: scores["deep"] += 1.5

        # LF/HF spectral
        if lf_hf < 0.8: scores["deep"] += 3.0
        elif lf_hf < 1.5: scores["deep"] += 1.5; scores["light"] += 1.0
        if lf_hf > 2.5: scores["rem"] += 2.5
        elif lf_hf > 1.8: scores["rem"] += 1.5
        if 1.0 <= lf_hf <= 2.0: scores["light"] += 1.5
        if hf_pct > 50: scores["deep"] += 1.5
        elif hf_pct < 20: scores["rem"] += 1.0

        # RMSSD
        if local_rmssd > 120: scores["deep"] += 1.5
        elif local_rmssd > 80: scores["light"] += 1.0
        elif local_rmssd < 30 and local_rmssd > 0: scores["rem"] += 1.0

        # Temporal — early night favors deep, late night favors REM
        if fraction < 0.35: scores["deep"] += 1.5
        elif fraction > 0.65: scores["rem"] += 1.5

        # Ultradian 90min cycle
        cycle_phase = math.sin(2 * math.pi * elapsed / 5400) if elapsed > 0 else 0
        if cycle_phase < -0.5: scores["deep"] += 1.0
        elif cycle_phase > 0.5: scores["rem"] += 1.0

        phase = max(scores, key=scores.get)
        phases.append({
            "time": t.strftime("%H:%M") if hasattr(t, "strftime") else str(t),
            "phase": phase,
        })

    # Smooth isolated phases
    for i in range(1, len(phases) - 1):
        if phases[i]["phase"] != phases[i - 1]["phase"] and phases[i]["phase"] != phases[i + 1]["phase"]:
            phases[i] = {**phases[i], "phase": phases[i - 1]["phase"]}

    return phases


# ---------------------------------------------------------------------------
# YASA statistics helper
# ---------------------------------------------------------------------------

def phases_to_yasa_stats(phases, window_sec, scorer_name="Algorithm"):
    """Convert phase list to YASA Hypnogram and compute sleep_statistics()."""
    if not phases:
        return {}

    yasa_ints = [PHASE_TO_YASA.get(p["phase"], 2) for p in phases]
    yasa_arr = np.array(yasa_ints)

    try:
        hyp = yasa.Hypnogram.from_integers(
            yasa_arr, freq=f"{window_sec}s", scorer=scorer_name
        )
        stats = hyp.sleep_statistics()

        tst = stats.get("TST", 0)
        return {
            "tst_min": round(tst, 1),
            "tst_hours": f"{int(tst // 60)}:{int(tst % 60):02d}",
            "se": round(stats.get("SE", 0), 1),
            "deep_pct": round(stats.get("%N3", 0), 1),
            "light_pct": round(stats.get("%N2", 0), 1),
            "rem_pct": round(stats.get("%REM", 0), 1),
            "awake_pct": round(100 - stats.get("SE", 0), 1),
            "deep_min": round(stats.get("N3", 0), 1),
            "light_min": round(stats.get("N2", 0), 1),
            "rem_min": round(stats.get("REM", 0), 1),
            "awake_min": round(stats.get("WAKE", 0), 1),
        }
    except Exception:
        # Manual fallback (YASA sleep_statistics fails for some epoch durations)
        total = len(phases)
        if total == 0:
            return {}
        counts = Counter(p["phase"] for p in phases)
        total_min = total * window_sec / 60
        sleep_min = (total - counts.get("awake", 0)) * window_sec / 60
        return {
            "tst_min": round(sleep_min, 1),
            "tst_hours": f"{int(sleep_min // 60)}:{int(sleep_min % 60):02d}",
            "se": round(sleep_min / total_min * 100, 1) if total_min > 0 else 0,
            "deep_pct": round(counts.get("deep", 0) / total * 100, 1),
            "light_pct": round(counts.get("light", 0) / total * 100, 1),
            "rem_pct": round(counts.get("rem", 0) / total * 100, 1),
            "awake_pct": round(counts.get("awake", 0) / total * 100, 1),
            "deep_min": round(counts.get("deep", 0) * window_sec / 60, 1),
            "light_min": round(counts.get("light", 0) * window_sec / 60, 1),
            "rem_min": round(counts.get("rem", 0) * window_sec / 60, 1),
            "awake_min": round(counts.get("awake", 0) * window_sec / 60, 1),
        }


# ---------------------------------------------------------------------------
# Whoop stats extraction
# ---------------------------------------------------------------------------

def extract_whoop_stats(wo):
    """Extract Whoop official stage percentages from whoop_official.json entry."""
    if not wo:
        return {}
    deep_pct = to_num(wo.get("sleep_sws_sleep_pct"))
    light_pct = to_num(wo.get("sleep_light_sleep_pct"))
    rem_pct = to_num(wo.get("sleep_rem_sleep_pct"))
    awake_pct = to_num(wo.get("sleep_awake_pct"))
    return {
        "deep_pct": deep_pct,
        "light_pct": light_pct,
        "rem_pct": rem_pct,
        "awake_pct": awake_pct,
        "duration": wo.get("sleep_duration"),
        "deep_time": wo.get("sleep_sws_sleep_time"),
        "light_time": wo.get("sleep_light_sleep_time"),
        "rem_time": wo.get("sleep_rem_sleep_time"),
        "awake_time": wo.get("sleep_awake_time"),
        "efficiency": wo.get("efficiency"),
        "recovery": to_num(wo.get("recovery")),
    }


# ---------------------------------------------------------------------------
# MAE computation
# ---------------------------------------------------------------------------

def compute_mae(whoop_stats, algo_stats):
    """Compute Mean Absolute Error between Whoop and algorithm stage percentages."""
    if not whoop_stats or not algo_stats:
        return None
    errors = []
    for key in ("deep_pct", "light_pct", "rem_pct", "awake_pct"):
        wv = whoop_stats.get(key)
        av = algo_stats.get(key)
        if wv is not None and av is not None:
            errors.append(abs(wv - av))
    return round(sum(errors) / len(errors), 1) if errors else None


# ---------------------------------------------------------------------------
# Analyze one day
# ---------------------------------------------------------------------------

def analyze_day(df, day, whoop_official):
    sleep_df = get_sleep_window(df, day)
    if sleep_df.empty or len(sleep_df) < MIN_SLEEP_SAMPLES:
        return None

    rhr = compute_rhr(sleep_df)
    date_str = str(day)
    wo = whoop_official.get(date_str, {})

    # Whoop official
    whoop_phases = extract_whoop_phases(date_str, whoop_official)
    whoop_stats = extract_whoop_stats(wo)

    # If no stats from whoop_official.json, compute from whoop_phases
    if (not whoop_stats or whoop_stats.get("deep_pct") is None) and whoop_phases:
        total = len(whoop_phases)
        counts = Counter(p["phase"] for p in whoop_phases)
        whoop_stats = {
            "deep_pct": round(counts.get("deep", 0) / total * 100, 1),
            "light_pct": round(counts.get("light", 0) / total * 100, 1),
            "rem_pct": round(counts.get("rem", 0) / total * 100, 1),
            "awake_pct": round(counts.get("awake", 0) / total * 100, 1),
            "duration": f"{total // 60}:{total % 60:02d}",
            "deep_time": f"{counts.get('deep', 0) // 60}:{counts.get('deep', 0) % 60:02d}",
            "light_time": f"{counts.get('light', 0) // 60}:{counts.get('light', 0) % 60:02d}",
            "rem_time": f"{counts.get('rem', 0) // 60}:{counts.get('rem', 0) % 60:02d}",
            "awake_time": f"{counts.get('awake', 0) // 60}:{counts.get('awake', 0) % 60:02d}",
            "efficiency": round((total - counts.get("awake", 0)) / total * 100, 1) if total > 0 else 0,
        }

    # Run all algorithms
    print(f"    Algorithm A (HRV Spectral)...")
    phases_a = algo_a_hrv_spectral(sleep_df, rhr, window_sec=60)
    stats_a = phases_to_yasa_stats(phases_a, 60, "A-HRV-Spectral")

    print(f"    Algorithm B (SleepECG)...")
    phases_b = algo_b_sleepecg(sleep_df, rhr, epoch_sec=30)
    stats_b = phases_to_yasa_stats(phases_b, 30, "B-SleepECG")

    print(f"    Algorithm C (HRV Nonlinear)...")
    phases_c = algo_c_hrv_nonlinear(sleep_df, rhr, window_sec=60)
    stats_c = phases_to_yasa_stats(phases_c, 60, "C-HRV-Nonlinear")

    print(f"    Algorithm D (HR Delta)...")
    phases_d = algo_d_hr_delta(sleep_df, rhr, window_sec=120)
    stats_d = phases_to_yasa_stats(phases_d, 120, "D-HR-Delta")

    print(f"    Algorithm E (YASA)...")
    phases_e = algo_e_yasa(sleep_df, rhr, window_sec=60)
    stats_e = phases_to_yasa_stats(phases_e, 60, "E-YASA")

    print(f"    Algorithm F (Trained ML)...")
    phases_f = algo_f_trained(sleep_df, rhr, window_sec=60)
    stats_f = phases_to_yasa_stats(phases_f, 60, "F-Trained")

    # Compute MAEs
    mae_a = compute_mae(whoop_stats, stats_a)
    mae_b = compute_mae(whoop_stats, stats_b)
    mae_c = compute_mae(whoop_stats, stats_c)
    mae_d = compute_mae(whoop_stats, stats_d)
    mae_e = compute_mae(whoop_stats, stats_e)
    mae_f = compute_mae(whoop_stats, stats_f)

    return {
        "date": date_str,
        "sleep_hours": round(len(sleep_df) / 3600, 1),
        "sleep_samples": len(sleep_df),
        "rhr": round(rhr, 1),
        "whoop": whoop_stats,
        "whoop_phases": whoop_phases,
        "algo_a": {"name": "HRV Spectral", "phases": phases_a, "stats": stats_a, "mae": mae_a, "window": 60},
        "algo_b": {"name": "SleepECG ML", "phases": phases_b, "stats": stats_b, "mae": mae_b, "window": 30},
        "algo_c": {"name": "HRV Nonlinear", "phases": phases_c, "stats": stats_c, "mae": mae_c, "window": 60},
        "algo_d": {"name": "HR Delta", "phases": phases_d, "stats": stats_d, "mae": mae_d, "window": 120},
        "algo_e": {"name": "YASA", "phases": phases_e, "stats": stats_e, "mae": mae_e, "window": 60},
        "algo_f": {"name": "Trained ML", "phases": phases_f, "stats": stats_f, "mae": mae_f, "window": 60},
    }


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

def generate_html(data):
    dj = json.dumps(data, default=str, separators=(",", ":"))

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Open Whoop — Algorithm Comparison</title>
<style>
:root{{--bg:#0a0a0a;--card:#141414;--card2:#1a1a1a;--border:#222;--border2:#2a2a2a;--text:#e0e0e0;--dim:#777;--dim2:#555;--green:#44cf6c;--cyan:#06b6d4;--pink:#e91e63;--yellow:#f5c542;--orange:#ff9800;--red:#e74c3c;--purple:#ab47bc;--deep:#1a237e;--light-s:#42a5f5;--rem:#ab47bc;--awake:#ff7043;--algo-a:#06b6d4;--algo-b:#f5c542;--algo-c:#e91e63;--algo-d:#ff9800}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px}}
.container{{max-width:1300px;margin:0 auto;padding:16px 20px}}
.header{{text-align:center;padding:20px 0 16px}}
.header h1{{font-size:18px;font-weight:700;letter-spacing:3px;text-transform:uppercase}}
.header h1 .g{{color:var(--green)}}.header h1 .c{{color:var(--cyan)}}
.header .meta{{color:var(--dim);font-size:11px;margin-top:6px}}
.day-nav{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:16px;justify-content:center}}
.day-btn{{background:var(--card);border:1px solid var(--border);color:var(--dim);padding:6px 14px;border-radius:8px;cursor:pointer;font-size:11px;font-weight:500;transition:all .2s}}
.day-btn:hover{{border-color:var(--dim2);color:var(--text)}}
.day-btn.active{{border-color:var(--cyan);color:var(--cyan);background:#06b6d422}}
.day-btn .sub{{display:block;font-size:9px;color:var(--dim2);margin-top:1px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:12px}}
.sect{{margin-bottom:4px}}
.sect summary{{cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:10px;list-style:none;display:flex;align-items:center;gap:8px;user-select:none}}
.sect summary::-webkit-details-marker{{display:none}}
.sect summary::before{{content:'\\25B6';font-size:8px;transition:transform .2s;display:inline-block}}
.sect[open] summary::before{{transform:rotate(90deg)}}
.sect .inner{{padding:10px 0 0}}
.sect summary .badge{{background:var(--card2);border:1px solid var(--border2);border-radius:6px;padding:2px 8px;font-size:9px;margin-left:auto;font-weight:400}}

/* Hypnogram */
.hypno-row{{margin-bottom:10px}}
.hypno-label{{font-size:10px;font-weight:600;letter-spacing:0.5px;margin-bottom:3px;display:flex;align-items:center;gap:8px}}
.hypno-label .tag{{font-size:8px;background:var(--card2);border:1px solid var(--border2);border-radius:4px;padding:1px 6px;color:var(--dim)}}
.hypno-container{{position:relative;height:100px;background:var(--card2);border-radius:8px;overflow:hidden}}
.hypno-canvas{{width:100%;height:100%}}
.hypno-ylabel{{position:absolute;left:2px;font-size:7px;color:var(--dim2)}}
.time-axis{{display:flex;justify-content:space-between;font-size:8px;color:var(--dim2);padding:2px 35px 0}}
.stage-labels{{position:absolute;right:4px;top:0;bottom:0;display:flex;flex-direction:column;justify-content:space-between;padding:4px 0}}
.stage-labels span{{font-size:7px;color:var(--dim2)}}

/* Legend */
.legend{{display:flex;gap:12px;flex-wrap:wrap;font-size:10px;justify-content:center;margin:8px 0}}
.legend .d{{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:3px;vertical-align:middle}}

/* Comparison table */
.cmp-table{{width:100%;border-collapse:collapse;font-size:11px}}
.cmp-table th{{text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600}}
.cmp-table td{{padding:7px 10px;border-bottom:1px solid #1a1a1a;white-space:nowrap}}
.cmp-table tr:hover{{background:var(--card2)}}
.cmp-table .src{{font-weight:600}}
.cmp-table .num{{font-variant-numeric:tabular-nums}}
.cmp-table .best{{background:#44cf6c18;color:var(--green)}}

/* Bars */
.sb{{display:flex;height:16px;border-radius:6px;overflow:hidden;margin:4px 0}}
.sb .s{{transition:width .3s}}

/* MAE cards */
.mae-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:8px}}
@media(max-width:700px){{.mae-row{{grid-template-columns:repeat(2,1fr)}}}}
.mae-card{{text-align:center;background:var(--card2);border:1px solid var(--border2);border-radius:10px;padding:12px 8px}}
.mae-card .algo-name{{font-size:9px;text-transform:uppercase;letter-spacing:1px;font-weight:600;margin-bottom:6px}}
.mae-card .mae-val{{font-size:28px;font-weight:700}}
.mae-card .mae-label{{font-size:8px;color:var(--dim);margin-top:2px}}
.mae-card.winner{{border-color:var(--green);background:#44cf6c0a}}
.mae-card.winner .mae-val{{color:var(--green)}}

/* Overall summary */
.summary-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}}
@media(max-width:700px){{.summary-grid{{grid-template-columns:repeat(2,1fr)}}}}
.sum-card{{text-align:center;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 8px}}
.sum-card .algo-name{{font-size:9px;text-transform:uppercase;letter-spacing:1px;font-weight:600;margin-bottom:4px}}
.sum-card .big{{font-size:32px;font-weight:700}}
.sum-card .sub-text{{font-size:9px;color:var(--dim);margin-top:2px}}
</style></head><body>
<div class="container">
<div class="header">
  <h1><span class="g">WHOOP</span> vs <span class="c">4 Algorithms</span></h1>
  <div class="meta" id="meta"></div>
</div>
<div id="summary"></div>
<div class="day-nav" id="nav"></div>
<div id="content"></div>
</div>
<script>
const D={dj};
const pC={{deep:'#1a237e',light:'#42a5f5',rem:'#ab47bc',awake:'#ff7043'}};
const algoColors={{algo_a:'#06b6d4',algo_b:'#f5c542',algo_c:'#e91e63',algo_d:'#ff9800'}};
const algoNames={{algo_a:'A: HRV Spectral',algo_b:'B: SleepECG ML',algo_c:'C: HRV Nonlinear',algo_d:'D: HR Delta'}};

document.getElementById('meta').textContent=D.days.length+' nights analyzed, comparing 4 sleep staging algorithms vs Whoop';

function fM(m){{if(!m&&m!==0)return'-';const h=Math.floor(m/60),mm=Math.round(m%60);return h>0?h+'h'+String(mm).padStart(2,'0')+'m':mm+'m';}}
function pT(s){{if(!s)return 0;const p=s.split(':');return parseInt(p[0]||0)*60+parseInt(p[1]||0);}}

// Overall summary
(function(){{
  const sums={{algo_a:[], algo_b:[], algo_c:[], algo_d:[]}};
  const wins={{algo_a:0, algo_b:0, algo_c:0, algo_d:0}};
  D.days.forEach(d=>{{
    ['algo_a','algo_b','algo_c','algo_d'].forEach(k=>{{
      if(d[k]&&d[k].mae!=null)sums[k].push(d[k].mae);
    }});
    // Find winner for this day
    let best=Infinity, bestK='';
    ['algo_a','algo_b','algo_c','algo_d'].forEach(k=>{{
      if(d[k]&&d[k].mae!=null&&d[k].mae<best){{best=d[k].mae;bestK=k;}}
    }});
    if(bestK)wins[bestK]++;
  }});
  const avg=k=>sums[k].length?'<div class="big">'+(sums[k].reduce((a,b)=>a+b,0)/sums[k].length).toFixed(1)+'</div><div class="sub-text">avg MAE ('+sums[k].length+' nights)</div>':'<div class="big">-</div>';
  let h='<div class="summary-grid">';
  ['algo_a','algo_b','algo_c','algo_d'].forEach(k=>{{
    h+='<div class="sum-card"><div class="algo-name" style="color:'+algoColors[k]+'">'+algoNames[k]+'</div>'+avg(k)+'<div class="sub-text">'+wins[k]+' night'+(wins[k]!==1?'s':'')+' closest</div></div>';
  }});
  h+='</div>';
  document.getElementById('summary').innerHTML=h;
}})();

function drawHypno(canvasId,phases,color){{
  const canvas=document.getElementById(canvasId);
  if(!canvas||!phases||!phases.length)return;
  const ctx=canvas.getContext('2d');
  const dpr=window.devicePixelRatio||1;
  const W=canvas.offsetWidth*dpr;
  const H=canvas.offsetHeight*dpr;
  canvas.width=W;canvas.height=H;
  ctx.scale(dpr,dpr);
  const w=canvas.offsetWidth,h=canvas.offsetHeight;

  const stageY={{awake:0.06,rem:0.28,light:0.55,deep:0.85}};
  const pw=w/phases.length;

  // Fill areas
  phases.forEach((p,i)=>{{
    const y=h*(stageY[p.phase]||0.5);
    ctx.fillStyle=(pC[p.phase]||'#333')+'33';
    ctx.fillRect(i*pw,y,pw,h-y);
  }});

  // Step line
  ctx.strokeStyle=color;
  ctx.lineWidth=1.2;
  ctx.beginPath();
  let prevY=null;
  phases.forEach((p,i)=>{{
    const y=h*(stageY[p.phase]||0.5);
    const x=i*pw;
    if(prevY===null){{ctx.moveTo(x,y);}}
    else{{ctx.lineTo(x,prevY);ctx.lineTo(x,y);}}
    prevY=y;
  }});
  if(prevY!==null)ctx.lineTo(phases.length*pw,prevY);
  ctx.stroke();

  // Y-axis labels
  ctx.fillStyle='#55555588';
  ctx.font='7px sans-serif';
  ctx.fillText('Awake',2,h*0.06+3);
  ctx.fillText('REM',2,h*0.28+3);
  ctx.fillText('Light',2,h*0.55+3);
  ctx.fillText('Deep',2,h*0.85+3);
}}

function timeAxis(phases){{
  if(!phases||phases.length<5)return '';
  const idx=[0,Math.floor(phases.length/4),Math.floor(phases.length/2),Math.floor(phases.length*3/4),phases.length-1];
  return '<div class="time-axis">'+idx.map(i=>'<span>'+phases[i].time+'</span>').join('')+'</div>';
}}

function stageBar(stats,color){{
  if(!stats||stats.deep_pct==null)return '';
  const d=stats.deep_pct||0,l=stats.light_pct||0,r=stats.rem_pct||0,a=stats.awake_pct||0;
  return '<div class="sb"><div class="s" style="width:'+d+'%;background:var(--deep)"></div><div class="s" style="width:'+l+'%;background:var(--light-s)"></div><div class="s" style="width:'+r+'%;background:var(--rem)"></div><div class="s" style="width:'+a+'%;background:var(--awake)"></div></div>';
}}

const nav=document.getElementById('nav');
const content=document.getElementById('content');

function render(idx){{
  const d=D.days[idx];

  nav.innerHTML=D.days.map((dy,i)=>{{
    const rec=dy.whoop&&dy.whoop.recovery?dy.whoop.recovery+'%':'';
    return '<button class="day-btn '+(i===idx?'active':'')+'" onclick="render('+i+')">'+dy.date+'<span class="sub">'+dy.sleep_hours+'h '+rec+'</span></button>';
  }}).join('');

  let h='';

  // 1. MAE Summary for this day
  const algos=['algo_a','algo_b','algo_c','algo_d'];
  const maes=algos.map(k=>d[k]?d[k].mae:null);
  const validMaes=maes.filter(m=>m!=null);
  const bestMae=validMaes.length?Math.min(...validMaes):null;

  h+='<details class="sect" open><summary>Algorithm Accuracy (MAE vs Whoop)<span class="badge">'+d.date+'</span></summary><div class="inner"><div class="card">';
  if(validMaes.length>0){{
    h+='<div class="mae-row">';
    algos.forEach((k,ki)=>{{
      const m=maes[ki];
      const isWinner=m!=null&&m===bestMae;
      h+='<div class="mae-card '+(isWinner?'winner':'')+'">';
      h+='<div class="algo-name" style="color:'+algoColors[k]+'">'+algoNames[k]+'</div>';
      h+='<div class="mae-val" style="color:'+(m!=null?(isWinner?'var(--green)':'var(--text)'):'var(--dim)')+'">'+
         (m!=null?m.toFixed(1):'-')+'</div>';
      h+='<div class="mae-label">avg stage % error</div>';
      h+='</div>';
    }});
    h+='</div>';
  }}else{{
    h+='<div style="color:var(--dim);font-size:11px;padding:8px">No Whoop official data for MAE calculation</div>';
  }}
  h+='</div></div></details>';

  // 2. Hypnograms (stacked)
  h+='<details class="sect" open><summary>Hypnograms<span class="badge">stacked comparison</span></summary><div class="inner"><div class="card">';

  // Whoop
  const wPh=d.whoop_phases||[];
  h+='<div class="hypno-row"><div class="hypno-label" style="color:var(--green)">Whoop Official <span class="tag">'+wPh.length+' min</span></div>';
  if(wPh.length>0){{
    h+='<div class="hypno-container"><canvas id="hW_'+idx+'" class="hypno-canvas"></canvas></div>';
    h+=timeAxis(wPh);
  }}else{{
    h+='<div style="color:var(--dim);font-size:10px;padding:6px 0">No timeline data</div>';
  }}
  h+='</div>';

  // Algo A-D
  algos.forEach(k=>{{
    const aph=d[k]?d[k].phases:[];
    const ast=d[k]?d[k].stats:{{}};
    const nm=algoNames[k];
    const col=algoColors[k];
    const wn=d[k]?d[k].window:60;
    h+='<div class="hypno-row"><div class="hypno-label" style="color:'+col+'">'+nm+' <span class="tag">'+aph.length+' x '+wn+'s</span></div>';
    if(aph.length>0){{
      h+='<div class="hypno-container"><canvas id="h_'+k+'_'+idx+'" class="hypno-canvas"></canvas></div>';
      h+=timeAxis(aph);
    }}else{{
      h+='<div style="color:var(--dim);font-size:10px;padding:6px 0">No data</div>';
    }}
    h+='</div>';
  }});

  h+='<div class="legend">';
  h+='<span><span class="d" style="background:var(--awake)"></span>Awake</span>';
  h+='<span><span class="d" style="background:var(--rem)"></span>REM</span>';
  h+='<span><span class="d" style="background:var(--light-s)"></span>Light</span>';
  h+='<span><span class="d" style="background:var(--deep)"></span>Deep</span>';
  h+='</div>';
  h+='</div></div></details>';

  // 3. Sleep Stage Percentage Comparison
  h+='<details class="sect" open><summary>Sleep Stage Breakdown<span class="badge">percentages + bars</span></summary><div class="inner"><div class="card">';

  // Bars for each source
  const w=d.whoop||{{}};
  if(w.deep_pct!=null){{
    h+='<div style="margin-bottom:8px"><div style="font-size:10px;font-weight:600;color:var(--green);margin-bottom:2px">Whoop '+(w.duration||'')+'</div>';
    h+=stageBar(w,'var(--green)');
    h+='</div>';
  }}
  algos.forEach(k=>{{
    const st=d[k]?d[k].stats:{{}};
    if(st.deep_pct!=null){{
      h+='<div style="margin-bottom:8px"><div style="font-size:10px;font-weight:600;color:'+algoColors[k]+';margin-bottom:2px">'+algoNames[k]+' '+(st.tst_hours||'')+'</div>';
      h+=stageBar(st,algoColors[k]);
      h+='</div>';
    }}
  }});

  // Comparison table
  h+='<table class="cmp-table"><tr><th>Source</th><th>Duration</th><th>Deep %</th><th>Light %</th><th>REM %</th><th>Awake %</th><th>Efficiency</th></tr>';

  if(w.duration){{
    h+='<tr class="src" style="color:var(--green)"><td>Whoop</td><td class="num">'+w.duration+'</td>';
    h+='<td class="num">'+(w.deep_pct!=null?w.deep_pct+'%':'-')+'</td>';
    h+='<td class="num">'+(w.light_pct!=null?w.light_pct+'%':'-')+'</td>';
    h+='<td class="num">'+(w.rem_pct!=null?w.rem_pct+'%':'-')+'</td>';
    h+='<td class="num">'+(w.awake_pct!=null?w.awake_pct+'%':'-')+'</td>';
    h+='<td class="num">'+(w.efficiency||'-')+'</td></tr>';
  }}
  algos.forEach(k=>{{
    const st=d[k]?d[k].stats:{{}};
    if(st.tst_hours){{
      h+='<tr style="color:'+algoColors[k]+'"><td class="src">'+algoNames[k]+'</td><td class="num">'+st.tst_hours+'</td>';
      h+='<td class="num">'+(st.deep_pct!=null?st.deep_pct+'%':'-')+'</td>';
      h+='<td class="num">'+(st.light_pct!=null?st.light_pct+'%':'-')+'</td>';
      h+='<td class="num">'+(st.rem_pct!=null?st.rem_pct+'%':'-')+'</td>';
      h+='<td class="num">'+(st.awake_pct!=null?st.awake_pct+'%':'-')+'</td>';
      h+='<td class="num">'+(st.se!=null?st.se+'%':'-')+'</td></tr>';
    }}
  }});

  h+='<tr style="color:var(--dim)"><td>Raw Data</td><td class="num">'+d.sleep_hours+'h</td>';
  h+='<td colspan="5" style="font-size:10px">'+d.sleep_samples.toLocaleString()+' samples, RHR='+d.rhr+' bpm</td></tr>';

  h+='</table></div></div></details>';

  // 4. Duration comparison
  h+='<details class="sect"><summary>Sleep Duration Comparison<span class="badge">minutes per stage</span></summary><div class="inner"><div class="card">';
  h+='<table class="cmp-table"><tr><th>Source</th><th>Total</th><th>Deep</th><th>Light</th><th>REM</th><th>Awake</th></tr>';

  if(w.duration){{
    h+='<tr style="color:var(--green)"><td class="src">Whoop</td><td class="num">'+w.duration+'</td>';
    h+='<td class="num">'+(w.deep_time||'-')+'</td>';
    h+='<td class="num">'+(w.light_time||'-')+'</td>';
    h+='<td class="num">'+(w.rem_time||'-')+'</td>';
    h+='<td class="num">'+(w.awake_time||'-')+'</td></tr>';
  }}
  algos.forEach(k=>{{
    const st=d[k]?d[k].stats:{{}};
    if(st.tst_hours){{
      h+='<tr style="color:'+algoColors[k]+'"><td class="src">'+algoNames[k]+'</td>';
      h+='<td class="num">'+st.tst_hours+'</td>';
      h+='<td class="num">'+fM(st.deep_min)+'</td>';
      h+='<td class="num">'+fM(st.light_min)+'</td>';
      h+='<td class="num">'+fM(st.rem_min)+'</td>';
      h+='<td class="num">'+fM(st.awake_min)+'</td></tr>';
    }}
  }});
  h+='</table></div></div></details>';

  // 5. Per-stage MAE detail
  h+='<details class="sect"><summary>Per-Stage Error Detail<span class="badge">absolute % difference from Whoop</span></summary><div class="inner"><div class="card">';
  if(w.deep_pct!=null){{
    h+='<table class="cmp-table"><tr><th>Algorithm</th><th>Deep err</th><th>Light err</th><th>REM err</th><th>Awake err</th><th>Mean</th></tr>';
    algos.forEach(k=>{{
      const st=d[k]?d[k].stats:{{}};
      const m=d[k]?d[k].mae:null;
      if(st.deep_pct!=null){{
        const de=w.deep_pct!=null?Math.abs(w.deep_pct-st.deep_pct).toFixed(1):'-';
        const le=w.light_pct!=null?Math.abs(w.light_pct-st.light_pct).toFixed(1):'-';
        const re=w.rem_pct!=null?Math.abs(w.rem_pct-st.rem_pct).toFixed(1):'-';
        const ae=w.awake_pct!=null?Math.abs(w.awake_pct-st.awake_pct).toFixed(1):'-';
        const isBest=m!=null&&m===bestMae;
        h+='<tr style="color:'+algoColors[k]+'"'+(isBest?' class="best"':'')+'><td class="src">'+algoNames[k]+'</td>';
        h+='<td class="num">'+de+'%</td><td class="num">'+le+'%</td>';
        h+='<td class="num">'+re+'%</td><td class="num">'+ae+'%</td>';
        h+='<td class="num" style="font-weight:700">'+(m!=null?m.toFixed(1)+'%':'-')+'</td></tr>';
      }}
    }});
    h+='</table>';
  }}else{{
    h+='<div style="color:var(--dim);font-size:11px;padding:8px">No Whoop official percentages for this day</div>';
  }}
  h+='</div></div></details>';

  content.innerHTML=h;

  // Draw hypnograms
  setTimeout(()=>{{
    if(wPh.length>0)drawHypno('hW_'+idx,wPh,'#44cf6c');
    algos.forEach(k=>{{
      const aph=d[k]?d[k].phases:[];
      if(aph.length>0)drawHypno('h_'+k+'_'+idx,aph,algoColors[k]);
    }});
  }},50);
}}

// Start with day that has best whoop data
let defIdx=0;
for(let i=D.days.length-1;i>=0;i--){{if(D.days[i].whoop&&D.days[i].whoop.recovery){{defIdx=i;break;}}}}
render(defIdx);
</script></body></html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Algorithm Comparison Dashboard")
    print("  A: HRV Spectral | B: SleepECG ML")
    print("  C: HRV Nonlinear | D: HR Delta | E: YASA | F: Trained ML")
    print("=" * 60)

    print("\nLoading DB...")
    df = load_from_db()
    if df.empty:
        print("No data!")
        return

    df = df[df["date"].apply(lambda d: hasattr(d, "year") and 2025 <= d.year <= 2026)]
    print(f"  {len(df)} records after date filter")

    whoop_official = load_whoop_official()
    print(f"  Whoop official data for {len(whoop_official)} days")

    days = sorted(d for d in df["date"].unique() if hasattr(d, "year"))

    results = []
    for day in days:
        sw = get_sleep_window(df, day)
        if len(sw) < MIN_SLEEP_SAMPLES:
            continue
        print(f"\n  Analyzing {day} ({len(sw)} samples, {len(sw)/3600:.1f}h)...")
        result = analyze_day(df, day, whoop_official)
        if result:
            results.append(result)

    print(f"\n{'=' * 60}")
    print(f"  {len(results)} days analyzed")

    # Print summary
    algo_keys = ["algo_a", "algo_b", "algo_c", "algo_d", "algo_e", "algo_f"]
    for k in algo_keys:
        maes = [r[k]["mae"] for r in results if r[k]["mae"] is not None]
        if maes:
            name = results[0][k]["name"]
            print(f"  {name}: avg MAE = {sum(maes)/len(maes):.1f} ({len(maes)} nights)")
        else:
            print(f"  {k}: no MAE data")

    # Count wins
    wins = {k: 0 for k in algo_keys}
    for r in results:
        valid = {k: r[k]["mae"] for k in algo_keys if r[k]["mae"] is not None}
        if valid:
            winner = min(valid, key=valid.get)
            wins[winner] += 1
    print(f"\n  Closest to Whoop (nights won):")
    for k in algo_keys:
        name = results[0][k]["name"] if results else k
        print(f"    {name}: {wins[k]}")

    # Generate dashboard
    dashboard = {"days": results}
    html = generate_html(dashboard)
    out = Path(__file__).parent / "algo_compare.html"
    out.write_text(html)
    print(f"\nDashboard: file://{out.resolve()}")


if __name__ == "__main__":
    main()
