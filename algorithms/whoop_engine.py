#!/usr/bin/env python3
"""WhoopEngine -- unified Open Whoop scoring pipeline.

Provides sleep staging, recovery, strain, sleep score, activity detection,
and sleep need estimation from raw sensor data. Uses trained ML models when
available, with rule-based fallbacks for everything.

Usage:
    from whoop_engine import WhoopEngine
    engine = WhoopEngine()
    sleep = engine.analyze_night(sleep_df)
    day   = engine.analyze_day(day_df)
    rec   = engine.compute_recovery(sleep, day)
"""

import math
import json
import warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import joblib

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Import feature extraction from train_whoop_model (no duplication)
# ---------------------------------------------------------------------------
import sys

_ALGO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_ALGO_DIR))

from train_whoop_model import (
    extract_minute_features,
    add_rolling_and_delta,
    FEATURE_NAMES,
    PHASE_TO_INT,
    INT_TO_PHASE,
    get_sleep_window,
    extract_whoop_labels,
    align_labels_to_sensor,
    build_night_data,
    detect_sleep_onset_offset,
    viterbi_decode,
    apply_viterbi,
)
from common.preprocessing import (
    compute_rhr,
    compute_hrv_rmssd,
    compute_respiratory_rate,
)

BERLIN = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Data classes for structured results
# ---------------------------------------------------------------------------

@dataclass
class SleepResult:
    """Results from analyze_night()."""
    phases: list[dict]          # [{"time": "HH:MM", "phase": str}, ...]
    duration_hours: float       # total time in bed (hours)
    sleep_hours: float          # time asleep (excl. awake)
    efficiency: float           # sleep_hours / duration_hours * 100
    deep_pct: float
    light_pct: float
    rem_pct: float
    awake_pct: float
    cycles: int                 # estimated sleep cycles (deep+rem transitions)
    rhr: float                  # resting HR during sleep
    hrv: float                  # RMSSD during SWS
    resp_rate: float            # breaths per minute
    onset_time: str             # "HH:MM" estimated sleep onset
    wake_time: str              # "HH:MM" estimated wake
    restorative_min: float      # deep + rem minutes
    disturbances: int           # number of awake bouts
    used_ml: bool = False       # whether ML model was used


@dataclass
class DayResult:
    """Results from analyze_day()."""
    strain: float               # 0-21
    calories: float             # estimated kcal
    hr_mean: float
    hr_max: float
    zone_minutes: dict          # {zone1: min, ..., zone5: min}
    zone_load: float            # weighted zone score
    activities: list[dict]      # detected exercise periods
    active_minutes: float       # total minutes HR > 50% max


@dataclass
class RecoveryResult:
    """Results from compute_recovery()."""
    score: float                # 0-100
    zone: str                   # "green" / "yellow" / "red"
    hrv_contribution: float
    rhr_contribution: float
    sleep_contribution: float
    resp_penalty: float
    used_ml: bool = False


@dataclass
class SleepScoreResult:
    """Results from compute_sleep_score()."""
    score: float                # 0-100
    hours_component: float
    efficiency_component: float
    consistency_component: float
    used_ml: bool = False


# ---------------------------------------------------------------------------
# WhoopEngine
# ---------------------------------------------------------------------------

