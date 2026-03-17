# Algorithms — Detailed Documentation

**Status: ACTIVE (Mar 2026)** — ML model trained on Whoop labels, MAE 8.0 (best of 6 algorithms).

---

## Quick Start — Trained ML Model

```bash
cd algorithms
source .venv/bin/activate

# Re-train model (needs sensor DB + Whoop deep dive labels)
python3 train_whoop_model.py

# Compare all 6 algorithms (generates algo_compare.html)
python3 algo_compare.py

# Serve dashboard
python3 -m http.server 8080
# → http://localhost:8080/algo_compare.html
```

### Adding More Training Data

1. Sync raw sensor data from Whoop strap via BLE companion app
2. Pull Whoop cloud labels: `whoop login --email you@email.com && whoop deep-dive --date all`
3. Re-run `python3 train_whoop_model.py` — model improves with more nights

### Key Files (New — Mar 2026)

| File | Purpose |
|------|---------|
| `train_whoop_model.py` | Train ML model on Whoop labels (HistGBT, 41 features, LONO-CV) |
| `whoop_model.joblib` | Trained sleep staging model (4-class: awake/light/deep/rem) |
| `whoop_score_models.joblib` | Recovery + Sleep Score regressors |
| `algo_compare.py` | Compare 6 algorithms, generate algo_compare.html |
| `dashboard_yasa.py` | Whoop vs YASA focused dashboard (older) |
| `algo8_yasa/engine.py` | YASA-inspired HRV spectral sleep staging |
| `algo9_neurokit/engine.py` | NeuroKit2 nonlinear HRV features |
| `algo10_sleepecg_full/engine.py` | Full SleepECG + cycle detection |

### Algorithm Comparison (16 nights, Mar 2026)

| Algorithm | MAE vs Whoop | Nights Won |
|-----------|-------------|------------|
| **F: Trained ML** | **8.0** | **13/16** |
| C: HRV Nonlinear | 13.5 | 2/16 |
| B: SleepECG ML | 18.3 | 0/16 |
| D: HR Delta | 20.9 | 1/16 |
| A: HRV Spectral | 32.8 | 0/16 |
| E: YASA | 32.8 | 0/16 |

### Trained Model Features (41 total)

Top 5 by importance:
1. `roll10_rmssd_mean` (0.158) — 10-min rolling HRV average
2. `hr_above_rhr` (0.117) — HR relative to resting
3. `fraction_of_night` (0.110) — temporal position
4. `roll5_rmssd_mean` (0.104) — 5-min rolling HRV
5. `roll10_hr_mean` (0.078) — 10-min rolling HR

Sensor features: HR, RR intervals (always available), accel/gyro/SpO2 (zeros currently, ready for when data available)

### Data Requirements

- **Sensor data**: `data/raw/whoop_capture.db` — per-second HR + RR from BLE sync
- **Whoop labels**: `ble-sync/data/backup/api/deep_dive/` OR `ble-sync/data/whoop_backup/deep_dive/`
- **Whoop official scores**: `data/raw/whoop_official.json` — for Recovery/Sleep Score training
- **Overlapping nights needed**: Currently 15 (Jan 28 - Feb 13, 2026)
- **More data = better model**: Target 50+ nights for production quality

### Known Limitations

1. **Awake recall is 24%** — without accelerometer, awake during sleep looks like light sleep in HR/HRV
2. **Recovery/Sleep Score models overfit** — only 8 training nights, need 30+ for useful regression
3. **SleepECG classifier corrupted** — `~/.sleepecg/classifiers/wrn-gru-mesa.zip` needs re-download
4. **REM tends to be over-predicted** — model bias from limited training data

---

## Original Quick Start (algo1-7)

```bash
cd algorithms

# Activate venv (local)
source .venv/bin/activate

# Full analysis + dashboard
python3 analyze_all.py
# → full_dashboard.html

# Sleep phase evaluation (LONO-CV)
python3 eval_lono.py            # Full LONO-CV (~13s)
python3 eval_lono.py --quick    # 4-fold CV (~3s)

# Optimize algo5 sleep phases
python3 optimize_algo5_phases.py              # Full (Phase 1+2, ~90 min)
python3 optimize_algo5_phases.py --baseline   # Evaluate only (~5s)
python3 optimize_algo5_phases.py --phase2only # Post-processing only (~5s)

# Optimize algo4 daily scores
python3 optimize_algo4.py       # DE optimizer (~5 min, 35K iterations)
```

