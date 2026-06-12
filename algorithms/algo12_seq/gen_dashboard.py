"""Generic comparison dashboard for ALL sleep-stage methods on our 78 nights.

Each method = a per-window int prediction array (0/1/2/3) aligned to
dataset.npz row order, saved as algo12_seq/preds/<name>.npy. This script
auto-discovers every preds/*.npy, computes per-stage recall + overall acc,
renders a comparison table and per-night hypnograms (one strip per method +
the Whoop ground-truth strip).

Register display order / labels in METHODS below; any preds/*.npy not listed
is appended automatically.
"""
import sys, json
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sklearn.metrics import confusion_matrix

HERE = Path(__file__).resolve().parent
PREDS = HERE / "preds"
INT2 = {0: "awake", 1: "light", 2: "deep", 3: "rem"}

# display order + friendly labels; headline = first that exists
METHODS = [
    ("hybrid", "hybrid (algo5+algo12 awake)"),
    ("algo12", "algo12 (cascade)"),
    ("algo5", "algo5 (HistGBT+Viterbi)"),
    ("sleepecg", "SleepECG (HRV)"),
    ("yasa_hrv", "YASA-HRV port"),
    ("kotzen_lstm", "Kotzen HR+actigraphy"),
    ("walch", "Walch HR+clock"),
]


def recalls(yt, yp):
    cm = confusion_matrix(yt, yp, labels=[0, 1, 2, 3])
    r = np.array([cm[i, i] / cm[i].sum() if cm[i].sum() else 0.0 for i in range(4)])
    return r, cm


def main():
    d = np.load(HERE / "dataset.npz", allow_pickle=True)
    y, night_ids, ts = d["y"], d["night_ids"], d["timestamps"]
    dates = list(d["dates"])

    # discover predictions
    found = {}
    if PREDS.exists():
        for f in PREDS.glob("*.npy"):
            arr = np.load(f)
            if len(arr) == len(y):
                found[f.stem] = arr
    # legacy locations (back-compat)
    for nm, fn in [("algo5", "algo5_pred.npy"), ("hybrid", "hybrid_pred.npy")]:
        if nm not in found and (HERE / fn).exists():
            a = np.load(HERE / fn)
            if len(a) == len(y):
                found[nm] = a

    ordered = [(k, lbl) for k, lbl in METHODS if k in found]
    extra = [(k, k) for k in found if k not in dict(METHODS)]
    ordered += extra
    if not ordered:
        print("no predictions found in", PREDS); return

    methods = []
    for key, label in ordered:
        yp = found[key]
        r, cm = recalls(y, yp)
        methods.append({"key": key, "label": label,
                        "acc": round(float((y == yp).mean()) * 100, 1),
                        "minrec": round(float(r.min()) * 100, 1),
                        "recalls": {INT2[i]: round(float(r[i]) * 100, 1) for i in range(4)},
                        "cm": cm.tolist()})

    head_key = ordered[0][0]
    nights = []
    for k, nid in enumerate(sorted(set(night_ids))):
        idx = np.where(night_ids == nid)[0]
        idx = idx[np.argsort(ts[idx])]
        nacc = float((y[idx] == found[head_key][idx]).mean())
        n = {"date": dates[k] if k < len(dates) else f"night{nid}",
             "acc": round(nacc * 100, 1), "n": int(len(idx)),
             "true": y[idx].astype(int).tolist()}
        for key, _ in ordered:
            n[key] = found[key][idx].astype(int).tolist()
        nights.append(n)
    nights.sort(key=lambda x: -x["acc"])

    data = {"methods": methods, "order": [k for k, _ in ordered],
            "labels": {k: l for k, l in ordered},
            "head": head_key, "n_nights": len(nights),
            "n_windows": int(len(y)), "nights": nights}
    (HERE / "dashboard.html").write_text(TEMPLATE.replace("__DATA__", json.dumps(data)))
    print("wrote dashboard.html with methods:", [k for k, _ in ordered])


