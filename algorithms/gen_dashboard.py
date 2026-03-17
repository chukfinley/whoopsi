#!/usr/bin/env python3
"""Quick dashboard generator: Whoop vs ML v4."""
import json, re, sys, numpy as np
from pathlib import Path
from datetime import timedelta, datetime, timezone
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data.db_loader import load_from_db
from common.preprocessing import compute_rhr, compute_hrv_rmssd, compute_respiratory_rate
from train_whoop_model import algo_f_trained, detect_sleep_onset_offset

BERLIN = timedelta(hours=1)

def gsw(df, day):
    p = day - timedelta(days=1)
    return df[((df['date']==p)&(df['datetime_local'].apply(lambda x:x.hour>=20)))|((df['date']==day)&(df['datetime_local'].apply(lambda x:x.hour<12)))]

def ewp(ds):
    for f in [Path(__file__).resolve().parent.parent/d for d in [f'ble-sync/data/backup/api/deep_dive/{ds}/sleep_lastnight.json', f'ble-sync/data/whoop_backup/deep_dive/{ds}.json']]:
        if not f.exists(): continue
        t = f.read_text(errors='replace')
        if 'scrubber_style' not in t: continue
        m = re.findall(r'"secondary_contextual_display"\s*:\s*"([^"]+)"\s*,\s*"scrubber_style"\s*:\s*"(AWAKE|LIGHT_SLEEP|SWS_SLEEP|REM_SLEEP)"', t)
        if not m: continue
        sm = {'AWAKE':'awake','LIGHT_SLEEP':'light','SWS_SLEEP':'deep','REM_SLEEP':'rem'}
        ph, seen = [], set()
        for ts, st in m:
            try: t24 = datetime.strptime(ts.strip(), '%I:%M %p').strftime('%H:%M')
            except: t24 = ts
            if t24 not in seen: seen.add(t24); ph.append({'time':t24,'phase':sm.get(st,'light')})
        ph.sort(key=lambda p: int(p['time'].split(':')[0])*60+int(p['time'].split(':')[1])+(1440 if int(p['time'].split(':')[0])<12 else 0))
        return ph
    return []

print('Loading...')
df = load_from_db()
df = df[df['date'].apply(lambda d: hasattr(d,'year') and 2025 <= d.year <= 2026)]
wo = json.load(open(Path(__file__).resolve().parent / 'data' / 'raw' / 'whoop_official.json'))

dd = []
for day in sorted(df['date'].unique()):
    s = gsw(df, day)
    if len(s) < 18000: continue
    ds = str(day)
    rhr = compute_rhr(s)
    hrv = compute_hrv_rmssd(s, method='sws')
    resp = compute_respiratory_rate(s) if len(s) > 60 else 14.0
    wp = ewp(ds)
    mp = algo_f_trained(s, rhr, window_sec=60)
    onset_ts, offset_ts = detect_sleep_onset_offset(s)
    onset_str = (datetime.fromtimestamp(onset_ts, timezone.utc) + BERLIN).strftime('%H:%M') if onset_ts else ''
    offset_str = (datetime.fromtimestamp(offset_ts, timezone.utc) + BERLIN).strftime('%H:%M') if offset_ts else ''

    def tm(t):
        h, m = t.split(':'); mn = int(h)*60+int(m); return mn+1440 if mn < 720 else mn
    wd = {tm(p['time']): p['phase'] for p in wp} if wp else {}
    md = {tm(p['time']): p['phase'] for p in mp} if mp else {}
    ov = set(wd.keys()) & set(md.keys())
    mt = sum(1 for t in ov if wd[t] == md[t]) if ov else 0
    ac = round(mt/len(ov)*100, 1) if ov else 0
    ha = (s['movement'] > 0.01).sum() > len(s) * 0.5

    def ps(ph):
        if not ph: return {}
        t = len(ph); c = Counter(p['phase'] for p in ph)
        return {'deep_pct':round(c.get('deep',0)/t*100,1),'light_pct':round(c.get('light',0)/t*100,1),'rem_pct':round(c.get('rem',0)/t*100,1),'awake_pct':round(c.get('awake',0)/t*100,1),'total_min':t,'deep_min':c.get('deep',0),'light_min':c.get('light',0),'rem_min':c.get('rem',0),'awake_min':c.get('awake',0),'sleep_min':t-c.get('awake',0)}
    def tn(v):
        if v is None or v == '--': return None
        try: return float(str(v).replace('%',''))
        except: return None

    ws = ps(wp); ms = ps(mp)
    mv = [abs(ws.get(k,0)-ms.get(k,0)) for k in ['deep_pct','light_pct','rem_pct','awake_pct'] if ws.get(k) is not None and ms.get(k) is not None]
    mae = round(np.mean(mv), 1) if mv else None
    w = wo.get(ds, {})
    dd.append({'date':ds,'hours':round(len(s)/3600,1),'rhr':round(rhr,1),'hrv':round(hrv,1),'resp':round(resp,1),'has_accel':ha,'accuracy':ac,'mae':mae,'overlap':len(ov),'onset':onset_str,'offset':offset_str,
        'whoop':{'phases':wp,'stats':ws,'recovery':tn(w.get('recovery')),'sleep_score':tn(w.get('sleep_score')),'duration':w.get('sleep_duration','')},
        'ml':{'phases':mp,'stats':ms}})
    print(f'  {ds}: MAE={mae} onset={onset_str} offset={offset_str}')

