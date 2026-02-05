# Agent Knowledge Base — Whoop Reverse Engineering Project

**Last updated: 2026**
**Read this file first in every new session.** Then read `CLAUDE.md` (project structure), `PLAN.md` (Flutter roadmap), and `algorithms/CLAUDE.md` (sleep phase classifier).

---

## User Preferences & Environment

- `cli` tool auto-refreshes token from `~/.whoop/token.json`.
- **NEVER call FORCE_TRIM_ALL** on the strap — preserves circular buffer history.
- **NEVER commit** private data: `token.json`, `*.har`, `whoop_backup/`, `whoop_capture.db`, `.env`

---

## Current Primary Goal (ON HOLD)

**Sleep phase classification**: Match Whoop's official per-2-minute sleep phase timeline (Awake, Light, Deep, REM) as closely as possible using raw sensor data from the Whoop 5.0 strap.

**This is NOT about Recovery/Sleep/Strain scores.** It's specifically about the per-2-minute sleep phase classification accuracy.

---

## What Has Been Built

### 1. BLE Companion App (Kotlin/Android) — DONE
- Full BLE sync with Whoop 5.0 strap
- Smart Sync (incremental) and Full Sync (rewind all)
- Decodes 0x2F sensor packets: HR (from RR intervals), SpO2, accelerometer, gyroscope
- Room DB stores 1-second-resolution sensor records
- Auto-syncs every 6h via WorkManager
- See `ble-sync/CLAUDE.md` for full BLE protocol docs

### 2. Whoop CLI (Python) — DONE
- `pip install -e ./cli`
- `whoop login`, `whoop status`, `whoop export`, `whoop deep-dive`
- Cognito auth with auto-refresh, token at `~/.whoop/token.json`
- Downloads deep-dive JSONs (per-day sleep stages, recovery, strain details)

### 3. Scoring Algorithms (Python) — DONE
- 5 algorithms (algo1-algo5), best is algo4_calibrated (MAE 2.76 for daily scores)
- `analyze_all.py` generates `full_dashboard.html` comparing all algorithms
- Optimizer: `optimize_algo4.py` (differential evolution, 35K iterations)

### 4. Sleep Phase Classifier (algo5_ml) — IN PROGRESS, ON HOLD
See detailed section below.

### 5. Flutter App (app/) — PLANNED
See `PLAN.md` for the full roadmap. Not yet started beyond initial scaffolding.

---

## Sleep Phase Classifier — Detailed State

### Architecture
```
Raw Sensor DB
    |
    v
features.py: extract_window_features() — 84 features per 2-min window
    |
    v
build_training_data() — matches windows to Whoop ground truth (per-second labels)
    |
    v
engine.py: HistGradientBoostingClassifier (max_iter=300, max_depth=3, lr=0.1)
    |
    v
Viterbi post-processing (transition matrix learned from training data)
    |
    v
Per-2-min sleep phase prediction: awake/light/deep/rem
```

### Current Accuracy
| Metric | Value |
|--------|-------|
| **4-fold CV** | **76.2%** |
| **LONO CV** | **74.7%** |
| Deep recall | 83.7% |
| REM recall | 70.1% |
| Light recall | 78.3% |
| Awake recall | 16.4% (main weakness) |

### Version Evolution
- **v1**: 5-min windows, 48 features, max_depth=2, simple smoothing → ~60% LONO
- **v2**: Hand-tuned thresholds from DE optimizer, 5-min windows → 75% (coarse metric, inflated)
- **v3**: 2-min windows, 61 features, per-second GT, sleep architecture rules → 71.8% (stricter metric)
- **v4**: 84 features, Viterbi decoding with learned transitions → 74.7% LONO, 76.2% 4-fold