---

## Algorithm Overview

### algo1_custom — Rule-Based (MAE ~8.3)
Heuristic scoring using HR zones and EPOC strain model. No training data needed.

### algo2_sleepecg — SleepECG ML (MAE ~8.3)
Uses the `sleepecg` library for sleep staging from RR intervals. Falls back to rule-based if sleepecg not installed.

### algo3_ml — Gradient Boosting (MAE ~1.7)
Standard gradient boosting regressor with leave-one-out cross-validation for daily scores. Best MAE but tiny training set (overfits).

### algo4_calibrated — Whoop-Calibrated (MAE 2.76)
Reverse-engineered Whoop scoring formulas, auto-optimized via differential evolution:
- 14 tunable parameters (recovery weights, sleep coefficients, strain formula)
- Optimized via `optimize_algo4.py`: 35K iterations, MAE 10.11 → 2.76
- Recovery: hits several days exactly, others within 2-10 points
- Sleep: consistently within 1-3 points
- Strain: accurate for rest days, underestimates high-activity (log-scale issue)

### algo5_ml — ML Sleep Phase Classifier (74.7% LONO)
**Primary focus.** Full details in `agents.md`.

### algo6_sleep_android — Sleep as Android (Reverse-Engineered)
Reverse-engineered actigraphy pipeline from Sleep as Android (com.urbandroid.sleep).
Uses HR-derived movement proxy fed through the full offline hypnogram pipeline:
adaptive normalization → deep/light classification → REM detection → awake overlay.
Source: `common/sleep_algorithm.py` (Part 1, ~1500 lines).

### algo7_mihealth — Mi Health / Xiaomi Fitness (Reverse-Engineered)
Reverse-engineered phone-based sleep trace from Mi Health (com.xiaomi.fitness).
Uses HR-derived synthetic sleep logs fed through SleepAnalyzerBeta (5-min debounce)
→ stage merge → trim → report. Classifies SLEEPING vs AWAKE (no deep/light/REM from
phone sensors alone). Includes wake time prediction utility.
Source: `common/sleep_algorithm.py` (Part 2, ~475 lines).

HistGradientBoostingClassifier + Viterbi post-processing for per-2-minute sleep phase classification:
- 84 features per 2-min window
- Trained on limited nights of sensor data + Whoop deep-dive ground truth
- Viterbi decoding with learned transition matrix for temporal coherence

---

## algo5_ml — How It Works (Detailed)

### Step 1: Ground Truth Extraction
`features.py::_parse_deep_dive_sleep_bounds(date_str)`:
1. Load `deep_dive/{date}.json`
2. Parse ISO timestamps `start_time`/`end_time` from `header_section.destination.parameters`
3. Compute `total_secs = end_ts - start_ts`
4. For each zone (AWAKE, LIGHT_SLEEP, SWS_SLEEP, REM_SLEEP):
   - Map `lower_endpoint`/`upper_endpoint` fractions to absolute unix timestamps
   - Fill `second_gt[unix_second] = phase_string` for every second in the segment
5. Return `(second_gt_dict, start_ts, end_ts)`

### Step 2: Window Extraction
`features.py::build_training_data(df)`:
1. For each date with both sensor data AND deep-dive GT:
   - Slice sensor DataFrame to sleep window `[start_ts, end_ts]`
   - Slide 2-min windows (120 samples) with 1-min stride (60 samples)
   - Extract 84 features per window via `extract_window_features()`
   - Assign label via majority vote: count per-second GT labels within window boundaries
2. Returns `X` (n_windows x 84), `y` (n_windows,), `night_ids`, `night_dates`

### Step 3: Feature Extraction
`features.py::extract_window_features(chunk, rhr, ...)`:

**84 features in 12 groups:**

