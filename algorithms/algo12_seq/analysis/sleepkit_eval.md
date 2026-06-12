# SleepKit (AmbiqAI) — Evaluation for Our Whoop Sleep-Staging Project

Researched: 2026-06-12. Source: https://github.com/AmbiqAI/sleepkit + https://ambiqai.github.io/sleepkit/

---

## TL;DR Verdict

**Useful as a parts donor, NOT as a drop-in solution.** SleepKit is a deployment-oriented
ADK for Ambiq MCUs. Its pretrained 4-class stage model hits **75.5% accuracy** — basically
the SAME as our current ~74% — so swapping to it wins nothing. Its weights are also useless
to us (trained on MESA PSG with PPG/IMU, but tied to its own feature pipeline and TFLite-for-MCU
export). The genuinely valuable parts are: (1) its **feature set FS-W-PA-14** definition, which
overlaps ours but adds a few signals we may be underusing, and (2) its **TCN sequence backbone**,
which is a concrete architecture to try against our per-window gates for the REM-vs-Light problem.

**It will NOT magically fix REM-vs-Light.** SleepKit uses the *same* autonomic signals we do
(HR/HRV/respiration from PPG, movement from IMU) and accepts the same ceiling — its own 4-class
number (75.5%) and 5-class (68.4%) confirm REM/Light separation is hard for everyone on wrist
PPG+IMU. There is no secret sensor or feature here that breaks the ceiling.

---

## 1. What IS SleepKit?

- **Purpose:** "An AI Development Kit (ADK) that enables developers to easily build and deploy
  real-time sleep-monitoring models on Ambiq's family of ultra-low power SoCs." It is an
  **embedded/edge deployment framework**, not primarily a research SOTA pursuit.
- **License:** **BSD-3-Clause** (permissive — we can freely reuse code/feature definitions).
  Note: pretrained weights are trained on public research datasets (MESA etc.) whose own data
  licenses are separate; the SleepKit *code* is BSD-3.
- **It is all three** you asked about, in this priority order:
  1. A **training + eval framework** (dataset factory, feature store, model factory, train/eval/export CLI).
  2. A **pretrained Model Zoo** (per-task configs + weights + metrics).
  3. **Deployment tooling** for Ambiq Apollo SoCs (TFLite-Micro export, quantization).
- **MCU relevance:** Yes — it targets Ambiq Apollo SoCs, the SAME vendor family as the Whoop
  band's Apollo4 Blue. But this is mostly irrelevant to us: we run our classifier offline in
  Python on captured data, we are not deploying to the band. The shared-vendor angle is a
  curiosity, not a lever.

## 2. Tasks, Classes, and Input Signals

**Tasks:** Detect (sleep/wake periods), **Stage** (our task), Apnea, BYOT (custom).

**Stage classes** — directly matches our 4-class scheme:
| Scheme | Classes |
|--------|---------|
| 2-class | Wake / Sleep |
| 3-class | Wake / Light(N1+N2) / REM |
| **4-class** | **Wake / Light(N1+N2) / Deep(N3+N4) / REM** ← exactly ours |
| 5-class | Wake / N1 / N2 / N3 / REM (AASM) |

**Input signals (wrist stage model, FS-W-PA-14):** PPG ("Pleth"), IMU/accelerometer ("Leg"
movement channel), SpO2. Explicitly **NOT** EEG/EOG/EMG. From the docs: models use "motor
activity, cardiovascular signals (e.g. heart rate), and derived respiratory signals" from the wrist.

**Match to our hardware:** Strong overlap. We have HR, RR/HRV, 3-axis accel, gyro, SpO2, skin temp,
PPG amplitude. SleepKit's wrist stage model needs PPG + accel + SpO2 — **all of which we have.**
Caveats:
- SleepKit derives RR intervals **from PPG peaks** (`pk.ppg.find_peaks`), not from real ECG.
  We already have RR/HRV from the band — likely cleaner than PPG-derived RR. So we are not missing
  anything; if anything we have better autonomic input.