### Data Requirements
- **Sensor DB**: `algorithms/data/raw/whoop_capture.db` — raw sensor records from ble-sync
- **Ground truth**: `ble-sync/data/whoop_backup/deep_dive/` — deep-dive JSON files from Whoop API
- Both sensor data AND deep-dive ground truth are needed for training/evaluation
- **Training set**: Imbalanced — awake is ~5% of windows (main challenge for classification)

### Key Files

#### `algorithms/algo5_ml/features.py` (903 lines)
- `FEATURE_NAMES` — list of all 84 feature names (defines feature vector order)
- `extract_window_features()` — extracts features from a single 2-min sensor window
- `build_training_data()` — builds X, y, night_ids, night_dates from DB + deep-dive JSONs
- `_parse_deep_dive_sleep_bounds()` — extracts per-second ground truth labels from deep-dive JSON
- `WINDOW_SEC=120`, `STRIDE_SEC=60` (2-min windows, 1-min stride)

**84 Features organized in groups:**
1. HR basic (11): mean, median, std, iqr, min, max, p10, p90, above_rhr, trend, range
2. HR dynamics (7): **masd**, **skewness**, **kurtosis**, **entropy**, **accel**, **cv**, **range_norm**
3. HRV/RR (11): rmssd, sdnn, pnn50, **pnn20**, rr_mean, rr_std, rr_trend, lf_hf, hrv_available, **rr_cv**, **rr_entropy**
4. Movement (12): mv_mean, std, max, p90, **p95**, energy, zcr, **active_frac**, **burst_count**, acc_x_std, acc_y_std, **acc_mag_std**
5. Gyro+SpO2 (4): gyro_mean, gyro_std, spo2_mean, spo2_min
6. Temporal (6): hours_since_onset, fraction_of_night, circadian_sin/cos, ultradian_sin/cos
7. Cross (3): hr_mv_interaction, hrv_hr_ratio, autonomic_balance
8. Delta (4): delta_hr, delta_mv, delta_hrv, delta_lf_hf
9. 3-window rolling (5): roll_hr_mean/std, roll_mv_mean/std, roll_hrv_mean
10. 10-window rolling (6): **roll10_hr_mean/std**, **roll10_lf_hf_mean**, **roll10_mv_mean**, **hr_dev_from_roll/roll10**
11. Sequence (9): prev1_hr/mv/hrv, prev2_hr/mv, prev3_hr, prev1_hr_delta, hr_trend_5win, mv_trend_5win
12. Architecture (6): expected_cycle_phase, **sleep_cycle_number**, **cumulative_sleep_hrs**, cumulative_deep_proxy, cumulative_rem_proxy, **cumulative_awake_proxy**

**(Bold = latest additions)**

#### `algorithms/algo5_ml/engine.py` (678 lines)
- `MLScoringEngine` class — main entry point
- `train_phase_model(X, y, night_ids=None)` — trains HistGBT + learns transition matrix
- `predict_phases()` — timestamp-aligned 2-min windows, Viterbi post-processing
- `viterbi_decode()` — Viterbi algorithm (log-space, backtracking)
- `classify_sleep()` — wraps predict_phases, returns phases + summary
- `compute()` — full daily scores (recovery, sleep, strain)
- Saves/loads: `phase_model.joblib`, `log_trans.joblib`, `log_init.joblib`, `score_*.joblib`

#### `algorithms/eval_lono.py` (523 lines)
- Standalone evaluation script for sleep phase accuracy
- `python eval_lono.py` — full LONO CV
- `python eval_lono.py --quick` — 4-fold CV (~3s)
- `python eval_lono.py --no-viterbi` — without Viterbi post-processing
- `python eval_lono.py --model-params 500 5 0.05` — custom hyperparams
- Learns transition matrix per fold from training labels
- Prints: per-night accuracy, confusion matrix, recalls, precisions, top 20 features

