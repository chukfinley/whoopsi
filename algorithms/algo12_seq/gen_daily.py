"""Whoop-style DAILY dashboard: click through days (left/right), each day shows
the 3 scores (Recovery / Sleep / Strain) Whoop-vs-ours, sleep duration, and the
night's hypnogram comparison (Whoop truth + our methods).

Inputs: dataset.npz (labels/dates), preds/*.npy (method predictions),
daily_scores.json (whoop-vs-ours daily scores, from build by sub-agent).
"""
import sys, json
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HERE = Path(__file__).resolve().parent
PREDS = HERE / "preds"
METHODS = [("hybrid", "Hybrid (unser bestes)"), ("algo12", "algo12"), ("algo5", "algo5")]
INT2 = {0: "awake", 1: "light", 2: "deep", 3: "rem"}


def main():
    d = np.load(HERE / "dataset.npz", allow_pickle=True)
    y, nid, ts = d["y"], d["night_ids"], d["timestamps"]
    dates = list(d["dates"])
    found = {}
    for k, _ in METHODS:
        f = PREDS / f"{k}.npy"
        if f.exists():
            a = np.load(f)
            if len(a) == len(y):
                found[k] = a
    scores = {}
    sj = HERE / "daily_scores.json"
    if sj.exists():
        for row in json.load(open(sj)):
            scores[row["date"]] = row

    days = []
    for k, n in enumerate(sorted(set(nid))):
        idx = np.where(nid == n)[0]; idx = idx[np.argsort(ts[idx])]
        date = dates[k] if k < len(dates) else f"night{n}"
        yy = y[idx]
        # stage % of the night (by 2-min windows, non-overlapping approximation via stride)
        cnt = {INT2[i]: int((yy == i).sum()) for i in range(4)}
        tot = max(1, len(yy))
        day = {"date": date, "n": int(len(idx)),
               "stage_pct": {k2: round(v / tot * 100) for k2, v in cnt.items()},
               "true": yy.astype(int).tolist(),
               "scores": scores.get(date)}
        for mk, _ in METHODS:
            if mk in found:
                yp = found[mk][idx]
                day[mk] = yp.astype(int).tolist()
                day[mk + "_acc"] = round(float((yy == yp).mean()) * 100)
        days.append(day)
    days.sort(key=lambda x: x["date"])

    data = {"days": days, "methods": [m for m in METHODS if m[0] in found],
            "has_scores": bool(scores)}
    (HERE / "daily.html").write_text(TEMPLATE.replace("__DATA__", json.dumps(data)))
    print(f"wrote daily.html — {len(days)} days, scores={'yes' if scores else 'NO (daily_scores.json missing)'}")


