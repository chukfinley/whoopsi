# Public Datasets: PSG Sleep Labels + Wrist/Wearable Cardiac & Motion Signals

**Goal:** Find datasets of `{wrist signals (PPG/HR/IBI/HRV + accel) + PSG/EEG-scored sleep stages}` to PRETRAIN a wrist→sleep-stage model, then fine-tune on our 78 Whoop nights. We're at ~74% (modality ceiling). We need **cardiac + motion**, NOT clean EEG/ECG.

**Researched:** 2026-06-12. Sources: NSRR (sleepdata.org), PhysioNet, Zenodo, arXiv, Oxford SLEEP.

---

## TL;DR Ranking (for OUR goal)

| Rank | Dataset | Wrist cardiac | Motion | PSG labels | Size | Access | Fit |
|------|---------|--------------|--------|-----------|------|--------|-----|
| **1** | **DREAMT** (PhysioNet) | PPG/BVP 64Hz + HR + IBI (E4 **wrist**) | accel 3-axis 32Hz | 5-class, 30s, tech-scored + raw PSG | 100 subj / 100 nights | DUA (PhysioNet restricted) | **BEST: exact modality match (wrist PPG waveform + accel + EEG labels)** |
| 2 | **MESA Sleep** (NSRR) | "Pleth" PPG **finger** oximeter 256Hz (Nonin) | wrist actigraphy (counts, concurrent night) | 5-class full PSG | 2,237 nights | DUA (NSRR committee) | Huge + raw PPG waveform, but PPG is FINGER not wrist; actigraphy is counts not raw accel |
| 3 | **Apple Watch / Walch 2019** (`sleep-accel`) | **derived HR (bpm) only**, no raw PPG | accel 3-axis (raw, g) **wrist** | 5-class (W/N1/N2/N3/REM) | 31 subj / 31 nights | **OPEN** (ODC-By) | Easiest to grab; but HR is derived bpm (like ours), no PPG waveform, small |
| 4 | **BIDSleep** (PhysioNet) | **instantaneous HR ~0.2Hz only** (Apple Watch), no PPG | accel 3-axis **wrist** | 5-class, EEG-scored (Dreem 2 headband) | 47 subj / **253 nights** | **OPEN** (ODC-By) | Multi-night, open, wrist accel+HR; but HR very low-rate, no PPG, labels from EEG headband not full PSG |
| 5 | **MMASH** (PhysioNet) | IBI/beat-to-beat (Polar **chest** strap) | wrist actigraphy | **NO PSG** (sleep diary only) | 22 subj | OPEN (ODbL) | DISQUALIFIED — no EEG ground truth, chest strap not wrist |
| 6 | **BiHeartS** (arXiv/dataset) | E4 **wrist** PPG/HR (bilateral) + Oura | accel | **NO PSG** (self-report) | ~91-146 sessions | check repo | DISQUALIFIED for labels — no PSG, only self-report |
| - | DOD-H / DOD-O (Dreem, Zenodo) | NONE (EEG headband) | none | 5-class, multi-scored | 25 + 55 | OPEN (Zenodo) | Not wrist — EEG only. Useless for our modality. |
| - | Sleep-EDF (PhysioNet) | NONE (EEG/EOG/chin EMG) | none | yes | 197 nights | OPEN | EEG only — not our modality |
| - | STAGES (NSRR) | PSG only (no consumer wrist) | actigraphy subset | yes | ~1,500 | DUA | PSG/EEG focus, wrist cardiac not the point |
| - | WESAD / PPG-DaLiA | wrist PPG | accel | NONE (not sleep) | - | open | Not a sleep dataset |
| - | OxWEARS | wrist PPG + accel vs PSG | yes | yes | ~15+ | **NOT YET RELEASED** (expected after 2026) | Watch for release |

---

## TOP PICK: DREAMT

**This is the single best actually-obtainable dataset for our exact problem.** It is the only large dataset with a **raw wrist PPG waveform + wrist accelerometer + derived HR + IBI**, time-aligned to **technician-scored PSG sleep stages**, AND it bundles the raw PSG signals too. The Empatica E4 modality is the closest public analogue to the Whoop wrist sensor suite.

### Details
- **Source/host:** PhysioNet — https://physionet.org/content/dreamt/ (latest v2.2.0; also 1.0.0–2.1.0)
- **Full name:** Dataset for Real-time sleep stage EstimAtion using Multisensor wearable Technology
- **Subjects/nights:** 100 participants, 1 overnight PSG each (~8pm–6am), recruited May–Sep 2022. Population recruited to include sleep apnea (good — diverse, harder cases).
- **Wrist device:** Empatica E4 on left wrist. Signals:
  - **BVP (PPG waveform) @ 64 Hz** ← the raw cardiac waveform we lack cleanly
  - **3-axis accelerometer @ 32 Hz**
  - **HR @ 1 Hz** (derived)
  - **IBI** (derived beat intervals → HRV)
  - EDA @ 4 Hz, skin temperature @ 4 Hz (bonus; temp matches a Whoop channel)