#### `algorithms/optimize_algo5_phases.py` (~660 lines)
- Two-phase sleep phase hyperparameter optimizer
- **Phase 1**: Grid search over ML configs (max_depth, lr, max_iter, msl, l2, bins) — 324 combos, ~90 min
  - Each config: pre-compute full LONO predictions (train on N-1 nights, predict on held-out)
  - Cache raw ML probabilities to avoid retraining during Phase 2
- **Phase 2**: Differential Evolution (scipy.optimize.differential_evolution) on 6 post-processing params:
  - `smooth_kernel`, `awake_mv_thresh`, `awake_hr_thresh`, `awake_energy_thresh`, `awake_zcr_thresh`, `awake_prob_thresh`
  - Runs on cached predictions — ~5 seconds for 200+ DE iterations
- **Usage**:
  - `python optimize_algo5_phases.py` — full Phase 1+2 (~90 min)
  - `python optimize_algo5_phases.py --baseline` — evaluate current params only (~5s)
  - `python optimize_algo5_phases.py --phase2only` — skip grid search, optimize post-processing only (~5s)
  - `python optimize_algo5_phases.py --maxiter 500` — more DE iterations
- Output: `algo5_best_params.json` + prints ready-to-paste code for engine.py
- Log: `optimize_algo5_log.txt`
- Note: v3 version uses `_apply_architecture()` (REM suppression, isolated smoothing); v4 uses Viterbi instead

#### `algorithms/analyze_all.py` (~1250 lines)
- Main dashboard generation script
- `python analyze_all.py` → `full_dashboard.html`
- Calls `a5_engine.train_phase_model(X, y, night_ids=night_ids)` to include Viterbi
- Runs all 5 algorithm variants, compares to Whoop ground truth
- Dashboard features: interactive sleep timelines (Whoop vs Ours), daily score comparison, recovery/sleep/strain charts
- Fixed: NoneType crash when `result["whoop"]` is None (days without Whoop data)
- Fixed: Dynamic `winMin` per timeline source (2 min for algo5/Whoop, 10 min for rule-based algos)

### Top Features by Importance
```
 1. roll10_lf_hf_mean         0.0997  ← 10-window rolling LF/HF ratio (NEW, #1)
 2. hr_above_rhr              0.0491
 3. hours_since_onset         0.0490
 4. fraction_of_night         0.0456
 5. roll10_hr_std             0.0230  ← 10-window rolling HR std (NEW)
 6. cumulative_deep_proxy     0.0198
 7. cumulative_rem_proxy      0.0148
 8. circadian_sin             0.0129
 9. cumulative_sleep_hrs      0.0127  ← NEW
10. circadian_cos             0.0121
11. hr_trend_5win             0.0111
12. pnn20                     0.0100  ← NEW
13. hr_dev_from_roll10        0.0089  ← NEW
14. ultradian_sin             0.0076
15. cumulative_awake_proxy    0.0074  ← NEW
16. sleep_cycle_number        0.0069  ← NEW
17. ultradian_cos             0.0068
18. roll_hr_std               0.0064
19. pnn50                     0.0063
20. hr_skewness               0.0059  ← NEW
```

### Confusion Matrix Pattern (LONO)
Awake is heavily confused with light sleep (main failure mode). Deep and REM are well-separated from each other. Light sleep is the dominant class.

#### Other Algorithm Files
| File | Purpose |
|------|---------|
| `algorithms/optimize_algo4.py` | DE optimizer for algo4 daily score params (14 params, MAE 2.76) |
| `algorithms/optimize_sleep_phases.py` | Older rule-based sleep phase optimizer (DE on thresholds, pre-ML) |
| `algorithms/optimize_sleep_timeline.py` | Per-window accuracy optimizer (precursor to optimize_algo5_phases.py) |
| `algorithms/optimize_continuous.py` | Continuous background optimizer (was run on server, now stopped) |
| `algorithms/algo4_calibrated/engine.py` | Best daily score algorithm (MAE 2.76, reverse-engineered Whoop formulas) |
| `algorithms/common/preprocessing.py` | HRV (RMSSD, SDNN, pNN50), RHR, respiratory rate computation |
| `algorithms/common/metrics.py` | `BaseAlgorithm` ABC, `WhoopScores` dataclass |
| `algorithms/data/loader.py` | Loads HAR-based sensor data + GT from whoop_official.json |
| `algorithms/data/db_loader.py` | Loads sensor data from whoop_capture.db (primary loader) |

