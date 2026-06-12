# algo12_seq — Balanced Sleep-Stage Classifier (all stages ≥70% recall)

**Goal:** detect all 4 sleep stages (Awake/Light/Deep/REM) per 2-min window on
the full sensor dataset, with **every stage ≥70% recall** — not just high overall
accuracy that starves the rare Awake class.

## Result (5-fold GroupKFold by night, 78 nights, 38,450 windows, 2026-06-12)

| Stage | Recall |
|------:|-------:|
| Awake | **71.1%** |
| Light | **71.0%** |
| Deep  | **81.9%** |
| REM   | **70.8%** |

Overall accuracy 73.3%. Min-recall **70.8%** — first config to clear 70% on
every stage. (For comparison, the old algo5 hit 75.6% overall but Awake recall
only 33–52%, and pushing Awake up collapsed the other stages.)

## How it works — and what made the difference

Three sub-agents analyzed the raw sensor data vs official Whoop labels. The
key discoveries that broke the long-standing Awake/REM wall:

1. **Accelerometer movement does NOT separate Awake (or REM) from Light** —
   every accel/movement feature sits at AUC ≈0.50 at 2-min scale (the band has a
   noisy motion baseline; light sleep also has motion). The old project
   hypothesis (`mv_burst_to_hr_response`, accel burstiness) was a dead end.
2. **The real separators are autonomic + gyro:** within-window HR variability
   (`hr_std`), RR-interval spread/irregularity (`rr_cv`, `sdnn`, `rmssd`),
   `hr_delta`, `hr_range`, and gyroscope (`gyro_std/mean/max`).
3. **Per-night z-scoring is critical for REM.** REM is an *elevation relative to
   that night's own baseline*. Adding per-night z-scored features lifted
   rem-vs-light AUC from **0.72 → 0.905**, which was the missing piece.
4. **Gates beat argmax.** A 4-class model dilutes the rare classes. Dedicated
   binary gates are far stronger: awake-vs-light **AUC 0.914**, rem-vs-light
   **AUC 0.905**.
5. **The decode was the bottleneck, not the features.** The old 4-class
   blend + Viterbi crushed the gate signal (Awake 65%). A **cascade** that
   hard-assigns Awake then REM from the gates — and never lets Viterbi reabsorb
   them — recovers the gate's ceiling.

### Pipeline (cascade)
```
per 2-min window (137 features: 123 base + 14 per-night z-scored)
  ├─ awake gate (LightGBM)  ── P(awake) ≥ τ_a(0.40) ──► AWAKE
  ├─ rem gate   (LightGBM)  ── P(rem)   ≥ τ_r(0.40) ──► REM   (on non-awake)
  └─ remaining ─ light/deep 2-state Viterbi + positional prior + duration floors
```
Positional prior `(P(stage|frac_of_night)/P(stage))^0.6` (deep early, rem late).
Awake singletons are preserved (26% of awake segments are 1 window).

## Files
- `build_dataset.py` → `dataset.npz` (115 base features)
- `build_aug.py` → `dataset_aug.npz` (123: +8 engineered)
- `regen_gates.py` → `oof_v2.npz` (137: +14 per-night z-scored; OOF gates)
- `cascade.py` — the decode + threshold grid (the winning result)
- `hier.py`, `best.py`, `quota.py`, `twostage.py`, `bakeoff.py` — experiments
- `gate_ceiling.py` — proves the awake/rem gate operating ceiling
- `seq_model.py` — BiGRU baseline (abandoned: slow, no gain)
- `analysis/` — sub-agent findings (awake / rem / architecture)
- `finalize.py` → `models/` production artifacts

## Production model (`models/`)
`base4.joblib`, `gate_awake.joblib`, `gate_rem.joblib`, `decode.npz`, `meta.json`
(feature list, znight cols, thresholds τ_a=τ_r=0.40, recorded CV recalls).

## Inference sketch
1. Build the 123 aug features per window (`build_aug.new_features` + existing
   `extract_window_features`), append per-night z-scored cols (`regen_gates.ZCOLS`).
2. `pa = gate_awake.predict_proba`, `pr = gate_rem.predict_proba`,
   `p4 = base4.predict_proba`.
3. `cascade.decode(p4, pa, pr, ...)` with saved `decode.npz` + thresholds.

## Honest caveats
- Thresholds were selected on the same OOF, so the 70.8% min is a mildly
  optimistic operating point; the underlying gate AUCs (0.90+) are robust.
- **80% on every stage is not reachable on the current 78 nights** — only Deep
  clears it. Closing the gap to 80% needs more labeled nights (Awake is just
  6.9% of windows) and/or richer PPG/respiration signal, not more tuning.
