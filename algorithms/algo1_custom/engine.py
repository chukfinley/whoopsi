"""Algorithm 1: Custom rule-based Whoop metric replication.

Based on sports physiology and the existing analysis in tools/build_dashboard.py.
Uses EPOC-inspired strain model and HRV/RHR-based recovery scoring.
"""

import math
import numpy as np
import pandas as pd

from common.metrics import BaseAlgorithm, WhoopScores
from common.preprocessing import (
    compute_rhr, compute_hrv_rmssd, compute_hr_zones,
    compute_sleep_features, compute_daily_features, compute_respiratory_rate,
)


class CustomAlgorithm(BaseAlgorithm):
    name = "custom_rule_based"

    def __init__(self, max_hr: int = 200, sleep_need_min: int = 480):
        self.max_hr = max_hr
        self.sleep_need_min = sleep_need_min  # 8 hours default
        # Personal baselines (will be updated with running averages)
        self._hrv_baseline = None
        self._rhr_baseline = None

    def _compute_strain(self, features: dict) -> float:
        """Compute strain 0-21 using EPOC-inspired HR zone accumulation.

        Whoop strain uses a logarithmic scale based on accumulated cardiovascular load.
        Zone weights increase exponentially for higher zones.
        """
        zone_weights = {
            "zone1_min": 1.0,
            "zone2_min": 2.0,
            "zone3_min": 4.0,
            "zone4_min": 8.0,
            "zone5_min": 16.0,
        }

        raw_load = sum(features.get(z, 0) * w for z, w in zone_weights.items())

        # Whoop strain is logarithmic: strain = k * ln(1 + load/c)
        # Calibrated so that ~60 min Zone 4-5 ≈ strain 15, full rest day ≈ 3-5
        if raw_load <= 0:
            return 0.0

        strain = 5.5 * math.log(1 + raw_load / 15.0)
        return min(21.0, max(0.0, round(strain, 1)))

    def _compute_recovery(self, hrv: float, rhr: float, sleep_score: float,
                          resp_rate: float) -> float:
        """Compute recovery 0-100% based on HRV, RHR, sleep quality.

        Whoop recovery is primarily driven by:
        - HRV relative to personal baseline (60% weight)
        - RHR relative to personal baseline (25% weight)
        - Sleep performance (15% weight)
        """
        # Update baselines with exponential moving average
        if self._hrv_baseline is None:
            self._hrv_baseline = hrv
            self._rhr_baseline = rhr
        else:
            alpha = 0.1  # slow adaptation
            self._hrv_baseline = alpha * hrv + (1 - alpha) * self._hrv_baseline
            self._rhr_baseline = alpha * rhr + (1 - alpha) * self._rhr_baseline

        # HRV score: compare to baseline using sigmoid
        if self._hrv_baseline > 0:
            hrv_ratio = hrv / self._hrv_baseline
            # Sigmoid centered at 1.0 (baseline), scaled 0-100
            hrv_score = 100 / (1 + math.exp(-4 * (hrv_ratio - 0.85)))
        else:
            hrv_score = 50.0

        # RHR score: lower is better, compare to baseline
        if self._rhr_baseline > 0:
            rhr_diff = self._rhr_baseline - rhr  # positive = better
            rhr_score = 50 + rhr_diff * 8  # ±6 BPM maps to ~0-100
        else:
            rhr_score = max(0, 100 - (rhr - 40) * 3)

        rhr_score = max(0, min(100, rhr_score))

        # Respiratory rate penalty (elevated resp rate = poor recovery)
        resp_penalty = max(0, (resp_rate - 16) * 5) if resp_rate > 16 else 0

        # Weighted combination
        recovery = (0.60 * hrv_score + 0.25 * rhr_score + 0.15 * sleep_score
                    - resp_penalty)
        return max(0, min(100, round(recovery, 0)))

    def _compute_sleep_score(self, sleep_feats: dict) -> float:
        """Compute sleep performance 0-100%.

        Based on:
        - Hours vs needed (40%)
        - Sleep efficiency (30%)
        - Deep + REM percentage (20%)
        - Low sleep stress / awake time (10%)
        """
        if sleep_feats.get("sleep_samples", 0) == 0:
            return 50.0  # no data, neutral

        total_min = sleep_feats.get("sleep_total_min", 0)
        efficiency = sleep_feats.get("sleep_efficiency", 0)
        deep_pct = sleep_feats.get("sleep_deep_pct", 0)
        rem_pct = sleep_feats.get("sleep_rem_pct", 0)
        awake_pct = sleep_feats.get("sleep_awake_pct", 0)

        # Hours vs needed
        hours_score = min(100, (total_min / self.sleep_need_min) * 100)

        # Efficiency
        eff_score = min(100, efficiency)

        # Quality (deep + REM should be ~40-50% of sleep)
        quality = deep_pct + rem_pct
        quality_score = min(100, quality * 2.5)  # 40% deep+rem = 100

        # Stress (low awake = good)
        stress_score = max(0, 100 - awake_pct * 5)

        sleep_score = (0.40 * hours_score + 0.30 * eff_score +
                       0.20 * quality_score + 0.10 * stress_score)
        return max(0, min(100, round(sleep_score, 0)))

    def compute(self, sensor_df: pd.DataFrame, day) -> WhoopScores:
        features = compute_daily_features(sensor_df, day, max_hr=self.max_hr)
        sleep_feats = compute_sleep_features(sensor_df, day)

        hrv = features.get("hrv_rmssd", 0)
        rhr = features.get("rhr", 60)
        resp_rate = features.get("resp_rate", 14)

        sleep_score = self._compute_sleep_score(sleep_feats)
        recovery = self._compute_recovery(hrv, rhr, sleep_score, resp_rate)
        strain = self._compute_strain(features)

        return WhoopScores(
            date=str(day),
            recovery=recovery,
            sleep=sleep_score,
            strain=strain,
            hrv_ms=round(hrv, 1),
            rhr_bpm=round(rhr, 1),
            resp_rate=round(resp_rate, 1),
        )