### Critical Bugs Found & Fixed
1. **NoneType crash in analyze_all.py**: `result["whoop"]` was None on days without Whoop data. Fixed: null check.
2. **Double class weighting**: Engine was using both `class_weight="balanced"` AND manual `sample_weight`. Fixed: removed manual weights.
3. **Timestamp misalignment**: `predict_phases()` used iloc-based windows instead of absolute timestamps — only 36% overlap with Whoop's windows. Fixed: timestamp-aligned windows from `sleep_start_ts`.
4. **Hybrid awake override**: Movement thresholds never fired during sleep, and high `AWAKE_PROB_THRESH` blocked ML awake predictions. Fixed: removed all hybrid overrides, pure ML.
5. **Aggressive post-processing**: REM suppression + median smoothing + short-awake removal destroyed correct predictions. Fixed: replaced with `_smooth_isolated` (only single-window glitches).
6. **Dashboard GT extraction**: `extract_whoop_sleep_phases()` only emitted start-point of each phase range, not full duration. Fixed: continuous 2-min blocks from per-second labels.
7. **NaN in score prediction**: Days with no sleep data had NaN features, crashing GradientBoostingRegressor. Fixed: `np.nan_to_num(X)`.

### What Was Tried But Didn't Help Much
- **Deeper trees** (depth 5, 500 iter): 74.7% overall but awake recall drops to 12%
- **Extra awake sample weight** (3x-5x): awake recall 20→26% but overall drops
- **Two-stage classification** (binary awake/sleep → 3-class sleep stages): awake 42% but overall 72.9%
- **Hybrid approach** (binary awake detector + 4-class, threshold override): awake 31% but overall 73.6%
- **Hand-tuned Viterbi transitions**: worse than learned-from-data transitions

### Next Steps to Improve (When Resuming)

**Priority 1 — Feature Engineering:**
- Better spectral features: current LF/HF uses Welch PSD on 2-min windows (noisy). Try longer windows (5-10 min) for LF/HF computation.
- Breathing rate estimation from RR interval modulation (respiratory sinus arrhythmia)
- HR complexity measures (approximate entropy, detrended fluctuation analysis)
- Movement micro-arousal features (short bursts of movement that indicate awake)

**Priority 2 — Awake Detection (16.4% recall, biggest weakness):**
- The fundamental problem: "awake" during sleep means lying still with eyes closed — physiologically almost identical to light sleep with these sensors
- Ideas: (a) longer context windows for awake (10-15 min), (b) movement pattern changes before/after awake periods, (c) HR reactivity to micro-movements, (d) separate awake-specific features
- Very few awake windows in training set (~5%) — heavily imbalanced

**Priority 3 — Sequence Modeling:**
- Replace HistGBT with a sequence model (LSTM, Transformer) that sees full night context
- Current model sees at most 10 windows of history via rolling features
- A sequence model could learn sleep cycle patterns directly
- Challenge: limited training data — may overfit

**Priority 4 — More Training Data:**
- Sync more nights (strap holds ~7-14 days in circular buffer)
- Download all available deep-dive JSONs: `whoop deep-dive --date all`
- Every additional night helps LONO generalization significantly

**Priority 5 — Transition Matrix Optimization:**
- Current Viterbi uses counting-based transition matrix from training labels
- Could optimize transition matrix directly on CV accuracy (grid search or DE)
- Time-varying transitions (different in first vs. second half of night)

---

## Quick Commands

