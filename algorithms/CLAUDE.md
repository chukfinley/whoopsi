# Algorithms — Living State

**Last Updated: 2026-05-20**

⚠️ **READ THIS FIRST before quoting any numbers.** This file is the canonical source for dataset size, MAE, accuracy, and parameter state.
**ALWAYS UPDATE THIS FILE after running an optimizer or eval** — add a row to the Score History table with date, dataset size, and result.

---

## Current Dataset (verify with sqlite/ls before quoting)

| Metric | Value | How to verify |
|---|---:|---|
| Sensor days in unified DB | **110** | `sqlite3 data/raw/whoop_unified.db "SELECT COUNT(DISTINCT date(timestamp,'unixepoch','+1 hour')) FROM sensor_records WHERE timestamp BETWEEN strftime('%s','2025-01-01') AND strftime('%s','2026-12-31');"` |
| Deep-dive label days | **278** | `ls ../whoop_backup/deep_dive/ \| wc -l` |
| algo4 trainable rows (matched GT × sensor) | **78** | `optimize_algo4.py` output line "GT rows matched" |
| algo5_ml trainable nights | **68** (33,455 windows) | `analyze_all.py` output line "algo5: trained on N windows from M nights" |

Data flow: **VPS mitmproxy → `scp dps:/opt/whoop-capture/data/whoop_sensor.db` → `tools/import_traffic_to_db.py` → `data/raw/whoop_unified.db`**. Phone-side adb pull is DEPRECATED.

---

## Score History

Each row = one optimizer/eval run. **APPEND a new row whenever you run `optimize_algo4.py`, `eval_lono.py`, or `optimize_algo5_phases.py`.**

### algo4_calibrated — Daily Scores (Recovery + Sleep + Strain combined MAE)
| Date | Nights | MAE Before | MAE After | Δ | Notes |
|---|---:|---:|---:|---:|---|
| 2026-05-20 | 78 | 10.91 | **8.05** | −2.86 | Full DE 35K iters. Bigger dataset (78 vs ~16 originally) → higher absolute MAE but real signal. |
| (pre-2026-05) | ~16 | ~10 | 2.76 | −7+ | Historic, old dataset, old metric weighting. Numerically incomparable to current run. |

### algo5_ml — Sleep Phase Classifier (LONO accuracy)
| Date | Nights | Windows | Overall | Awake Rec | Light Rec | Deep Rec | REM Rec | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **2026-06-12 (algo12_seq HYBRID)** | **78** | **38,450** | **74.2% (5-fold)** | **74.8%** | **70.1%** | **86.8%** | **71.5%** | **BEST balanced. Hybrid = algo5 sleep structure + algo12 awake-gate override (REM-protected, tau=0.32) + awake-bridge L=1.** Recovers algo5's Deep (87) while keeping all stages ≥70 and Awake at 75. `algo12_seq/hybrid.py` → `hybrid_pred.npy`. |
| **2026-06-12 (algo12_seq cascade)** | **78** | **38,450** | **73.3% (5-fold)** | **71.1%** | **71.0%** | **81.9%** | **70.8%** | FIRST config with EVERY stage ≥70% recall. Cascade: awake-gate→rem-gate→light/deep Viterbi on 137 feats (123 aug + 14 per-night z-scored). Breakthroughs: accel movement is a dead end (AUC 0.5); real separators are HR/HRV variability + gyro; per-night z-scoring lifted rem-vs-light AUC 0.72→0.905; gates beat argmax (awake AUC 0.914). Models in `algo12_seq/models/`. See `algo12_seq/README.md`. |
| 2026-06-12 (algo5 on same 78) | 78 | 38,450 | 76.1% (5-fold) | 25.7% | 77.7% | 89.0% | 75.7% | algo5 baseline re-run on the new 78-night set for comparison. High overall but Awake collapses — the reason the hybrid exists. |
| 2026-05-20 (boost-tuned) | 60 | 29,431 | 74.85% (4-fold) | **52%** | 74% | 88% | 71% | aw=1.9 dp=0.9 rm=1.0. **Awake +11pp vs baseline at same overall acc.** Deep was over-predicted, damping fixed Light↔Deep confusion. |
| 2026-05-17 (baseline aw=1.5 dp=1.2) | 60 | 29,431 | 74.88% (4-fold) | 40% | 74% | 91% | 73% | Pre-tune baseline. |
| 2026-05-17 (full LONO) | 60 | 29,431 | 75.6% | 33-52% | ~75% | ~80% | ~75% | HistGBT+Viterbi reported number. |
| 2026-05-17 (CRF alt) | 60 | 29,431 | 64.6% | 16% | 75% | 71% | 50% | sklearn-crfsuite — REJECTED. |

### Score-Reverse-Engineering Experiments (per-target MAE)
Welle 1+2 (2026-05-17). All trained on the same ~60 trainable nights.

