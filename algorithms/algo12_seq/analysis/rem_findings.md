# REM vs LIGHT/DEEP discrimination — findings

**Mission:** find raw-signal patterns that cleanly separate REM (label 3) from
LIGHT (1) and DEEP (2), to push REM recall toward 80% while protecting light
(and deep) recall.

**TL;DR:**
- A LightGBM **REM-vs-rest gate** (GroupKFold by night) reaches **OOF AUC 0.885**
  and **REM recall 88.3 % at rest recall 70 %** (83.6 % @ 75 %, **78.2 % @ 80 %**).
  The 80 % target is reachable.
- **REM-vs-DEEP is already easy** (single-feature AUC up to 0.94, model
  REM→deep leak only **1.8 %**). The *entire* problem is **REM-vs-LIGHT**
  (AUC ≈ 0.72–0.73 per feature; 26 % of true-REM windows leak into light).
- The separating signal is **autonomic, not motor**. The strongest features are
  **RR-interval irregularity** (`rr_cv`), **HR within-window variability**
  (`hr_std`), and **Poincaré SD2 / SDNN**. The **atonia / movement hypothesis is
  a dead end**: every accelerometer-movement feature sits at **AUC ≈ 0.50–0.52**
  (no signal at all) — because DEEP and LIGHT are *also* motionless, so "REM = no
  movement" does not distinguish REM from anything.
- The hard 3-class constraint (light **and** deep recall ≥ 70 %) caps REM at
  **~69.6 %** because in argmax mode REM and deep compete; relaxing deep to ≥ 68 %
  gives REM 70.8 %. The clean win comes from running REM as a **two-stage gate**
  (like the awake gate), not from biasing the 3-class argmax.

---

## Data used

- Raw per-second sensor: `data/raw/whoop_unified.db` via
  `data.db_loader.load_from_db` (2.0 M valid rows after merge/dedup).
- Per-second GT labels: `algo5_ml.features._parse_deep_dive_sleep_bounds`.
- 2-min windows, 60-s stride, majority-vote label.
- **79 sleep-windows that the raw DB overlaps, 38 170 labelled 2-min windows**:
  light 19 499 (51 %), deep 8 145 (21 %), rem 8 025 (21 %), awake 2 501 (7 %).
- Models trained on the **sleep-only subset** (light/deep/rem, 35 669 windows).
- AUC = single-feature ROC-AUC (`max(auc, 1-auc)`, i.e. separability magnitude),
  REM as positive class. Cohen's d on the same pairs.

---

## 1-2. Ranked per-feature separation (REM vs LIGHT, REM vs DEEP)