```bash
# Run LONO evaluation
cd algorithms && python eval_lono.py

# Quick 4-fold CV
cd algorithms && python eval_lono.py --quick

# Regenerate dashboard
cd algorithms && python analyze_all.py

# Sync new sensor data from strap
adb shell "run-as com.whoopcapture cat databases/whoop_capture.db" > whoop_capture.db
cp whoop_capture.db algorithms/data/raw/

# Download new deep-dive data
whoop deep-dive --date all
```

---

## Ground Truth Format

Deep-dive JSONs contain `heart_rate_zones` with `time_bound_ranges` — fractional positions (0.0-1.0) within the sleep window. `_parse_deep_dive_sleep_bounds()` in `features.py` converts these to per-second labels by:
1. Parsing `start_time` and `end_time` from `header_section.destination.parameters`
2. For each zone (AWAKE, LIGHT_SLEEP, SWS_SLEEP, REM_SLEEP), mapping `lower_endpoint`/`upper_endpoint` fractions to absolute timestamps
3. Filling a `second_gt` dict: `{unix_second: phase_string}`

This gives genuine per-second ground truth, not just aggregate percentages.

### Deep-Dive JSON Structure (path to sleep stages)
```
{date}.json
  └─ last_night
      ├─ header_section
      │   └─ destination
      │       └─ parameters
      │           ├─ start_time: "YYYY-MM-DDThh:mm:ss.000+00:00"
      │           └─ end_time:   "YYYY-MM-DDThh:mm:ss.000+00:00"
      └─ sections[0]
          └─ items[0]
              └─ content
                  └─ card_content[2]
                      └─ content
                          └─ heart_rate_zones: [
                              {id: "SWS_SLEEP", bar_graph: {time_bound_ranges: [{lower: 0.023, upper: 0.089}, ...]}},
                              {id: "REM_SLEEP", ...},
                              {id: "LIGHT_SLEEP", ...},
                              {id: "AWAKE", ...}
                          ]
```

### Other GT Metrics Available
`extract_deep_dive_metrics()` in features.py also extracts from deep-dive JSONs:
- Recovery score, Sleep score, Strain score (from SCORE_GAUGE items)
- HRV ms, RHR bpm, Respiratory rate (from CONTRIBUTORS_TILE items)
- Sleep efficiency %, Sleep consistency %, Stress % (from sleep CONTRIBUTORS_TILE)
- Steps (from strain CONTRIBUTORS_TILE)

---

## Complete Data Pipeline

