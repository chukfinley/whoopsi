# AWAKE vs LIGHT discrimination — findings

**Mission:** find raw-signal patterns that separate AWAKE (label 0, lying in bed
awake) from LIGHT sleep (label 1), good enough to push awake recall to 70-80%.

**TL;DR:** It is very achievable. A LightGBM trained on awake-vs-light only,
with the feature set below, reaches **OOF AUC 0.90** (GroupKFold by night) and
**awake recall 90.9% at light recall 71.9%** (or 83.6% awake at 80.3% light).
The single biggest surprise: **accelerometer "movement" does NOT separate the
two classes at all** — the separation is almost entirely **autonomic
(HR/HRV variability)** plus **gyroscope micro-motion**.

---

## Data used

- Raw per-second sensor: `data/raw/whoop_unified.db` via `data.db_loader.load_from_db`
  (2.0M valid rows after merge/dedup).
- Per-second GT labels: `algo5_ml.features._parse_deep_dive_sleep_bounds`.
- 222 deep-dive nights exist, but the **raw sensor DB only overlaps 20 of them**
  — that is the binding constraint on sample size, not the labels.
- **20 nights, 4 362 labelled 2-min windows**: awake 274 (6.3%), light 2 044
  (46.9%), deep 1 011, rem 1 033.
- AUC = single-feature ROC-AUC separating awake (pos) from light (neg), oriented
  so >0.5 means "higher value ⇒ awake". Cohen's d on the same pair.

> Caveat: 20 nights is modest and awake is rare. Numbers are encouraging but
> should be re-confirmed once the unified DB covers more of the 222 GT nights.

---

## 1-2. Ranked per-feature separation (awake vs light, window aggregate)

| feature | AUC | Cohen d | awake μ | light μ |
|---|---|---|---|---|
| **hr_std** (HR variability in window) | **0.793** | +1.08 | 10.2 | 6.27 |
| **rr_std / sdnn** (RR-interval spread) | **0.791** | +1.13 | 165 | 114 |
| **hr_delta_mean_abs** (mean \|ΔHR\|) | 0.759 | +0.67 | 6.36 | 4.43 |
| **gyro_std** | 0.755 | +0.62 | 0.0259 | 0.0112 |
| **hr_rise_after_burst** | 0.746 | +0.80 | 12.5 | 8.52 |
| **rmssd** (HRV) | 0.726 | +0.83 | 158 | 117 |
| gyro_mean | 0.722 | +0.63 | 0.0132 | 0.0086 |
| acc_z_var | 0.721 | +0.62 | 0.182 | 0.037 |
| gyro_max | 0.719 | +0.53 | 0.167 | 0.077 |
| hr_range | 0.707 | +0.68 | 44.8 | 32.3 |
| hr_max | 0.705 | +0.61 | 87.4 | 75.9 |
| hr_mean | 0.679 | +0.38 | 57.4 | 53.8 |
| acc_var_sum | 0.671 | +0.88 | 0.509 | 0.214 |
| mv_longest_burst | 0.320 | **−0.66** | 46.2 | 68.0 |
| **mv_mean / mv_std / mv_frac_above_thr** | **≈0.48–0.52** | ≈0 | — | — |

### The headline (counterintuitive) result

**Accelerometer movement magnitude does NOT discriminate awake from light.**
Mean movement, movement std, p90, fraction-of-seconds-moving, sustained-motion
fraction, jerk — all sit at AUC ≈ 0.48–0.52 (no signal). Verified across
thresholds:

```
frac_above movement-thr, awake vs light AUC:
  thr=0.02 -> 0.505   thr=0.05 -> 0.492   thr=0.10 -> 0.502
  thr=0.20 -> 0.492   thr=0.30 -> 0.488
```

Why: the Whoop accel stream has a **high, noisy baseline** (median |‖a‖−1| =
0.38, p90 = 1.01). At 2-min aggregation, light-sleep windows already contain
plenty of small accel motion, so "awake people move more" washes out. In fact
`mv_longest_burst` is *longer in light* (68 s vs 46 s) — light sleep has longer
continuous low-amplitude drift, while awake motion is sharper and more
intermittent (better captured by gyro than by accel magnitude).

> This contradicts the project-memory hypothesis that the fix is richer accel
> burstiness / `mv_burst_to_hr_response`. `mv_burst_to_hr_response` scored
> AUC 0.493 (nothing). The real levers are **HR/HRV variability and gyro**.

### What DOES separate them

1. **Heart-rate variability *within the window*** — `hr_std` (AUC 0.79),
   `hr_delta_mean_abs` (0.76), `hr_range` (0.71). Awake HR wanders (≈10 bpm SD)
   vs light's tight ≈6 bpm. This is the strongest family.
2. **RR-interval spread / HRV** — `rr_std`/`sdnn` (0.79), `rmssd` (0.73). Awake
   autonomic tone is far less regular.