- SleepKit does **not** use gyroscope or skin temperature. We have both. These are signals SleepKit
  ignores — a potential edge for us, not them.

## 3. Pretrained Models & Datasets

**Pretrained stage models (TCN), reported on the docs:**
| Classes | Params | Accuracy | AP |
|---------|--------|----------|-----|
| 2-class | 10K | 88.9% | 96.0% |
| 3-class | 11K | 84.4% | 91.8% |
| **4-class** | **26K** | **75.5%** | **81.6%** |
| 5-class | 28K | 68.4% | 74.1% |

(No per-class precision/recall published — so we cannot directly compare their *awake recall* or
*REM recall* to ours. Their headline 4-class 75.5% ≈ our ~74%.)

**Datasets integrated:**
- **MESA** — 6,814 subjects, **PSG** (lab polysomnography) but includes a **wrist PPG ("Pleth")
  + leg/IMU + SpO2** channel set. This is what the wrist FS-W-PA-14 stage model trains on.
  PSG-graded labels, but using PPG/IMU-derived *features* → the closest public analog to our setup.
- **CMIDSS / CMSS** — ~300 subjects, 500+ wrist **accelerometer** recordings (actigraphy; sleep
  detect, not fine staging).
- **YSYW** — 1,983 PSG recordings (MGH). EEG-based PSG.
- **STAGES** — Stanford multi-site PSG.

**Bottom line on datasets:** MESA is the one that matters for us — it is large, has wrist PPG+IMU,
and PSG-grade 5-stage labels. It is a legitimate **pretraining corpus** we could exploit. The
others are either actigraphy-only (CMIDSS) or EEG-PSG that needs signals we don't capture (YSYW/STAGES).

**Can we use the weights directly?** No. They are exported/tuned for MCU TFLite-Micro and bound to
SleepKit's exact feature-extraction pipeline + input tensor layout. Transfer would mean adopting
their whole preprocessing, not just loading a `.tflite`.

## 4. Model Architectures

Model factory includes: **TCN** (all published stage models), U-Net, U-NeXt (MBConv), EfficientNetV2,
MobileOne, ResNet, **Conformer** (conv + self-attention), MetaFormer, TSMixer (all-MLP).

Relevant to us: the **TCN** is the proven choice here and is a natural fit for our sequence/window
problem — it consumes a sequence of per-epoch feature vectors and outputs per-epoch stages, with
dilated causal convolutions giving a long receptive field. This is a direct alternative to our
"per-window LightGBM gates + Viterbi" stack: the TCN learns the temporal smoothing/transition
structure that Viterbi currently hand-encodes. **Conformer** is the upgrade path if a plain TCN
plateaus (self-attention can exploit long-range REM-cycle periodicity ~90 min).

## 5. Feature Engineering

SleepKit is **feature-based, not raw end-to-end** — it has a "mini feature store" that computes
engineered features into HDF5, then feeds sequences of those vectors to the model. This is the
SAME design philosophy as ours. The relevant feature set:

**FS-W-PA-14** ("14 features from PPG + IMU on wrist for sleep stage classification"):
```
hr_bpm,                # HR from PPG, bandpass 0.5–2.0 Hz
hrv_td_mean_nn,        # time-domain HRV: mean NN
hrv_td_sd_nn,          # SDNN
hrv_td_median_nn,      # median NN
hrv_fd_lfhf_ratio,     # LF/HF ratio, bands (0.04–0.15),(0.15–0.4)
spo2_mu, spo2_std, spo2_med,   # SpO2 mean/std/median
mov_mu, mov_std, mov_med,      # movement (IMU bandpass 3–11 Hz) mean/std/median
rsp_bpm,               # respiration rate, derived from PPG via "rifv", clipped 3–120
spo2_qos, hrv_qos,     # signal-quality scores (mask bad frames)
```
Windowing: `win_size = frame_size` with 50% overlap.