| Algo | Recovery | Sleep | Strain | Notes |
|---|---:|---:|---:|---|
| algo4_calibrated (current) | **2.76** † | **1.99** † | ~3.0 † | †Old metric on smaller dataset; new combined MAE = 8.05 on 78 days |
| PySR symbolic regression | 6.20 | 3.33 | **1.04** | Strain formula interpretable: `log(steps·(√zone13+0.105)) + √zone45` — best Strain result |
| TabPFN-V2 (legacy) | 11.45 | 8.48 | 3.33 | Negative R² on Sleep/Strain. v2.5/2.6 license-gated. |
| EBM (InterpretML) | 13.95 | 9.39 | 3.53 | Glassbox partial-dependence curves are the deliverable, not MAE. |
| XGBoost | (crashed 3×) | — | — | Re-run pending |
| CRF (sleep phases) | — | — | — | 64.6% overall, rejected |

### 2026-05-28: Daily Score v2 (5-fold TimeSeriesSplit CV, 96 nights, no GT leakage)
Honest CV baseline of legacy v1 GBR vs new v2 pipeline (detected sleep window via SleepGate,
RHR/HRV from confirmed-deep candidates, HR-zone integrals, TRIMP, sleep architecture
from algo5 phase model).

| Score | v1 GBR (legacy) | v2 winner | Δ | v2 model | Top features |
|---|---:|---:|---:|---|---|
| Recovery | 14.23 | **14.09** | −0.14 | XGB d3 | sleep_perf_pct, rhr_sleep_p5/p10, burst_count, movement_int_active, spo2_min |
| Sleep    | 9.26  | **7.57**  | −1.69 | GBR d2 | det_deep_pct, rmssd_vs_rhr, epoc_proxy, hrv_rmssd_deep, sleep_onset_hour |
| Strain   | 3.57  | 3.70      | +0.13 | GBR d2 | sleep_wake_hour, det_deep_pct, skin_temp_std, det_light_pct, hr_std_day |

Note: in-sample MAE on training tail looks much better (R 1.1, S 0.7, T 0.3) — that is overfitting
illusion, not a real holdout. CV numbers above are the honest ones. v1 reported MAE 2.6/1.5/0.7
came from training-set residuals, not CV.

Engine: v2 models loaded automatically via `engine.load()`; `_predict_daily_scores` uses v2 path
when models present, falls back to v1 otherwise. Existing dashboards keep working.

Files: `algo5_ml/features_v2.py` (new), `train_scores_v2.py`, `tune_recovery_v2.py`,
`algo5_ml/models/score_{recovery,sleep,strain}_v2.joblib`, `score_feature_names_v2.joblib`,
`score_cv_summary_v2.json`.

---

## Current algo4_calibrated Parameters (auto-applied 2026-05-20)

| Param | Old (pre-2026-05-20) | New |
|---|---:|---:|
| rec_hrv_w | 0.3042 | **0.1302** |
| rec_rhr_w | 0.3764 | **0.2742** |
| rec_sleep_w | 0.2029 | **0.1446** |
| rec_resp_w | 0.0405 | **0.1813** |
| sigmoid_slope | 11.25 | **5.23** |
| sigmoid_center | 0.904 | **0.9364** |
| rhr_scale | 9.8 | **12.78** |
| strain_k | 5.04 | **3.15** |
| strain_c | 10.32 | **3.26** |
| sleep_hours_w | 0.4892 | **0.1602** |
| sleep_consistency_w | 0.061 | **0.2937** |
| sleep_eff_w | 0.3006 | **0.1825** |
| sleep_stress_w | 0.0631 | **0.2737** |
| consistency_default | 74.9 | **51.44** |

Persisted in: `algo4_calibrated/engine.py` + `optimization_history.json`.

---

## Quick Start

```bash
cd algorithms
source .venv/bin/activate

# Pull fresh data + ground truth
scp dps:/opt/whoop-capture/data/whoop_sensor.db /tmp/vps_sensor.db
python3 ../tools/import_traffic_to_db.py --old-db /tmp/vps_sensor.db --output data/raw/whoop_unified.db
whoop deep-dive --date all

# Re-optimize algo4 daily scores (~50 min on 78-day dataset, was ~5 min on 16 days)
python3 optimize_algo4.py
# → Updates algo4_calibrated/engine.py + optimization_history.json
# → APPEND result row to Score History above!

# Eval algo5 sleep classifier
python3 eval_lono.py            # Full LONO (~15-25 min on 60 nights)
python3 eval_lono.py --quick    # 4-fold CV (~3 min)

# Full dashboard
python3 analyze_all.py          # → full_dashboard.html
python3 -m http.server 8080     # serves from this dir
```

---

## Algorithm Inventory

