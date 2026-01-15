# Algorithms

Python scoring algorithms that reproduce Whoop's daily Recovery, Sleep, and Strain scores from raw sensor data. The best algorithm (algo4_calibrated) achieves MAE 2.76 on a 0-100 scale.

## Quick Start

```bash
# Copy sensor database from ble-sync
cp ../whoop_capture.db data/raw/

# Copy ground truth from cli deep-dive export
cp -r ../ble-sync/data/whoop_backup/deep_dive/ data/whoop_ground_truth/

# Install dependencies
pip install numpy scipy scikit-learn

# Run all algorithms and generate dashboard
python analyze_all.py
# -> opens full_dashboard.html
```

## Algorithms

### algo1_custom &mdash; Rule-Based (MAE ~8.3)

Simple rule-based approach using HR zones and EPOC (excess post-exercise oxygen consumption). Maps HR data to strain via zone thresholds, estimates recovery from HRV and sleep duration.

### algo2_sleepecg &mdash; SleepECG ML (MAE ~8.3)

Uses the SleepECG library for ML-based sleep staging. Falls back to rule-based scoring when the ML model can't process the data.

### algo3_ml &mdash; Gradient Boosting (MAE ~1.7)

Gradient boosting regression with leave-one-out cross-validation. Low MAE but effectively memorizes the training data &mdash; not generalizable.

### algo4_calibrated &mdash; Whoop-Calibrated (MAE 2.76)

The best algorithm. Uses formulas calibrated to match Whoop's official scoring, optimized with differential evolution (35K iterations). Computes:

- **Recovery** from HRV (RMSSD), RHR, respiratory rate, sleep performance
- **Sleep** from duration vs. need, efficiency, consistency, stages
- **Strain** from HR zones, EPOC, activity duration

### algo5_ml &mdash; Sleep Phase Classifier (74.7% accuracy)

Per-2-minute sleep phase classification (awake/light/deep/REM) using:
- 84 features per window (HR, HRV, movement, spectral, temporal, sequential)
- HistGradientBoostingClassifier
- Viterbi post-processing for temporal smoothness

Evaluated with leave-one-night-out cross-validation.

| Phase | Recall |
|-------|--------|
| Deep | 83.7% |
| Light | 78.3% |
| REM | 70.1% |
| Awake | 16.4% |

Main weakness: awake periods during sleep look similar to light sleep in HR/HRV features.

## Evaluation

```bash
# Leave-one-night-out evaluation (algo5 sleep phases)
python eval_lono.py

# Quick 4-fold cross-validation
python eval_lono.py --quick

# Optimize algo4 daily scores
python optimize_algo4.py
```

## Data Requirements

- `data/raw/whoop_capture.db` &mdash; Sensor database from ble-sync (1-second HR, SpO2, accelerometer, gyroscope)
- `data/whoop_ground_truth/` &mdash; Deep-dive JSON files from cli (per-day sleep stages, recovery scores, strain scores)

Both directories are gitignored since they contain personal health data.

## Structure

```
algorithms/
  analyze_all.py           # Main dashboard generator
  eval_lono.py             # Sleep phase evaluation (LONO + k-fold)
  optimize_algo4.py        # Differential evolution optimizer
  algo1_custom/engine.py   # Rule-based scoring
  algo2_sleepecg/engine.py # SleepECG-based scoring
  algo3_ml/engine.py       # Gradient boosting scoring
  algo4_calibrated/engine.py # Whoop-calibrated scoring (MAE 2.76)
  algo5_ml/
    engine.py              # Sleep phase classifier
    features.py            # 84-feature extraction
    train.py               # Model training
  common/
    preprocessing.py       # HRV, RHR, respiratory rate computation
    metrics.py             # Evaluation metrics
  data/
    db_loader.py           # SQLite sensor data loader
    loader.py              # Ground truth loader
```
