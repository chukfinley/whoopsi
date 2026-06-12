import sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.db_loader import load_from_db as load_sensor_db
from algo5_ml.features import build_training_data, FEATURE_NAMES

t0=time.time()
print("loading sensor DB...", flush=True)
df = load_sensor_db()
print(f"  rows={len(df)}  [{time.time()-t0:.0f}s]", flush=True)
print("building windows...", flush=True)
X,y,nid,dates,ts = build_training_data(df, overlap=True)
print(f"  X={X.shape} nights={len(dates)} [{time.time()-t0:.0f}s]", flush=True)
import collections
print("class dist:", collections.Counter(y.tolist()))
out="algo12_seq/dataset.npz"
np.savez_compressed(out, X=X, y=y, night_ids=nid, timestamps=ts,
                    dates=np.array(dates), feature_names=np.array(FEATURE_NAMES))
print("saved", out, f"[{time.time()-t0:.0f}s]")
