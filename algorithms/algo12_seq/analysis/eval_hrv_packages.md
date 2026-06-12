# HRV/Accel-Only Sleep-Staging Packages — Runnable Evaluation

Researched: 2026-06-12. Goal: a CONCRETE, pip-installable, open-source sleep-stager that runs on
HR + HRV (RR intervals) + accelerometer (NO EEG), runnable on our 78-night Whoop data NOW.

Env checked: `/home/user/git/whoop/algorithms/.venv/bin/python3` (Python 3.13).

---

## TL;DR — THE WINNER (zero-training, runs right now)

**`sleepecg` + bundled pretrained `wrn-gru-mesa` classifier.**

- **Already installed** in our venv (`sleepecg 0.5.9`).
- **Pretrained weights are SHIPPED INSIDE the pip wheel** — no network, no download, no training.
  Verified: `.venv/lib/python3.13/site-packages/sleepecg/classifiers/wrn-gru-mesa.zip` (+ `-weighted`, + `ws-gru-mesa`).
- **Input = exactly what we have:** RR intervals (ms) → cumulative heartbeat times. That's it for the
  base classifier (it does NOT require accel; movement is optional/unused by these heart-only models).
- **Output:** per-30s epoch, `stages_mode = wake-rem-nrem` → `0=WAKE, 1=REM, 2=NREM`.
- **Verified runnable:** loaded the classifier and ran `sleepecg.stage()` on synthetic 8h RR data →
  960 epochs, returns int labels. Works headless, ~0.7s inference for a full night on CPU.
- **We already use it** — `algorithms/algo2_sleepecg/engine.py` loads this exact classifier
  (`load_classifier("wrn-gru-mesa", classifiers_dir="SleepECG")`). It was wired for daily-score
  aggregation, NOT evaluated per-epoch against Whoop's 4-class labels. **That per-epoch eval is the
  open opportunity.**

**Catch:** it is **3-class (Wake/REM/NREM)**, not 4-class. Our task is Awake/Light/Deep/REM. So out
of the box it gives Wake + REM directly, and NREM must be split into Light vs Deep — either by a thin
secondary rule/model, or just evaluate Deep+Light merged as a sanity baseline first. There is a
**5-class** variant configured upstream (`stages_mode` supports `wake-rem-n1-n2-n3`) but only the
3-class MESA weights are bundled; getting true 4-class needs training (see "second place").

---

## Installed-package reality check (our venv)

| Package | Status | Version | Relevance |
|---|---|---|---|
| `sleepecg` | **INSTALLED** | 0.5.9 | **WINNER** — bundled pretrained HR/HRV classifier |
| `yasa` | INSTALLED | 0.7.0 | EEG-first; HRV staging not its strength (see below) |
| `sklearn` | INSTALLED | 1.8.0 | our own pipelines |
| `lightgbm` | INSTALLED | 4.6.0 | our gates (algo5/algo12) |
| `xgboost` | INSTALLED | 3.2.0 | — |
| `torch` | INSTALLED | 2.11.0+cu130 | for TCN/transformer routes |
| `tensorflow` | INSTALLED | 2.20.0 | sleepecg uses Keras backend |
| `neurokit2` | **MISSING** | — | only needed if we want its HRV feature funcs |

So the prize candidate needs **zero new installs**.

---

## Ranked Shortlist

### 1. sleepecg (wrn-gru-mesa) — RUN THIS FIRST. ★ best ROI, zero training, already here
- **Signals:** RR intervals only (HR/HRV). Accel not required.
- **Classes:** 3 — Wake / REM / NREM (`0/1/2`). Need NREM→Light/Deep split for full 4-class.
- **Pretrained:** YES, bundled in wheel (offline). License: BSD-3-Clause.
- **pip:** `pip install sleepecg` (already satisfied).
- **Trained on:** MESA (large PSG corpus, heartbeat-derived features).
- **Why first:** literally already wired in `algo2_sleepecg`; only missing piece is a per-epoch eval
  vs our Whoop labels at 2-min resolution. Gives an honest external baseline for free.
- **Architecture detail:** WRN-GRU (wide ResNet + GRU). Feature extraction params (from the loaded
  clf): `lookback=120, lookforward=150, min_rri=0.3, max_rri=2, max_nans=0.5`, features =
  hrv-time + hrv-frequency + recording_start_time + age + gender.
  → Provide `recording_start_time` (seconds-of-day of sleep onset) for best results; age/gender
  optional but improve it.

### 2. sleepecg — TRAIN our own 4-class on Whoop labels — best accuracy path within sleepecg
- Same package exposes the full train API (`prepare_used_features`, Keras model, `save_classifier`).
  We have 78 nights / 38,450 windows of Whoop 4-class labels → train a sleepecg-style classifier that
  outputs Wake/Light/Deep/REM directly. Reuses their proven HRV feature extraction + WRN-GRU backbone.
- Effort: medium. Payoff: real 4-class, comparable-or-better than our algo12 hybrid, and a clean
  second opinion that isn't our own feature pipeline.

### 3. YASA (`yasa 0.7.0`, installed) — NOT recommended for our case
- World-class **EEG** auto-stager (`yasa.SleepStaging` needs EEG + optional EOG/EMG). We have NO EEG.
- It has actigraphy/HR helpers but no pretrained HRV-only 4-class stager. Skip for staging; keep for
  its sleep-statistics utilities (`yasa.sleep_statistics`, transition matrices) once we have a hypnogram.

### 4. SleepKit (AmbiqAI) — parts donor only, NOT a drop-in (full eval in `sleepkit_eval.md`)
- Pretrained 4-class TCN hits **75.5%** ≈ our ~74% — swapping wins nothing, and weights are
  MCU-TFLite-bound to their own feature layout (not loadable as-is). BSD-3 code is reusable.