| Group | Count | Features | Purpose |
|-------|-------|----------|---------|
| HR basic | 11 | mean, median, std, iqr, min, max, p10, p90, above_rhr, trend, range | Core HR statistics |
| HR dynamics | 7 | masd, skewness, kurtosis, entropy, accel, cv, range_norm | REM has irregular HR, Deep has stable |
| HRV/RR | 11 | rmssd, sdnn, pnn50, pnn20, rr_mean, rr_std, rr_trend, lf_hf, hrv_available, rr_cv, rr_entropy | Parasympathetic/sympathetic balance |
| Movement | 12 | mv_mean, std, max, p90, p95, energy, zcr, active_frac, burst_count, acc_x/y_std, acc_mag_std | Awake vs sleep discrimination |
| Gyro+SpO2 | 4 | gyro_mean, gyro_std, spo2_mean, spo2_min | Rotation and oxygenation |
| Temporal | 6 | hours_since_onset, fraction_of_night, circadian_sin/cos, ultradian_sin/cos | Time context (90-min REM cycles) |
| Cross | 3 | hr_mv_interaction, hrv_hr_ratio, autonomic_balance | Feature combinations |
| Delta | 4 | delta_hr, delta_mv, delta_hrv, delta_lf_hf | Change from previous window |
| 3-win rolling | 5 | roll_hr_mean/std, roll_mv_mean/std, roll_hrv_mean | Short-term context (~6 min) |
| 10-win rolling | 6 | roll10_hr_mean/std, roll10_lf_hf_mean, roll10_mv_mean, hr_dev_from_roll/roll10 | Medium-term context (~20 min) |
| Sequence | 9 | prev1_hr/mv/hrv, prev2_hr/mv, prev3_hr, prev1_hr_delta, hr/mv_trend_5win | Window history |
| Architecture | 6 | expected_cycle_phase, sleep_cycle_number, cumulative_sleep_hrs, deep/rem/awake_proxy | Sleep structure |

**Key features (by importance):**
1. `roll10_lf_hf_mean` (0.10) — 10-window rolling LF/HF ratio, best discriminator
2. `hr_above_rhr` (0.05) — HR relative to resting, separates deep (low) from REM (high)
3. `hours_since_onset` (0.05) — Deep dominates early night, REM late
4. `fraction_of_night` (0.05) — Normalized time position

### Step 4: Model Training
`engine.py::train_phase_model(X, y, night_ids)`:
- **Model**: `HistGradientBoostingClassifier(max_iter=300, max_depth=3, lr=0.1, msl=5, l2=0.01, class_weight="balanced")`
- **Transition matrix**: Count label transitions within each night (Laplace smoothing 0.1), normalize to probabilities, take log
- **Initial state probs**: Count first label of each night, normalize, take log
- Saved artifacts: `phase_model.joblib`, `log_trans.joblib`, `log_init.joblib`

### Step 5: Prediction with Viterbi
`engine.py::predict_phases(sleep_df, rhr, sleep_start_ts, sleep_end_ts)`:
1. Build timestamp-aligned 2-min windows from `sleep_start_ts` to `sleep_end_ts`
2. Extract features for each window (skip windows with <30 seconds of data)
3. Get ML probabilities: `predict_proba(X)` → log probabilities
4. Run Viterbi decoding: find most likely state sequence given log emissions + learned transitions
5. Fallback (no transition matrix): raw predictions + `_smooth_isolated()` (fix single-window glitches)

### Step 6: Viterbi Algorithm
`engine.py::viterbi_decode(log_probs, log_trans, log_init)`:
- Standard Viterbi in log-space (avoids underflow)
- `V[t,j] = max_i(V[t-1,i] + log_trans[i,j]) + log_probs[t,j]`
- Backtracking from best final state
- Forces temporal coherence: model can't rapidly oscillate between phases

---

## Optimizer Pipeline

### optimize_algo5_phases.py (Two-Phase)

