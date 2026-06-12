"""Hierarchical decode — the fix. The awake gate has AUC 0.914 (awake 86% @
light 80%) but the old 4-class blend+Viterbi crushed it to ~65%. Here the gate
decides awake FIRST (threshold tuned), then a 3-state Viterbi labels the
remaining windows light/deep/rem. Awake is never re-absorbed.

Reuses cached OOF (oof_best_aug.npz: p4, pa awake-gate, pr rem-gate).
"""
import sys, time
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sklearn.metrics import confusion_matrix

from best import (load_data, build_pos_prior, viterbi, context_blend, INT_TO_PHASE)

HERE = Path(__file__).resolve().parent
FORBIDDEN3 = [(1, 2), (2, 1)]  # in 3-state {0:light,1:deep,2:rem}: deep<->rem forbidden


def trans3(y, night_ids):
    """3-state (light/deep/rem) transition from non-awake labels."""
    remap = {1: 0, 2: 1, 3: 2}
    c = np.ones((3, 3)) * 0.1
    for nid in set(night_ids):
        s = y[night_ids == nid]
        s = [remap[v] for v in s if v in remap]
        for i in range(len(s) - 1):
            c[s[i], s[i + 1]] += 1
    t = c / c.sum(1, keepdims=True)
    for i in range(3):
        t[i, i] = min(0.96, t[i, i] * 1.5)
        off = t[i].sum() - t[i, i]
        if off > 0:
            t[i, [j for j in range(3) if j != i]] *= (1 - t[i, i]) / off
    lt = np.log(np.clip(t, 1e-10, 1.0))
    lt[1, 2] = lt[2, 1] = -20.0  # deep<->rem forbidden
    return lt


def smooth_awake(mask, min_run=1):
    """Optionally drop awake runs shorter than min_run (default keep singletons)."""
    if min_run <= 1:
        return mask
    mask = mask.copy(); i = 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            if j - i < min_run:
                mask[i:j] = False
            i = j
        else:
            i += 1
    return mask


def floor3(lab3, mn_deep=4, mn_rem=3):
    lab3 = list(lab3)
    for stage, mn in [(1, mn_deep), (2, mn_rem)]:
        i = 0
        while i < len(lab3):
            if lab3[i] == stage:
                j = i
                while j < len(lab3) and lab3[j] == stage:
                    j += 1
                if j - i < mn:
                    bef = lab3[i - 1] if i > 0 else 0
                    aft = lab3[j] if j < len(lab3) else 0
                    rep = bef if bef == aft else 0
                    for k in range(i, j):
                        lab3[k] = rep
                i = j
            else:
                i += 1
    return np.array(lab3)


def decode(p4, pa, pr, y, night_ids, frac, tau, pos_prior, lt3, log_init3,
           gate_w=0.6, prior_alpha=0.6, nbins=10, awake_min_run=1):
    pred = np.zeros(len(y), int)
    inv = {0: 1, 1: 2, 2: 3}
    for nid in sorted(set(night_ids)):
        idx = np.where(night_ids == nid)[0]
        idx = idx[np.argsort(frac[idx])]
        awake = pa[idx] >= tau
        awake = smooth_awake(awake, awake_min_run)
        # 3-class posterior for light/deep/rem
        post = p4[idx][:, 1:4].copy()
        post[:, 2] = (1 - gate_w) * post[:, 2] + gate_w * pr[idx]  # rem gate
        post = post / np.maximum(post.sum(1, keepdims=True), 1e-10)
        b = np.clip((frac[idx] * nbins).astype(int), 0, nbins - 1)
        pp = pos_prior[b][:, 1:4]
        post = post * (pp ** prior_alpha)
        post = post / np.maximum(post.sum(1, keepdims=True), 1e-10)
        post = context_blend(post, 0.2, 3)
        path3 = viterbi(np.log(np.clip(post, 1e-10, 1.0)), lt3, log_init3)
        path3 = floor3(path3)
        out = np.array([inv[s] for s in path3])
        out[awake] = 0
        pred[idx] = out
    return pred


def recalls(yt, yp):
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    r = np.array([cm[i, i] / cm[i].sum() if cm[i].sum() else 0.0 for i in range(4)])
    return r, cm


def main():
    X, y, night_ids, ts, fn = load_data("dataset_aug.npz")
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    frac = X[:, fn.index("fraction_of_night")]
    z = np.load(HERE / "oof_best_aug.npz")
    p4, pa, pr = z["p4"], z["pa"], z["pr"]
    pos_prior = build_pos_prior(y, frac)
    lt3 = trans3(y, night_ids)
    init3 = np.bincount([{1:0,2:1,3:2}[v] for v in y if v != 0], minlength=3).astype(float)
    log_init3 = np.log(np.clip(init3 / init3.sum(), 1e-10, 1.0))

    print(f"nights={len(set(night_ids))} dist={dict(Counter(y.tolist()))}")
    print("sweep awake-gate threshold tau:")
    best = None
    for tau in [0.10, 0.13, 0.16, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
        yp = decode(p4, pa, pr, y, night_ids, frac, tau, pos_prior, lt3, log_init3)
        r, cm = recalls(y, yp)
        acc = (y == yp).mean()
        tag = "  <<<" if r.min() >= 0.70 else ""
        print(f"  tau={tau:.2f}  acc={acc*100:.1f}%  min={r.min()*100:.1f}%  | "
              + " ".join(f"{INT_TO_PHASE[i][0].upper()}{r[i]*100:.0f}" for i in range(4)) + tag)
        if best is None or r.min() > best[1]:
            best = (tau, r.min(), r, cm, acc)
    tau, mn, r, cm, acc = best
    print(f"\n=== BEST tau={tau:.2f}  acc={acc*100:.2f}%  MIN-recall={mn*100:.1f}% ===")
    print("  " + "  ".join(f"{INT_TO_PHASE[i]} {r[i]*100:.1f}%" for i in range(4)))
    print("  confusion (rows=true awake/light/deep/rem):")
    for i in range(4):
        print("   ", "  ".join(f"{cm[i,j]:5d}" for j in range(4)))


if __name__ == "__main__":
    main()
