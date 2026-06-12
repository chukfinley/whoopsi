# YASA Sleep Staging — Evaluation for Whoop Wrist Data

**Date:** 2026-06-12
**Question:** Can YASA (Yet Another Spindle Algorithm) be used to stage our Whoop wrist data
(HR, RR/HRV, accel, gyro, SpO2, skin temp, PPG *amplitude scalar* — NO EEG/EOG/EMG)?
**Target:** per-2-min Awake/Light/Deep/REM vs Whoop labels, 78 nights, 38,450 windows. Current best ~74%.

**Sources:**
- Paper: Vallat & Walker 2021, eLife — https://pmc.ncbi.nlm.nih.gov/articles/PMC8516415/
- API: https://yasa-sleep.org/generated/yasa.SleepStaging.html
- Package: https://github.com/raphaelvallat/yasa  (yasa 0.7.0)

---

## TL;DR VERDICT: NOT USABLE on our data.

`yasa.SleepStaging` **requires a central EEG channel**. No EEG = it cannot run, full stop.
There is no HR/HRV/accelerometer staging path inside YASA's main staging API.
The *only* cardiac hook in YASA (`yasa.hrv_stage`) detects heartbeats from **raw ECG**, which we
also do not have (we have decoded HR + RR intervals, not an ECG waveform), and even that just feeds
**SleepECG** — which this project already implements as **algo2_sleepecg**.

So: don't port YASA. The reusable ideas (feature engineering + temporal smoothing) are generic and
we already do most of them. The one concrete, free win is YASA's **two-stage temporal smoothing
trick** (7.5-min triangular + 2-min trailing rolling features) — portable to our wrist features.

---

## 1. What does `yasa.SleepStaging` REQUIRE? Can it run on our signals?

**REQUIRED: one central EEG channel** (C4-M1, C3-M2, or Fpz-referenced). Mandatory positional arg.

Optional: EOG (e.g. E1-M2) and EMG (chin), each can be `None`. Optional metadata dict (age, sex).

**Heart rate, HRV, accelerometer, SpO2, temp, PPG = NOT accepted inputs. Not mentioned anywhere.**

**Blunt answer: It cannot run at all on our data.** Every feature YASA computes is derived from the
EEG/EOG/EMG time series (spectral power in delta/theta/alpha/sigma/beta bands, Hjorth params,
entropy, fractal dimension). With no electrophysiology channel there is literally nothing to feed it.
Our PPG is a per-packet *amplitude scalar*, not a sampled waveform, so it can't even masquerade as a
1-channel signal for spectral analysis.

## 2. Does YASA have ANY HR/HRV/accelerometer staging path?

**No staging path.** YASA staging is strictly EEG-PSG.

The single cardiac function is `yasa.hrv_stage` — it uses the **SleepECG** dependency to detect
R-peaks/heartbeats **from a raw ECG signal**, then computes per-stage HRV metrics. It is a
descriptive/analysis helper, not a classifier, and it needs raw ECG (we have decoded HR + RR, not ECG).
No accelerometer path exists anywhere in YASA.

If you want cardiac/actigraphy staging, the relevant package is **SleepECG** (separate project),
which we already wrap in **algo2_sleepecg**. YASA adds nothing on top of that for our setup.

## 3. The PMC paper (Vallat & Walker 2021, eLife)

- **Model:** LightGBM gradient-boosted trees. 500 estimators, max_depth 5, ~90 leaves,
  feature_fraction 0.6. Class weights to fight imbalance (N1=2.2, N2=1.0, N3=1.2, REM=1.4, Wake=1.0).
- **Trained on:** 31,000+ hours / 3,163 nights from 7 NSRR PSG datasets
  (CCSHS, CFS, CHAT, HomePAP, MESA, MrOS, SHHS). All clinical PSG with EEG.
- **Accuracy:** ~87.5% median, Cohen kappa 0.819 on 585-night NSRR holdout;
  84–87% on the DOD consensus set. Per-stage F1: Wake/N2/REM ~0.86, N3 0.84, **N1 only 0.43**.
- **Features (per 30-s epoch, from EEG/EOG/EMG only):**
  - Time-domain: std, IQR, skew, kurtosis, zero-crossings, **Hjorth mobility + complexity**,
    permutation entropy, fractal dimension.
  - Frequency-domain: relative spectral power in slow(0.4-1), delta(1-4), theta(4-8), alpha(8-12),
    sigma(12-16), beta(16-30) Hz; absolute broadband power; power ratios.
  - Plus: normalized time-elapsed-from-onset (0-1).
- **Reusable for a NON-EEG wrist setup?** The *model choice and pipeline shape* validate what we
  already do (LightGBM/HistGBT on per-epoch features + temporal context). The **EEG spectral features
  themselves are not transferable** — no EEG. The transferable pieces are pipeline-level, not signal-level.

## 4. Reusable feature-extraction / post-processing code (even without EEG)

YASA staging code is tightly coupled to EEG/MNE `Raw` objects, so **direct code reuse is impractical**.
But three *design patterns* are worth porting (cheap, signal-agnostic):

1. **Two-stage temporal smoothing of features (the most actionable item).** YASA triples every feature:
   (a) raw 30-s epoch, (b) **7.5-min centered triangular-weighted rolling average**, (c) **2-min
   trailing rolling average**. This injects temporal context without an RNN. We can apply the identical
   rolling transforms to our wrist features (HR, HRV/RMSSD, accel activity, etc.). Low effort, plausibly
   helps Light/Deep/REM separation and especially the Awake-vs-Light boundary (our known weakness).
2. **Per-night z-scoring of features** to absorb between-subject/between-night baseline shifts. Useful
   for us across 78 nights (resting HR/HRV baseline drifts night to night).
3. **Confidence/probability output + transition awareness.** YASA exposes per-epoch class probabilities
   and notes accuracy collapses at transitions (69% vs 94% stable). Mirrors our Viterbi/temperature work;
   reinforces keeping a temporal smoother (Viterbi/HMM) on top of the per-window classifier.

Class weighting (upweight rare stages) is also worth copying for our Awake-recall problem.

## 5. FINAL VERDICT

| Aspect | Result |
|--------|--------|
| Run `yasa.SleepStaging` on our data | **NO** — requires central EEG, which we don't have |
| YASA HR/HRV/accel staging path | **None** (only `yasa.hrv_stage` analysis from raw ECG via SleepECG) |
| Port YASA feature extraction | **No** — EEG-spectral, not transferable |
| Port YASA *pipeline patterns* | **Yes, partially** — temporal smoothing, per-night z-score, class weights |
| Net recommendation | Don't adopt YASA. Steal its smoothing/normalization tricks. Cardiac staging already covered by algo2_sleepecg. |

**pip:** `pip install yasa` works (deps: numpy, scipy, pandas, mne, scikit-learn, lightgbm, antropy,
lspopt; SleepECG/pyriemann optional). Heavy MNE/EEG stack — no reason to install it for our wrist setup.

### Top actionable items (independent of YASA install)
1. Add YASA-style **two-stage rolling features** to our wrist feature set: 7.5-min centered triangular
   + 2-min trailing rolling means (and slopes) on HR, RMSSD/HRV, accel activity, skin temp, SpO2.
2. Add **per-night z-scoring** of features before the classifier.
3. Add **class weights** (upweight Awake/REM) to attack the Awake-recall ceiling.
4. Keep the temporal smoother (Viterbi/HMM) — consistent with YASA's transition findings.