class WhoopEngine:
    """Unified Open Whoop scoring engine.

    Loads trained ML models if available, falls back to rule-based scoring.
    All methods accept raw sensor DataFrames from db_loader.load_from_db().
    """

    def __init__(self, model_dir: str = "."):
        model_path = Path(model_dir).resolve()

        # Sleep staging model (HistGBT, 58 features, 4 classes)
        self.sleep_model = None
        self.log_trans = None
        self.log_init = None
        staging_file = model_path / "whoop_model.joblib"
        if staging_file.exists():
            try:
                self.sleep_model = joblib.load(staging_file)
                n = getattr(self.sleep_model, "n_features_in_", 0)
                if n != len(FEATURE_NAMES):
                    print(f"[WhoopEngine] WARNING: model expects {n} features, "
                          f"have {len(FEATURE_NAMES)} -- falling back to rules")
                    self.sleep_model = None
                else:
                    print(f"[WhoopEngine] Loaded sleep staging model ({n} features)")
            except Exception as e:
                print(f"[WhoopEngine] Failed to load sleep model: {e}")

        # Viterbi transition matrix for temporal coherence
        trans_file = model_path / "whoop_transition_matrix.joblib"
        if trans_file.exists():
            try:
                tm = joblib.load(trans_file)
                self.log_trans = tm["log_trans"]
                self.log_init = tm["log_init"]
                print(f"[WhoopEngine] Loaded Viterbi transition matrix")
            except Exception as e:
                print(f"[WhoopEngine] Failed to load transition matrix: {e}")

        # Recovery + sleep score regressors
        self.recovery_model = None
        self.sleep_score_model = None
        self.score_feature_names = None
        score_file = model_path / "whoop_score_models.joblib"
        if score_file.exists():
            try:
                bundle = joblib.load(score_file)
                self.recovery_model = bundle.get("recovery")
                self.sleep_score_model = bundle.get("sleep")
                self.score_feature_names = bundle.get("feature_names")
                print(f"[WhoopEngine] Loaded recovery + sleep score models")
            except Exception as e:
                print(f"[WhoopEngine] Failed to load score models: {e}")

        # Baseline HRV/RHR for rule-based recovery (updated per call)
        self._hrv_history: list[float] = []
        self._rhr_history: list[float] = []

        if not self.sleep_model:
            print("[WhoopEngine] No ML sleep model -- using rule-based staging")
        if not self.recovery_model:
            print("[WhoopEngine] No ML score models -- using rule-based recovery/sleep scores")

    # ------------------------------------------------------------------
    # 1. analyze_night -- full sleep analysis
    # ------------------------------------------------------------------

    def analyze_night(self, sleep_df: pd.DataFrame, rhr: float | None = None) -> SleepResult:
        """Analyze a sleep period. Input: sensor DataFrame for the sleep window.

        sleep_df must have columns: timestamp, hr, rr1_ms, datetime_local.
        Optionally: acc_x, acc_y, acc_z, gyro, spo2.
        """
        if sleep_df.empty or len(sleep_df) < 300:
            return self._empty_sleep_result()

        if rhr is None:
            rhr = compute_rhr(sleep_df)

        hrv = compute_hrv_rmssd(sleep_df, method="sws")
        resp = compute_respiratory_rate(sleep_df)

        # Update baselines
        if hrv > 0:
            self._hrv_history.append(hrv)
        if rhr > 0:
            self._rhr_history.append(rhr)

        # Sleep staging
        if self.sleep_model is not None:
            phases = self._stage_ml(sleep_df, rhr)
            used_ml = True
        else:
            phases = self._stage_rules(sleep_df, rhr)
            used_ml = False

        if not phases:
            return self._empty_sleep_result()

        # Compute stats from phases
        total = len(phases)
        counts = Counter(p["phase"] for p in phases)
        deep_pct = counts.get("deep", 0) / total * 100
        light_pct = counts.get("light", 0) / total * 100
        rem_pct = counts.get("rem", 0) / total * 100
        awake_pct = counts.get("awake", 0) / total * 100

        duration_hours = total / 60.0  # 1-min windows
        sleep_hours = (total - counts.get("awake", 0)) / 60.0
        efficiency = sleep_hours / duration_hours * 100 if duration_hours > 0 else 0

        # Sleep cycles: count deep->rem transitions
        cycles = 0
        in_deep = False
        for p in phases:
            if p["phase"] == "deep":
                in_deep = True
            elif p["phase"] == "rem" and in_deep:
                cycles += 1
                in_deep = False
            elif p["phase"] != "deep":
                in_deep = False
        cycles = max(cycles, 1)  # at least 1 if there's sleep

        # Disturbances: count awake bouts
        disturbances = 0
        prev_awake = False
        for p in phases:
            if p["phase"] == "awake" and not prev_awake:
                disturbances += 1
            prev_awake = p["phase"] == "awake"

        restorative_min = (counts.get("deep", 0) + counts.get("rem", 0))

        return SleepResult(
            phases=phases,
            duration_hours=round(duration_hours, 2),
            sleep_hours=round(sleep_hours, 2),
            efficiency=round(efficiency, 1),
            deep_pct=round(deep_pct, 1),
            light_pct=round(light_pct, 1),
            rem_pct=round(rem_pct, 1),
            awake_pct=round(awake_pct, 1),
            cycles=cycles,
            rhr=round(rhr, 1),
            hrv=round(hrv, 1),
            resp_rate=round(resp, 1),
            onset_time=phases[0]["time"] if phases else "00:00",
            wake_time=phases[-1]["time"] if phases else "00:00",
            restorative_min=restorative_min,
            disturbances=disturbances,
            used_ml=used_ml,
        )

    def _stage_ml(self, sleep_df: pd.DataFrame, rhr: float) -> list[dict]:
        """Stage sleep using the trained ML model with Viterbi post-processing."""
        # Detect sleep onset/offset for trimming
        onset_ts, offset_ts = detect_sleep_onset_offset(sleep_df)

        ts_arr = sleep_df["timestamp"].values
        sleep_start_ts = int(ts_arr[0])
        sleep_end_ts = int(ts_arr[-1])

        # Use detected boundaries if available and sensible (>= 3 hours)
        if onset_ts is not None and offset_ts is not None and offset_ts > onset_ts:
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

        window_sec = 60
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
        if self.log_trans is not None and self.log_init is not None:
            predictions = apply_viterbi(self.sleep_model, X, self.log_trans, self.log_init)
        else:
            predictions = self.sleep_model.predict(X)
            # Smooth isolated predictions (fallback)
            for i in range(1, len(predictions) - 1):
                if predictions[i] != predictions[i - 1] and predictions[i] != predictions[i + 1]:
                    predictions[i] = predictions[i - 1]

        return [
            {"time": times_list[i], "phase": INT_TO_PHASE.get(int(predictions[i]), "light")}
            for i in range(len(predictions))
        ]

    def _stage_rules(self, sleep_df: pd.DataFrame, rhr: float) -> list[dict]:
        """Rule-based sleep staging using HR/HRV thresholds."""
        window_sec = 60
        phases = []

        for i in range(0, len(sleep_df) - window_sec, window_sec):
            chunk = sleep_df.iloc[i:i + window_sec]
            t = chunk["datetime_local"].iloc[0]
            time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)

            hr = chunk["hr"].values
            hr_valid = hr[hr > 30]
            if len(hr_valid) < 5:
                phases.append({"time": time_str, "phase": "light"})
                continue

            avg_hr = float(np.mean(hr_valid))
            std_hr = float(np.std(hr_valid)) if len(hr_valid) > 1 else 0.0
            hr_above = avg_hr - rhr

            # Movement if available
            movement = 0.0
            if "movement" in chunk.columns:
                mv = chunk["movement"].values
                movement = float(np.mean(np.abs(mv)))

            # HRV from RR intervals
            rr = chunk["rr1_ms"].dropna().values
            rr = rr[(rr > 200) & (rr < 2500)]
            rmssd = 0.0
            if len(rr) >= 5:
                diffs = np.diff(rr)
                diffs = diffs[np.abs(diffs) < 300]
                if len(diffs) >= 3:
                    rmssd = float(np.sqrt(np.mean(diffs ** 2)))

            # Classification rules
            if hr_above > 15 or movement > 0.5:
                phase = "awake"
            elif hr_above < 4 and std_hr < 3 and rmssd > 30:
                phase = "deep"
            elif std_hr > 5 and rmssd > 20 and hr_above > 3:
                phase = "rem"
            else:
                phase = "light"

            phases.append({"time": time_str, "phase": phase})

        # Smooth isolated phases
        for i in range(1, len(phases) - 1):
            if (phases[i]["phase"] != phases[i - 1]["phase"]
                    and phases[i]["phase"] != phases[i + 1]["phase"]):
                phases[i] = {**phases[i], "phase": phases[i - 1]["phase"]}

        return phases

    def _empty_sleep_result(self) -> SleepResult:
        return SleepResult(
            phases=[], duration_hours=0, sleep_hours=0, efficiency=0,
            deep_pct=0, light_pct=0, rem_pct=0, awake_pct=0,
            cycles=0, rhr=0, hrv=0, resp_rate=0,
            onset_time="--:--", wake_time="--:--",
            restorative_min=0, disturbances=0,
        )

    # ------------------------------------------------------------------
    # 2. analyze_day -- daily strain and activity
    # ------------------------------------------------------------------

    def analyze_day(self, day_df: pd.DataFrame, max_hr: int = 200) -> DayResult:
        """Analyze a full day of sensor data for strain and activities.

        day_df: sensor DataFrame for waking hours (or full day).
        """
        if day_df.empty:
            return DayResult(
                strain=0, calories=0, hr_mean=0, hr_max=0,
                zone_minutes={}, zone_load=0, activities=[], active_minutes=0,
            )

        hrs = day_df["hr"].values
        valid_hr = hrs[hrs > 30]

        if len(valid_hr) == 0:
            return DayResult(
                strain=0, calories=0, hr_mean=0, hr_max=0,
                zone_minutes={}, zone_load=0, activities=[], active_minutes=0,
            )

        hr_mean = float(np.mean(valid_hr))
        hr_max = float(np.max(valid_hr))

        # HR zones and strain
        zone_minutes, zone_load = self._compute_zone_load(day_df, max_hr)
        strain = self.compute_strain(day_df, max_hr)

        # Activity detection
        activities = self.detect_activities(day_df, max_hr)

        # Calories
        calories = self._estimate_calories(day_df, zone_load, max_hr)

        # Active minutes (HR > 50% max)
        threshold = max_hr * 0.5
        active_seconds = float(np.sum(valid_hr >= threshold))
        active_minutes = active_seconds / 60.0

        return DayResult(
            strain=round(strain, 1),
            calories=round(calories, 0),
            hr_mean=round(hr_mean, 1),
            hr_max=round(hr_max, 1),
            zone_minutes=zone_minutes,
            zone_load=round(zone_load, 1),
            activities=activities,
            active_minutes=round(active_minutes, 1),
        )

    def _compute_zone_load(self, day_df: pd.DataFrame, max_hr: int) -> tuple[dict, float]:
        """Compute time in HR zones and weighted zone load."""
        hrs = day_df["hr"].values
        hrs = hrs[hrs > 30]

        boundaries = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        weights = [1, 2, 4, 8, 16]
        zone_minutes = {}
        zone_load = 0.0

        for i in range(5):
            lo = max_hr * boundaries[i]
            hi = max_hr * boundaries[i + 1]
            if i < 4:
                count = int(np.sum((hrs >= lo) & (hrs < hi)))
            else:
                count = int(np.sum(hrs >= lo))
            minutes = count / 60.0
            zone_minutes[f"zone{i + 1}"] = round(minutes, 1)
            zone_load += minutes * weights[i]

        return zone_minutes, zone_load

    # ------------------------------------------------------------------
    # 3. compute_recovery
    # ------------------------------------------------------------------

    def compute_recovery(
        self,
        sleep_result: SleepResult,
        day_result: DayResult | None = None,
    ) -> RecoveryResult:
        """Compute recovery score 0-100 with zone classification.

        Uses ML model if available, otherwise rule-based formula:
        0.60 * sigmoid(hrv/baseline) + 0.25 * (50 + (rhr_base - rhr) * 8)
        + 0.15 * sleep_eff - resp_penalty
        """
        # Try ML first
        if self.recovery_model is not None and self.score_feature_names is not None:
            try:
                features = self._build_score_features(sleep_result)
                X = np.array([features])
                X = np.nan_to_num(X, nan=0.0)
                score = float(self.recovery_model.predict(X)[0])
                score = max(0, min(100, score))
                zone = self._recovery_zone(score)
                return RecoveryResult(
                    score=round(score, 0),
                    zone=zone,
                    hrv_contribution=0,
                    rhr_contribution=0,
                    sleep_contribution=0,
                    resp_penalty=0,
                    used_ml=True,
                )
            except Exception:
                pass  # fall through to rules

        # Rule-based fallback
        hrv = sleep_result.hrv
        rhr = sleep_result.rhr
        eff = sleep_result.efficiency
        resp = sleep_result.resp_rate

        # Baseline: rolling average (or sensible defaults)
        hrv_baseline = float(np.mean(self._hrv_history[-14:])) if self._hrv_history else 60.0
        rhr_baseline = float(np.mean(self._rhr_history[-14:])) if self._rhr_history else 55.0

        # HRV component: sigmoid of hrv relative to baseline (0-100)
        hrv_ratio = hrv / hrv_baseline if hrv_baseline > 0 else 1.0
        hrv_comp = 100.0 / (1.0 + math.exp(-4.0 * (hrv_ratio - 1.0)))

        # RHR component (50 ± deviation from baseline * 8)
        rhr_comp = 50.0 + (rhr_baseline - rhr) * 8.0
        rhr_comp = max(0, min(100, rhr_comp))

        # Sleep efficiency component
        sleep_comp = min(100, eff)

        # Respiratory rate penalty (normal = 12-18 brpm)
        resp_penalty = 0.0
        if resp > 18:
            resp_penalty = (resp - 18) * 5.0
        elif resp < 10:
            resp_penalty = (10 - resp) * 5.0

        score = (0.60 * hrv_comp + 0.25 * rhr_comp + 0.15 * sleep_comp
                 - resp_penalty)
        score = max(0, min(100, score))
        zone = self._recovery_zone(score)

        return RecoveryResult(
            score=round(score, 0),
            zone=zone,
            hrv_contribution=round(hrv_comp, 1),
            rhr_contribution=round(rhr_comp, 1),
            sleep_contribution=round(sleep_comp, 1),
            resp_penalty=round(resp_penalty, 1),
        )

    @staticmethod
    def _recovery_zone(score: float) -> str:
        if score >= 67:
            return "green"
        elif score >= 34:
            return "yellow"
        return "red"

    # ------------------------------------------------------------------
    # 4. compute_sleep_score
    # ------------------------------------------------------------------

    def compute_sleep_score(self, sleep_result: SleepResult) -> SleepScoreResult:
        """Compute sleep performance score 0-100.

        Uses ML model if available, otherwise:
        0.4 * hours_vs_needed + 0.3 * efficiency + 0.3 * consistency
        """
        # Try ML first
        if self.sleep_score_model is not None and self.score_feature_names is not None:
            try:
                features = self._build_score_features(sleep_result)
                X = np.array([features])
                X = np.nan_to_num(X, nan=0.0)
                score = float(self.sleep_score_model.predict(X)[0])
                score = max(0, min(100, score))
                return SleepScoreResult(
                    score=round(score, 0),
                    hours_component=0,
                    efficiency_component=0,
                    consistency_component=0,
                    used_ml=True,
                )
            except Exception:
                pass

        # Rule-based
        need = self.compute_sleep_need(strain=10.0)  # default moderate strain
        hours_ratio = sleep_result.sleep_hours / need if need > 0 else 1.0
        hours_comp = min(100, hours_ratio * 100)

        eff_comp = min(100, sleep_result.efficiency)

        # Consistency: 100 if onset in typical range (22:00-00:00), deductions otherwise
        consistency = 70.0  # baseline without history
        try:
            onset_h, onset_m = map(int, sleep_result.onset_time.split(":"))
            onset_mins = onset_h * 60 + onset_m
            if onset_mins > 720:  # after noon = evening
                onset_mins -= 1440
            # Ideal: 22:00-00:00 (1320-1440 -> -120 to 0 after adjustment)
            ideal_center = -90  # 22:30
            deviation = abs(onset_mins - ideal_center)
            consistency = max(30, 100 - deviation * 0.5)
        except (ValueError, AttributeError):
            pass

        score = 0.4 * hours_comp + 0.3 * eff_comp + 0.3 * consistency
        score = max(0, min(100, score))

        return SleepScoreResult(
            score=round(score, 0),
            hours_component=round(hours_comp, 1),
            efficiency_component=round(eff_comp, 1),
            consistency_component=round(consistency, 1),
        )

    # ------------------------------------------------------------------
    # 5. compute_strain
    # ------------------------------------------------------------------

    def compute_strain(self, day_df: pd.DataFrame, max_hr: int = 200) -> float:
        """Compute strain 0-21 from HR zone accumulated load.

        Formula: min(21, 5.5 * log(1 + zone_load / 15))
        Zone weights: [1, 2, 4, 8, 16] for 50-60%, 60-70%, ..., 90-100% MaxHR
        """
        _, zone_load = self._compute_zone_load(day_df, max_hr)
        if zone_load <= 0:
            return 0.0
        strain = min(21.0, 5.5 * math.log(1.0 + zone_load / 15.0))
        return round(strain, 1)

    # ------------------------------------------------------------------
    # 6. compute_sleep_need
    # ------------------------------------------------------------------

    @staticmethod
    def compute_sleep_need(
        strain: float,
        baseline: float = 7.5,
        debt: float = 0.0,
    ) -> float:
        """Estimated sleep need in hours.

        Formula: baseline + max(0, (strain - 10) * 0.1) + debt * 0.2
        """
        need = baseline + max(0, (strain - 10) * 0.1) + debt * 0.2
        return round(need, 2)

    # ------------------------------------------------------------------
    # 7. detect_activities
    # ------------------------------------------------------------------

    def detect_activities(
        self,
        day_df: pd.DataFrame,
        max_hr: int = 200,
    ) -> list[dict]:
        """Detect exercise periods: HR > 50% MaxHR sustained > 5 min.

        Returns list of dicts with start, end, duration_min, avg_hr, max_hr,
        strain, calories.
        """
        if day_df.empty:
            return []

        threshold = max_hr * 0.5
        min_duration_sec = 300  # 5 minutes

        hrs = day_df["hr"].values
        timestamps = day_df["timestamp"].values.astype(int)
        above = hrs >= threshold

        activities = []
        start_idx = None

        for i in range(len(above)):
            if above[i] and start_idx is None:
                start_idx = i
            elif not above[i] and start_idx is not None:
                # End of an elevated period -- allow 30s gaps
                gap = 0
                if i + 1 < len(above):
                    # Look ahead for quick resumption
                    for j in range(i, min(i + 30, len(above))):
                        if above[j]:
                            gap = j - i
                            break
                    if 0 < gap <= 30:
                        continue  # bridge the gap

                duration = timestamps[i - 1] - timestamps[start_idx]
                if duration >= min_duration_sec:
                    seg_hr = hrs[start_idx:i]
                    seg_hr_valid = seg_hr[seg_hr > 30]
                    act_df = day_df.iloc[start_idx:i]
                    act_strain = self.compute_strain(act_df, max_hr)

                    # Estimate activity calories via EPOC
                    zone_min, zone_load = self._compute_zone_load(act_df, max_hr)
                    act_cal = self._estimate_calories(act_df, zone_load, max_hr)

                    start_dt = day_df.iloc[start_idx]["datetime_local"]
                    end_dt = day_df.iloc[i - 1]["datetime_local"]

                    activities.append({
                        "start": start_dt.strftime("%H:%M") if hasattr(start_dt, "strftime") else str(start_dt),
                        "end": end_dt.strftime("%H:%M") if hasattr(end_dt, "strftime") else str(end_dt),
                        "duration_min": round(duration / 60, 1),
                        "avg_hr": round(float(np.mean(seg_hr_valid)), 1) if len(seg_hr_valid) > 0 else 0,
                        "max_hr": round(float(np.max(seg_hr_valid)), 0) if len(seg_hr_valid) > 0 else 0,
                        "strain": act_strain,
                        "calories": round(act_cal, 0),
                    })

                start_idx = None

        # Handle case where activity extends to end of data
        if start_idx is not None:
            i = len(above)
            duration = timestamps[i - 1] - timestamps[start_idx]
            if duration >= min_duration_sec:
                seg_hr = hrs[start_idx:i]
                seg_hr_valid = seg_hr[seg_hr > 30]
                act_df = day_df.iloc[start_idx:i]
                act_strain = self.compute_strain(act_df, max_hr)
                zone_min, zone_load = self._compute_zone_load(act_df, max_hr)
                act_cal = self._estimate_calories(act_df, zone_load, max_hr)

                start_dt = day_df.iloc[start_idx]["datetime_local"]
                end_dt = day_df.iloc[i - 1]["datetime_local"]

                activities.append({
                    "start": start_dt.strftime("%H:%M") if hasattr(start_dt, "strftime") else str(start_dt),
                    "end": end_dt.strftime("%H:%M") if hasattr(end_dt, "strftime") else str(end_dt),
                    "duration_min": round(duration / 60, 1),
                    "avg_hr": round(float(np.mean(seg_hr_valid)), 1) if len(seg_hr_valid) > 0 else 0,
                    "max_hr": round(float(np.max(seg_hr_valid)), 0) if len(seg_hr_valid) > 0 else 0,
                    "strain": act_strain,
                    "calories": round(act_cal, 0),
                })

        return activities

    # ------------------------------------------------------------------
    # 8. retrain -- static method
    # ------------------------------------------------------------------

    @staticmethod
    def retrain(data_dir: str = ".") -> None:
        """Retrain all models from available data.

        Loads sensor DB + Whoop labels, trains sleep staging with LONO-CV,
        trains score regressors, saves models, prints results.
        """
        from data.db_loader import load_from_db
        from sklearn.ensemble import HistGradientBoostingClassifier, GradientBoostingRegressor
        from sklearn.metrics import accuracy_score, confusion_matrix, classification_report

        data_path = Path(data_dir).resolve()
        model_path = data_path

        print("=" * 70)
        print("WHOOP ENGINE -- RETRAIN ALL MODELS")
        print("=" * 70)

        # 1. Load sensor data
        print("\n[1/4] Loading sensor data...")
        df = load_from_db()
        df = df[df["date"].apply(lambda d: 2025 <= d.year <= 2026 if hasattr(d, "year") else False)]
        print(f"  {len(df)} samples")

        dates = sorted(df["date"].unique())
        print(f"  {len(dates)} dates: {dates[0]} to {dates[-1]}")

        # 2. Build training data
        print("\n[2/4] Building training data from Whoop labels...")
        MIN_SLEEP_SAMPLES = 18000

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

            X, y, times = build_night_data(sleep_df, aligned, rhr)
            if X is None or len(X) < 60:
                continue

            all_nights.append({
                "date": date_str, "X": X, "y": y, "times": times,
                "sleep_df": sleep_df, "rhr": rhr,
                "labels": labels, "aligned": aligned,
            })
            counts = Counter(y)
            label_str = ", ".join(f"{INT_TO_PHASE[k]}:{v}" for k, v in sorted(counts.items()))
            print(f"  {date_str}: {len(X)} windows ({label_str})")

        print(f"\n  Total: {len(all_nights)} nights")

        if len(all_nights) < 2:
            print("ERROR: Need >= 2 nights for training")
            return

        # 3. LONO-CV + final model
        print("\n[3/4] Leave-one-night-out CV + final model training...")
        print("-" * 70)

        X_all = np.concatenate([n["X"] for n in all_nights])
        y_all = np.concatenate([n["y"] for n in all_nights])
        X_all = np.nan_to_num(X_all, nan=0.0, posinf=5.0, neginf=-5.0)

        all_y_true, all_y_pred = [], []

        for hold_idx in range(len(all_nights)):
            held = all_nights[hold_idx]
            train_X = np.concatenate([n["X"] for i, n in enumerate(all_nights) if i != hold_idx])
            train_y = np.concatenate([n["y"] for i, n in enumerate(all_nights) if i != hold_idx])
            train_X = np.nan_to_num(train_X, nan=0.0, posinf=5.0, neginf=-5.0)
            test_X = np.nan_to_num(held["X"], nan=0.0, posinf=5.0, neginf=-5.0)
            test_y = held["y"]

            mdl = HistGradientBoostingClassifier(
                max_iter=500, max_depth=4, learning_rate=0.05,
                min_samples_leaf=10, l2_regularization=0.01,
                max_bins=128, class_weight="balanced", random_state=42,
            )
            mdl.fit(train_X, train_y)
            pred = mdl.predict(test_X)

            # Smooth
            for i in range(1, len(pred) - 1):
                if pred[i] != pred[i - 1] and pred[i] != pred[i + 1]:
                    pred[i] = pred[i - 1]

            acc = accuracy_score(test_y, pred)
            all_y_true.extend(test_y)
            all_y_pred.extend(pred)
            print(f"  {held['date']}: acc={acc:.1%}")

        overall_acc = accuracy_score(all_y_true, all_y_pred)
        print(f"\n  Overall LONO accuracy: {overall_acc:.1%}")
        print(f"\n{classification_report(all_y_true, all_y_pred, labels=[0,1,2,3], target_names=['awake','light','deep','rem'], digits=3)}")

        # Train final model on all data
        final_model = HistGradientBoostingClassifier(
            max_iter=500, max_depth=4, learning_rate=0.05,
            min_samples_leaf=10, l2_regularization=0.01,
            max_bins=128, class_weight="balanced", random_state=42,
        )
        final_model.fit(X_all, y_all)
        joblib.dump(final_model, model_path / "whoop_model.joblib")
        print(f"  Saved: whoop_model.joblib")

        # 4. Score regressors
        print("\n[4/4] Training Recovery & Sleep Score regressors...")
        wo_path = _ALGO_DIR / "data" / "raw" / "whoop_official.json"
        if wo_path.exists():
            whoop_official = json.loads(wo_path.read_text())

            score_X, recovery_y, sleep_y = [], [], []
            for night in all_nights:
                date_str = night["date"]
                wo = whoop_official.get(date_str, {})
                rec = wo.get("recovery")
                slp = wo.get("sleep_score")
                if not rec or rec == "--" or not slp:
                    continue

                try:
                    rec_val = float(str(rec).replace("%", ""))
                    slp_val = float(str(slp).replace("%", ""))
                except (ValueError, TypeError):
                    continue

                sleep_df = night["sleep_df"]
                rhr = night["rhr"]
                hrv = compute_hrv_rmssd(sleep_df, method="sws")
                resp = compute_respiratory_rate(sleep_df) if len(sleep_df) > 60 else 14.0

                # Use final model for phase pcts
                ts_arr = sleep_df["timestamp"].values
                ss, se = int(ts_arr[0]), int(ts_arr[-1])
                td = max(se - ss, 1)

                feat_list, time_list = [], []
                ws = 60
                for idx in range(0, len(sleep_df) - ws, ws):
                    chunk = sleep_df.iloc[idx:idx + ws]
                    ts_mid = int(chunk["timestamp"].iloc[len(chunk) // 2])
                    f = extract_minute_features(chunk, rhr, (ts_mid - ss) / 3600.0, (ts_mid - ss) / td)
                    if f is None:
                        f = np.zeros(len(FEATURE_NAMES), dtype=np.float64)
                    feat_list.append(f)

                if not feat_list:
                    continue
                add_rolling_and_delta(feat_list)
                Xp = np.nan_to_num(np.array(feat_list), nan=0.0, posinf=5.0, neginf=-5.0)
                preds = final_model.predict(Xp)
                total = len(preds)
                c = Counter(preds)
                deep_pct = c.get(2, 0) / total * 100
                light_pct = c.get(1, 0) / total * 100
                rem_pct = c.get(3, 0) / total * 100
                awake_pct = c.get(0, 0) / total * 100
                sleep_eff = (total - c.get(0, 0)) / total * 100
                sleep_hours = total / 60.0

                score_X.append([hrv, rhr, resp, deep_pct, light_pct, rem_pct,
                                awake_pct, sleep_eff, sleep_hours])
                recovery_y.append(rec_val)
                sleep_y.append(slp_val)

            if len(score_X) >= 5:
                score_X_arr = np.nan_to_num(np.array(score_X), nan=0.0)

                rec_model = GradientBoostingRegressor(
                    n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42)
                rec_model.fit(score_X_arr, np.array(recovery_y))
                rec_mae = np.mean(np.abs(rec_model.predict(score_X_arr) - np.array(recovery_y)))
                print(f"  Recovery MAE: {rec_mae:.1f} (train, {len(recovery_y)} nights)")

                slp_model = GradientBoostingRegressor(
                    n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42)
                slp_model.fit(score_X_arr, np.array(sleep_y))
                slp_mae = np.mean(np.abs(slp_model.predict(score_X_arr) - np.array(sleep_y)))
                print(f"  Sleep score MAE: {slp_mae:.1f} (train, {len(sleep_y)} nights)")

                joblib.dump(
                    {"recovery": rec_model, "sleep": slp_model,
                     "feature_names": ["hrv", "rhr", "resp", "deep_pct", "light_pct",
                                       "rem_pct", "awake_pct", "sleep_eff", "sleep_hours"]},
                    model_path / "whoop_score_models.joblib",
                )
                print(f"  Saved: whoop_score_models.joblib")
            else:
                print(f"  Not enough data ({len(score_X)} nights)")
        else:
            print("  whoop_official.json not found, skipping")

        print("\n" + "=" * 70)
        print("RETRAIN COMPLETE")
        print("=" * 70)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_score_features(self, sleep_result: SleepResult) -> list[float]:
        """Build the 9-feature vector for recovery/sleep score models."""
        return [
            sleep_result.hrv,
            sleep_result.rhr,
            sleep_result.resp_rate,
            sleep_result.deep_pct,
            sleep_result.light_pct,
            sleep_result.rem_pct,
            sleep_result.awake_pct,
            sleep_result.efficiency,
            sleep_result.sleep_hours,
        ]

    @staticmethod
    def _estimate_calories(
        df: pd.DataFrame,
        zone_load: float,
        max_hr: int,
    ) -> float:
        """Estimate calories via HR-zone EPOC model.

        Simplified: BMR contribution + zone-load scaled active calories.
        Assumes ~75kg adult for rough estimation.
        """
        hrs = df["hr"].values
        valid_hr = hrs[hrs > 30]
        if len(valid_hr) == 0:
            return 0.0

        duration_hours = len(valid_hr) / 3600.0

        # Basal: ~1 kcal/kg/hr * 75kg
        bmr_cal = 75.0 * duration_hours

        # Active calories from zone load (empirical scaling)
        # Roughly: 1 zone-load-minute = 5-10 kcal depending on zone
        active_cal = zone_load * 0.15

        # HR-based EPOC estimate for vigorous exercise
        high_hr = valid_hr[valid_hr > max_hr * 0.8]
        epoc = len(high_hr) / 60.0 * 2.0  # ~2 extra kcal per minute in zone 4-5

        return bmr_cal + active_cal + epoc

    # ------------------------------------------------------------------
    # Convenience: process a full day (sleep + wake)
    # ------------------------------------------------------------------

    def process_day(
        self,
        df: pd.DataFrame,
        day,
        max_hr: int = 200,
    ) -> dict:
        """One-call convenience: analyze sleep + day + recovery + scores.

        day: datetime.date for the day to process.
        df: full sensor DataFrame (all dates).

        Returns dict with all results.
        """
        sleep_df = get_sleep_window(df, day)
        sleep_result = self.analyze_night(sleep_df)

        # Day data: current day 06:00 to 23:59
        day_mask = (
            (df["date"] == day)
            & (df["datetime_local"].apply(
                lambda x: x.hour if hasattr(x, "hour") else 12) >= 6)
        )
        day_df = df[day_mask]
        day_result = self.analyze_day(day_df, max_hr)

        recovery = self.compute_recovery(sleep_result, day_result)
        sleep_score = self.compute_sleep_score(sleep_result)
        sleep_need = self.compute_sleep_need(day_result.strain)

        return {
            "date": str(day),
            "sleep": sleep_result,
            "day": day_result,
            "recovery": recovery,
            "sleep_score": sleep_score,
            "sleep_need": sleep_need,
        }


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

def _test():
    """Run engine on one night from the sensor DB."""
    from data.db_loader import load_from_db

    print("Loading sensor data...")
    df = load_from_db()
    df = df[df["date"].apply(lambda d: 2025 <= d.year <= 2026 if hasattr(d, "year") else False)]
    dates = sorted(df["date"].unique())
    print(f"  {len(dates)} dates available: {dates[0]} to {dates[-1]}")

    # Pick a date with enough data
    engine = WhoopEngine(model_dir=str(_ALGO_DIR))

    print("\n" + "=" * 70)
    print("TESTING ON AVAILABLE NIGHTS")
    print("=" * 70)

    # Load official scores for comparison
    wo_path = _ALGO_DIR / "data" / "raw" / "whoop_official.json"
    whoop_official = {}
    if wo_path.exists():
        whoop_official = json.loads(wo_path.read_text())

    tested = 0
    for day in dates:
        sleep_df = get_sleep_window(df, day)
        if len(sleep_df) < 18000:
            continue

        result = engine.process_day(df, day)
        sr = result["sleep"]
        dr = result["day"]
        rec = result["recovery"]
        ss = result["sleep_score"]

        if sr.duration_hours == 0:
            continue

        tested += 1
        date_str = str(day)
        wo = whoop_official.get(date_str, {})

        print(f"\n--- {date_str} ---")
        print(f"  Sleep: {sr.sleep_hours:.1f}h / {sr.duration_hours:.1f}h "
              f"({sr.efficiency:.0f}% eff), "
              f"{sr.cycles} cycles, {sr.disturbances} disturbances"
              f"  [ML={sr.used_ml}]")
        print(f"  Stages: deep={sr.deep_pct:.0f}% light={sr.light_pct:.0f}% "
              f"rem={sr.rem_pct:.0f}% awake={sr.awake_pct:.0f}%")
        print(f"  Bio: RHR={sr.rhr:.0f} HRV={sr.hrv:.0f}ms Resp={sr.resp_rate:.1f}brpm")
        print(f"  Recovery: {rec.score:.0f} ({rec.zone})"
              f"  [ML={rec.used_ml}]", end="")
        if wo.get("recovery") and wo["recovery"] != "--":
            print(f"  (Whoop: {wo['recovery']})", end="")
        print()
        print(f"  Sleep Score: {ss.score:.0f}"
              f"  [ML={ss.used_ml}]", end="")
        if wo.get("sleep_score"):
            print(f"  (Whoop: {wo['sleep_score']})", end="")
        print()
        print(f"  Strain: {dr.strain:.1f}", end="")
        if wo.get("strain"):
            print(f"  (Whoop: {wo['strain']})", end="")
        print()
        print(f"  Sleep need: {result['sleep_need']:.1f}h")

        if dr.activities:
            print(f"  Activities ({len(dr.activities)}):")
            for a in dr.activities:
                print(f"    {a['start']}-{a['end']} "
                      f"({a['duration_min']:.0f}min, "
                      f"avg={a['avg_hr']:.0f}bpm, "
                      f"strain={a['strain']:.1f})")

        if tested >= 5:
            break

    if tested == 0:
        print("\nNo nights with enough data found.")
    else:
        print(f"\n  Tested {tested} nights.")


if __name__ == "__main__":
    _test()
