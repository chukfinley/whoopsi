# Eval: JBR 2025 — SIESTA (DOI 10.1177/07487304251336649)

**Verdict: NOT useful for us. Skip it.**

## What the paper actually is

- **Title:** "Sleep Identification Enabled by Supervised Training Algorithms (SIESTA): An Open-Source Platform for Automatic Sleep Staging of Rodent Electrocorticographic and Electromyographic Data"
- **Journal:** Journal of Biological Rhythms, Vol 40, Issue 4, Aug 2025 (online June 6 2025), pp. 330-346.
- **Authors:** Beck, Caldart, Ben-Hamo, Weil, Perez, Kalume, Brunton, de la Iglesia, Sanchez (UW / de la Iglesia lab).
- **Keywords:** sleep scoring, machine learning, web application, automated, rodent model.
- **Code/data:** GitHub https://github.com/delaiglesialab/SIESTA ; Zenodo https://doi.org/10.5281/zenodo.15322394

## Q1 — Topic & signals

Automated **sleep staging in rodents (mice/rats)**. It is a tooling/computational paper, NOT a circadian-modeling paper despite the journal. No body-clock model, no time-of-night prior, no oscillator/process-S/process-C math.

Signals used:
- **ECoG** (electrocorticography — implanted brain surface electrodes)
- **EMG** (electromyography — muscle electrodes)

Both are invasive electrode recordings on animals.

## Q2 — Applicability to our wrist data

**No.** Hard mismatch on every axis:
- Requires implanted brain (ECoG) + muscle (EMG) electrodes. We have none — wrist optical/IMU only (HR, RR/HRV, accel, gyro, SpO2, skin temp, PPG amplitude).
- Species is rodent, not human. Rodent sleep architecture, frequencies, and bout structure differ fundamentally.
- Stage set is **3-class Wake/NREM/REM**. We need **4-class Awake/Light/Deep/REM** (Light vs Deep split — exactly the human distinction rodent ECoG papers collapse). Their pipeline does not even attempt the split we are graded on.
- Their discriminative power comes from ECoG spectral bands (delta for NREM, theta for REM) — signals we physically cannot observe.

## Q3 — Method / dataset / results

- **Method:** feature extraction from ECoG+EMG, then a **hierarchical classifier built on logistic regression** (cascade: split obvious classes first, then harder ones). Packaged as an open-source Python toolkit + web app.
- **Dataset:** rodent recordings — wild-type mice, mutant lines, and rats; includes cross-laboratory validation.
- **Results (F1):** Wake 0.94, NREM 0.94, REM 0.74. REM is their weak class (consistent with REM being hardest everywhere).

## Q4 — Reusable technique?

Almost nothing transfers.
- **No circadian/time-of-night prior.** The thing the task brief hoped for (a body-clock model to sharpen stage priors) is **not in this paper.** It is a same-night supervised classifier with no temporal-position or circadian-phase feature. We already have a positional fraction-of-night prior; this paper offers no improvement to it.
- **Hierarchical / cascade logistic regression** is the only structural idea, and it is generic and old. We already do better than a 2-stage LR cascade (we run HistGBT + Viterbi, and prior algo5 work already experimented with a two-stage Awake/REM classifier — commits 672ca7f / 68f4810). Their cascade is tuned for the easy Wake-vs-NREM rodent boundary, not our Light/Deep/REM problem.
- Their feature set is ECoG/EMG-spectral — non-portable to PPG/IMU.

## Q5 — Verdict (skeptical, concrete)

**Reject.** This is a rodent ECoG/EMG lab-tooling paper that happened to land in a circadian journal. Wrong species, wrong signals (invasive electrodes vs wrist optical/IMU), wrong stage taxonomy (3-class, no Light/Deep), and — critically — it contains **none** of the circadian/time-of-night prior machinery the search was actually looking for. The single generic idea (hierarchical LR) we already exceed.

### Actionable items
- None. Do not implement anything from SIESTA.
- If still hunting for a circadian/time-of-night prior to improve stage priors, search instead for **human consumer-wearable (Apple Watch / Fitbit / Oura / actigraphy+HR) sleep-staging** papers that model **time-since-sleep-onset / circadian phase** as a feature — e.g. Walch et al. 2019 (Apple Watch HR+motion staging), Sundararajan et al. 2021 (random-forest actigraphy staging), or the Physionet "You Snooze You Win" / MESA-derived HR-based stagers. Those operate on our actual signal modality and on the 4-class human problem.