dj = json.dumps(dd, default=str, separators=(',',':'))

HTML_TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Whoop vs ML v4</title><style>:root{--bg:#0a0a0a;--card:#141414;--card2:#1a1a1a;--border:#222;--text:#e0e0e0;--dim:#777;--green:#44cf6c;--ml:#f59e0b;--deep:#1a237e;--light:#42a5f5;--rem:#ab47bc;--awake:#ff7043}*{margin:0;padding:0;box-sizing:border-box}body{background:var(--bg);color:var(--text);font-family:-apple-system,sans-serif}.c{max-width:1100px;margin:0 auto;padding:16px}h1{text-align:center;font-size:18px;letter-spacing:3px;padding:16px 0}h1 .g{color:var(--green)}h1 .m{color:var(--ml)}.sub{text-align:center;color:var(--dim);font-size:11px;margin:-12px 0 16px}.nav{display:flex;gap:4px;flex-wrap:wrap;justify-content:center;margin-bottom:14px}.nb{background:var(--card);border:1px solid var(--border);color:var(--dim);padding:5px 12px;border-radius:8px;cursor:pointer;font-size:11px}.nb:hover{color:var(--text)}.nb.a{border-color:var(--ml);color:var(--ml)}.nb .s{display:block;font-size:8px;color:#555}.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:10px}.card h2{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:10px}.hypno{position:relative;height:120px;background:var(--card2);border-radius:8px;overflow:hidden;margin-bottom:2px}.hypno canvas{width:100%;height:100%}.hy{position:absolute;left:2px;font-size:7px;color:#444}.tax{display:flex;justify-content:space-between;font-size:9px;color:#444;padding:0 30px;margin-bottom:6px}.hl{font-size:10px;font-weight:600;margin-bottom:3px}.sb{display:flex;height:16px;border-radius:6px;overflow:hidden;margin-bottom:3px}.sb .s{transition:width .3s}.lg{display:flex;gap:8px;font-size:9px;flex-wrap:wrap}.lg .d{width:6px;height:6px;border-radius:50%;display:inline-block;margin-right:2px;vertical-align:middle}.lg .t{color:var(--dim)}table{width:100%;border-collapse:collapse;font-size:11px}th{text-align:left;font-size:8px;text-transform:uppercase;color:var(--dim);padding:4px 6px;border-bottom:1px solid var(--border)}td{padding:4px 6px;border-bottom:1px solid #1a1a1a}.sr{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-bottom:12px}.st{text-align:center;background:var(--card2);border-radius:8px;padding:10px 14px;min-width:70px;flex:1}.st .v{font-size:20px;font-weight:700}.st .l{font-size:8px;text-transform:uppercase;color:var(--dim);margin-top:2px}.good{color:#44cf6c}.ok{color:#f5c542}.bad{color:#e74c3c}.badge{display:inline-block;padding:2px 6px;border-radius:4px;font-size:8px;font-weight:600}</style></head><body><div class="c"><h1><span class="g">WHOOP</span> vs <span class="m">ML v4</span></h1><div class="sub">58 features · Viterbi · Sleep Onset Detection · LONO 72.0%</div><div class="nav" id="nav"></div><div id="ct"></div></div><script>
const D=__DATA__;const pC={deep:'#1a237e',light:'#42a5f5',rem:'#ab47bc',awake:'#ff7043'};function fM(m){if(!m&&m!==0)return'-';const h=Math.floor(m/60),mm=m%60;return h>0?h+'h'+String(mm).padStart(2,'0')+'m':mm+'m';}
function toMin(t){const[h,m]=t.split(':').map(Number);return(h*60+m)<720?(h*60+m)+1440:(h*60+m);}
function drawA(id,ph,col,minT,span){const cv=document.getElementById(id);if(!cv||!ph.length)return;const ctx=cv.getContext('2d');const W=cv.width=cv.offsetWidth*2,H=cv.height=cv.offsetHeight*2;ctx.scale(2,2);const w=W/2,h=H/2;const sY={awake:.08,rem:.30,light:.55,deep:.85};ctx.strokeStyle=col;ctx.lineWidth=1.5;ctx.beginPath();let py=null,px=null;ph.forEach(p=>{const t=toMin(p.time),x=(t-minT)/span*w,y=h*(sY[p.phase]||.5);if(py===null)ctx.moveTo(x,y);else{ctx.lineTo(x,py);ctx.lineTo(x,y);}ctx.fillStyle=(pC[p.phase]||'#333')+'33';if(px!==null)ctx.fillRect(px,py,x-px,h-py);py=y;px=x;});if(py!==null)ctx.lineTo(w,py);ctx.stroke();}
function render(idx){const d=D[idx],w=d.whoop,m=d.ml,ws=w.stats,ms=m.stats;
document.getElementById('nav').innerHTML=D.map((dy,i)=>`<button class="nb ${i===idx?'a':''}" onclick="render(${i})">${dy.date}<span class="s">${dy.hours}h${dy.mae?' MAE '+dy.mae:''}</span></button>`).join('');
let h='';
h+=`<div class="card"><div class="sr">`;
h+=`<div class="st"><div class="v ${d.mae<5?'good':d.mae<10?'ok':'bad'}">${d.mae||'-'}</div><div class="l">MAE</div></div>`;
h+=`<div class="st"><div class="v ${d.accuracy>85?'good':d.accuracy>75?'ok':'bad'}">${d.accuracy}%</div><div class="l">Match</div></div>`;
if(d.onset){h+=`<div class="st"><div class="v" style="font-size:16px">${d.onset}</div><div class="l">Onset</div></div>`;}
if(d.offset){h+=`<div class="st"><div class="v" style="font-size:16px">${d.offset}</div><div class="l">Offset</div></div>`;}
h+=`<div class="st"><div class="v">${d.has_accel?'<span class="badge" style="background:#44cf6c22;color:#44cf6c">FULL</span>':'<span class="badge" style="background:#e74c3c22;color:#e74c3c">HR</span>'}</div><div class="l">Sensors</div></div>`;
if(w.recovery){h+=`<div class="st"><div class="v" style="color:var(--green)">${w.recovery}</div><div class="l">Rec</div></div><div class="st"><div class="v" style="color:var(--green)">${w.sleep_score||'-'}</div><div class="l">Sleep</div></div>`;}
h+=`<div class="st"><div class="v">${d.hrv}</div><div class="l">HRV</div></div><div class="st"><div class="v">${d.rhr}</div><div class="l">RHR</div></div>`;
h+=`</div></div>`;
const wPh=w.phases||[],mPh=m.phases||[];let allM=[];wPh.forEach(p=>allM.push(toMin(p.time)));mPh.forEach(p=>allM.push(toMin(p.time)));
const minT=allM.length?Math.min(...allM):0,maxT=allM.length?Math.max(...allM)+1:1,span=maxT-minT||1;
h+=`<div class="card"><h2>Hypnogram</h2>`;
if(wPh.length){h+=`<div class="hl" style="color:var(--green)">Whoop (${wPh.length}min${w.duration?' / '+w.duration:''})</div><div class="hypno"><canvas id="hW"></canvas><div class="hy" style="top:3%">Wake</div><div class="hy" style="top:25%">REM</div><div class="hy" style="top:50%">Light</div><div class="hy" style="top:80%">Deep</div></div>`;}
if(mPh.length){h+=`<div class="hl" style="color:var(--ml)">ML v4 (${mPh.length}min · MAE ${d.mae||'?'})</div><div class="hypno"><canvas id="hM"></canvas><div class="hy" style="top:3%">Wake</div><div class="hy" style="top:25%">REM</div><div class="hy" style="top:50%">Light</div><div class="hy" style="top:80%">Deep</div></div>`;}
if(allM.length){h+=`<div class="tax">`;
for t in range(0,25):h+=f'<span>{t:02d}:00</span>';  # placeholder, JS handles
h+=`</div>`;}
h+=`<div class="lg" style="justify-content:center"><span><span class="d" style="background:var(--awake)"></span>Awake</span><span><span class="d" style="background:var(--rem)"></span>REM</span><span><span class="d" style="background:var(--light)"></span>Light</span><span><span class="d" style="background:var(--deep)"></span>Deep</span></div></div>`;
h+=`<div class="card"><h2>Sleep Stages</h2>`;
function bar(l,c,s){if(!s||!s.total_min)return`<div class="hl" style="color:${c}">${l} — no data</div>`;const t=s.total_min||1;return`<div class="hl" style="color:${c}">${l}</div><div class="sb"><div class="s" style="width:${s.deep_min/t*100}%;background:var(--deep)"></div><div class="s" style="width:${s.light_min/t*100}%;background:var(--light)"></div><div class="s" style="width:${s.rem_min/t*100}%;background:var(--rem)"></div><div class="s" style="width:${s.awake_min/t*100}%;background:var(--awake)"></div></div><div class="lg"><span><span class="d" style="background:var(--deep)"></span>Deep ${fM(s.deep_min)} <span class="t">${s.deep_pct}%</span></span><span><span class="d" style="background:var(--light)"></span>Light ${fM(s.light_min)} <span class="t">${s.light_pct}%</span></span><span><span class="d" style="background:var(--rem)"></span>REM ${fM(s.rem_min)} <span class="t">${s.rem_pct}%</span></span><span><span class="d" style="background:var(--awake)"></span>Awake ${fM(s.awake_min)} <span class="t">${s.awake_pct}%</span></span></div>`;}
h+=bar('Whoop','var(--green)',ws);h+=`<div style="height:8px"></div>`;h+=bar('ML v4','var(--ml)',ms);h+=`</div>`;
h+=`<div class="card"><h2>Duration</h2><table><tr><th></th><th>Total</th><th>Deep</th><th>Light</th><th>REM</th><th>Awake</th><th>Eff</th></tr>`;
if(ws.total_min){h+=`<tr style="color:var(--green)"><td>Whoop</td><td>${fM(ws.total_min)}</td><td>${fM(ws.deep_min)}</td><td>${fM(ws.light_min)}</td><td>${fM(ws.rem_min)}</td><td>${fM(ws.awake_min)}</td><td>${ws.total_min>0?Math.round(ws.sleep_min/ws.total_min*100):'?'}%</td></tr>`;}
if(ms.total_min){h+=`<tr style="color:var(--ml)"><td>ML</td><td>${fM(ms.total_min)}</td><td>${fM(ms.deep_min)}</td><td>${fM(ms.light_min)}</td><td>${fM(ms.rem_min)}</td><td>${fM(ms.awake_min)}</td><td>${ms.total_min>0?Math.round(ms.sleep_min/ms.total_min*100):'?'}%</td></tr>`;}
h+=`<tr style="color:var(--dim)"><td>Raw</td><td>${d.hours}h</td><td colspan="5">HRV ${d.hrv}ms · RHR ${d.rhr}bpm · ${d.has_accel?'Accel+Gyro+SpO2':'HR+RR'}</td></tr></table></div>`;
document.getElementById('ct').innerHTML=h;
setTimeout(()=>{if(wPh.length)drawA('hW',wPh,'#44cf6c',minT,span);if(mPh.length)drawA('hM',mPh,'#f59e0b',minT,span);},50);
}let di=0;for(let i=D.length-1;i>=0;i--){if(D[i].whoop.recovery){di=i;break;}}render(di);
</script></body></html>"""

# Fix the time axis (can't use Python f-string inside JS template)
html = HTML_TEMPLATE.replace('__DATA__', dj)
# Fix the broken time axis line
html = html.replace(
    "if(allM.length){h+=`<div class=\"tax\">`;",
    "if(allM.length){h+=`<div class=\"tax\">`;"
)
# Remove the Python for loop that snuck in
import re as re2
html = re2.sub(r'for t in range.*?placeholder.*?\n', '', html)
# Add proper JS time axis
html = html.replace(
    'h+=`</div>`;}',
    'for(let t=minT;t<=maxT;t+=60){const hh=Math.floor((t%1440)/60);h+=`<span>${String(hh).padStart(2,\'0\')}:00</span>`;}h+=`</div>`;}',
    1
)

Path(__file__).resolve().parent.joinpath('whoop_vs_ml.html').write_text(html)
avg_mae = np.mean([d['mae'] for d in dd if d['mae']])
print(f'\n{len(dd)} days, avg MAE={avg_mae:.1f}')
print(f'Dashboard: whoop_vs_ml.html')
