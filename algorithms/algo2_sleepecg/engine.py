"""Algorithm 2: SleepECG-based sleep staging + custom strain/recovery.

Uses the SleepECG library (JOSS paper: Brunner & Hofer 2023) for ML-based
sleep stage classification from heartbeat times. The pre-trained WRN-GRU
classifier takes cumulative heartbeat times and classifies 30-second epochs
into Wake/N1/N2/N3/REM.
"""

import math
import numpy as np
import pandas as pd
from pathlib import Path

from common.metrics import BaseAlgorithm, WhoopScores
from common.preprocessing import (
    compute_rhr, compute_hrv_rmssd, compute_hr_zones,
    compute_daily_features, compute_respiratory_rate,
)

try:
    import sleepecg
    HAS_SLEEPECG = True
except ImportError:
    HAS_SLEEPECG = False
    print("WARNING: sleepecg not installed. Install with: pip install sleepecg")


class SleepECGAlgorithm(BaseAlgorithm):
    name = "sleepecg_hybrid"

    def __init__(self, max_hr: int = 200, sleep_need_min: int = 480):
        self.max_hr = max_hr
        self.sleep_need_min = sleep_need_min
        self._hrv_baseline = None
        self._rhr_baseline = None
        self._clf = None

    def _get_classifier(self):
        """Load pre-trained SleepECG classifier (cached)."""
        if self._clf is not None:
            return self._clf
        if not HAS_SLEEPECG:
            return None
        try:
            self._clf = sleepecg.load_classifier("wrn-gru-mesa", classifiers_dir="SleepECG")
            print(f"    Loaded SleepECG classifier: wrn-gru-mesa (3-class: WAKE/REM/NREM)")
        except Exception as e:
            print(f"    Failed to load SleepECG classifier: {e}")
        return self._clf

    def _classify_sleep_sleepecg(self, rr_vals: np.ndarray) -> dict | None:
        """Classify sleep stages using SleepECG's stage() function."""
        clf = self._get_classifier()
        if clf is None or len(rr_vals) < 100:
            return None

        try:
            # Convert RR intervals (ms) to cumulative heartbeat times (seconds)
            beat_times = np.cumsum(rr_vals) / 1000.0

            # Create SleepRecord with heartbeat times
            record = sleepecg.SleepRecord(
                heartbeat_times=beat_times,
                recording_start_time=None,
            )

            # Classify using pre-trained model
            stages = sleepecg.stage(clf, record, return_mode="int")

            # wrn-gru-mesa 3-class: 0=WAKE, 1=REM, 2=NREM
            n_epochs = len(stages)
            if n_epochs == 0:
                return None

            wake = int(np.sum(stages == 0))
            rem = int(np.sum(stages == 1))
            nrem = int(np.sum(stages == 2))
            deep = int(nrem * 0.25)  # estimate deep as 25% of NREM
            light = nrem - deep
            sleep_epochs = rem + nrem

            return {
                "method": "sleepecg",
                "sleep_total_min": sleep_epochs * 0.5,
                "sleep_efficiency": sleep_epochs / n_epochs * 100 if n_epochs > 0 else 0,
                "sleep_deep_pct": deep / n_epochs * 100 if n_epochs > 0 else 0,
                "sleep_light_pct": light / n_epochs * 100 if n_epochs > 0 else 0,
                "sleep_rem_pct": rem / n_epochs * 100 if n_epochs > 0 else 0,
                "sleep_awake_pct": wake / n_epochs * 100 if n_epochs > 0 else 0,
                "n_epochs": n_epochs,
                "raw_stages": stages,
            }
        except Exception as e:
            print(f"    SleepECG stage() failed: {e}")
            return None

    def _classify_sleep_stages(self, sensor_df: pd.DataFrame, day) -> dict:
        """Classify sleep using SleepECG, fallback to rule-based."""
        prev_day = day - pd.Timedelta(days=1) if isinstance(day, pd.Timestamp) else pd.Timestamp(day) - pd.Timedelta(days=1)

        sleep_mask = (
            (sensor_df["datetime_local"].dt.date == prev_day.date())
            & (sensor_df["datetime_local"].dt.hour >= 20)
        ) | (
            (sensor_df["date"] == day)
            & (sensor_df["datetime_local"].dt.hour < 12)
        )

        sleep_df = sensor_df[sleep_mask].copy()
        if sleep_df.empty or len(sleep_df) < 300:
            return self._fallback_sleep(sleep_df)

        rr_vals = sleep_df["rr1_ms"].dropna().values
        rr_vals = rr_vals[(rr_vals > 200) & (rr_vals < 2500)]

        # Try SleepECG first
        result = self._classify_sleep_sleepecg(rr_vals)
        if result is not None:
            return result

        return self._fallback_sleep(sleep_df)

    def _fallback_sleep(self, sleep_df: pd.DataFrame) -> dict:
        """Fallback rule-based sleep staging."""
        if sleep_df.empty:
            return {"method": "fallback", "sleep_total_min": 0, "sleep_efficiency": 0,
                    "sleep_deep_pct": 0, "sleep_light_pct": 0, "sleep_rem_pct": 0,
                    "sleep_awake_pct": 100, "n_epochs": 0}

        rhr = compute_rhr(sleep_df)
        window = 300
        phases = []

        for i in range(0, len(sleep_df) - window, window):
            chunk = sleep_df.iloc[i:i + window]
            hr_valid = chunk["hr"].values
            hr_valid = hr_valid[hr_valid > 0]
            mv = chunk["movement"].values

            if len(hr_valid) < 10:
                phases.append("unknown")
                continue

            avg_hr = hr_valid.mean()
            std_hr = hr_valid.std()
            avg_mv = mv.mean()
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
            return {"method": "fallback", "sleep_total_min": 0, "sleep_efficiency": 0,
                    "sleep_deep_pct": 0, "sleep_light_pct": 0, "sleep_rem_pct": 0,
                    "sleep_awake_pct": 100, "n_epochs": 0}

        sleep_count = total - phases.count("awake") - phases.count("unknown")
        return {
            "method": "fallback",
            "sleep_total_min": sleep_count * 5,
            "sleep_efficiency": sleep_count / total * 100,
            "sleep_deep_pct": phases.count("deep") / total * 100,
            "sleep_light_pct": phases.count("light") / total * 100,
            "sleep_rem_pct": phases.count("rem") / total * 100,
            "sleep_awake_pct": phases.count("awake") / total * 100,
            "n_epochs": total,
        }

    def _compute_sleep_score(self, sleep_feats: dict) -> float:
        total_min = sleep_feats.get("sleep_total_min", 0)
        efficiency = sleep_feats.get("sleep_efficiency", 0)
        deep_pct = sleep_feats.get("sleep_deep_pct", 0)
        rem_pct = sleep_feats.get("sleep_rem_pct", 0)
        awake_pct = sleep_feats.get("sleep_awake_pct", 0)

        hours_score = min(100, (total_min / self.sleep_need_min) * 100)
        eff_score = min(100, efficiency)
        quality = deep_pct + rem_pct
        quality_score = min(100, quality * 2.5)
        stress_score = max(0, 100 - awake_pct * 5)

        return max(0, min(100, round(
            0.40 * hours_score + 0.30 * eff_score +
            0.20 * quality_score + 0.10 * stress_score, 0)))

    def _compute_strain(self, features: dict) -> float:
        zone_weights = {
            "zone1_min": 1.0, "zone2_min": 2.0, "zone3_min": 4.0,
            "zone4_min": 8.0, "zone5_min": 16.0,
        }
        raw_load = sum(features.get(z, 0) * w for z, w in zone_weights.items())
        if raw_load <= 0:
            return 0.0
        strain = 5.5 * math.log(1 + raw_load / 15.0)
        return min(21.0, max(0.0, round(strain, 1)))

    def _compute_recovery(self, hrv, rhr, sleep_score, resp_rate):
        if self._hrv_baseline is None:
            self._hrv_baseline = hrv
            self._rhr_baseline = rhr
        else:
            self._hrv_baseline = 0.1 * hrv + 0.9 * self._hrv_baseline
            self._rhr_baseline = 0.1 * rhr + 0.9 * self._rhr_baseline

        if self._hrv_baseline > 0:
            hrv_ratio = hrv / self._hrv_baseline
            hrv_score = 100 / (1 + math.exp(-4 * (hrv_ratio - 0.85)))
        else:
            hrv_score = 50.0

        rhr_diff = self._rhr_baseline - rhr
        rhr_score = max(0, min(100, 50 + rhr_diff * 8))

        resp_penalty = max(0, (resp_rate - 16) * 5) if resp_rate > 16 else 0

        recovery = 0.60 * hrv_score + 0.25 * rhr_score + 0.15 * sleep_score - resp_penalty
        return max(0, min(100, round(recovery, 0)))

    def compute(self, sensor_df: pd.DataFrame, day) -> WhoopScores:
        features = compute_daily_features(sensor_df, day, max_hr=self.max_hr)
        sleep_feats = self._classify_sleep_stages(sensor_df, day)

        hrv = features.get("hrv_rmssd", 0)
        rhr = features.get("rhr", 60)
        resp_rate = features.get("resp_rate", 14)

        sleep_score = self._compute_sleep_score(sleep_feats)
        recovery = self._compute_recovery(hrv, rhr, sleep_score, resp_rate)
        strain = self._compute_strain(features)

        method = sleep_feats.get("method", "unknown")
        print(f"    [{day}] Sleep staging: {method}, eff={sleep_feats.get('sleep_efficiency', 0):.0f}%")

        return WhoopScores(
            date=str(day),
            recovery=recovery,
            sleep=sleep_score,
            strain=strain,
            hrv_ms=round(hrv, 1),
            rhr_bpm=round(rhr, 1),
            resp_rate=round(resp_rate, 1),
        )
