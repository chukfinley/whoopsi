"""Algorithm 5: ML-Based Scoring Engine (v4).

HistGradientBoosting trained on 84 features per 2-min window for sleep phase
classification with class balancing, Viterbi post-processing with learned
transition probabilities, plus daily score regressors and stress from LF/HF.

v4 improvements over v3:
- 84 features (was 61): HR dynamics (MASD, skewness, kurtosis, entropy),
  RR features (CV, entropy, pNN20), movement (active fraction, burst count,
  accelerometer magnitude), 10-window rolling stats, sleep architecture
  (cycle number, cumulative sleep hours, awake proxy)
- Viterbi post-processing with transition matrix learned from training data
- Replaced smooth_isolated + architecture rules with principled HMM decoding
- LONO accuracy: 74.7% (was 68%), 4-fold: 76.2% (was 71%)
"""

import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from datetime import timedelta, date as date_cls
from collections import Counter

from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    GradientBoostingRegressor,
)
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from common.metrics import BaseAlgorithm, WhoopScores
from common.preprocessing import (
    compute_rhr,
    compute_hrv_rmssd,
    compute_hrv_sdnn,
    compute_pnn50,
    compute_respiratory_rate,
    compute_daily_features,
    compute_sleep_features,
)
from algo5_ml.features import (
    extract_window_features,
    compute_lf_hf_ratio,
    FEATURE_NAMES,
    PHASE_TO_INT,
    INT_TO_PHASE,
    WINDOW_SEC,
)

MODEL_DIR = Path(__file__).resolve().parent / "models"

# Window duration in minutes (for summary calculations)
WIN_MIN = WINDOW_SEC / 60.0  # 2 min


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


def _smooth_predictions(predictions, kernel_size=3):
    """Apply median filter to remove impossible sleep stage transitions."""
    if len(predictions) < kernel_size:
        return predictions

    smoothed = predictions.copy()
    half = kernel_size // 2
    for i in range(half, len(predictions) - half):
        window = predictions[i - half : i + half + 1]
        counts = Counter(window)
        smoothed[i] = counts.most_common(1)[0][0]
    return smoothed


def _smooth_isolated(predictions):
    """Smooth only truly isolated single-window phases (1 window surrounded by same phase).

    Minimal intervention — only fixes obvious 2-min glitches where a single
    window disagrees with both neighbors and those neighbors agree.
    """
    if len(predictions) < 3:
        return predictions
    result = predictions.copy()
    for i in range(1, len(result) - 1):
        if result[i] != result[i - 1] and result[i - 1] == result[i + 1]:
            result[i] = result[i - 1]
    return result


def _apply_sleep_architecture(predictions, hours_since_onset_list):
    """Apply sleep architecture constraints to predictions.

    Rules:
    1. REM rarely occurs in first 60 min of sleep -> reclassify as light
    2. Isolated single-window phases surrounded by different: smooth to neighbors
    3. Minimum 2-window (2 min) duration for any phase
    """
    if len(predictions) < 3:
        return predictions

    result = predictions.copy()

    # Rule 1: Suppress REM in first 60 minutes
    for i, pred in enumerate(result):
        if pred == PHASE_TO_INT["rem"] and i < len(hours_since_onset_list):
            if hours_since_onset_list[i] < 1.0:  # first 60 min
                result[i] = PHASE_TO_INT["light"]

    # Rule 2: Isolated single-window phases -> match neighbors
    for i in range(1, len(result) - 1):
        if result[i] != result[i - 1] and result[i] != result[i + 1]:
            # This window is isolated. Use neighbor consensus.
            if result[i - 1] == result[i + 1]:
                result[i] = result[i - 1]

    # Rule 3: Short awake bursts (< 3 windows = < 6 min) surrounded by sleep -> light
    # This catches brief awakenings that are likely arousals
    i = 0
    while i < len(result):
        if result[i] == PHASE_TO_INT["awake"]:
            j = i
            while j < len(result) and result[j] == PHASE_TO_INT["awake"]:
                j += 1
            awake_len = j - i
            if awake_len <= 2:  # <= 4 min of awake
                # Check if surrounded by sleep
                before_sleep = i > 0 and result[i - 1] != PHASE_TO_INT["awake"]
                after_sleep = j < len(result) and result[j] != PHASE_TO_INT["awake"]
                if before_sleep and after_sleep:
                    for k in range(i, j):
                        result[k] = PHASE_TO_INT["light"]
            i = j
        else:
            i += 1

    return result