- Real value: borrow its autonomic REM features (LF/HF, respiratory-rate variability, pulse-amplitude
  variability PIAV/PIIV) and its TCN/Conformer arch; or pretrain on MESA then fine-tune on our 78
  nights (the only realistic path past the 78-night data ceiling, esp. for awake recall).

### 5. Walch et al. 2019 (Apple Watch HR+accel, `ojwalch/sleep-classifiers`) — reference, not pip
- Classic open repo: Apple Watch motion (accel/actigraphy) + HR + clock-proxy → Wake/NREM/REM (and a
  Wake/Sleep model). MESA-trained. **Not pip-installable** (research scripts, older TF/sklearn), needs
  porting. Good feature-engineering reference (activity counts, HR-derived features, circadian term)
  but `sleepecg` supersedes it as a runnable artifact. License: see repo (research/academic).

### 6. "Virtual Sleep Lab" / LSTM-HRV papers (Nature SciRep 2019, Sensors 2023) — methods, no pip artifact
- 4-class HRV-LSTM achieving Cohen's κ≈0.6. No maintained pip package with bundled weights → would be
  a reimplementation. Lower ROI than training within sleepecg.

---

## EXACT usage — run sleepecg on ONE night of our data

`db_loader.py` already yields per-record dicts with `timestamp, hr, rr1_ms, movement, gyro, spo2,
skin_temp`. Minimal end-to-end for one night:

```python
import numpy as np, pandas as pd, sleepecg

# --- 1. get one night's records (reuse our loader) ---
from data.db_loader import load_sensor_records           # adjust to actual loader fn name
recs = load_sensor_records("data/raw/whoop_unified.db")   # list[dict]
df = pd.DataFrame(recs)
df = df.sort_values("timestamp")

# pick a sleep window (e.g. 22:00 prev day -> 12:00) -- reuse algo2's masking logic
night = df[(df.timestamp >= START_TS) & (df.timestamp < END_TS)].copy()

# --- 2. RR intervals (ms) -> cumulative heartbeat times (seconds) ---
rr = night["rr1_ms"].dropna().to_numpy()
rr = rr[(rr > 300) & (rr < 2000)]                          # sane RR, matches clf min/max_rri
beat_times = np.cumsum(rr) / 1000.0                        # seconds from first beat

# --- 3. load BUNDLED pretrained classifier (offline) ---
clf = sleepecg.load_classifier("wrn-gru-mesa", "SleepECG") # "SleepECG" => bundled set in the wheel

# recording_start_time helps the model (seconds since midnight of sleep onset)
import datetime as dt
onset = dt.datetime.utcfromtimestamp(int(night.timestamp.iloc[0]))
start_sod = onset.hour*3600 + onset.minute*60 + onset.second

rec = sleepecg.SleepRecord(
    heartbeat_times=beat_times,
    recording_start_time=start_sod,        # optional but recommended
    sleep_stage_duration=30,               # 30s epochs (model native)
    # age=..., gender=...,                  # optional, improves accuracy
)

# --- 4. classify: 0=WAKE, 1=REM, 2=NREM ---
stages_30s = sleepecg.stage(clf, rec, return_mode="int")
print(len(stages_30s), np.unique(stages_30s, return_counts=True))

# --- 5. map to OUR 4-class @ 2-min windows for eval ---
# 30s -> 2-min: group every 4 epochs, majority vote.
# 3-class -> 4-class: WAKE->Awake, REM->REM, NREM-> {Light|Deep}.
#   Baseline: call all NREM "Light" (gives a floor); or split NREM by HR/HRV depth
#   (lowest-HR, lowest-HRV-variability NREM epochs -> Deep) to approximate Whoop.
```

**Note on `classifiers_dir="SleepECG"`:** this is the magic string that points `load_classifier`
at the bundled in-wheel classifier set (verified present: `wrn-gru-mesa.zip`,
`wrn-gru-mesa-weighted.zip`, `ws-gru-mesa.zip`). No download, fully offline. `wrn-gru-mesa-weighted`
is class-weighted (may help our chronic awake/REM imbalance — worth A/B-ing).

---

## Recommended next action

1. **Write `algo12_seq/sleepecg_eval.py`**: loop all 78 nights, run `wrn-gru-mesa`, downsample 30s→2min
   majority, align to Whoop labels, report per-class recall for {Wake, REM, NREM-merged}. This is a
   free external baseline (~1 min total inference). Append the number to `algorithms/CLAUDE.md` Score
   History. Try `wrn-gru-mesa` vs `wrn-gru-mesa-weighted`.
2. If the 3-class Wake/REM split beats our hybrid's Awake (74.8%) or REM (71.5%) recall, it's a strong
   signal to either ensemble it in, or train a 4-class sleepecg model on our labels (shortlist #2).
3. NREM→Light/Deep: start with a trivial HR/HRV-depth threshold; only invest in a learned split if the
   merged-NREM baseline is competitive.

## Bottom line
- **Best runnable candidate: `sleepecg` with bundled `wrn-gru-mesa` — installed, offline, pretrained,
  takes our RR intervals, runs a full night in ~1s.** Zero new dependencies.
- It's 3-class, so it's a baseline/second-opinion, not a 4-class drop-in. The 4-class win path is to
  TRAIN a sleepecg-style model on our 78 nights (shortlist #2), or borrow SleepKit's autonomic REM
  features (shortlist #4).
- YASA needs EEG → not applicable. Walch/LSTM papers → reference code, no pip artifact.