TEMPLATE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>Sleep-stage method comparison</title>
<style>
:root{--awake:#e8574a;--light:#5b9bd5;--deep:#2c3e8c;--rem:#8e5bd5;--bg:#0f1117;--card:#1a1d27;--tx:#e6e8ee;--mut:#9aa0ad}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:22px}
.cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}
.kpi{background:var(--card);border-radius:12px;padding:14px 18px;min-width:110px}
.kpi .v{font-size:26px;font-weight:700}.kpi .l{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.kpi.ok .v{color:#46c98b}
.legend{display:flex;gap:16px;margin:6px 0 18px;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px;color:var(--mut)}
.sw{width:14px;height:14px;border-radius:3px;display:inline-block}
table{border-collapse:collapse;margin:8px 0 22px;font-size:13px}td,th{padding:6px 11px;text-align:center;border:1px solid #2a2e3a}
th{color:var(--mut);font-weight:600}.diag{background:#1f3a2e;color:#46c98b;font-weight:700}
.bad{color:#e8574a}
.night{background:var(--card);border-radius:10px;padding:11px 13px;margin-bottom:11px}
.night h3{margin:0 0 7px;font-size:13px;font-weight:600;display:flex;justify-content:space-between}
.night .acc{color:var(--mut);font-weight:400}
.hyp{display:flex;align-items:center;gap:9px;margin:2px 0}
.hyp .tag{width:120px;color:var(--mut);font-size:11px;text-align:right;white-space:nowrap;overflow:hidden}
.hyp.truth .tag{color:#e6e8ee;font-weight:700}
canvas{border-radius:3px;image-rendering:pixelated;width:100%;height:22px}
small{color:var(--mut)}
.controls{margin:10px 0}select{background:var(--card);color:var(--tx);border:1px solid #2a2e3a;border-radius:6px;padding:5px 8px}
</style></head><body><div class=wrap>
<h1>Sleep-stage method comparison</h1>
<div class=sub id=sub></div>
<div class=cards id=kpis></div>
<div class=legend>
<span><i class=sw style="background:var(--awake)"></i>Awake</span>
<span><i class=sw style="background:var(--light)"></i>Light</span>
<span><i class=sw style="background:var(--deep)"></i>Deep</span>
<span><i class=sw style="background:var(--rem)"></i>REM</span></div>
<h3 style="font-size:15px">All methods — recall per stage (same 78 nights, out-of-fold)</h3>
<div id=cmp></div>
<div class=controls>Sort nights by: <select id=sortby></select></div>
<h3 style="font-size:15px">Per-night hypnograms <small>(Whoop = ground truth, bold)</small></h3>
<div id=nights></div>
</div>
<script>
const D=__DATA__;
const COL={0:'#e8574a',1:'#5b9bd5',2:'#2c3e8c',3:'#8e5bd5'};
const ORDER=['awake','light','deep','rem'];
const head=D.methods.find(m=>m.key===D.head)||D.methods[0];
document.getElementById('sub').textContent=
 `${D.methods.length} methods · ${D.n_nights} nights · ${D.n_windows.toLocaleString()} windows · headline: ${head.label}`;
const kp=document.getElementById('kpis');
const card=(l,v,ok)=>`<div class="kpi ${ok?'ok':''}"><div class=v>${v}</div><div class=l>${l}</div></div>`;
kp.innerHTML=card('Overall',head.acc+'%',false)+card('Min recall',head.minrec+'%',head.minrec>=70)+
 ORDER.map(s=>card(s,head.recalls[s]+'%',head.recalls[s]>=70)).join('');
// comparison table
const cell=v=>`<td class="${v>=70?'diag':(v<50?'bad':'')}">${v}%</td>`;
let t='<table><tr><th>method</th><th>overall</th><th>min</th>'+ORDER.map(s=>`<th>${s}</th>`).join('')+'</tr>';
D.methods.forEach(m=>{t+=`<tr><th style="text-align:left">${m.label}</th><td>${m.acc}%</td>${cell(m.minrec)}`+
 ORDER.map(s=>cell(m.recalls[s])).join('')+'</tr>'});
t+='</table><small>Green ≥70%, red &lt;50%. Out-of-fold (5-fold GroupKFold by night).</small>';
document.getElementById('cmp').innerHTML=t;
// sort control
const sb=document.getElementById('sortby');
sb.innerHTML='<option value="head">'+head.label+' acc</option>'+
 D.order.map(k=>`<option value="${k}">${D.labels[k]} acc</option>`).join('')+'<option value="date">date</option>';
function acc(n,key){let c=0;for(let i=0;i<n.true.length;i++)if(n[key][i]===n.true[i])c++;return c/n.true.length}
function draw(arr){const c=document.createElement('canvas');c.width=arr.length;c.height=4;
 const x=c.getContext('2d');arr.forEach((v,i)=>{x.fillStyle=COL[v];x.fillRect(i,0,1,4)});return c}
function strip(tag,arr,truth){const r=document.createElement('div');r.className='hyp'+(truth?' truth':'');
 r.innerHTML=`<span class=tag title="${tag}">${tag}</span>`;r.appendChild(draw(arr));return r}
const nd=document.getElementById('nights');
function render(key){nd.innerHTML='';let ns=D.nights.slice();
 if(key==='date')ns.sort((a,b)=>a.date<b.date?-1:1);
 else{const kk=key==='head'?D.head:key;ns.sort((a,b)=>acc(b,kk)-acc(a,kk))}
 ns.forEach(n=>{const el=document.createElement('div');el.className='night';
  el.innerHTML=`<h3>${n.date}<span class=acc>${n.n} windows</span></h3>`;
  // headline method first, then truth, then rest
  el.appendChild(strip(D.labels[D.head]+'  '+(acc(n,D.head)*100).toFixed(0)+'%',n[D.head]));
  el.appendChild(strip('Whoop (truth)',n.true,true));
  D.order.filter(k=>k!==D.head).forEach(k=>el.appendChild(strip(D.labels[k]+'  '+(acc(n,k)*100).toFixed(0)+'%',n[k])));
  nd.appendChild(el)})}
sb.onchange=()=>render(sb.value);render('head');
</script></body></html>"""


if __name__ == "__main__":
    main()
