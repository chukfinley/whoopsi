"""Cascade decode: awake gate -> rem gate -> {light,deep} Viterbi.
Both agents proved the gates (awake AUC 0.914, rem AUC 0.885) beat argmax.
Hard-assign awake then rem by tuned thresholds; light/deep settled by a
2-state Viterbi with positional prior + duration floors. Tune (tau_a, tau_r)
jointly to lift the MINIMUM per-stage recall.
"""
import sys
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sklearn.metrics import confusion_matrix
from best import load_data, build_pos_prior, viterbi, context_blend, INT_TO_PHASE

HERE = Path(__file__).resolve().parent


def trans_ld(y, night_ids):
    """2-state light(0)/deep(1) transitions from non-awake-non-rem labels."""
    c = np.ones((2, 2)) * 0.1
    remap = {1: 0, 2: 1}
    for nid in set(night_ids):
        s = [remap[v] for v in y[night_ids == nid] if v in remap]
        for i in range(len(s) - 1):
            c[s[i], s[i + 1]] += 1
    t = c / c.sum(1, keepdims=True)
    for i in range(2):
        t[i, i] = min(0.97, t[i, i] * 1.4)
        t[i, 1 - i] = 1 - t[i, i]
    return np.log(np.clip(t, 1e-10, 1.0))


def floor_run(lab, val, mn):
    lab = list(lab); i = 0
    while i < len(lab):
        if lab[i] == val:
            j = i
            while j < len(lab) and lab[j] == val:
                j += 1
            if j - i < mn:
                bef = lab[i - 1] if i > 0 else (1 if val != 1 else 0)
                for k in range(i, j):
                    lab[k] = bef
            i = j
        else:
            i += 1
    return np.array(lab)


def decode(p4, pa, pr, y, night_ids, frac, tau_a, tau_r, pos_prior, lt_ld,
           log_init_ld, prior_alpha=0.6, nbins=10):
    pred = np.zeros(len(y), int)
    for nid in sorted(set(night_ids)):
        idx = np.where(night_ids == nid)[0]
        idx = idx[np.argsort(frac[idx])]
        n = len(idx)
        awake = pa[idx] >= tau_a
        rem = (~awake) & (pr[idx] >= tau_r)
        out = np.full(n, -1)
        out[awake] = 0
        out[rem] = 3
        rest = out == -1
        if rest.any():
            ri = np.where(rest)[0]
            post = p4[idx[ri]][:, 1:3].copy()  # light, deep
            post = post / np.maximum(post.sum(1, keepdims=True), 1e-10)
            b = np.clip((frac[idx[ri]] * nbins).astype(int), 0, nbins - 1)
            pp = pos_prior[b][:, 1:3]
            post = post * (pp ** prior_alpha)
            post = post / np.maximum(post.sum(1, keepdims=True), 1e-10)
            post = context_blend(post, 0.2, 3)
            path = viterbi(np.log(np.clip(post, 1e-10, 1.0)), lt_ld, log_init_ld)
            for k, p in zip(ri, path):
                out[k] = 1 if p == 0 else 2
        # duration floors (keep awake singletons; clean deep/rem blips)
        out = floor_run(out, 2, 4)   # deep
        out = floor_run(out, 3, 3)   # rem
        pred[idx] = out
    return pred


def recalls(yt, yp):
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    r = np.array([cm[i, i] / cm[i].sum() if cm[i].sum() else 0.0 for i in range(4)])
    return r, cm


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="oof_best_aug.npz")
    args = ap.parse_args()
    X, y, night_ids, ts, fn = load_data("dataset_aug.npz")
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    frac = X[:, fn.index("fraction_of_night")]
    z = np.load(HERE / args.cache)
    p4, pa, pr = z["p4"], z["pa"], z["pr"]
    pos_prior = build_pos_prior(y, frac)
    lt_ld = trans_ld(y, night_ids)
    init = np.bincount([{1:0,2:1}[v] for v in y if v in (1,2)], minlength=2).astype(float)
    log_init_ld = np.log(np.clip(init / init.sum(), 1e-10, 1.0))
    print(f"nights={len(set(night_ids))} dist={dict(Counter(y.tolist()))}")

    best = None
    feasible = []
    for ta in [0.40, 0.45, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.63, 0.66]:
        for tr in [0.30, 0.33, 0.36, 0.40, 0.43, 0.46, 0.50, 0.55]:
            yp = decode(p4, pa, pr, y, night_ids, frac, ta, tr, pos_prior, lt_ld, log_init_ld)
            r, cm = recalls(y, yp)
            acc = (y == yp).mean()
            mn = r.min()
            if best is None or mn > best[0]:
                best = (mn, ta, tr, r, cm, acc)
            if mn >= 0.70:
                feasible.append((mn, acc, ta, tr, r))
    mn, ta, tr, r, cm, acc = best
    print(f"\n=== BEST  tau_a={ta:.2f} tau_r={tr:.2f}  acc={acc*100:.2f}%  MIN={mn*100:.1f}% ===")
    print("  " + "  ".join(f"{INT_TO_PHASE[i]} {r[i]*100:.1f}%" for i in range(4)))
    for i in range(4):
        print("   ", "  ".join(f"{cm[i,j]:5d}" for j in range(4)))
    if feasible:
        feasible.sort(key=lambda x: -x[1])  # by accuracy among all-≥70
        print(f"\n{len(feasible)} configs with ALL stages >=70%. Top by accuracy:")
        for mn, acc, ta, tr, r in feasible[:5]:
            print(f"  tau_a={ta:.2f} tau_r={tr:.2f} acc={acc*100:.1f}% min={mn*100:.1f}% | "
                  + " ".join(f"{INT_TO_PHASE[i][0].upper()}{r[i]*100:.0f}" for i in range(4)))
    else:
        print("\nNo config reached all-four >=70%.")


if __name__ == "__main__":
    main()
