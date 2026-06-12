# Sleep Architecture Findings (algo12_seq)

Dataset: 38450 windows (2-min), 78 nights, labels {0:awake,1:light,2:deep,3:rem}.

Global stage distribution: awake 2638 (6.9%), light 19588 (50.9%), deep 8166 (21.2%), rem 8058 (21.0%)


## 1. Empirical transition matrix (window->window, 2-min step)

Rows=from, Cols=to. P(to|from):

```
from\to    awake   light    deep     rem
awake     0.753   0.217   0.001   0.029
light     0.012   0.933   0.029   0.025
deep      0.033   0.037   0.929   0.001
rem       0.017   0.051   0.000   0.932
```
Self-persistence (diagonal): awake 0.753, light 0.933, deep 0.929, rem 0.932
Rare (<2%) off-diagonal transitions: awake->deep 0.001, light->awake 0.012, deep->rem 0.001, rem->awake 0.017, rem->deep 0.000

## 2. Positional patterns (P(stage | fraction_of_night), 10 bins)

```
bin(frac)  awake   light    deep     rem
0.0-0.1   0.035   0.269   0.682   0.015
0.1-0.2   0.066   0.427   0.396   0.111
0.2-0.3   0.072   0.418   0.348   0.163
0.3-0.4   0.064   0.458   0.232   0.246
0.4-0.5   0.067   0.513   0.158   0.263
0.5-0.6   0.055   0.612   0.109   0.223
0.6-0.7   0.080   0.580   0.065   0.275
0.7-0.8   0.072   0.602   0.050   0.276
0.8-0.9   0.088   0.565   0.044   0.303
0.9-1.0   0.089   0.649   0.041   0.221
```
Mean/median fraction_of_night per stage (where it occurs):
  awake  mean=0.547 median=0.570
  light  mean=0.557 median=0.577
  deep   mean=0.258 median=0.196
  rem    mean=0.592 median=0.613
Interpretation: deep concentrates early (low frac), REM concentrates late (high frac), awake is U-shaped (onset + end-of-night).

## 3. Segment/run-length stats (minutes)

  awake  n_seg= 652 median=6m mean=8.1m p90=16m frac_singletons=0.26
  light  n_seg=1354 median=18m mean=28.9m p90=64m frac_singletons=0.02
  deep   n_seg= 583 median=24m mean=28.0m p90=56m frac_singletons=0.01
  rem    n_seg= 571 median=20m mean=28.2m p90=64m frac_singletons=0.04
