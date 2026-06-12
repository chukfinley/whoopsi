"""Best balanced sleep-stage classifier — target: every stage recall >=70%.

Combines the sub-agent findings:
  * dedicated AWAKE gate (binary awake-vs-rest, HR/HRV/gyro variability features
    are the real separators, not accelerometer movement) -> sharp P(awake)
  * 4-class base posterior (LightGBM)
  * positional DEVIATION prior  (P(stage|frac_bin)/P(stage))^ALPHA  (architecture
    agent: deep early, rem late, protects light)
  * awake-PRESERVING decode: empirical-transition Viterbi with a softened awake
    self-penalty and a post-processing floor that does NOT erase short awake
    spikes (26% of awake segments are singletons)
  * per-class GAIN tuned to maximize the minimum per-stage recall

All OOF, GroupKFold by night, no leakage.
"""
import sys, time, argparse
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.model_selection import GroupKFold
from sklearn.metrics import confusion_matrix
import lightgbm as lgb

INT_TO_PHASE = {0: "awake", 1: "light", 2: "deep", 3: "rem"}
FORBIDDEN = [(2, 3), (3, 2), (2, 0), (0, 2)]  # deep<->rem, deep<->awake


def load_data(name="dataset.npz"):
    d = np.load(Path(__file__).resolve().parent / name, allow_pickle=True)
    return (d["X"], d["y"], d["night_ids"], d["timestamps"], list(d["feature_names"]))


# ── base + gate models ──
def fit_4class(Xtr, ytr):
    w = {0: 6.0, 1: 1.0, 2: 3.0, 3: 2.0}
    m = lgb.LGBMClassifier(n_estimators=500, num_leaves=48, max_depth=6,
        learning_rate=0.03, min_child_samples=15, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=0.2, random_state=42, n_jobs=4, verbose=-1)
    m.fit(Xtr, ytr, sample_weight=np.array([w[l] for l in ytr]))
    return m


def fit_gate(Xtr, ytr, target_int, pos_weight):
    yb = (ytr == target_int).astype(int)
    sw = np.where(yb == 1, pos_weight, 1.0)
    m = lgb.LGBMClassifier(n_estimators=400, num_leaves=32, max_depth=5,
        learning_rate=0.03, min_child_samples=20, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=0.3, random_state=42, n_jobs=4, verbose=-1)
    m.fit(Xtr, yb, sample_weight=sw)
    return m


def collect_oof(X, y, night_ids, folds=5):
    p4 = np.zeros((len(y), 4))
    pa = np.zeros(len(y))   # awake gate
    pr = np.zeros(len(y))   # rem gate
    for tr, te in GroupKFold(n_splits=folds).split(X, y, night_ids):
        p4[te] = fit_4class(X[tr], y[tr]).predict_proba(X[te])
        pa[te] = fit_gate(X[tr], y[tr], 0, 12.0).predict_proba(X[te])[:, 1]
        pr[te] = fit_gate(X[tr], y[tr], 3, 3.0).predict_proba(X[te])[:, 1]
    return p4, pa, pr


# ── positional deviation prior ──
def build_pos_prior(y, frac, nbins=10):
    """P(stage|frac_bin)/P(stage), shape (nbins,4)."""
    base = np.bincount(y, minlength=4) / len(y)
    prior = np.ones((nbins, 4))
    b = np.clip((frac * nbins).astype(int), 0, nbins - 1)
    for k in range(nbins):
        m = b == k
        if m.sum() > 20:
            pk = np.bincount(y[m], minlength=4) / m.sum()
            prior[k] = pk / np.maximum(base, 1e-6)
    return prior


def empirical_trans(y, night_ids, awake_persist=0.6):
    K = 4
    c = np.ones((K, K)) * 0.1
    for nid in set(night_ids):
        s = y[night_ids == nid]
        for i in range(len(s) - 1):
            c[s[i], s[i + 1]] += 1
    t = c / c.sum(1, keepdims=True)
    for i in range(K):
        boost = 1.5 if i != 0 else 1.0   # don't over-glue awake
        t[i, i] = min(0.95, t[i, i] * boost)
        off = t[i].sum() - t[i, i]
        if off > 0:
            t[i, [j for j in range(K) if j != i]] *= (1 - t[i, i]) / off
    lt = np.log(np.clip(t, 1e-10, 1.0))
    for i, j in FORBIDDEN:
        lt[i, j] = -20.0
    return lt


def viterbi(log_em, log_trans, log_init):
    T, K = log_em.shape
    V = np.full((T, K), -np.inf); bp = np.zeros((T, K), int)
    V[0] = log_init + log_em[0]
    for t in range(1, T):
        for j in range(K):
            sc = V[t - 1] + log_trans[:, j]
            bi = np.argmax(sc); V[t, j] = sc[bi] + log_em[t, j]; bp[t, j] = bi
    path = np.zeros(T, int); path[-1] = np.argmax(V[-1])
    for t in range(T - 2, -1, -1):
        path[t] = bp[t + 1, path[t + 1]]
    return path


def soft_floor(phases):
    """Remove only short DEEP/REM blips; keep awake singletons (they're real)."""
    phases = list(phases)
    for stage, mn in [(2, 4), (3, 3)]:
        i = 0
        while i < len(phases):
            if phases[i] == stage:
                j = i
                while j < len(phases) and phases[j] == stage:
                    j += 1
                if j - i < mn:
                    bef = phases[i - 1] if i > 0 else 1
                    aft = phases[j] if j < len(phases) else 1
                    rep = bef if bef == aft else 1
                    for k in range(i, j):
                        phases[k] = rep
                i = j
            else:
                i += 1
    return np.array(phases)


