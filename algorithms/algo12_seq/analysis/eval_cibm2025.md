# Eval: CIBM 2025 paper (PII S0010482525005839)

**Date:** 2026-06-12
**Verdict: NOT RELEVANT. Cannot help us.** This is an EEG software-tooling paper, not a
sleep-staging method, and it uses signals we do not have.

---

## What the paper actually is

- **Title:** "SleepEEGpy: a Python-based software integration package to organize
  preprocessing, analysis, and visualization of sleep EEG data"
- **Authors:** R. Falach, G. Belonosov, J.F. Schmidig, M. Aderka, V. Zhelezniakov,
  R. Shani-Hershkovich, E. Bar, Y. Nir (Nir Lab, Tel Aviv University)
- **Venue/year:** Computers in Biology and Medicine, vol. 192 (2025), article 110232
- **DOI:** 10.1016/j.compbiomed.2025.110232 (PII S0010482525005839 confirmed via Crossref)
- **Type:** Open-source *software package* paper — a wrapper/integration library, NOT a
  novel classification algorithm or a benchmark study.
- **Code (public):** https://github.com/NirLab-TAU/sleepeegpy
  - Docs: https://nirlab-tau.github.io/sleepeegpy/
  - Preprint: https://www.biorxiv.org/content/10.1101/2023.12.17.572046
  - Zenodo: https://zenodo.org/records/15222132

It is a Python "glue" package built on top of MNE-Python, YASA, PyPREP, and
specparam (FOOOF). It organizes a preprocessing -> analysis -> visualization workflow
for lab/clinical **sleep EEG** recordings and ships a QC dashboard plus example Jupyter
notebooks. The headline contribution is usability/reproducibility for EEG researchers,
not accuracy on a staging task.

---

## Answers to the 5 questions

**1. Method/topic and sensors/signals.**
Topic = a software toolkit for sleep **EEG** (multi-channel scalp electrodes).
Signals = EEG (and the surrounding PSG montage it ingests). NOT PPG waveform, NOT
HRV/IBI, NOT accelerometer, NOT wrist-worn. Any actual staging it touches is delegated
to YASA, which itself is EEG/EOG/EMG-based.

**2. Does it match our signals?**
No. We have NO EEG/EOG/EMG. The paper's entire premise is clinical scalp EEG. There is
no cardiac/HRV/motion pathway in it. Hard mismatch.

**3. Sleep stages / dataset / accuracy / REM F1.**
N/A. It is not a classification paper. It reports no per-class accuracy/F1 and runs no
staging benchmark of its own; staging is just one optional step routed to YASA on EEG.
There is no REM-vs-Light number to compare against our ~74%.

**4. Architecture + features + code.**
No model of its own. Architecture = a layered Python API wrapping existing libraries
(MNE/YASA/PyPREP/FOOOF). Features = EEG preprocessing primitives (filtering, ICA,
bad-channel interpolation, spectral parametrization), not HR/HRV/accel features. Code is
public (link above) but is EEG-only plumbing — nothing reusable for our sensor stack.

**5. Concrete technique to reimplement for REM-vs-Light / awake recall.**
None. Nothing in this package operates on HR, HRV/IBI, or accelerometer. There is no
transferable feature, model, or post-processing trick for our problem.

---

## Why the confusion / how to avoid re-pulling this

The ScienceDirect article page is paywalled (HTTP 403 on fetch). The PII *looked* like
it could be a cardiac/wearable staging paper, but Crossref resolution of the PII
(alternative-id S0010482525005839 -> DOI 10.1016/j.compbiomed.2025.110232) pins it firmly
to SleepEEGpy. Do not re-research this one for the Whoop classifier.

## If the intent was "find a cardiac/wearable staging paper in CIBM 2025"

A genuinely relevant CIBM paper in the same era is the **ECG-only feed-forward NN**
("Expert-level sleep staging using an electrocardiography-only feed-forward neural
network", CIBM 2024, DOI 10.1016/j.compbiomed.2024.108545, median 5-stage kappa ~0.725).
That one at least uses cardiac signal (ECG -> beat-derived features), which is closer to
our HRV/IBI pathway than EEG — but it still assumes a clean ECG waveform / accurate beat
detection, not a wrist PPG-derived HR+IBI stream. If we want a paper to mine for
REM-vs-Light technique, that ECG-NN (or wrist-PPG staging papers like the WHOOP/Apple
Watch HRV-staging literature) is the place to look, NOT SleepEEGpy.