```
  ┌──────────────────────────────────────────────────────────────────┐
  │ SENSOR DATA (Input)                                              │
  │                                                                  │
  │ Whoop 5.0 Strap (BLE)                                          │
  │   ↓ 0x2F Sensor Packets (AA01 frame)                           │
  │ ble-sync app (Android/Kotlin)                            │
  │   ↓ WhoopDataDecoder.kt decodes HR, RR, SpO2, Accel, Gyro     │
  │ whoop_capture.db (Room SQLite, 1-sec resolution)                │
  │   ↓ adb shell "run-as com.whoopcapture cat databases/..."      │
  │ algorithms/data/raw/whoop_capture.db                            │
  │   ↓ data/db_loader.py → pandas DataFrame                       │
  │                                                                  │
  │ Columns: timestamp, hr, rr1_ms, spo2, acc_x/y/z, gyro,        │
  │          movement (computed: sqrt(acc_x²+acc_y²+acc_z²)),       │
  │          date, datetime_local                                    │
  └──────────────────────────────────────────────────────────────────┘
                            ↓
  ┌──────────────────────────────────────────────────────────────────┐
  │ GROUND TRUTH (Labels)                                            │
  │                                                                  │
  │ Whoop Cloud API (Cognito auth)                                  │
  │   ↓ whoop deep-dive --date all                                  │
  │ ble-sync/data/whoop_backup/deep_dive/{date}.json         │
  │   ↓ _parse_deep_dive_sleep_bounds()                             │
  │ Per-second labels: {unix_second → "awake"/"light"/"deep"/"rem"} │
  └──────────────────────────────────────────────────────────────────┘
                            ↓
  ┌──────────────────────────────────────────────────────────────────┐
  │ FEATURE EXTRACTION (features.py)                                 │
  │                                                                  │
  │ For each night with both sensor data AND deep-dive GT:          │
  │   1. Slice sensor data to sleep window (start_ts → end_ts)     │
  │   2. Slide 2-min windows with 1-min stride                     │
  │   3. Extract 84 features per window                             │
  │   4. Majority vote from per-second GT labels → window label     │
  │                                                                  │
  │ Output: X (n_windows × 84), y (n_windows,), night_ids          │
  └──────────────────────────────────────────────────────────────────┘
                            ↓
  ┌──────────────────────────────────────────────────────────────────┐
  │ TRAINING & EVALUATION (engine.py, eval_lono.py)                  │
  │                                                                  │
  │ Leave-One-Night-Out CV:                                         │
  │   For each night k in 1..N:                                     │
  │     Train HistGBT on all nights except k                        │
  │     Learn transition matrix from training labels (Laplace)      │
  │     Predict on night k → log probs → Viterbi decode            │
  │     Measure accuracy on night k                                 │
  │                                                                  │
  │ Final model: train on ALL nights, save to models/*.joblib       │
  └──────────────────────────────────────────────────────────────────┘
                            ↓
  ┌──────────────────────────────────────────────────────────────────┐
  │ OUTPUT (analyze_all.py)                                          │
  │                                                                  │
  │ Per-2-min sleep phase predictions + daily scores                │
  │   → full_dashboard.html (interactive, Whoop vs Ours timelines)  │
  │   → serve locally or open full_dashboard.html                    │
  └──────────────────────────────────────────────────────────────────┘
```

### db_loader.py Details
- Loads from SQLite, filters corrupted timestamps (year > 2027 → bad packets)
- Computes `movement = sqrt(accelX² + accelY² + accelZ²)` if accel columns exist
- `datetime_local` column: NOT actually local timezone — it's UTC+1 naive timestamps with tzinfo=UTC. Use `timestamp` column (unix seconds) for reliable comparisons.
- Returns pandas DataFrame sorted by timestamp

---

## Firmware Reverse Engineering — DONE

Complete RE of the Whoop 5.0 ("Maverick") firmware. All tools and docs in `firmware/`.

### What Was Discovered
- **Chip**: Ambiq Apollo4 Blue Plus (ARM Cortex-M4F, 96 MHz, BLE 5.1 integrated)
- **RTOS**: QP (Quantum Platform) — event-driven Active Object framework, NOT FreeRTOS
  - 24 Active Objects: Supervisor, BLE_Command, Whoop_Cordio, Sensors, Analytics, Flash, etc.
  - 394 inter-AO signals, Listener AO has 26 states (most complex)
- **Sensors** (all identified via I2C addresses in firmware):
  - ICM-45686 (IMU, I2C 0x68/0x69), AS6221 (temp, 0x47/0x48)
  - LC709205F (fuel gauge, 0x0B/0x55), LP5562 (LED, 0x30)
  - DRV2625 (haptic, 0x5A), unknown PPG/AFE (SPI, likely Maxim)
- **13,000+ functions**, 20,000+ embedded strings recovered
- **Security**: CRC32-only firmware auth (no RSA/ECDSA/AES), no crypto library, no anti-rollback
- **SBL (ROM bootloader)**: May or may not enforce crypto — unknown, can't determine from app FW