| ID | Approach | Best Metric | Status |
|---|---|---|---|
| algo1_custom | Rule-based HR zones + EPOC | MAE ~8.3 | Done |
| algo2_sleepecg | SleepECG ML | MAE ~8.3 | Done |
| algo3_ml | Gradient Boosting (LOO-CV) | MAE ~1.7 | Done (overfits, tiny train set) |
| **algo4_calibrated** | Whoop-calibrated formulas, DE-optimized | **MAE 8.05** (combined, 78 days, 2026-05-20) | **CHAMPION** |
| **algo5_ml** | HistGBT + Viterbi (per 2-min window) | **75.6% LONO** (60 nights, 2026-05-17) | **PRIMARY FOCUS** |
| algo6_sleep_android | Sleep-as-Android reverse-engineered | — | Done |
| algo7_mihealth | Mi Health reverse-engineered | — | Done |
| algo_pysr | Symbolic regression (PySR) | Strain MAE 1.04 | Strain formula adopt-able |
| algo_tabpfn | TabPFN-V2 regressor | Underperforms | Skip (license-gated for v2.5+) |
| algo_ebm | Explainable Boosting (InterpretML) | Curves only | Diagnostic only |
| algo5_crf | CRF over algo5 features | 64.6% | Rejected |

---

## algo5_ml — Sleep Phase Classifier (Primary Focus)

**Pipeline:** sensor → 84 features per 2-min window → HistGradientBoostingClassifier → Viterbi smoothing → 4-class (Awake/Light/Deep/REM) hypnogram.

**Current state (2026-05-17):**
- 75.6% overall LONO accuracy
- Main weakness: **Awake recall 33-52%** — when user lies still while awake, strap can't tell from light sleep
- Two-stage variant + REM weight 1.8 helped (Awake recall 17.3% → 34.7% on smaller 33-night set)

**SHAP diagnosis of Awake errors:**
- Awake → Light driven by `hr_vs_night_median` (Awake HR near night median = looks like quiet Light)
- Awake → REM driven by `hr_entropy`, `roll10_hr_std` (irregular HR matches REM)
- Movement features almost absent from top-10 — strap blind when user still

**Next-feature recommendation (from SHAP+CRF agent run):**
`mv_burst_to_hr_response` — lag-correlation between movement bursts in previous 3 windows and HR rise in current window. Awake-after-movement has characteristic 5-15s HR spike; quiet Light/REM does not.

**Performance hint:** `OMP_NUM_THREADS=4` reduces HistGBT fit from >9 min to 21s on this machine when sub-agents contend for CPU.

### Feature Groups (84-115 total, evolving)
HR basic (11), HR dynamics (7), HRV/RR (11), Movement (12), Gyro+SpO2 (4), Temporal (6), Cross (3), Delta (4), 3-win rolling (5), 10-win rolling (6), Sequence (9), Architecture (6).

Full feature list + Step-by-step pipeline lived in old version of this file — see git blame if needed; truncated here for brevity.

---

## Optimizer Scripts

### `optimize_algo4.py`
- DE over 14 params, 35K iters
- **Was ~5 min on 16 days, now ~50 min on 78 days.** Scale linearly with dataset size.
- Auto-applies optimized params to `algo4_calibrated/engine.py`
- Saves `optimization_history.json`
- **DUTY:** when done, append a row to the Score History table above with date, nights count, before/after MAE.

### `optimize_algo5_phases.py` (two-phase)
- Phase 1: ML hyperparam grid search (~90 min, 324 combos)
- Phase 2: post-processing DE on cached predictions (~5 sec)
- Output: `algo5_best_params.json` + code snippets

---

## Known Data Issues

1. **Future timestamps (2029+)** from malformed BLE packets — filtered by `db_loader.py` (`year > 2027`).
2. **Some dates in `whoop_official.json`** have "--" for recovery/HRV/RHR — incomplete cycles, validate before `float()`.
3. **Awake windows ~5% of training data** — extreme class imbalance, main cause of poor awake recall.
4. **`datetime_local` column misleading** — actually UTC+1 naive timestamps with `tzinfo=UTC`. Use `timestamp` column for time comparisons.
5. **`rawHex` NULL** in old `whoop_capture.db` — can't re-decode. Newer VPS-imported records have full rawHex.

---

## File Reference

| File | Purpose |
|---|---|
| `analyze_all.py` | Main dashboard runner (all algos → full_dashboard.html) |
| `eval_lono.py` | Standalone LONO sleep-phase eval |
| `optimize_algo4.py` | DE optimizer for daily scores |
| `optimize_algo5_phases.py` | Two-phase optimizer for sleep classifier |
| `optimization_history.json` | Persisted params (latest run) |
| `algo4_calibrated/engine.py` | Recovery/Sleep/Strain formulas |
| `algo5_ml/{features,engine,train}.py` | Sleep phase classifier |
| `common/preprocessing.py` | HRV, RHR, respiratory rate |
| `common/sleep_algorithm.py` | Reverse-engineered SAA + Mi Health algorithms |
| `data/db_loader.py` | Loads sensor data from unified DB |
| `data/loader.py` | Loads deep-dive labels + cycles JSON |

---

## Hygiene Rules

1. **Verify before quoting numbers.** Dataset sizes, MAE, accuracy — check sqlite/ls/JSON before claiming any value in chat.
2. **Date everything.** Numbers without a date are noise.
3. **Don't trust historic MAE comparisons across dataset sizes.** More data ≠ worse model; it just means a harder fit.
4. **Append, don't replace.** Score History rows are additive — keep old runs for comparison.