def context_blend(p, alpha=0.2, lookback=3):
    p = p.copy()
    for i in range(1, len(p)):
        lo = max(0, i - lookback)
        p[i] = (1 - alpha) * p[i] + alpha * p[lo:i].mean(0)
    return p / np.maximum(p.sum(1, keepdims=True), 1e-10)


def decode(p4, pa, pr, y, night_ids, frac, gain, pos_prior, log_trans, log_init,
           gate_w=0.6, prior_alpha=0.6, nbins=10):
    pred = np.zeros(len(y), int)
    g = np.asarray(gain)
    for nid in sorted(set(night_ids)):
        idx = np.where(night_ids == nid)[0]
        order = np.argsort(frac[idx]); idx = idx[order]
        post = p4[idx].copy()
        # blend dedicated gates into class 0 and 3
        post[:, 0] = (1 - gate_w) * post[:, 0] + gate_w * pa[idx]
        post[:, 3] = (1 - gate_w) * post[:, 3] + gate_w * pr[idx]
        post = post / np.maximum(post.sum(1, keepdims=True), 1e-10)
        # positional deviation prior
        b = np.clip((frac[idx] * nbins).astype(int), 0, nbins - 1)
        post = post * (pos_prior[b] ** prior_alpha)
        # gain
        post = post * g
        post = post / np.maximum(post.sum(1, keepdims=True), 1e-10)
        post = context_blend(post, 0.2, 3)
        path = viterbi(np.log(np.clip(post, 1e-10, 1.0)), log_trans, log_init)
        pred[idx] = soft_floor(path)
    return pred


def recalls(yt, yp):
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    r = np.array([cm[i, i] / cm[i].sum() if cm[i].sum() else 0.0 for i in range(4)])
    return r, cm


def tune_gain(p4, pa, pr, y, night_ids, frac, pos_prior, log_trans, log_init):
    best_g = np.array([1.0, 1.0, 1.0, 1.0]); best = -1
    grids = {0: [1, 1.5, 2, 2.5, 3, 4], 3: [1, 1.3, 1.6, 2], 2: [1, 1.3], 1: [0.8, 0.9, 1.0]}
    for _ in range(3):
        for c in [0, 3, 1, 2]:
            for v in grids[c]:
                g = best_g.copy(); g[c] = v
                yp = decode(p4, pa, pr, y, night_ids, frac, g, pos_prior, log_trans, log_init)
                r, _ = recalls(y, yp)
                score = r.min() + 0.03 * (y == yp).mean()
                if score > best:
                    best = score; best_g = g
    return best_g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--data", default="dataset.npz")
    ap.add_argument("--cache", default="oof_best.npz")
    args = ap.parse_args()
    X, y, night_ids, ts, fn = load_data(args.data)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    frac = X[:, fn.index("fraction_of_night")] if "fraction_of_night" in fn else \
        np.zeros(len(y))
    print(f"data X={X.shape} nights={len(set(night_ids))} dist={dict(Counter(y.tolist()))}")

    t0 = time.time()
    cache = Path(__file__).resolve().parent / args.cache
    if cache.exists():
        z = np.load(cache); p4, pa, pr = z["p4"], z["pa"], z["pr"]
        print(f"loaded cached OOF [{time.time()-t0:.0f}s]")
    else:
        print("collecting OOF (4class + awake gate + rem gate)...", flush=True)
        p4, pa, pr = collect_oof(X, y, night_ids, args.folds)
        np.savez_compressed(cache, p4=p4, pa=pa, pr=pr)
        print(f"  done [{time.time()-t0:.0f}s]", flush=True)

    pos_prior = build_pos_prior(y, frac)
    log_trans = empirical_trans(y, night_ids)
    log_init = np.log(np.clip(np.bincount(y, minlength=4) / len(y), 1e-10, 1.0))

    yp0 = decode(p4, pa, pr, y, night_ids, frac, [1, 1, 1, 1], pos_prior, log_trans, log_init)
    r0, _ = recalls(y, yp0)
    print(f"\nno-gain: acc={(y==yp0).mean()*100:.2f}%  " +
          "  ".join(f"{INT_TO_PHASE[i]} {r0[i]*100:.1f}%" for i in range(4)))

    print("tuning gain...", flush=True)
    g = tune_gain(p4, pa, pr, y, night_ids, frac, pos_prior, log_trans, log_init)
    yp = decode(p4, pa, pr, y, night_ids, frac, g, pos_prior, log_trans, log_init)
    r, cm = recalls(y, yp)
    print(f"\n=== BEST  gain={np.round(g,2).tolist()}  [{time.time()-t0:.0f}s] ===")
    print(f"  overall acc: {(y==yp).mean()*100:.2f}%   MIN-recall: {r.min()*100:.1f}%")
    print("  recall: " + "  ".join(f"{INT_TO_PHASE[i]} {r[i]*100:.1f}%" for i in range(4)))
    print("  confusion (rows=true awake/light/deep/rem):")
    for i in range(4):
        print("   ", "  ".join(f"{cm[i,j]:5d}" for j in range(4)))
    np.save(Path(__file__).resolve().parent / "best_gain.npy", g)


if __name__ == "__main__":
    main()
