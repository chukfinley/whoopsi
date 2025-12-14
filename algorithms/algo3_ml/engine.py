"""Algorithm 3: ML self-improving (inspired by sleep_classifiers / Neurobit paper).

Uses feature extraction from HR + accelerometer data, then trains
Gradient Boosting regressors on the available ground truth to predict
Recovery, Sleep, and Strain scores.

Self-improving: uses leave-one-out cross-validation and retrains when
new data is added.
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path

from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from common.metrics import BaseAlgorithm, WhoopScores
from common.preprocessing import (
    compute_daily_features, compute_sleep_features,
    compute_hrv_rmssd, compute_rhr, compute_respiratory_rate,
)

MODEL_DIR = Path(__file__).resolve().parent / "saved_models"


class MLAlgorithm(BaseAlgorithm):
    name = "ml_self_improving"

    def __init__(self, max_hr: int = 200):
        self.max_hr = max_hr
        self.models = {}  # target -> trained pipeline
        self.feature_names = []
        self._is_trained = False

    def _build_feature_vector(self, sensor_df: pd.DataFrame, day) -> dict:
        """Build complete feature vector for one day."""
        daily = compute_daily_features(sensor_df, day, max_hr=self.max_hr)
        sleep = compute_sleep_features(sensor_df, day)
        features = {**daily, **sleep}
        # Remove non-numeric keys
        features.pop("date", None)
        return features

    def train(self, sensor_df: pd.DataFrame, gt_df: pd.DataFrame):
        """Train models on available ground truth data."""
        if gt_df.empty:
            print("    No ground truth data for training")
            return

        # Build feature matrix
        feature_rows = []
        targets = {"recovery_score": [], "sleep_score": [], "strain_score": []}
        valid_dates = []

        for _, row in gt_df.iterrows():
            date_str = row["date"]
            day = pd.Timestamp(date_str).date()

            day_data = sensor_df[sensor_df["date"] == day]
            if day_data.empty:
                continue

            feats = self._build_feature_vector(sensor_df, day)
            if not feats or feats.get("n_samples", 0) == 0:
                continue

            # Check we have all targets
            has_all = all(pd.notna(row.get(t, np.nan)) for t in targets)
            if not has_all:
                continue

            feature_rows.append(feats)
            for t in targets:
                targets[t].append(float(row[t]))
            valid_dates.append(date_str)

        if len(feature_rows) < 3:
            print(f"    Only {len(feature_rows)} valid days, need >= 3 for ML training")
            self._is_trained = False
            return

        # Create feature DataFrame
        feat_df = pd.DataFrame(feature_rows)
        # Keep only numeric columns
        numeric_cols = feat_df.select_dtypes(include=[np.number]).columns.tolist()
        feat_df = feat_df[numeric_cols].fillna(0)
        self.feature_names = numeric_cols

        X = feat_df.values
        print(f"    Training on {len(X)} days, {len(numeric_cols)} features")

        # Train a model for each target
        for target_name, y_vals in targets.items():
            y = np.array(y_vals)

            pipeline = Pipeline([
                ("scaler", StandardScaler()),
                ("model", GradientBoostingRegressor(
                    n_estimators=100,
                    max_depth=3,
                    learning_rate=0.1,
                    min_samples_leaf=2,
                    random_state=42,
                )),
            ])

            # Leave-one-out cross-validation for evaluation
            if len(X) >= 4:
                loo = LeaveOneOut()
                y_pred_cv = cross_val_predict(pipeline, X, y, cv=loo)
                mae = np.mean(np.abs(y - y_pred_cv))
                corr = np.corrcoef(y, y_pred_cv)[0, 1] if len(y) > 2 else 0
                print(f"    {target_name}: LOO-CV MAE={mae:.1f}, r={corr:.2f}")

            # Train on all data
            pipeline.fit(X, y)
            self.models[target_name] = pipeline

        self._is_trained = True

        # Save feature importances
        for target_name, pipeline in self.models.items():
            model = pipeline.named_steps["model"]
            importances = model.feature_importances_
            top_idx = np.argsort(importances)[-5:][::-1]
            top_feats = [(self.feature_names[i], importances[i]) for i in top_idx]
            print(f"    {target_name} top features: "
                  + ", ".join(f"{name}={imp:.3f}" for name, imp in top_feats))

    def compute(self, sensor_df: pd.DataFrame, day) -> WhoopScores:
        feats = self._build_feature_vector(sensor_df, day)
        hrv = feats.get("hrv_rmssd", 0)
        rhr = feats.get("rhr", 60)
        resp = feats.get("resp_rate", 14)

        if not self._is_trained or not self.models:
            # Fallback to simple heuristics
            return WhoopScores(
                date=str(day),
                recovery=50.0,
                sleep=50.0,
                strain=8.0,
                hrv_ms=round(hrv, 1),
                rhr_bpm=round(rhr, 1),
                resp_rate=round(resp, 1),
            )

        # Build feature vector matching training features
        feat_dict = {name: feats.get(name, 0) for name in self.feature_names}
        X = np.array([[feat_dict[name] for name in self.feature_names]])

        recovery = float(self.models["recovery_score"].predict(X)[0])
        sleep = float(self.models["sleep_score"].predict(X)[0])
        strain = float(self.models["strain_score"].predict(X)[0])

        return WhoopScores(
            date=str(day),
            recovery=max(0, min(100, round(recovery, 0))),
            sleep=max(0, min(100, round(sleep, 0))),
            strain=max(0, min(21, round(strain, 1))),
            hrv_ms=round(hrv, 1),
            rhr_bpm=round(rhr, 1),
            resp_rate=round(resp, 1),
        )