class MLScoringEngine(BaseAlgorithm):
    """ML-based sleep phase classifier + daily score predictor + stress."""

    name = "algo5_ml"

    def __init__(self, max_hr=200):
        self.max_hr = max_hr
        self.phase_model = None
        self.score_models = {}
        self._is_trained = False
        self.score_feature_names = []
        self.log_trans = None  # Learned transition matrix (log)
        self.log_init = None  # Learned initial probs (log)

    def train_phase_model(self, X, y, night_ids=None):
        """Train the sleep phase HistGradientBoostingClassifier.

        Uses class_weight="balanced" for handling imbalance.
        If night_ids is provided, learns transition matrix from labels.
        """
        model = HistGradientBoostingClassifier(
            max_iter=300,
            max_depth=3,
            learning_rate=0.1,
            min_samples_leaf=5,
            l2_regularization=0.01,
            max_bins=128,
            max_features=0.8,
            class_weight="balanced",
            random_state=42,
        )
        model.fit(X, y)
        self.phase_model = model
        self._is_trained = True

        # Learn transition matrix from training labels
        if night_ids is not None:
            K = 4
            # Transition counts with Laplace smoothing
            counts = np.ones((K, K)) * 0.1
            unique_nights = sorted(set(night_ids))
            for night in unique_nights:
                mask = night_ids == night
                night_labels = y[mask]
                for i in range(len(night_labels) - 1):
                    counts[night_labels[i], night_labels[i + 1]] += 1
            trans = counts / counts.sum(axis=1, keepdims=True)
            self.log_trans = np.log(np.clip(trans, 1e-10, 1.0))

            # Initial state probs
            init_counts = np.ones(K) * 0.1
            for night in unique_nights:
                mask = night_ids == night
                night_labels = y[mask]
                if len(night_labels) > 0:
                    init_counts[night_labels[0]] += 1
            init_probs = init_counts / init_counts.sum()
            self.log_init = np.log(np.clip(init_probs, 1e-10, 1.0))

    def predict_phases(self, sleep_df, rhr, sleep_start_ts=None, sleep_end_ts=None):
        """Predict sleep phases using ML classifier.

        Uses timestamp-aligned 2-min windows for consistent grid matching
        with Whoop's ground truth timeline. Pure ML predictions with
        minimal post-processing (isolated-window smoothing only).

        Returns list of {"time": "HH:MM", "phase": str, "hr": float} dicts.
        """
        if self.phase_model is None:
            return []

        # Build timestamp index for fast lookup
        ts_col = sleep_df["timestamp"].values
        if len(ts_col) == 0:
            return []

        # Use aligned 2-min windows starting from sleep_start_ts
        # This ensures our time grid matches Whoop's exactly
        if sleep_start_ts is None:
            sleep_start_ts = float(ts_col[0])
        if sleep_end_ts is None:
            sleep_end_ts = float(ts_col[-1])

        preds = []
        meta = []
        all_vecs = []
        prev_feats = None
        history = []

        t = int(sleep_start_ts)
        t_end = int(sleep_end_ts)

        while t + WINDOW_SEC <= t_end:
            # Select sensor records within this 2-min window
            mask = (ts_col >= t) & (ts_col < t + WINDOW_SEC)
            chunk = sleep_df[mask]

            # Need at least 30 seconds of data in the window
            if len(chunk) < 30:
                # No data — emit light placeholder (most common phase)
                from datetime import datetime

                time_str = datetime.fromtimestamp(t).strftime("%H:%M")
                preds.append(PHASE_TO_INT["light"])
                meta.append(
                    {"time": time_str, "hr": 0, "movement": 0, "has_features": False}
                )
                t += WINDOW_SEC
                prev_feats = None
                continue

            feats = extract_window_features(
                chunk,
                rhr,
                sleep_start_ts=sleep_start_ts,
                sleep_end_ts=sleep_end_ts,
                prev_features=prev_feats,
                history=history,
            )
            if feats is None:
                from datetime import datetime

                time_str = datetime.fromtimestamp(t).strftime("%H:%M")
                preds.append(PHASE_TO_INT["light"])
                meta.append(
                    {"time": time_str, "hr": 0, "movement": 0, "has_features": False}
                )
                t += WINDOW_SEC
                prev_feats = None
                continue

            vec = np.array([[feats.get(name, 0.0) for name in FEATURE_NAMES]])
            all_vecs.append(vec[0])

            hr_v = chunk["hr"].values
            hr_v = hr_v[hr_v > 30]
            from datetime import datetime

            time_str = datetime.fromtimestamp(t).strftime("%H:%M")
            # Placeholder prediction — will be overwritten by Viterbi or raw predict
            preds.append(PHASE_TO_INT["light"])
            meta.append(
                {
                    "time": time_str,
                    "hr": round(float(np.median(hr_v)), 1) if len(hr_v) > 0 else 0,
                    "movement": round(float(chunk["movement"].mean()), 3),
                    "has_features": True,
                }
            )
            prev_feats = feats
            history.append(feats)
            t += WINDOW_SEC

        # Apply Viterbi if transition matrix is available, else smooth_isolated
        if self.log_trans is not None and self.log_init is not None and all_vecs:
            # Get probabilities for windows that have features
            feat_indices = [i for i, m in enumerate(meta) if m.get("has_features")]
            if feat_indices:
                X_all = np.array(all_vecs)
                proba = self.phase_model.predict_proba(X_all)
                log_probs = np.log(np.clip(proba, 1e-10, 1.0))
                viterbi_path = viterbi_decode(log_probs, self.log_trans, self.log_init)

                # Fill predictions: Viterbi for feature windows, placeholder for gaps
                vi = 0
                for i in range(len(meta)):
                    if meta[i].get("has_features"):
                        preds[i] = int(viterbi_path[vi])
                        vi += 1
        else:
            # Fallback: raw predictions with smoothing
            feat_indices = [i for i, m in enumerate(meta) if m.get("has_features")]
            if feat_indices and all_vecs:
                X_all = np.array(all_vecs)
                raw_preds = self.phase_model.predict(X_all)
                vi = 0
                for i in range(len(meta)):
                    if meta[i].get("has_features"):
                        preds[i] = int(raw_preds[vi])
                        vi += 1
            preds = _smooth_isolated(preds)

        phases = []
        for pred, m in zip(preds, meta):
            phases.append(
                {
                    "time": m["time"],
                    "phase": INT_TO_PHASE.get(pred, "light"),
                    "hr": m.get("hr", 0),
                    "movement": m.get("movement", 0),
                }
            )

        return phases

    def compute_stress(self, sleep_df, window_sec=300):
        """Compute per-window stress level from LF/HF ratio."""
        results = []
        for i in range(0, len(sleep_df) - window_sec, window_sec):
            chunk = sleep_df.iloc[i : i + window_sec]
            rr = chunk["rr1_ms"].dropna().values
            rr = rr[(rr > 200) & (rr < 2500)]

            t = chunk["datetime_local"].iloc[0]
            time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)

            lf_hf = compute_lf_hf_ratio(rr)

            if lf_hf < 1.5:
                level = "LOW"
            elif lf_hf < 3.0:
                level = "MEDIUM"
            else:
                level = "HIGH"

            results.append(
                {
                    "time": time_str,
                    "stress": round(lf_hf, 2),
                    "level": level,
                }
            )
        return results

    def estimate_vo2max(self, day_df, rhr, max_hr=200):
        """Estimate VO2max from HR/activity relationship."""
        hr = day_df["hr"].values
        valid_hr = hr[hr > 30]
        if len(valid_hr) < 100 or rhr < 30:
            return None

        observed_max_hr = float(np.percentile(valid_hr, 99))
        vo2max = 15.3 * (observed_max_hr / rhr)

        hr_reserve = observed_max_hr - rhr
        if hr_reserve < 40:
            vo2max *= 0.85

        return round(min(70, max(20, vo2max)), 1)

    def train_score_models(self, sensor_df, gt_df):
        """Train daily score regressors (recovery, sleep, strain)."""
        if gt_df.empty:
            return

        feature_rows = []
        targets = {"recovery": [], "sleep": [], "strain": []}
        valid_dates = []

        for _, row in gt_df.iterrows():
            date_str = row["date"]
            day = pd.Timestamp(date_str).date()

            day_data = sensor_df[sensor_df["date"] == day]
            if day_data.empty:
                continue

            daily = compute_daily_features(sensor_df, day, max_hr=self.max_hr)
            sleep = compute_sleep_features(sensor_df, day)
            feats = {**daily, **sleep}
            feats.pop("date", None)

            rec = row.get("recovery_score")
            slp = row.get("sleep_score")
            strn = row.get("strain_score", row.get("cycle_strain"))
            if pd.isna(rec) or pd.isna(slp) or pd.isna(strn):
                continue

            feature_rows.append(feats)
            targets["recovery"].append(float(rec))
            targets["sleep"].append(float(slp))
            targets["strain"].append(float(strn))
            valid_dates.append(date_str)

        if len(feature_rows) < 3:
            return

        feat_df = pd.DataFrame(feature_rows)
        numeric_cols = feat_df.select_dtypes(include=[np.number]).columns.tolist()
        feat_df = feat_df[numeric_cols].fillna(0)
        self.score_feature_names = numeric_cols
        X = feat_df.values

        for target_name, y_vals in targets.items():
            y = np.array(y_vals)
            pipeline = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    (
                        "model",
                        GradientBoostingRegressor(
                            n_estimators=100,
                            max_depth=3,
                            learning_rate=0.1,
                            min_samples_leaf=2,
                            random_state=42,
                        ),
                    ),
                ]
            )
            pipeline.fit(X, y)
            self.score_models[target_name] = pipeline

    def _predict_daily_scores(self, sensor_df, day):
        """Predict recovery, sleep, strain from daily features."""
        if not self.score_models:
            return None, None, None

        daily = compute_daily_features(sensor_df, day, max_hr=self.max_hr)
        sleep = compute_sleep_features(sensor_df, day)
        feats = {**daily, **sleep}
        feats.pop("date", None)

        vec = {name: feats.get(name, 0) for name in self.score_feature_names}
        X = np.array([[vec[name] for name in self.score_feature_names]])
        # Replace NaN with 0 to avoid crashes on days with missing data
        X = np.nan_to_num(X, nan=0.0)

        rec = (
            float(self.score_models["recovery"].predict(X)[0])
            if "recovery" in self.score_models
            else None
        )
        slp = (
            float(self.score_models["sleep"].predict(X)[0])
            if "sleep" in self.score_models
            else None
        )
        strn = (
            float(self.score_models["strain"].predict(X)[0])
            if "strain" in self.score_models
            else None
        )
        return rec, slp, strn

    def save(self, path=None):
        """Save trained models to disk."""
        path = path or MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)
        if self.phase_model:
            joblib.dump(self.phase_model, path / "phase_model.joblib")
        if self.log_trans is not None:
            joblib.dump(self.log_trans, path / "log_trans.joblib")
        if self.log_init is not None:
            joblib.dump(self.log_init, path / "log_init.joblib")
        for name, model in self.score_models.items():
            joblib.dump(model, path / f"score_{name}.joblib")
        if self.score_feature_names:
            joblib.dump(self.score_feature_names, path / "score_feature_names.joblib")

    def load(self, path=None):
        """Load trained models from disk."""
        path = path or MODEL_DIR
        phase_path = path / "phase_model.joblib"
        if phase_path.exists():
            self.phase_model = joblib.load(phase_path)
            self._is_trained = True
        trans_path = path / "log_trans.joblib"
        if trans_path.exists():
            self.log_trans = joblib.load(trans_path)
        init_path = path / "log_init.joblib"
        if init_path.exists():
            self.log_init = joblib.load(init_path)
        for name in ["recovery", "sleep", "strain"]:
            score_path = path / f"score_{name}.joblib"
            if score_path.exists():
                self.score_models[name] = joblib.load(score_path)
        names_path = path / "score_feature_names.joblib"
        if names_path.exists():
            self.score_feature_names = joblib.load(names_path)

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

    def compute(self, sensor_df, day) -> WhoopScores:
        """Compute all Whoop scores for a single day."""
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

        rec, slp, strn = self._predict_daily_scores(sensor_df, day)

        return WhoopScores(
            date=str(day),
            recovery=max(0, min(100, round(rec))) if rec is not None else 50,
            sleep=max(0, min(100, round(slp))) if slp is not None else 50,
            strain=max(0, min(21, round(strn, 1))) if strn is not None else 0,
            hrv_ms=round(hrv, 1),
            rhr_bpm=round(rhr, 1),
            resp_rate=round(resp, 1),
        )

    def classify_sleep(self, sleep_df, rhr, sleep_start_ts=None, sleep_end_ts=None):
        """Classify sleep phases and return (phases_list, summary_dict)."""
        phases = self.predict_phases(sleep_df, rhr, sleep_start_ts, sleep_end_ts)

        total = len(phases)
        if total == 0:
            return phases, {
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

        counts = Counter(p["phase"] for p in phases)
        sleep_count = total - counts["awake"] - counts.get("unknown", 0)

        summary = {
            "total_min": round(total * WIN_MIN, 1),
            "sleep_min": round(sleep_count * WIN_MIN, 1),
            "efficiency": round(sleep_count / total * 100, 1) if total > 0 else 0,
            "deep_min": round(counts["deep"] * WIN_MIN, 1),
            "light_min": round(counts["light"] * WIN_MIN, 1),
            "rem_min": round(counts["rem"] * WIN_MIN, 1),
            "awake_min": round(counts["awake"] * WIN_MIN, 1),
            "deep_pct": round(counts["deep"] / total * 100, 1),
            "light_pct": round(counts["light"] / total * 100, 1),
            "rem_pct": round(counts["rem"] / total * 100, 1),
            "awake_pct": round(counts["awake"] / total * 100, 1),
        }
        return phases, summary

    def full_analysis(self, sensor_df, day):
        """Full analysis including stress and VO2max."""
        sleep_df = self._get_sleep_window(sensor_df, day)
        day_df = sensor_df[sensor_df["date"] == day]

        if day_df.empty:
            return {"date": str(day)}

        rhr = compute_rhr(sleep_df) if not sleep_df.empty else compute_rhr(day_df)
        hrv = compute_hrv_rmssd(sleep_df, method="sws") if not sleep_df.empty else 0
        resp = (
            compute_respiratory_rate(sleep_df)
            if not sleep_df.empty and len(sleep_df) > 60
            else 14.0
        )

        from algo5_ml.features import extract_whoop_timeline

        _, start_ts, end_ts = extract_whoop_timeline(str(day))
        phases, summary = self.classify_sleep(sleep_df, rhr, start_ts, end_ts)

        rec, slp, strn = self._predict_daily_scores(sensor_df, day)

        stress = self.compute_stress(sleep_df) if not sleep_df.empty else []
        stress_avg = float(np.mean([s["stress"] for s in stress])) if stress else 0
        stress_high_pct = (
            sum(1 for s in stress if s["level"] == "HIGH") / max(len(stress), 1) * 100
        )

        vo2max = self.estimate_vo2max(day_df, rhr)

        return {
            "date": str(day),
            "recovery": max(0, min(100, round(rec))) if rec is not None else 50,
            "sleep_score": max(0, min(100, round(slp))) if slp is not None else 50,
            "strain": max(0, min(21, round(strn, 1))) if strn is not None else 0,
            "hrv_ms": round(hrv, 1),
            "rhr_bpm": round(rhr, 1),
            "resp_rate": round(resp, 1),
            "sleep_phases": phases,
            "sleep_summary": summary,
            "stress_timeline": stress,
            "stress_avg": round(stress_avg, 2),
            "stress_high_pct": round(stress_high_pct, 1),
            "vo2max": vo2max,
        }