### Analysis Pipeline
6 automated tracks generate JSON outputs, then combined into HTML report:
```bash
cd firmware/analysis
python3 track_a_disassembly.py   # Function discovery + call graph (r2pipe + angr)
python3 track_b_strings.py       # 20K+ strings, categorized
python3 track_c_peripherals.py   # I2C devices, GPIO, MMIO registers
python3 track_d_algorithms.py    # FPU regions, signal processing core
python3 track_e_rtos.py          # QP AOs, state machines, signals
python3 track_f_security.py      # Boot chain, crypto assessment
python3 generate_report.py       # → firmware_analysis_report.html
```

### Tools Built
- `tools/zbin_builder.py` — Build/verify/extract .zbin OTA containers (3 CRC checks)
- `tools/firmware_diff.py` — Compare firmware versions (byte, function, string diffs)
- `tools/firmware_patcher.py` — Patch firmware + recalculate CRCs
- `custom_firmware/` — Proof-of-concept ARM binary ("Hello World" on UART, 668 bytes)

### Custom Firmware Feasibility
- Application firmware has NO crypto signatures (CRC32 only)
- .zbin format fully understood, can build valid containers
- ARM cross-compiler available (`arm-none-eabi-gcc`)
- **Blocker**: SBL in ROM may enforce signatures — testable only by attempting OTA
- **Blocker**: PPG/AFE chip unknown — no open-source driver available
- **Alternative**: Zephyr RTOS has Apollo4 support in-tree

### Key Insight (methodology)
C compilers embed `__FILE__` paths via `assert()` and logging macros. The Whoop firmware logs extensively, revealing the complete source tree structure: `./modules/ble/src/ble_cmd_ao.c`, `./modules/sensors/src/ppg_ao.c`, etc.

---

## Publication Preparation — DONE

### Sanitization Completed
- Employee names removed from all docs
- User ID, strap serial, MAC address, email replaced with `<PLACEHOLDERS>`
- Cognito ClientId removed from docs (kept in code via env var)
- .gitignore blocks: all personal data, backups, databases, HAR files, health exports

### Before Publishing Checklist
- [ ] Responsible disclosure to Whoop Security (90 days before public)
- [ ] Extract `whoop/` as standalone git repo
- [ ] Clean git author/email to pseudonym
- [ ] Add LICENSE (MIT or Apache-2.0)
- [ ] Add legal disclaimer referencing UrhG §69e / EU Directive 2009/24/EC
- [ ] Final personal data scan

---

## How to Resume This Project

### Sleep Phase Classifier (agents.md priority)
1. Read this file (agents.md) — full state of algo5_ml at 74.7% LONO
2. Sync new sensor data from strap (`adb shell "run-as..."`)
3. Download new deep-dive ground truth (`whoop deep-dive --date all`)
4. Run `eval_lono.py` to verify baseline
5. Work on Priority 1-5 listed in "Next Steps to Improve" section above

### Flutter App (PLAN.md)
1. Read PLAN.md — 6 phases, Sprint 1 is foundation
2. Existing Flutter code in `app/` (15+ screens, working BLE)
3. Existing Kotlin BLE in `ble-sync/` (fully working)
4. Goal: merge both into unified app at `app/`

### Firmware (firmware/README.md)
1. Run `firmware_downloader.py` — check if new FW version available
2. If new version: run all 6 analysis tracks, then `firmware_diff.py` old vs new
3. Custom firmware: blocked on SBL crypto question (needs test OTA)

---

## Other Documentation Files
- `CLAUDE.md` — Project overview, quick start, component status, publication readiness
- `PLAN.md` — Flutter app replacement roadmap (Phase 1-6, 6 sprints)
- `ble-sync/CLAUDE.md` — Full BLE protocol documentation (AA01 format, 40+ commands)
- `app/CLAUDE.md` — Flutter app architecture (Provider, go_router)
- `firmware/README.md` — Firmware RE toolkit for open-source publication
- `firmware/WHOOP_FIRMWARE_REPORT.md` — German-language firmware architecture report
- `algorithms/CLAUDE.md` — Algorithm-specific notes