- **PSG ground truth:** Nihon Kohden Polysmith. Raw PSG (EEG/EOG/EMG/ECG/resp/airflow/SpO2, 15+ channels) included in the `data_100Hz` folder. Technician-scored every **30 s**.
- **Classes:** Wake, N1, N2, N3, REM (+ Preparation/Missing markers). 5 real classes — same target as ours.
- **Format:** Two tiers — `data_64Hz` (wearable only) and `data_100Hz` (wearable + PSG time-aligned, PSG downsampled 200→100 Hz).
- **ACCESS:** **Restricted — requires signing a Data Use Agreement** (PhysioNet Restricted Health Data License 1.5.0). NOT one-click. You must:
  1. Create a PhysioNet account, complete required training (CITI "Data or Specimens Only Research" course is standard for PhysioNet credentialed/restricted access),
  2. Sign the dataset-specific DUA on the PhysioNet page,
  3. Download once approved.
  Friction: moderate. Credentialing/training can take days; DUA for restricted (non-credentialed) sets is usually faster than full credentialed access. License permits research/ML use; redistribution restricted.

### Why it wins for us
- Wrist PPG waveform (64 Hz) lets us pretrain a PPG encoder, not just HR-from-bpm.
- Has BOTH derived HR+IBI (matches what we actually feed our model) AND the raw waveform (matches Whoop's PPG-amplitude / PPG-RR channels).
- 30 s PSG labels collapse cleanly to our 2-min windows.
- 100 nights ≈ ~1.3× our entire Whoop set, in one grab — meaningful pretraining corpus.
- Apnea-enriched → more Wake/arousal events, directly targets our weakest class (Awake recall).

---

## Detailed per-dataset notes

### 1. MESA Sleep (NSRR) — biggest, but PPG is finger not wrist
- URL: https://sleepdata.org/datasets/mesa
- 2,237 participants, one full overnight unattended PSG (2010–2012) + **7-day wrist actigraphy** + questionnaire.
- **Cardiac:** PSG includes a **"Pleth" PPG waveform from a Nonin 8000J finger pulse-oximeter @ ~256 Hz** (PPG duration ~10.6 h/night). This is a clean PPG waveform — heavily used in PPG sleep-staging papers (SleepPPG-Net etc.). **But it's a FINGER sensor**, not wrist; morphology differs from a wrist PPG.
- **Motion:** wrist actigraphy = **activity counts + white-light** (Actiwatch Spectrum), NOT raw triaxial accel. The first actigraphy night overlaps the PSG night, so counts can be aligned to PSG epochs — but it's counts, not raw motion.
- **Labels:** full PSG, 5-class.
- **Format:** EDF (signals) + XML annotations.
- **ACCESS:** NSRR Data Access & Use Agreement (DAUA), reviewed by NSRR Review Committee. **Free** but committee approval required (consistency with participant consent). ~4,989/7,820 historical requests approved.
- **Verdict:** Excellent for a clean-PPG pretrain track (huge N), but the finger-PPG + counts-only motion is a partial modality mismatch vs Whoop wrist. Use as a SECONDARY large-PPG corpus, not the primary.

### 2. Apple Watch / Walch et al. 2019 — `sleep-accel`
- URL: https://physionet.org/content/sleep-accel/1.0.0/
- 31 subjects (39 recruited, 8 excluded), 1 night each. U-Michigan 2017–2019.
- **Cardiac: derived HR in bpm only — NO raw PPG waveform.** (Same limitation as our Whoop-derived HR — useful as a like-for-like, useless for PPG-encoder pretraining.)
- **Motion: raw 3-axis acceleration (g) from Apple Watch wrist** + step counts.
- **Labels:** 5-class (wake=0,N1=1,N2=2,N3=3,REM=5).
- **Format:** tab-separated TXT, folders heart_rate/motion/labels/steps. ~2.2 GB.
- **ACCESS: fully OPEN, Open Data Commons Attribution License v1.0 (ODC-By). One-click download, no DUA.** This is the zero-friction option to prototype the pipeline TODAY.
- **Verdict:** Great for immediate prototyping (open, wrist accel + derived HR, real PSG labels) but small (31 nights) and no PPG waveform.

### 3. BIDSleep (PhysioNet) — multi-night, open, wrist accel + HR
- URL: https://physionet.org/content/bidsleep-dataset/1.0.0/
- 47 healthy adults, **253 nights** (3–7 nights/subj) — best night count among open sets.
- **Cardiac: instantaneous HR from Apple Watch PPG @ ~0.2 Hz only** (no PPG waveform, very low rate).
- **Motion: 3-axis wrist accelerometry** (Apple Watch).
- **Labels:** EEG-scored via **Dreem 2 headband** (AASM, 30 s), 5-class + unknown; includes auto + one-expert-corrected labels. (Note: headband EEG, not full clinical PSG — slightly noisier ground truth.)
- **Format:** CSV (motion, HR) + MAT (labels). DOI 10.13026/a0sy-7t69.
- **ACCESS: OPEN, ODC-By. No DUA.**
- **Verdict:** Good open multi-night source for an HR+accel track; HR sampling is too coarse for HRV and there's no PPG. Complements Walch.

### 4. MMASH (PhysioNet) — DISQUALIFIED (no PSG)
- URL: https://physionet.org/content/mmash/1.0.0/
- 22 healthy adults, 24 h. Beat-to-beat IBI from **Polar H7 CHEST strap** + wrist ActiGraph wGT3X-BT + saliva cortisol/melatonin.
- **Sleep "ground truth" = sleep-quality questionnaire / diary, NOT PSG/EEG.** No stage labels.
- OPEN (ODbL). **Verdict: unusable for sleep-stage supervision** (no PSG, cardiac from chest not wrist). Skip.

### 5. BiHeartS — DISQUALIFIED for labels
- arXiv 2308.06811. ~91 E4 sessions bilateral + Oura; up to 146 sessions w/ self-reports.
- **Ground truth is self-report sleep quality, no PSG.** Wrist E4 PPG is nice but no EEG labels. Skip for supervision.

### 6. DOD-H / DOD-O (Dreem Open Datasets) — wrong modality
- Zenodo (.h5), GitHub Dreem-Organization/dreem-learning-open. 25 healthy + 55 OSA, multi-scored (5 experts), 5-class.
- **Signals are EEG/EOG from a head device — NO wrist cardiac/motion.** OPEN, but useless for wrist→stage. Only relevant if we ever want EEG references.

### 7. STAGES (NSRR), Sleep-EDF, SHHS — EEG/PSG-centric
- Large clinical PSG cohorts. Cardiac = ECG/Pleth from PSG, motion = limited/none or counts. NSRR DUA (STAGES/SHHS) or open (Sleep-EDF). Not wrist-modality datasets; only useful as auxiliary PPG/ECG corpora at best.

### 8. WESAD, PPG-DaLiA — NOT sleep
- Wrist PPG + accel but stress/activity tasks, no sleep staging. Irrelevant except for PPG self-supervised pretraining of an encoder.

### 9. OxWEARS — future
- U-Oxford protocol (JMIR). Wrist PPG + accel vs PSG, free-living. Recruiting from Nov 2024; **public release expected after data collection ends ~2026.** Not yet available — monitor.

---

## Recommended plan for OUR project

1. **Prototype immediately (no friction):** download **Walch `sleep-accel`** (open) — wire up the wrist-accel + derived-HR → 5-class pipeline and validate our fine-tuning harness. Add **BIDSleep** (open, 253 nights) for more accel+HR pretraining volume.
2. **Primary pretrain (after DUA):** apply for **DREAMT** on PhysioNet — the only large public set with **wrist PPG waveform + accel + IBI + technician PSG labels**, the closest analogue to Whoop. Start the PhysioNet account + CITI training + DUA now (lead time = days).
3. **Optional large clean-PPG track:** apply for **MESA** via NSRR (free DAUA, committee review) for 2,237 nights of PPG-waveform + PSG to pretrain a PPG encoder — accept that PPG is finger-sourced.
4. Fine-tune on our **78 Whoop nights** last.

**Single best actually-obtainable target: DREAMT (PhysioNet).** How to get it: create a PhysioNet account → complete the required CITI training listed on the dataset page → sign the dataset DUA on https://physionet.org/content/dreamt/ → download (use the `data_100Hz` variant for time-aligned wearable + PSG). Use **Walch sleep-accel** as the zero-friction bridge while the DUA clears.

## Access friction summary
- **Zero friction (download now):** Walch `sleep-accel`, BIDSleep, DOD-H/O, Sleep-EDF, MMASH (ODC-By/ODbL).
- **DUA, moderate (days):** DREAMT (PhysioNet restricted license + likely CITI training).
- **DUA + committee review (days–weeks):** MESA, STAGES, SHHS (NSRR DAUA).
- **Not available yet:** OxWEARS (~post-2026).