Sorted by `auc_avg = ½(auc_rem_vs_light + auc_rem_vs_deep)`. `_znight` = per-night
z-scored (each night's own light baseline subtracted) — this consistently lifts
the *vs-light* AUC because REM is an elevation **relative to that night**.

| feature | AUC REM-vs-LIGHT | AUC REM-vs-DEEP | d vs light | d vs deep |
|---|---|---|---|---|
| **rr_cv_znight** (RR-interval CV, night-z) | **0.728** | 0.942 | +0.67 | +1.87 |
| **rr_cv** (RR-interval coeff. of variation) | **0.726** | 0.939 | +0.63 | +1.69 |
| **hr_std** (HR std within window) | **0.723** | 0.930 | +0.44 | +1.10 |
| hr_std_znight | 0.722 | 0.930 | +0.53 | +1.35 |
| **rem_signature_znight** (engineered, night-z) | 0.720 | 0.931 | +0.56 | +1.48 |
| rem_signature | 0.718 | 0.930 | +0.30 | +0.72 |
| atonia_score2 (`hr_std / (1+50·mv_active_frac)`) | 0.718 | 0.926 | +0.22 | +0.49 |
| **sd2_znight** (Poincaré SD2, night-z) | 0.695 | **0.946** | +0.61 | +2.21 |
| sd2 | 0.692 | 0.942 | +0.60 | +2.10 |
| **breath_irreg** (`(1−resp_frac)·hr_std`) | 0.716 | 0.918 | +0.42 | +0.98 |
| hr_cv | 0.682 | 0.929 | +0.41 | +1.35 |
| sdnn | 0.675 | 0.932 | +0.52 | +1.83 |
| hr_range | 0.698 | 0.903 | +0.50 | +1.25 |
| hr_masd_std (std of \|ΔHR\|) | 0.686 | 0.903 | +0.32 | +0.80 |
| rmssd | 0.601 | 0.856 | +0.29 | +1.17 |
| hr_diff_entropy | 0.631 | 0.799 | **−0.39** | **−0.97** |
| sd1_sd2 | 0.631 | 0.754 | −0.49 | −1.03 |
| hr_mean_znight | 0.722 | 0.508 | +0.82 | +0.12 |
| **mv_mean / mv_std / mv_max / mv_p90 / mv_active_frac** | **≈0.50–0.52** | **≈0.50–0.52** | **≈0** | **≈0** |

Full table: `rem_separation.csv` (52 features).

### Headline result: REM is autonomic, not motor

1. **REM-vs-DEEP is nearly solved.** `sd2` / `sdnn` / `rr_cv` all hit AUC 0.93–0.95.
   Deep = slow, *regular* RR with high RMSSD but low CV; REM = irregular RR with
   high CV. Cohen's d for `sd2` is **+2.1**. The model leaks only **1.8 %** of
   true-REM into deep.

2. **REM-vs-LIGHT is the whole problem** and it is hard (best single feature
   AUC 0.73). The discriminators are all **HR/HRV irregularity within the
   window**:
   - `rr_cv` (RR-interval coefficient of variation, AUC 0.73) — REM's
     beat-to-beat intervals scatter more than light's.
   - `hr_std` (AUC 0.72) — REM HR wanders; light HR is tighter.
   - `sd2` / `sdnn` (RR spread).
   - `breath_irreg` — irregular breathing proxy (low HF-band concentration ×
     `hr_std`) genuinely helps (AUC 0.72 vs light).

3. **Atonia / movement does NOT separate REM** — the central physiological
   intuition fails on this hardware. Every accel-movement feature is at chance:

   ```
   mv_mean        AUC 0.504   d≈0
   mv_std         AUC 0.505
   mv_max         AUC 0.512
   mv_p90         AUC 0.510
   mv_active_frac AUC 0.517
   acc_mag_std    AUC 0.514
   ```
   Reason: LIGHT and DEEP are *also* near-motionless at 2-min aggregation, and
   Whoop's accel baseline is high/noisy (same effect seen in the awake analysis).
   The only way movement helps is *interactively* — gating HR-variability by
   stillness (`atonia_score2 = hr_std/(1+50·mv_active_frac)` reaches AUC 0.72),
   but that AUC comes from the `hr_std` numerator, not the movement denominator.

4. **hr_mean is a light/REM lever but NOT a deep lever.** `hr_mean_znight`
   separates REM-vs-light at AUC 0.72 (REM HR is elevated vs that night's light
   baseline) but is useless vs deep (AUC 0.51) — deep also has low HR. So absolute
   HR level only helps the *light* boundary. Use the **night-z** version; raw
   `hr_mean` is weaker across nights.

5. **rr_cv beats rmssd for REM.** RMSSD (the classic HRV index) only reaches
   AUC 0.60 vs light because it is dominated by deep's large slow oscillations.
   The **coefficient-of-variation / SD2 / irregularity** family is the right
   parameterization for REM.

---

## 3. Engineered REM features (tested)

Implemented in `rem_features.py:window_features`. Verdict per feature:

| engineered feature | definition | verdict |
|---|---|---|
| **rem_signature** | `(rr_cv + 0.02·hr_std)/(1+30·mv_active_frac)` | **keep** — AUC 0.72 vs light, top-6 |
| **atonia_score2** | `hr_std/(1+50·mv_active_frac)` | **keep** — AUC 0.72 (carried by hr_std) |
| **breath_irreg** | `(1−resp_frac_rr)·hr_std` | **keep** — AUC 0.72 vs light |
| **hr_var_x_level** | `hr_masd · hr_mean/60` | ok — AUC 0.66 (variability × elevation) |
| sd2 / rr_cv / hr_std (`_znight`) | per-night z-score | **keep** — night-z lifts vs-light AUC ~+0.01–0.03 |
| hr_accel_var | var of 2nd-diff HR | weak — AUC 0.62 |
| hr_sampen / rr_sampen | sample entropy | **drop** — AUC ≈ 0.50–0.55, expensive |
| lf_hf / lf_norm / lfhf_over_rmssd | RR-spectral LF/HF | weak — AUC 0.58–0.64 (sparse 2-min RR) |
| resp_rate_* (RR & accel) | breathing-rate peak | **drop** — AUC ≈ 0.51–0.63 |
| atonia_score (`hr_masd/(1+50·mv_mean)`) | | **drop** — AUC 0.56 (mv dominates) |

LF/HF underperforms because 2-min windows give too few RR beats for a stable
PSD. `rr_cv` is the robust, cheap substitute.

---

## 4. Positional prior (free, real)

From `architecture_findings.md` (78-night cached set): **REM concentrates in the
second half of the night.**

```
P(REM | fraction_of_night):  0.0-0.1 → 0.015   0.4-0.5 → 0.263   0.8-0.9 → 0.303
mean fraction_of_night per stage:  deep 0.258   light 0.557   REM 0.592
```

Deep is early, REM is late — so `fraction_of_night` *also* sharpens the
REM-vs-deep boundary for free. The 115-feature cached dataset already includes
`fraction_of_night`, `hours_since_onset`, `expected_cycle_phase`,
`cumulative_rem_proxy` — keep them.

---

## 5. Best achievable REM recall (the deliverable)

LightGBM (400 trees, lr 0.03, `scale_pos_weight` = rest/REM), GroupKFold by
night, out-of-fold.

### (A) Two-stage REM-vs-rest gate — the strong lever
**OOF AUC = 0.885.** Operating points (rest = light+deep):

| rest recall held ≥ | **REM recall** | thr |
|---|---|---|
| **0.70** | **88.3 %** | 0.355 |
| 0.75 | 83.6 % | 0.430 |
| **0.80** | **78.2 %** | 0.505 |

→ The mission target (REM recall ≈ 80 %) is met at rest recall ≈ 0.80.

### (B) 3-class light/deep/rem argmax (with probability bias)
The hard constraint *light ≥ 0.70 AND deep ≥ 0.70* caps REM because REM and deep
fight over the same probability mass in argmax:

| constraint | light | deep | **rem** |
|---|---|---|---|
| light ≥ 0.70, deep ≥ 0.70 | 0.700 | 0.702 | **0.696** |
| light ≥ 0.70, deep ≥ 0.68 | 0.701 | 0.681 | **0.708** |
| light ≥ 0.70, deep ≥ 0.65 | 0.701 | 0.655 | **0.721** |

Confusion (argmax, rows = true L/D/R):
```
        predL  predD  predR
trueL   12548   2962   3989
trueD    1540   6273    332
trueR    2083    144   5798     ← REM leaks 26% → light, 1.8% → deep
```

**Conclusion:** keep deep where it is and lift REM via the **two-stage gate**
(A), then let Viterbi + transition prior arbitrate. The 3-class argmax alone
cannot exceed ~70 % REM without sacrificing deep.

---

## Concrete proposal — features to add to the 4-class window model

The cached 115-feature set under-weights **intra-window RR irregularity**. Add:

**High priority (AUC ≥ 0.72 vs light, add first):**
1. `rr_cv` — RR-interval coefficient of variation (std/mean). *The single best
   REM feature.*
2. `rr_cv_znight`, `sd2_znight`, `hr_std_znight` — per-night z-scored versions
   (REM is an elevation relative to that night's light baseline).
3. `hr_std` at this exact 2-min scale (within-window HR variability).
4. `sd2` / `sdnn` — RR spread (also nails REM-vs-deep, d up to +2.2).
5. `rem_signature` = `(rr_cv + 0.02·hr_std)/(1+30·mv_active_frac)`.
6. `breath_irreg` = `(1−resp_frac_rr)·hr_std`.
7. `hr_mean_znight` — HR elevation vs night baseline (helps light boundary only).

**Do NOT add** (AUC ≈ 0.50, no marginal signal): `mv_mean`, `mv_std`, `mv_max`,
`mv_p90`, `mv_active_frac`, `acc_mag_std`, `resp_rate_*`, `hr_sampen`,
`rr_sampen`, `lf_hf`/`lf_norm`. The atonia/movement and RR-spectral families are
dead ends for REM at 2-min scale.

**Training tactic:** add a **two-stage REM gate** (REM-vs-rest LightGBM with
`scale_pos_weight`) exactly mirroring the awake gate, pick the threshold on a
validation fold to hold rest recall ≥ 0.80 (→ REM recall ~78 %), and fold its
probability into the 4-class blend before Viterbi. Expect REM recall to move from
~70 % into the high-70s/80s while light stays ≥ 0.70 (REM barely competes with
light once the gate adds the `rr_cv` autonomic signal, and never competes with
deep).

---

## Reproduce

```bash
OMP_NUM_THREADS=4 .venv/bin/python3 algo12_seq/analysis/rem_features.py
```
Outputs: `rem_separation.csv` (per-feature AUC/Cohen-d), `rem_importance.csv`
(LightGBM gain), `rem_oof.npz` (OOF probabilities for operating-point sweeps).
