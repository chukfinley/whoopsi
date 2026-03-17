# Sleep Staging Model Research Log

## Baseline (v3, pre-improvements)
- **LONO Accuracy: 70.4%** | MAE: 5.3 (LONO), 7.2 (full model)
- 48 features, max_depth=4, no Viterbi, simple smoothing
- Awake: 28.8% | Light: 73.2% | Deep: 78.7% | REM: 68.9%

## Experiment Results (Mar 17, 2026)

### Experiment Runner Results (experiment_runner.py)
| Configuration | LONO | MAE | Awake | Light | Deep | REM |
|---|---|---|---|---|---|---|
| Baseline (48 feat, no Viterbi) | 67.4% | 6.7 | 26.0% | 72.6% | 70.9% | 65.9% |
| Baseline + Viterbi | 70.1% | 6.8 | 21.4% | 78.5% | 69.9% | 68.1% |
| Extended (58 feat) + Viterbi | 70.8% | 6.6 | 19.8% | 77.7% | 71.8% | 71.2% |
| Extended + Viterbi + no class_weight | 70.0% | 8.2 | 16.0% | 83.1% | 66.0% | 62.0% |
| **Best: d3_lr0.05_i500_msl10 + Viterbi** | **71.0%** | **6.1** | **29.0%** | 73.8% | 75.4% | **74.2%** |
| Best + no class_weight | 70.2% | 8.2 | 14.7% | 82.5% | 68.6% | 63.8% |

### Hyperparameter Grid Search Results
| Config | Accuracy | MAE | Awake | Notes |
|---|---|---|---|---|
| d3_lr0.05_i500_msl10 | **71.0%** | 6.1 | 29% | **Best** |
| d4_lr0.05_i500_msl10 | 70.8% | 6.6 | 20% | Original config |
| d3_lr0.1_i500_msl5 | 70.8% | 6.3 | 26% | Fast, good |
| d4_lr0.05_i800_msl10 | 70.8% | 6.7 | 23% | More iters doesn't help |
| d4_lr0.05_i500_msl5 | 70.6% | 6.5 | 24% | |
| d4_lr0.05_i500_msl20 | 70.6% | 6.5 | 23% | |
| d4_lr0.1_i300_msl10 | 70.4% | 6.9 | 22% | |
| d5_lr0.05_i500_msl10 | 70.2% | 7.2 | 20% | Deeper = worse |
| d5_lr0.05_i800_msl5 | 70.0% | 7.3 | 23% | |
| d4_lr0.02_i800_msl10 | 69.9% | 6.3 | 24% | |
| d5_lr0.02_i800_msl10 | 69.7% | 7.2 | 19% | |
| d6_lr0.05_i500_msl10 | 69.2% | 7.7 | 18% | Way too deep |

### Key Findings
1. **Viterbi adds +2.7% accuracy** (67.4% -> 70.1%) - significant improvement
2. **Extended features add +0.7%** on top of Viterbi (70.1% -> 70.8%)
3. **max_depth=3 is optimal** - deeper trees overfit to 15-night dataset
4. **class_weight="balanced" essential** for awake recall (14.7% -> 29.0%)
5. **Awake recall remains the bottleneck** at ~29% - fundamental issue with
   only 7% of data being awake (524/7476 windows)

## v4 Final Results (committed)
- **LONO Accuracy: 72.0%** (+1.6pp from v3)
- 58 features, max_depth=3, Viterbi, sleep onset/offset detection
- Awake: 26.7% | Light: 72.5% | Deep: 78.9% | REM: 77.2% (+8.3pp)
- MAE: 6.5 (LONO)

## Worst Nights Analysis
| Night | MAE | Acc | Windows | Awake% | Issue |
|---|---|---|---|---|---|
| 2026-01-31 | 15.2 | 57.5% | 497 | 9% | Heavy REM over-prediction (47.7% vs 17.3%) |
| 2026-01-29 | 12.3 | 70.8% | 483 | 9% | Deep under-predicted, Light over-predicted |
| 2026-02-11 | 9.0 | 73.8% | 450 | 7% | Light over-predicted, REM under-predicted |
| 2026-02-10 | 8.5 | 69.7% | 581 | 9% | REM over-predicted |
| 2026-02-09 | 7.4 | 69.9% | 929 | 8% | Long night (15.5h), deep over-predicted |

### Systematic Bias
The model tends to:
- Over-predict REM (especially on nights where Whoop says low REM)
- Under-predict awake (Viterbi makes this worse by smoothing away short awake bouts)
- Over-predict deep on some nights, under-predict on others

## Viterbi Temperature Experiment
| Temperature | Accuracy | MAE | Awake | Light | Deep | REM |
|---|---|---|---|---|---|---|
| 0.5 | 70.6% | 5.7 | **40.3%** | 71.6% | 75.9% | 72.3% |
| 0.6 | 70.8% | 5.9 | 37% | 72% | 75% | 73% |
| **0.7** | **71.0%** | **5.9** | **36%** | 73% | 75% | 73% |
| 0.8 | 70.9% | 6.0 | 33% | 73% | 75% | 73% |
| 1.0 | 71.0% | 6.1 | 29% | 74% | 75% | 74% |
| 1.5 | 70.7% | - | 21% | 75% | 75% | 74% |
| 2.0 | 70.3% | - | 16% | 78% | 72% | 72% |

Lower temperature = sharper emission probs = more state transitions = better awake recall.
Selected temp=0.7 as sweet spot: same accuracy as temp=1.0 but +7pp awake recall.

## v4 Final Results (committed)
- **LONO Accuracy: 72.1%** (+1.7pp from v3 baseline 70.4%)
- 58 features, max_depth=3, Viterbi (temp=0.7), sleep onset/offset detection
- Awake: 30.9% (+2.1pp) | Light: 72.1% | Deep: 79.2% | REM: 77.3% (+8.4pp)
- LONO MAE: 6.3

## Next Steps
- [ ] Try gradient-boosted transition matrix (different weights for different times of night)
- [ ] Augment awake class with synthetic examples
- [ ] Try ensemble of depth=3 and depth=4 models
- [ ] Try XGBoost/LightGBM as alternative
- [ ] More data (currently only 15 nights)