**Phase 1: ML Grid Search** (~90 min)
```python
ml_grid = {
    "max_iter": [300, 500, 800],
    "max_depth": [3, 4, 5, 6],
    "lr": [0.02, 0.05, 0.1],
    "msl": [5, 10, 15],
    "l2": [0.005, 0.01, 0.05],
    "bins": [128],
}
# = 324 combinations
```
For each config:
1. Pre-compute full LONO predictions (train on N-1 nights, predict on held-out)
2. Cache raw ML probabilities per night
3. Evaluate with default post-processing
4. Track best config

**Phase 2: Post-Processing DE** (~5 seconds)
```python
pp_bounds = [
    (1, 9),          # smooth_kernel
    (0.01, 1.0),     # awake_mv_thresh
    (1.0, 30.0),     # awake_hr_thresh
    (0.5, 50.0),     # awake_energy_thresh
    (0.05, 0.8),     # awake_zcr_thresh
    (0.1, 0.9),      # awake_prob_thresh
]
```
- Uses `scipy.optimize.differential_evolution` on cached predictions
- Objective: maximize overall LONO accuracy
- Very fast because no retraining needed

**Output**: `algo5_best_params.json` + printed code snippets for engine.py

### optimize_algo4.py (Daily Scores)
- Optimizes 14 parameters of algo4_calibrated recovery/sleep/strain formulas
- `scipy.optimize.differential_evolution`, ~35K iterations, ~5 min
- Objective: minimize MAE of recovery + sleep + strain vs Whoop ground truth
- Auto-applies to `algo4_calibrated/engine.py` and reruns `analyze_all.py`

---

## Known Data Issues

1. **`rawHex` field is NULL** in whoop_capture.db — companion app stores decoded fields only. No raw AA01 packets. Impact: can't re-decode or extract additional fields later.
2. **Movement data may be zero** if accel columns are all NULL (early sync versions). Algos 1/2/3 show sleep=100% because they rely on movement. Algo4/5 compensate.
3. **Future timestamps (2029+)** in DB from malformed BLE packets — filtered by `db_loader.py` (year > 2027).
4. **Some dates in whoop_official.json** have "--" for recovery/HRV/RHR — incomplete cycles, must validate before `float()`.
5. **`datetime_local` column** in db_loader: actually UTC+1 naive timestamps with tzinfo=UTC. Misleading name. Use `timestamp` column for reliable time comparisons.
6. **Very few awake windows** in training set (~5%) — extreme class imbalance, main cause of poor awake recall.
7. **`sleepecg` not installed** in algorithms venv — algo2 falls back to rule-based sleep staging.

---

## File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `analyze_all.py` | ~1250 | Main dashboard: runs all algos, generates full_dashboard.html |
| `eval_lono.py` | ~523 | Standalone LONO evaluation with Viterbi |
| `optimize_algo5_phases.py` | ~660 | Two-phase optimizer (grid search + DE) |
| `optimize_algo4.py` | ~300 | DE optimizer for algo4 daily score params |
| `algo5_ml/features.py` | ~903 | 84 features, per-second GT, training data builder |
| `algo5_ml/engine.py` | ~678 | MLScoringEngine: HistGBT + Viterbi + daily scores |
| `algo5_ml/train.py` | ~197 | Training script with LONO-CV evaluation |
| `algo4_calibrated/engine.py` | ~400 | Whoop-calibrated scoring (reverse-engineered) |
| `common/sleep_algorithm.py` | ~2130 | Reverse-engineered sleep algorithms (Sleep as Android + Mi Health) |
| `common/preprocessing.py` | ~300 | HRV, RHR, respiratory rate computation |
| `common/metrics.py` | ~100 | BaseAlgorithm ABC, WhoopScores dataclass |
| `algo6_sleep_android/engine.py` | ~420 | SAA wrapper: HR→movement proxy→offline hypnogram |
| `algo7_mihealth/engine.py` | ~310 | Mi Health wrapper: HR→synthetic logs→phone sleep trace |
| `data/db_loader.py` | ~150 | Loads sensor data from whoop_capture.db |
| `data/loader.py` | ~200 | Loads HAR-based data + whoop_official.json GT |

---

## Dependencies

```
numpy, pandas, scipy, scikit-learn, joblib, matplotlib (optional, for dashboard)
```

The venv at `algorithms/.venv/` should have all of these.