**Vs our ~84 features:** We are far richer on HRV/spectral/sequential features. SleepKit's set is
deliberately lean (14 features, for a 26K-param MCU model). Things in FS-W-PA-14 worth auditing
against ours:
- **LF/HF ratio** (hrv_fd_lfhf_ratio) — classic REM marker (REM has sympathetic surges → higher
  LF/HF). If we are not already computing a clean per-window LF/HF on our band RR series, this is
  the single most likely feature to help **REM-vs-Light**.
- **Respiration rate + respiratory variability** — REM has irregular breathing; Light is more
  regular. SleepKit derives rsp from PPG; we can derive it from our PPG amplitude or from the
  band's own respiratory rate. Respiratory *irregularity* (std of breath-to-breath interval), not
  just mean rate, is the discriminative bit and is cheap to add.
- **Signal-quality masking (qos)** — they explicitly mask low-quality HRV/SpO2 frames. If our
  awake/REM confusion is partly driven by noisy windows, a QoS gate could clean labels.
- **FS-W-P-5** (apnea PPG set) adds **PIAV/PIIV/PIFV** = pulse-amplitude and pulse-interval
  variability from PPG. Pulse-amplitude variability tracks autonomic tone and could be an
  *additional* REM-vs-Light feature we don't currently have (we have PPG amplitude raw — we may
  not be computing its beat-to-beat variability).

Feature **code is BSD-3 and reusable** — we can lift the LF/HF, respiration (rifv), and PIAV/PIIV/PIFV
implementations directly.

## 6. CONCRETE VERDICT

**Can it improve REM-vs-Light or overall accuracy?** Marginally and indirectly — through borrowed
features and architecture, not through its model. Its own 4-class number (75.5%) is at our level,
and it relies on the same autonomic-only signal space, so it inherits the same REM/Light ceiling.
Do not expect a step-change. Be skeptical of the shared-MCU-vendor framing: it is irrelevant to an
offline Python classifier.

### Top 3 actionable ways it CAN help (ranked by expected payoff):

1. **Steal the autonomic REM features, not the model.** Add to our feature set: clean per-window
   **LF/HF ratio** (bands 0.04–0.15 / 0.15–0.4 on our band RR series), **respiratory-rate
   variability** (irregularity of breath intervals), and **pulse-amplitude variability**
   (PIAV/PIIV from PPG amplitude — code in `fs_w_p_5.py`). These are exactly the autonomic
   contrasts that separate REM from Light, the signals are ones we have, and the code is BSD-3.
   This is the highest-leverage, lowest-cost item and the most likely to move REM-vs-Light AUC.

2. **Prototype their TCN sequence model as an alternative to LightGBM-gates + Viterbi.** Feed our
   per-window feature vectors as a sequence into a small TCN (their 26K-param 4-class config is a
   starting point) that outputs per-window stages. The TCN learns temporal/transition structure
   end-to-end, potentially subsuming Viterbi. Lift the arch from their model factory (BSD-3); train
   on our 78 nights. If it plateaus, try their **Conformer** to exploit ~90-min REM-cycle periodicity.

3. **Pretrain on MESA (their pipeline), then fine-tune on our 78 nights.** MESA = 6,814 subjects of
   wrist PPG+IMU+SpO2 with PSG-grade 5-stage labels — orders of magnitude more data than ours, and
   the closest public analog. Use SleepKit's MESA dataset loader + FS-W-PA-14 features to pretrain a
   stage model, then fine-tune/calibrate on our Whoop windows. This is the only realistic path to
   beat the 78-night data ceiling, and directly attacks our **awake recall** problem (MESA has far
   more awake/arousal examples than our 6.9%). Caveat: domain gap (MESA PPG-derived RR vs our band
   RR; different label source = PSG techs vs Whoop's own algorithm) means fine-tuning, not zero-shot.

### What WON'T help (skeptic's note):
- **Loading their pretrained .tflite** — MCU-export-bound, tied to their feature layout, and only
  75.5% anyway. No.
- **Expecting a new sensor/signal to break the ceiling** — there isn't one. Same PPG+IMU+SpO2
  autonomic space we already use; they even ignore our gyro and skin-temp.
- **The Ambiq-MCU-deployment tooling** — irrelevant; we classify offline in Python.
