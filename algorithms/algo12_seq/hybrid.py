"""Hybrid: algo5's sleep-stage structure (it wins on light/deep/rem) +
algo12's awake gate (it wins on awake). Start from algo5's full prediction,
override with AWAKE wherever the algo12 awake-gate is confident. Best of both.

Sweeps the awake-gate threshold and an awake min-run, reports per-stage recall.
"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sklearn.metrics import confusion_matrix

HERE = Path(__file__).resolve().parent
INT2 = {0: "awake", 1: "light", 2: "deep", 3: "rem"}


def load():
    d = np.load(HERE / "dataset_aug.npz", allow_pickle=True)
    y, nid, ts = d["y"], d["night_ids"], d["timestamps"]
    z = np.load(HERE / "oof_v2.npz")
    pa, pr, p4 = z["pa"], z["pr"], z["p4"]
    a5 = np.load(HERE / "algo5_pred.npy")
    return y, nid, ts, pa, pr, p4, a5


def recalls(yt, yp):
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    r = np.array([cm[i, i] / cm[i].sum() if cm[i].sum() else 0.0 for i in range(4)])
    return r, cm


def run_min(mask, min_run):
    """keep awake runs >= min_run (per night handled by caller via contiguous)."""
    if min_run <= 1:
        return mask
    m = mask.copy(); i = 0
    while i < len(m):
        if m[i]:
            j = i
            while j < len(m) and m[j]:
                j += 1
            if j - i < min_run:
                m[i:j] = False
            i = j
        else:
            i += 1
    return m


def awake_bridge(seq, L):
    """Relabel any non-awake run of length <=L to awake if flanked by awake
    on both sides (recovers restless-awake pockets the gate drops)."""
    if L <= 0:
        return seq
    seq = seq.copy(); i = 0
    while i < len(seq):
        if seq[i] != 0:
            j = i
            while j < len(seq) and seq[j] != 0:
                j += 1
            if (j - i) <= L and i > 0 and j < len(seq) and seq[i-1] == 0 and seq[j] == 0:
                seq[i:j] = 0
            i = j
        else:
            i += 1
    return seq


def hybrid_pred(a5, pa, pr, y, nid, ts, tau_a, min_run=1, rem_protect=0.60, bridge=0):
    """Override algo5 with awake where pa>=tau_a, BUT to override an algo5-REM
    window require pa>=rem_protect (REM HR looks awake-ish; protect it)."""
    pred = a5.copy()
    for n in sorted(set(nid)):
        idx = np.where(nid == n)[0]; idx = idx[np.argsort(ts[idx])]
        sub = pred[idx]
        awake = pa[idx] >= tau_a
        # protect algo5-rem windows unless the gate is very confident
        is_rem = sub == 3
        awake = awake & (~is_rem | (pa[idx] >= rem_protect))
        awake = run_min(awake, min_run)
        sub[awake] = 0
        if bridge:
            sub = awake_bridge(sub, bridge)
        pred[idx] = sub
    return pred


def main():
    y, nid, ts, pa, pr, p4, a5 = load()
    print("baselines on same 78 nights:")
    for name, p in [("algo5", a5)]:
        r, _ = recalls(y, p)
        print(f"  {name}: acc={(y==p).mean()*100:.1f}%  " +
              "  ".join(f"{INT2[i]} {r[i]*100:.0f}%" for i in range(4)))

    print("\nHYBRID = algo5 + algo12 awake-gate override:")
    best = None
    for tau in [0.22, 0.25, 0.28, 0.30, 0.32, 0.35]:
        for rp in [0.45, 0.55, 0.65]:
          for mr in [1]:
            p = hybrid_pred(a5, pa, pr, y, nid, ts, tau, mr, rp)
            r, cm = recalls(y, p)
            acc = (y == p).mean()
            mn = r.min()
            mark = "  <<<" if mn >= 0.70 else ""
            print(f"  tau={tau:.2f} rp={rp:.2f}  acc={acc*100:.1f}%  min={mn*100:.1f}%  | "
                  + " ".join(f"{INT2[i][0].upper()}{r[i]*100:.0f}" for i in range(4)) + mark)
            if best is None or (mn, acc) > (best[0], best[1]):
                best = (mn, acc, tau, mr, r, cm, p)
    mn, acc, tau, mr, r, cm, p = best
    print(f"\n=== BEST HYBRID tau={tau:.2f} minrun={mr}  acc={acc*100:.2f}%  min={mn*100:.1f}% ===")
    print("  " + "  ".join(f"{INT2[i]} {r[i]*100:.1f}%" for i in range(4)))

    print("\n+ awake-bridge (fill restless-awake pockets dropped to REM):")
    chosen = (p, 0)
    for L in [0, 1, 2, 3]:
        pb = hybrid_pred(a5, pa, pr, y, nid, ts, tau, mr, 0.55, bridge=L)
        rb, _ = recalls(y, pb)
        accb = (y == pb).mean()
        keep = rb.min() >= 0.70
        print(f"  bridge L={L}  acc={accb*100:.2f}%  min={rb.min()*100:.1f}%  | "
              + " ".join(f"{INT2[i][0].upper()}{rb[i]*100:.0f}" for i in range(4))
              + ("  <<<keeps all>=70" if keep else ""))
        if keep and L > chosen[1]:
            chosen = (pb, L)
    p, L = chosen
    rf, cmf = recalls(y, p)
    print(f"\n=== FINAL HYBRID tau={tau:.2f} bridge L={L}  acc={(y==p).mean()*100:.2f}%  min={rf.min()*100:.1f}% ===")
    print("  " + "  ".join(f"{INT2[i]} {rf[i]*100:.1f}%" for i in range(4)))
    for i in range(4):
        print("   ", "  ".join(f"{cmf[i,j]:5d}" for j in range(4)))
    np.save(HERE / "hybrid_pred.npy", p)
    print("saved hybrid_pred.npy")


if __name__ == "__main__":
    main()
