# Mentalab Sleep Analysis — Applicability to Whoop Wrist Data

Date: 2026-06-12
Source: https://wiki.mentalab.com/applications/sleep-analysis/ (+ mentalab.com, YASA docs)

## TL;DR VERDICT

**NOT usable.** Mentalab's sleep analysis is a thin tutorial wrapper around the
**YASA** toolbox, and YASA **requires a central EEG channel** (C3/C4) — it is the
input that "accounts for almost all of the classifier's performance." We have
**zero EEG/EOG/EMG**. None of the Mentalab pipeline (preprocessing, spectral
features, YASA classifier, slow-wave/spindle detection) operates on HR/HRV/accel.
There is nothing to port. This is a dead end for our wrist data — do not invest in it.

The one indirect takeaway is methodological (epoching, HMM-style smoothing), and
we already do that. The *right* reference family for our signals is the
HR/HRV/accel literature (SleepECG, which we already have as algo2_sleepecg), not Mentalab.

---

## 1. What Mentalab's sleep analysis actually does

Mentalab is an **EEG/biosignal hardware company** (Mentalab Explore, an 8-channel
wireless EEG amplifier @ 250 Hz). Their "sleep analysis" wiki page is a tutorial,
not a novel algorithm:

- **Signals used:** EEG (multiple scalp leads), EOG (2 ch, eye movement), EMG
  (1 ch, chin). Standard polysomnography (PSG) montage with mastoid reference.
- **Method:** Automated sleep staging via the **YASA** (Yet Another Spindle
  Algorithm) open-source Python package.
  - YASA computes features per **30-s epoch** from a **central EEG channel
    (required)**, plus optional EOG, EMG, age, sex.
  - Classifier: **LightGBM** (gradient-boosted trees), trained on >3000 nights
    from the National Sleep Research Resource (NSRR).
  - 5-stage AASM output (Wake, N1, N2, N3, REM).
- **Extra steps:** 0.1–40 Hz bandpass (MNE-Python), band-power inspection,
  slow-wave detection (0.5–3 Hz delta), spindle detection (11–16 Hz), hypnogram +
  spectrogram plots.

It is fundamentally a **frequency-domain EEG** pipeline. The discriminative power
lives entirely in the EEG spectrum (delta for N3, spindles/K-complexes for N2,
mixed-frequency + EOG for REM). Per Mentalab's own docs: the EEG channel accounts
for almost all classifier performance; EOG/EMG are marginal add-ons.

## 2. Does any of it apply to our non-EEG wrist signals?

**No — not the signals, not the features, not the model.**

| Mentalab/YASA needs | We have | Transferable? |
|---|---|---|
| Central EEG (C3/C4) @ 250 Hz | none | No |
| EOG (eye movement) | none | No |
| EMG (chin tone) | none | No |
| EEG band power (delta/theta/sigma) | none | No |
| Sleep-spindle / slow-wave detectors | none | No |
| YASA pretrained LightGBM (EEG-feature space) | incompatible inputs | No |

Our inputs (HR, RR/HRV, 3-axis accel, gyro, SpO2, skin temp, PPG amplitude
scalar @ 1–2 s) live in a **completely different feature space**. YASA cannot
ingest them, and its pretrained model expects EEG-derived columns. There is no
adapter that makes this work — it would be a from-scratch rebuild, at which point
Mentalab/YASA contributes nothing.

Only generic, non-Mentalab-specific concepts carry over, and we already use them:
- Epoching into fixed windows (we use 2-min; YASA uses 30-s).
- Temporal smoothing of stage sequence (YASA does light smoothing; we use Viterbi).
- Tree-based classifier (YASA = LightGBM; we = HistGBT). Same family, but the
  features are what matter, and ours share nothing with YASA's.

## 3. Reusable open method / package / code?

From Mentalab specifically: **none** for our data.

The correct analog for HR/HRV/accel sleep staging — what Mentalab would be *if* it
were wrist-based — is the cardiorespiratory + actigraphy literature:

- **SleepECG** (`pip install sleepecg`) — Python, sleep staging from heart
  rate/HRV. JOSS 2023. **We already have this as `algo2_sleepecg` (~8.3 MAE-tier,
  done).** This is the real "YASA equivalent" for our signal set, and we already
  evaluated it.
- HR + accel + PPG-IBI neural nets (e.g. Walch/Apple-style actigraphy+HR 4-stage
  models; "AI-driven sleep staging from actigraphy and heart rate," PMC10191307)
  — these match our exact signal availability and are the literature to mine for
  features, NOT Mentalab.

If you want new feature ideas for our awake-recall weakness, pull them from the
HR/HRV/accel wearable papers above, not from the EEG/YASA stack.

## 4. Concrete verdict + actionable items

**Verdict:** Mentalab sleep analysis is **EEG-only**. It is inapplicable to Whoop
wrist data. There is no feature set, package, or code from it we can reuse.
Drop it. (Stated plainly, no hedging: this is not a fit.)

**Actionable items:**
1. **Close this lead.** Do not attempt to wire YASA/Mentalab to our pipeline — the
   pretrained model is EEG-feature-locked and our signals don't map.
2. **Reuse what we already have:** `algo2_sleepecg` is the legitimate HR/HRV-based
   counterpart. If revisiting cardiorespiratory staging, iterate there, not on Mentalab.
3. **For new features** (esp. to fix awake recall 33–52%), mine the **actigraphy +
   HR + PPG wearable** literature (Walch/Apple actigraphy+HR; PMC10191307;
   PMC10244431 efficient wearable staging), which uses exactly our signal set.
4. **Keep the only generic borrow we don't already exploit:** none — we already do
   epoching + tree model + temporal smoothing (Viterbi). No gap Mentalab fills.

## Sources
- https://wiki.mentalab.com/applications/sleep-analysis/
- https://mentalab.com/studying-sleep-using-mobile-eeg/
- https://yasa-sleep.org/generated/yasa.SleepStaging.html  (EEG channel required)
- https://github.com/raphaelvallat/yasa ; https://elifesciences.org/articles/70092 (YASA, NSRR-trained LightGBM)
- https://joss.theoj.org/papers/10.21105/joss.05411 (SleepECG — the HR-based analog we already use)
- https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10191307/ (actigraphy + HR staging — correct reference family)
