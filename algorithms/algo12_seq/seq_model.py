"""BiGRU sequence model for sleep-stage labeling, same CV recipe as bakeoff.

Each night = a sequence of (T, 115) window features. A bidirectional GRU labels
every window using full-night context (forward + backward), which is exactly
what a per-window GBT + short Viterbi cannot do. Compared head-to-head against
the GBT baseline on identical GroupKFold splits.

Eval variants reported:
  GRU-raw    : argmax of GRU posteriors
  GRU+Vit    : GRU posteriors -> context blend -> Viterbi -> physiological rules
"""
import sys, time, argparse
from pathlib import Path
import numpy as np
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch
import torch.nn as nn
from sklearn.model_selection import GroupKFold, LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import confusion_matrix

from eval_lono import viterbi_decode, learn_transition_matrix, context_blend_proba
from algo5_ml.engine import apply_post_processing

INT_TO_PHASE = {0: "awake", 1: "light", 2: "deep", 3: "rem"}
torch.manual_seed(42)
np.random.seed(42)


class BiGRU(nn.Module):
    def __init__(self, n_feat, hidden=96, layers=2, n_class=4, drop=0.3):
        super().__init__()
        self.gru = nn.GRU(n_feat, hidden, num_layers=layers, batch_first=True,
                          bidirectional=True, dropout=drop if layers > 1 else 0)
        self.head = nn.Sequential(
            nn.Linear(hidden * 2, hidden), nn.ReLU(), nn.Dropout(drop),
            nn.Linear(hidden, n_class))

    def forward(self, x):  # x: (1, T, F)
        h, _ = self.gru(x)
        return self.head(h)  # (1, T, C)


def load_data():
    d = np.load(Path(__file__).resolve().parent / "dataset.npz", allow_pickle=True)
    return d["X"], d["y"], d["night_ids"], d["timestamps"]


def night_sequences(X, y, night_ids, ts, nights):
    """Return list of (Xseq, yseq, idx_global) per night, ts-sorted."""
    seqs = []
    for nid in nights:
        idx = np.where(night_ids == nid)[0]
        idx = idx[np.argsort(ts[idx])]
        seqs.append((X[idx], y[idx], idx))
    return seqs


def train_gru(train_seqs, n_feat, class_w, epochs=60, lr=1.5e-3, device="cpu"):
    model = BiGRU(n_feat).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, epochs)
    cw = torch.tensor(class_w, dtype=torch.float32, device=device)
    lossf = nn.CrossEntropyLoss(weight=cw)
    order = list(range(len(train_seqs)))
    for ep in range(epochs):
        model.train()
        np.random.shuffle(order)
        for i in order:
            Xs, ys, _ = train_seqs[i]
            xt = torch.tensor(Xs, dtype=torch.float32, device=device).unsqueeze(0)
            yt = torch.tensor(ys, dtype=torch.long, device=device)
            opt.zero_grad()
            out = model(xt).squeeze(0)
            loss = lossf(out, yt)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
        sched.step()
    return model


@torch.no_grad()
def predict_proba_night(model, Xs, device="cpu"):
    model.eval()
    xt = torch.tensor(Xs, dtype=torch.float32, device=device).unsqueeze(0)
    logits = model(xt).squeeze(0)
    return torch.softmax(logits, dim=1).cpu().numpy()


def report(name, yt, yp, elapsed):
    acc = (yt == yp).mean()
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    rec = {INT_TO_PHASE[i]: (cm[i, i] / cm[i].sum() if cm[i].sum() else 0.0) for i in range(4)}
    print(f"\n=== {name}  [{elapsed:.0f}s] ===")
    print(f"  overall acc: {acc*100:.2f}%")
    print("  recall: " + "  ".join(f"{k} {v*100:.1f}%" for k, v in rec.items()))
    for i in range(4):
        print("   ", "  ".join(f"{cm[i,j]:5d}" for j in range(4)))
    return acc, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--awake-weight", type=float, default=3.0)
    args = ap.parse_args()

    X, y, night_ids, ts = load_data()
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    print(f"data: X={X.shape} nights={len(set(night_ids))} dist={dict(Counter(y.tolist()))}")

    # class weights: inverse-freq, awake boosted
    counts = np.bincount(y, minlength=4).astype(float)
    cw = counts.sum() / (4 * counts)
    cw[0] *= args.awake_weight / cw[0] * cw[0]  # keep inverse-freq, then scale awake
    cw = counts.sum() / (4 * counts)
    cw[0] *= args.awake_weight
    print(f"class weights: {np.round(cw,2)}")

    if args.full:
        splits = list(LeaveOneGroupOut().split(X, y, night_ids))
    else:
        splits = list(GroupKFold(n_splits=args.folds).split(X, y, night_ids))
    print(f"CV: {'LONO' if args.full else f'{args.folds}-fold'} ({len(splits)} folds)")

    yt_raw, yp_raw, yt_vit, yp_vit = [], [], [], []
    t0 = time.time()
    for fi, (tr, te) in enumerate(splits):
        scaler = StandardScaler().fit(X[tr])
        Xs = scaler.transform(X)
        train_nights = sorted(set(night_ids[tr]))
        test_nights = sorted(set(night_ids[te]))
        train_seqs = night_sequences(Xs, y, night_ids, ts, train_nights)
        model = train_gru(train_seqs, X.shape[1], cw, epochs=args.epochs)

        trans = learn_transition_matrix(y[tr], night_ids[tr])
        log_trans = np.log(np.clip(trans, 1e-10, 1.0))
        log_init = np.log(np.clip(np.bincount(y[tr], minlength=4) / len(tr), 1e-10, 1.0))

        for nid in test_nights:
            idx = np.where(night_ids == nid)[0]
            idx = idx[np.argsort(ts[idx])]
            proba = predict_proba_night(model, Xs[idx])
            yt_raw.extend(y[idx].tolist())
            yp_raw.extend(proba.argmax(1).tolist())
            pb = context_blend_proba(proba, alpha=0.2, lookback=3)
            path = viterbi_decode(np.log(np.clip(pb, 1e-10, 1.0)), log_trans, log_init)
            path = np.array(apply_post_processing(list(path), list(ts[idx])))
            yt_vit.extend(y[idx].tolist())
            yp_vit.extend(path.tolist())
        print(f"  fold {fi+1}/{len(splits)} done [{time.time()-t0:.0f}s]", flush=True)

    r1 = report("GRU-raw", np.array(yt_raw), np.array(yp_raw), time.time() - t0)
    r2 = report("GRU+Vit", np.array(yt_vit), np.array(yp_vit), time.time() - t0)
    print("\n========== SUMMARY ==========")
    for name, (acc, rec) in [("GRU-raw", r1), ("GRU+Vit", r2)]:
        print(f"  {name:10s} acc={acc*100:.2f}%  awake_rec={rec['awake']*100:.1f}%")


if __name__ == "__main__":
    main()