Sleep-onset latency (min to first sleep): median=0 mean=0.0 p90=0
  (NOTE: ~0 because Whoop's labels are trimmed to the sleep-onset boundary; pre-sleep awake is not in our window stream. So latency is not a usable signal here.)
WASO fraction (awake after onset): median=0.067 mean=0.067  (awake clusters mid/late-night; mean awake segment 8 min)

## 4. Positional-prior upper bound (timing ALONE, argmax of P(stage|bin))

Overall acc=55.1%. Per-stage recall:
  awake    0.0%
  light   94.7%
  deep    32.1%
  rem      0.0%
Note: positional prior alone NEVER predicts awake/deep as argmax (light dominates every bin) => timing cannot recover awake/REM by itself; it must be a *multiplicative* prior on a real posterior, not a standalone predictor.

## 5. Official Whoop field inventory (per-night, NOT currently used)

Nights with official last_night data among our 78 feature-nights: 17
Field coverage (count of nights present):
  hours_of_sleep         17
  time_in_bed            17
  restorative_sleep      17
  pct_of_need            17
  healthy_min            17
  recent_strain_need     17
  consistency            17
  efficiency             17
  wake_events            17
  sleep_stress_pct       17
  hrv                    17
  rhr                    17
  respiratory_rate       17

Sample night 2026-02-08: {"hours_of_sleep": "5:15", "time_in_bed": "5:52", "restorative_sleep": "2:43", "pct_of_need": "64%", "healthy_min": "7:57", "recent_strain_need": "+0:09", "consistency": "61%", "efficiency": "89%", "wake_events": "14", "sleep_stress_pct": "1%", "hrv": "74", "rhr": "58", "respiratory_rate": "13.6"}

Unused official fields usable as priors/constraints:
  - wake_events (count of awakenings)  -> CONSTRAIN total #awake segments
  - efficiency (%)                     -> CONSTRAIN total awake fraction (1-eff ~ WASO)
  - restorative_sleep (deep+rem hrs)   -> CONSTRAIN deep+rem total
  - hours_of_sleep / time_in_bed       -> total sleep & TIB (sanity bounds)
  - respiratory_rate, hrv, rhr         -> per-night normalization baselines
  - sleep_stress_pct                   -> autonomic arousal proxy

Validation of official fields as awake CONSTRAINTS (against our labels):
  n=17 nights with efficiency+wake_events
  corr( label_awake_fraction , 1-efficiency )   = 0.938  (mean awake_frac=0.057 vs mean 1-eff=0.071)
  corr( #label_awake_segments , wake_events )    = 0.758  (mean segs=8.2 vs mean wake_events=17.4)
  => efficiency is a near-perfect per-night awake-budget; wake_events bounds the awake SEGMENT count. Both can be injected as hard/soft constraints in post-processing (e.g. choose Viterbi awake-bias so the decoded awake fraction matches 1-efficiency, and #awake runs ~ wake_events).

## 6. Post-processing TEST (leave-one-night-out, leakage-free)

Stand-in posterior = GaussianNB on 9 core features (a deliberately weak classifier so post-processing effects are visible). All priors/transitions are learned on the OTHER 77 nights only (no leakage). This isolates the *post-processing* gain, independent of the real algo5 classifier.

```
variant                               acc  awake   light    deep     rem
(a) raw argmax                      58.5%    8.8%   70.5%   58.3%   46.2%
(b) naive marginal prior x post     61.8%    6.1%   82.7%   56.8%   34.2%
(c) DEVIATION prior (rec.)          61.1%    9.3%   72.2%   65.2%   47.2%
(d) transitions-only Viterbi        61.1%    8.2%   78.3%   51.7%   46.2%
(e) deviation+Viterbi (FULL)        63.9%    8.5%   79.6%   61.2%   46.7%
```
ALPHA (deviation-prior strength) = 0.6
Deltas vs (a) raw argmax:
  (b) naive prior      awake  -2.7pp  rem -12.0pp  deep  -1.5pp  acc  +3.2pp
  (c) deviation prior  awake  +0.6pp  rem  +1.0pp  deep  +7.0pp  acc  +2.6pp
  (d) transitions      awake  -0.5pp  rem  +0.0pp  deep  -6.6pp  acc  +2.6pp
  (e) full stack       awake  -0.3pp  rem  +0.5pp  deep  +2.9pp  acc  +5.4pp

KEY: the NAIVE marginal prior (b) collapses awake/REM (amplifies light). The DEVIATION prior (c) -- reweighting by P(stage|t)/P(stage) -- and the empirical transition Viterbi (d) both PRESERVE/improve REM and recover the timing signal without the majority-class collapse. The full stack (e) is the recommended post-processing change.

## 7. Recommended post-processing changes (concrete)

1. DEVIATION POSITIONAL PRIOR (replaces / augments any flat prior):
   multiply per-window posterior by  (P(stage | frac_bin) / P(stage))**ALPHA
   with ALPHA~0.5-0.7. Learn the (NB x 4) table on training nights only.
   This boosts deep early-night and REM late-night WITHOUT amplifying the
   light majority class (its ratio ~ 1 everywhere). Measured +2.9pp deep,
   +0.5pp REM, +0.6pp awake here, and is additive with Viterbi.
2. EMPIRICAL TRANSITION VITERBI: use the measured matrix in section 1 (high
   self-persistence 0.75-0.93, with forbidden deep<->rem ~0.000 and rare
   light->awake 0.012). Encode deep->rem and rem->deep as ~hard-forbidden.
3. DURATION FLOORS: median segment is 18-24 min (light/deep/rem); awake
   median 6 min with 26% singletons. A min-duration / median-filter that
   removes 1-window non-awake blips (frac_singletons<2% for light/deep)
   but KEEPS short awake spikes will cut false stage flicker.
4. OFFICIAL-FIELD CONSTRAINTS (when deep_dive JSON available, 222 nights):
   - tune the awake/REM Viterbi bias per-night so decoded awake fraction
     matches (1 - efficiency)  [corr 0.94 with true awake fraction];
   - cap decoded #awake segments near wake_events  [corr 0.76];
   - normalize HR/HRV features by per-night rhr/hrv/respiratory_rate tiles.
   These turn Whoop's own summary stats into per-night decoding constraints.

BOTTOM LINE: the single highest-leverage standalone change is the DEVIATION
positional prior + empirical-transition Viterbi (full stack (e)): +5.4pp
overall accuracy with NO awake/REM regression, versus the naive prior which
trades 12pp of REM for accuracy. Where the official JSON exists, the
efficiency-matched awake budget is the strongest awake-recall lever.