3. **Gyroscope micro-motion** — `gyro_std` (0.76), `gyro_mean` (0.72),
   `gyro_max` (0.72). Gyro captures the small wrist re-orientations of an awake
   person that accel-magnitude misses.
4. **Movement→HR response** — `hr_rise_after_burst` (0.75): when an awake person
   moves, HR jumps more and stays up; in light sleep HR barely responds. This is
   the *correct* form of the movement-HR-coupling idea — coupling on **gyro/HR**,
   not accel-magnitude correlation.
5. **Elevated HR level** — `hr_mean` (0.68), `hr_max` (0.71).
6. **acc component variance** (`acc_z_var`, `acc_var_sum`) — note these beat
   `mv_mean` because *variance of raw axes* captures posture shifts that the
   `|‖a‖−1|` magnitude collapses away.

## 4. Positional prior

Weak but real and free:

```
awake rate by night-position decile:
  [0.0,0.1) 2.4%   [0.1,0.2) 7.8%  ...  [0.9,1.0) 12.3%
mean pos_frac: awake 0.561, light 0.549, deep 0.274, rem 0.627
```

- Awake is **2x more likely in the last 10% of the night** (12.3% vs ~6%
  baseline) — morning wake.
- Sleep-onset latency shows as a mild bump in [0.1,0.2).
- As a standalone feature `pos_frac` AUC is only 0.51, but the model still ranks
  it 3rd in importance (it sharpens the last-20-min decision). Keep it as a
  prior, not a primary signal.

## 5. Best achievable awake recall (the deliverable)

LightGBM (300 trees, lr 0.03, `scale_pos_weight` = light/awake ratio),
GroupKFold by night, awake-vs-light only, out-of-fold predictions:

- **OOF AUC = 0.900**

Operating points (threshold chosen on OOF probs):

| light recall held ≥ | awake recall | awake precision | thr |
|---|---|---|---|
| **0.70** | **90.9%** | 30.2% | 0.025 |
| 0.75 | 88.7% | — | 0.040 |
| 0.80 | 83.6% | — | 0.070 |
| 0.85 | 77.0% | — | 0.135 |

So the mission target (awake recall 70–80%) is comfortably met — at light
recall ≥ 0.85 we already get 77% awake recall, and at ≥ 0.70 we get ~91%.
The cost is awake **precision** (~30%): many light windows get flagged awake.
In the full 4-class + Viterbi pipeline that is mitigated by the transition
prior and the deep/rem competition, so the realized improvement to awake recall
should be large without destroying overall accuracy.

Top LightGBM importances: `hr_slope`, `hr_mean`, `pos_frac`, `mv_longest_burst`,
`gyro_mean`, `acc_x_var`, `gyro_max`, `gyro_std`, `mv_burst_to_hr_response`,
`mv_event_rate`, `rr_std`, `rmssd`, `hr_delta_mean_abs`. (Tree models exploit
even weak features interactively, which is why a couple of low-AUC movement
features still rank — but the AUC table is the honest measure of marginal
signal.)

---

## Concrete proposal — features to add to the 4-class window model

The existing 115-feature set (algo12_seq/dataset.npz) clearly under-weights
intra-window autonomic variability. Add the following window features (all
implemented in `awake_features.py`):

**High priority (AUC ≥ 0.72, add first):**
1. `hr_std` — HR standard deviation within the 2-min window.
2. `rr_std` / `sdnn` — RR-interval standard deviation (raw HRV spread).
3. `hr_delta_mean_abs` — mean absolute second-to-second HR change.
4. `gyro_std`, `gyro_mean`, `gyro_max` — gyro is under-used; it beats accel.
5. `hr_rise_after_burst` — max HR rise in the 10 s following a movement burst.
6. `rmssd` — if not already present at this exact window scale.
7. `hr_range`, `hr_max` — HR envelope.
8. `acc_z_var` / `acc_var_sum` — raw-axis variance (not magnitude).

**Medium priority:**
9. `pos_frac`, `is_last_20min` — positional prior for morning wake.

**Do NOT bother adding** (no marginal signal, AUC ≈ 0.5): `mv_mean`,
`mv_std`, `mv_frac_above_thr`, `mv_sustained_frac`, `jerk_*`,
`mv_burst_to_hr_response`, `still_frac`, `time_since_last_move`. The accel-
magnitude "burstiness" family is a dead end for awake-vs-light at 2-min scale.

**Training tactic:** use class weighting (`scale_pos_weight`) or a two-stage
gate (awake-vs-rest first), then pick the threshold on a validation fold to hold
light/deep/rem recall at an acceptable floor. Expect awake recall to jump from
the current ~16–52% into the 70–90% band while keeping light recall ≥ 0.70.

---

## Reproduce

```bash
OMP_NUM_THREADS=4 .venv/bin/python3 algo12_seq/analysis/run_awake_analysis.py
```

Outputs `awake_results.json` (full feature ranking + operating points).
Feature implementation: `algo12_seq/analysis/awake_features.py`
(`build_windows`, `WINDOW_FEATURE_NAMES`).