TEMPLATE = r"""<!doctype html><html lang=de><head><meta charset=utf-8>
<title>Whoop-Vergleich — Tagesansicht</title>
<style>
:root{--awake:#e8574a;--light:#5b9bd5;--deep:#2c3e8c;--rem:#8e5bd5;--bg:#0d0f15;--card:#181b24;--card2:#1f2330;--tx:#e8eaf0;--mut:#949aa8;--whoop:#00d4aa;--ours:#ffb020}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:22px}
.nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.nav h1{font-size:20px;margin:0}
.nav .sub{color:var(--mut);font-size:12px}
.btn{background:var(--card2);color:var(--tx);border:1px solid #2c313f;border-radius:9px;padding:9px 15px;cursor:pointer;font-size:18px;user-select:none}
.btn:hover{background:#2a2f3d}.btn:disabled{opacity:.3;cursor:default}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.card{background:var(--card);border-radius:14px;padding:15px}
.card .t{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px}
.vs{display:flex;justify-content:space-between;align-items:flex-end;gap:8px}
.vs .col{flex:1}.vs .lbl{font-size:10px;text-transform:uppercase;letter-spacing:.04em}
.vs .num{font-size:25px;font-weight:700;line-height:1.1}
.whoop .num,.whoop .lbl{color:var(--whoop)}.ours .num,.ours .lbl{color:var(--ours)}
.delta{font-size:11px;color:var(--mut);margin-top:6px}
.hyp-card{background:var(--card);border-radius:14px;padding:16px;margin-bottom:14px}
.hyp-card h3{margin:0 0 12px;font-size:14px}
.legend{display:flex;gap:14px;margin-bottom:12px;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px;color:var(--mut);font-size:12px}
.sw{width:13px;height:13px;border-radius:3px}
.hyp{display:flex;align-items:center;gap:10px;margin:4px 0}
.hyp .tag{width:150px;text-align:right;color:var(--mut);font-size:12px;white-space:nowrap}
.hyp.truth .tag{color:var(--tx);font-weight:700}
canvas{border-radius:4px;image-rendering:pixelated;width:100%;height:30px}
.pct{display:flex;gap:16px;margin-top:12px;flex-wrap:wrap;font-size:13px}
.pct b{color:var(--tx)}
.miss{color:var(--mut);font-size:12px}
.hint{color:var(--mut);font-size:11px;text-align:center;margin-top:14px}
</style></head><body><div class=wrap>
<div class=nav>
 <button class=btn id=prev>&larr;</button>
 <div style=text-align:center><h1 id=date></h1><div class=sub id=daysub></div></div>
 <button class=btn id=next>&rarr;</button>
</div>
<div class=cards id=scorecards></div>
<div class=hyp-card>
 <h3>Schlafphasen — Whoop (Wahrheit) vs. unsere Modelle</h3>
 <div class=legend>
  <span><i class=sw style=background:var(--awake)></i>Wach</span>
  <span><i class=sw style=background:var(--light)></i>Leicht</span>
  <span><i class=sw style=background:var(--deep)></i>Tief</span>
  <span><i class=sw style=background:var(--rem)></i>REM</span></div>
 <div id=hyp></div>
 <div class=pct id=pct></div>
</div>
<div class=hint>Pfeiltasten &larr; &rarr; oder Buttons zum Tag wechseln</div>
</div>
<script>
const D=__DATA__;const days=D.days;let cur=days.length-1;
const COL={0:'#e8574a',1:'#5b9bd5',2:'#2c3e8c',3:'#8e5bd5'};
function fmtH(h){if(h==null)return '–';const m=Math.round(h*60);return Math.floor(m/60)+'h '+(m%60)+'m'}
function num(v,suf){return v==null?'–':(Math.round(v*10)/10)+(suf||'')}
function scoreCard(title,w,o,suf,better){
 let d='';
 if(w!=null&&o!=null){const diff=o-w;d=`Δ ${diff>0?'+':''}${Math.round(diff*10)/10}${suf||''} vs Whoop`}
 return `<div class=card><div class=t>${title}</div><div class=vs>
  <div class="col whoop"><div class=lbl>Whoop</div><div class=num>${num(w,suf)}</div></div>
  <div class="col ours" style=text-align:right><div class=lbl>Unser</div><div class=num>${num(o,suf)}</div></div>
  </div><div class=delta>${d}</div></div>`}
function draw(arr){const c=document.createElement('canvas');c.width=arr.length;c.height=4;
 const x=c.getContext('2d');arr.forEach((v,i)=>{x.fillStyle=COL[v];x.fillRect(i,0,1,4)});return c}
function strip(tag,arr,truth){const r=document.createElement('div');r.className='hyp'+(truth?' truth':'');
 r.innerHTML=`<span class=tag>${tag}</span>`;r.appendChild(draw(arr));return r}
function render(){
 const day=days[cur];
 document.getElementById('date').textContent=day.date;
 document.getElementById('daysub').textContent=`Tag ${cur+1} / ${days.length} · ${day.n} Fenster`;
 document.getElementById('prev').disabled=cur<=0;
 document.getElementById('next').disabled=cur>=days.length-1;
 // score cards
 const s=day.scores;const w=s?s.whoop:{},o=s?s.ours:{};
 let cards='';
 if(s){
  cards+=scoreCard('Schlafdauer',w.sleep_hours,o.sleep_hours,'h');
  cards+=scoreCard('Schlaf-Score',w.sleep_score,o.sleep_score,'%');
  cards+=scoreCard('Recovery',w.recovery,o.recovery,'%');
  cards+=scoreCard('Strain',w.strain,o.strain,'');
  // override hours formatting
 }else{
  cards='<div class=card style=grid-column:1/-1><div class=miss>daily_scores.json fehlt — Score-Vergleich noch nicht berechnet. (Hypnogramm unten funktioniert.)</div></div>';
 }
 const sc=document.getElementById('scorecards');sc.innerHTML=cards;
 // fix sleep hours display (scoreCard rounds; redo first card cleanly)
 if(s){const cardsEl=sc.querySelectorAll('.card .num');
  cardsEl[0].textContent=fmtH(w.sleep_hours);cardsEl[1].textContent=fmtH(o.sleep_hours);}
 // hypnograms
 const h=document.getElementById('hyp');h.innerHTML='';
 D.methods.forEach(m=>{if(day[m[0]])h.appendChild(strip(`${m[1]}  ${day[m[0]+'_acc']}%`,day[m[0]]))});
 h.appendChild(strip('Whoop (Wahrheit)',day.true,true));
 // stage pct
 const p=day.stage_pct;
 document.getElementById('pct').innerHTML=
  `<span>Wach <b>${p.awake}%</b></span><span>Leicht <b>${p.light}%</b></span>`+
  `<span>Tief <b>${p.deep}%</b></span><span>REM <b>${p.rem}%</b></span>`;
}
document.getElementById('prev').onclick=()=>{if(cur>0){cur--;render()}};
document.getElementById('next').onclick=()=>{if(cur<days.length-1){cur++;render()}};
document.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'&&cur>0){cur--;render()}
 else if(e.key==='ArrowRight'&&cur<days.length-1){cur++;render()}});
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
