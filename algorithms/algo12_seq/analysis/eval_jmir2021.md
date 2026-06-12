# Eval: JMIR mHealth 2021;9(10):e29849 — for Whoop 4-class sleep staging

**Date:** 2026-06-12
**Our context:** Whoop wrist band, NO EEG. Per 1-2s: HR, RR intervals (HRV/IBI), 3-axis accel, gyro,
SpO2, skin temp, PPG amplitude scalar (NO raw PPG waveform). Task: per-2-min Awake/Light/Deep/REM vs
Whoop labels. 78 nights, 38,450 windows. Current best ~74% all-stages-≥70; weak: REM-vs-Light, awake recall.

---

## TL;DR verdict

**The requested URL (e29849) is the WRONG paper for our task.** It is **DPSleep** — an
**accelerometer-only, binary wake-vs-sleep** longitudinal pipeline. It does **NO** sleep staging (no
Light/Deep/REM), uses **NO heart rate or HRV**, and reports **no classification accuracy / kappa**
(it validates against self-report and manual QC, not PSG per-epoch). It is essentially an actigraphy
sleep-period-detection tool. **Nothing in it helps with REM-vs-Light or 4-class staging.**

The task brief (HR + HRV + accel, 4/5-class staging, per-class REM/wake numbers) actually describes a
**different JMIR mHealth 2021 paper: e24704** ("Heart Rate Variability and Firstbeat Method for
Detecting Sleep Stages in Healthy Young Adults"). That one IS relevant — covered below as the real find.

---

## 1. e29849 (the requested paper) — DPSleep

| Field | Value |
|---|---|
| **Exact title** | "Open-source Longitudinal Sleep Analysis From Accelerometer Data (DPSleep): Algorithm Development and Validation" |
| **Topic** | Detect the major sleep episode (onset/offset/duration) over long longitudinal records |
| **Signals** | Triaxial accelerometer ONLY (30 Hz study 1 / 20 Hz study 2). Light + temperature recorded but used only for visualization/manual QC. **No HR, no HRV, no PPG, no ECG.** |
| **Device** | GENEActiv wrist-worn actigraph |
| **Stages** | **Binary: wake vs sleep.** Treats sleep as one monolithic episode. **No Light/Deep/REM.** |
| **Dataset** | Study 1: 6 healthy subjects, 1,448 nights total. Study 2: 2 subjects w/ severe mental illness, 309-543 days. Epoch = 1 min. |
| **Accuracy / kappa** | **None reported.** Validated via correlation with self-reported sleep quality (p<0.05) and auto-vs-manual agreement r=0.92-0.98. 18% of nights needed manual adjustment. |
| **Model** | Heuristic / rule-based: per-minute Welch PSD, within-subject spectral percentiles (10/25/50/75th), RMS + SD of accel, moving windows (150/100/60/90 min). **Not ML.** |
| **REM/Light distinction** | N/A — does not exist in this method. |
| **Code** | Open source: https://github.com/harvard-nrg/dpsleep |

### Match to our signals?
Trivially yes (we have accel) — but **irrelevant**, because it does not stage sleep. We are already
far past binary actigraphy. Adopting DPSleep would be a regression.

### Reusable for us? — essentially no
- The only mildly transferable idea: **within-subject spectral/percentile normalization** of accel
  power (normalize each night's movement features to that night's own distribution rather than global
  scales). We may already do per-night normalization; if not, it's a cheap thing to try. That is the
  whole transferable payload. Everything else (rule windows for finding the sleep period) we don't need.

---

## 2. e24704 (the paper the brief actually describes) — HRV / Firstbeat — RELEVANT

| Field | Value |
|---|---|
| **Exact title** | "Heart Rate Variability and Firstbeat Method for Detecting Sleep Stages in Healthy Young Adults: Feasibility Study" |
| **Signals** | HRV (from RR intervals), HRV-derived **respiration rate**, movement/accel, **time of day**. ECG-derived RR (chest), accel from wrist. |
| **Device** | Firstbeat Bodyguard 2 (chest, 2-electrode ECG → RR) + GENEActiv (wrist accel). Ground truth = PSG. |
| **Stages** | 5 labels: Wake, Light (N1+N2), SWS (N3), REM, plus a "sleep onset" state. |
| **Dataset** | 20 healthy adults (mean 24.5 y, 50% F), 40 nights, **30-s epochs**, vs PSG. |
| **Per-class** | **Wake:** sens 0.95 / spec 0.77. **Light:** sens 0.66 / spec 0.69. **SWS:** sens 0.72 / spec 0.91. **REM:** sens 0.60 / spec 0.92. No overall kappa reported. |
| **Model** | Proprietary neural-network (Firstbeat). Architecture/features NOT disclosed. |
| **REM bias** | Underestimates REM by ~18 min, overestimates wake by ~14 min vs PSG. |
| **Code** | NOT public (proprietary commercial method). |

### Honest assessment of e24704
- **Same input modality as us** (HR/RR/HRV + accel + time-of-day, no EEG) — so the **ceiling it shows is
  the most directly comparable benchmark we have**: REM sens only **0.60**, Light sens only **0.66**,
  even with clean chest-ECG RR and a tuned commercial NN. This is sobering: REM-vs-Light from cardiac +
  motion alone is genuinely hard, and our ~74% is already in the same ballpark as a commercial product.
- **Limitation for us:** it is a *feasibility* paper. It does NOT disclose the feature set or
  architecture and provides **no code**. So there is no recipe to copy — only confirmation of which
  signals matter and a realistic performance ceiling.

### Concretely reusable techniques (from e24704, skeptical)
1. **HRV-derived respiration rate as an explicit feature.** They feed respiration (estimated from RR
   interval modulation / RSA) directly to the classifier. REM is marked by *irregular/variable*
   respiration; SWS by *regular, slow* respiration. We have RR intervals → we can compute
   **respiratory-rate variability** (SD of breath-to-breath interval, or the high-freq RSA amplitude)
   over each 2-min window. **Respiration-variability is a textbook REM discriminator** and is the single
   most likely-useful idea here. Worth adding if not already present.
2. **Time-of-day as a feature.** REM proportion rises across the night, SWS concentrates early. A
   normalized "elapsed time since sleep onset" / clock-time feature gives the model a strong prior. Cheap;
   add if missing.
3. **Realistic expectations / no overselling:** their numbers say do NOT expect to "solve" REM-vs-Light
   with HR+accel. Target marginal gains (a few pp on REM sens), not a breakthrough. If we already beat
   their REM sens 0.60 and Light sens 0.66, our weak spot is closer to the modality ceiling than to a
   fixable bug.

---

## Top actionable items
1. **Drop e29849 / DPSleep** — wrong tool (binary actigraphy, no staging, no HR). Do not invest.
2. **Add an HRV-derived respiration feature** per window: breath-to-breath interval variability + RSA
   high-frequency amplitude from RR intervals. Best single candidate for **REM-vs-Light** separation
   (REM = irregular respiration, SWS = regular/slow). Verify we don't already have an equivalent.
3. **Add normalized time-since-sleep-onset** (and/or clock time) as a feature — strong prior for REM
   (late-night) vs SWS (early-night). Cheap.
4. **Try within-night percentile normalization** of movement/HR features (the one DPSleep idea worth
   borrowing) if we still use global scaling.
5. **Recalibrate expectations:** a directly-comparable commercial HRV+accel NN (e24704) hits REM sens
   0.60 / Light sens 0.66. Our ~74% all-stages-≥70 is already near that modality ceiling — chase a few
   pp, don't expect a fix. Wake recall: e24704 gets wake sens 0.95 (but with clean chest ECG and spec
   only 0.77 = over-calls wake). Our wake-recall problem is likely the PPG-RR noise floor, not algorithm.

## Sources
- e29849 DPSleep: https://mhealth.jmir.org/2021/10/e29849 | https://pmc.ncbi.nlm.nih.gov/articles/PMC8529474/ | code https://github.com/harvard-nrg/dpsleep
- e24704 Firstbeat HRV (the relevant one): https://mhealth.jmir.org/2021/2/e24704
- Related accel-only staging (not requested, for ref): https://github.com/wadpac/Sundararajan-SleepClassification-2021
