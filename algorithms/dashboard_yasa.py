#!/usr/bin/env python3
"""Generate a focused Whoop vs YASA comparison dashboard.

Only includes days with full sleep data (>5h). Shows:
- YASA-style hypnogram (step chart) side by side with Whoop
- Sleep duration comparison across all algorithms
- Sleep stage percentages
- Recovery/HRV/RHR comparison
"""

import sys
import json
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
import yasa

from data.db_loader import load_from_db
from common.preprocessing import compute_rhr, compute_hrv_rmssd, compute_respiratory_rate

BERLIN = timedelta(hours=1)
MIN_SLEEP_SAMPLES = 18000  # 5 hours minimum


def get_sleep_window(df, day):
    prev = day - timedelta(days=1)
    mask = (
        (df["date"] == prev)
        & (df["datetime_local"].apply(lambda x: x.hour if hasattr(x, "hour") else 0) >= 20)
    ) | (
        (df["date"] == day)
        & (df["datetime_local"].apply(lambda x: x.hour if hasattr(x, "hour") else 12) < 12)
    )
    return df[mask]


def yasa_classify(sleep_df, rhr, window_sec=60):
    """Run YASA-inspired staging and return YASA Hypnogram object + raw phases.

    Maps our 4-class (deep/light/rem/awake) to YASA integers:
      0=WAKE, 2=N2(light), 3=N3(deep), 4=REM
    We skip N1 since wearable HR can't distinguish N1 from N2.
    """
    if sleep_df.empty or len(sleep_df) < 300:
        return None, [], {}

    phases = []
    yasa_ints = []
    timestamps = []

    ts_all = sleep_df["timestamp"].values
    sleep_start = ts_all[0]
    total_dur = max(ts_all[-1] - ts_all[0], 1)

    from scipy.interpolate import interp1d
    from scipy.signal import welch

    for i in range(0, len(sleep_df) - window_sec, window_sec):
        chunk = sleep_df.iloc[i:i + window_sec]
        t = chunk["datetime_local"].iloc[0]

        hr = chunk["hr"].values
        hr_v = hr[hr > 30]
        mv = chunk["movement"].values
        rr = chunk["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]

        if len(hr_v) < 5:
            phase = "light"
            yasa_int = 2
        else:
            avg_hr = float(np.median(hr_v))
            hr_std = float(np.std(hr_v)) if len(hr_v) > 1 else 0
            hr_above = avg_hr - rhr
            avg_mv = float(np.mean(mv))
            max_mv = float(np.max(mv))

            elapsed = ts_all[min(i + window_sec // 2, len(ts_all) - 1)] - sleep_start
            fraction = elapsed / total_dur

            # Spectral HRV
            lf_hf = 1.5
            hf_pct = 33
            if len(rr) > 20:
                try:
                    cumtime = np.cumsum(rr) / 1000.0
                    cumtime -= cumtime[0]
                    if cumtime[-1] > 15:
                        fs = 4.0
                        t_uni = np.arange(0, cumtime[-1], 1.0 / fs)
                        if len(t_uni) >= 32:
                            f_int = interp1d(cumtime, rr, kind="linear", fill_value="extrapolate")
                            rr_uni = f_int(t_uni) - np.mean(rr)
                            nperseg = min(128, len(rr_uni))
                            freqs, psd = welch(rr_uni, fs=fs, nperseg=nperseg)
                            lf_m = (freqs >= 0.04) & (freqs <= 0.15)
                            hf_m = (freqs >= 0.15) & (freqs <= 0.40)
                            lf_p = np.trapezoid(psd[lf_m], freqs[lf_m]) if lf_m.any() else 0
                            hf_p = np.trapezoid(psd[hf_m], freqs[hf_m]) if hf_m.any() else 0
                            total_p = lf_p + hf_p
                            lf_hf = lf_p / hf_p if hf_p > 1e-10 else 5.0
                            hf_pct = hf_p / total_p * 100 if total_p > 1e-10 else 33
                except Exception:
                    pass

            # RMSSD
            local_rmssd = 0
            if len(rr) > 5:
                diffs = np.diff(rr)
                diffs = diffs[np.abs(diffs) < 300]
                if len(diffs) > 3:
                    local_rmssd = float(np.sqrt(np.mean(diffs**2)))

            # Scoring
            scores = {"deep": 0.0, "light": 0.0, "rem": 0.0, "awake": 0.0}

            # HR
            if hr_above < -2:
                scores["deep"] += 3.0
            elif hr_above < 3:
                scores["deep"] += 1.5; scores["light"] += 1.0
            elif hr_above < 10:
                scores["light"] += 2.0
            elif hr_above < 18:
                scores["rem"] += 1.5; scores["light"] += 0.5
            else:
                scores["awake"] += 3.0

            # HR variability
            if hr_std < 2.5:
                scores["deep"] += 2.0
            elif hr_std < 5:
                scores["light"] += 1.5
            elif hr_std > 8:
                scores["rem"] += 2.5
            else:
                scores["rem"] += 1.0

            # Movement
            if avg_mv > 1.0 or max_mv > 3.0:
                scores["awake"] += 4.0
            elif avg_mv > 0.3:
                scores["awake"] += 1.5
            elif avg_mv < 0.05:
                scores["deep"] += 1.5

            # Spectral
            if lf_hf < 0.8:
                scores["deep"] += 3.0
            elif lf_hf < 1.5:
                scores["deep"] += 1.5; scores["light"] += 1.0
            if lf_hf > 2.5:
                scores["rem"] += 2.5
            elif lf_hf > 1.8:
                scores["rem"] += 1.5
            if 1.0 <= lf_hf <= 2.0:
                scores["light"] += 1.5
            if hf_pct > 50:
                scores["deep"] += 1.5
            elif hf_pct < 20:
                scores["rem"] += 1.0

            # RMSSD
            if local_rmssd > 120:
                scores["deep"] += 1.5
            elif local_rmssd > 80:
                scores["light"] += 1.0
            elif local_rmssd < 30 and local_rmssd > 0:
                scores["rem"] += 1.0

            # Temporal
            if fraction < 0.35:
                scores["deep"] += 1.5
            elif fraction > 0.65:
                scores["rem"] += 1.5

            # Ultradian
            cycle_phase = math.sin(2 * math.pi * elapsed / 5400) if elapsed > 0 else 0
            if cycle_phase < -0.5:
                scores["deep"] += 1.0
            elif cycle_phase > 0.5:
                scores["rem"] += 1.0

            phase = max(scores, key=scores.get)

        # Map to YASA integers: 0=WAKE, 2=N2(light), 3=N3(deep), 4=REM
        phase_to_yasa = {"awake": 0, "light": 2, "deep": 3, "rem": 4}
        yasa_int = phase_to_yasa.get(phase, 2)

        time_str = t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)
        phases.append({"time": time_str, "phase": phase, "ts": int(ts_all[min(i, len(ts_all)-1)])})
        yasa_ints.append(yasa_int)
        timestamps.append(time_str)

    # Smooth isolated phases
    for i in range(1, len(phases) - 1):
        if phases[i]["phase"] != phases[i-1]["phase"] and phases[i]["phase"] != phases[i+1]["phase"]:
            phases[i] = {**phases[i], "phase": phases[i-1]["phase"]}
            phase_to_yasa = {"awake": 0, "light": 2, "deep": 3, "rem": 4}
            yasa_ints[i] = phase_to_yasa.get(phases[i]["phase"], 2)

    if not yasa_ints:
        return None, [], {}

    # Create YASA Hypnogram
    yasa_arr = np.array(yasa_ints)
    start_time = sleep_df["datetime_local"].iloc[0]
    start_str = start_time.strftime("%Y-%m-%d %H:%M:%S") if hasattr(start_time, "strftime") else None
    hyp = yasa.Hypnogram.from_integers(yasa_arr, freq=f"{window_sec}s", scorer="YASA-HRV", start=start_str)
    stats = hyp.sleep_statistics()

    return hyp, phases, stats


def extract_whoop_phases(date_str, whoop_official):
    """Extract Whoop official timeline from deep_dive or whoop_official data."""
    # From algo5_ml features (deep_dive JSON parsing)
    try:
        from algo5_ml.features import _parse_deep_dive_sleep_bounds
        from datetime import datetime as _dt

        second_gt, start_ts, end_ts = _parse_deep_dive_sleep_bounds(date_str)
        if second_gt and start_ts:
            block_size = 60  # 1-minute blocks
            phases = []
            t = int(start_ts)
            t_end = int(end_ts)
            while t + block_size <= t_end:
                labels = [second_gt.get(s) for s in range(t, t + block_size) if s in second_gt]
                if labels:
                    majority = Counter(labels).most_common(1)[0][0]
                else:
                    majority = "light"
                time_str = _dt.fromtimestamp(t + BERLIN.total_seconds()).strftime("%H:%M")
                phases.append({"time": time_str, "phase": majority})
                t += block_size
            return phases
    except Exception:
        pass
    return []


def load_whoop_official():
    """Load Whoop official data."""
    p = Path(__file__).parent / "data" / "raw" / "whoop_official.json"
    if p.exists():
        return json.load(p.open())
    return {}


def to_num(v):
    if v is None or v == "--" or v == "":
        return None
    try:
        return float(str(v).replace("%", ""))
    except (ValueError, TypeError):
        return None


def analyze_day(df, day, hrv_base, rhr_base, whoop_official):
    sleep_df = get_sleep_window(df, day)
    if sleep_df.empty or len(sleep_df) < MIN_SLEEP_SAMPLES:
        return None

    day_df = df[df["date"] == day]

    # Physiology
    rhr = compute_rhr(sleep_df)
    hrv = compute_hrv_rmssd(sleep_df, method="sws")
    resp = compute_respiratory_rate(sleep_df) if len(sleep_df) > 60 else 14.0

    # YASA analysis
    hyp, yasa_phases, yasa_stats = yasa_classify(sleep_df, rhr)

    # Whoop official data
    wo = whoop_official.get(str(day), {})
    whoop_phases = extract_whoop_phases(str(day), whoop_official)

    # Whoop sleep stages from official
    whoop_sleep = {}
    if wo:
        whoop_sleep = {
            "recovery": to_num(wo.get("recovery")),
            "sleep_score": to_num(wo.get("sleep_score")),
            "strain": to_num(wo.get("strain")),
            "hrv_ms": to_num(wo.get("hrv_ms")),
            "rhr_bpm": to_num(wo.get("rhr_bpm")),
            "resp_rate": to_num(wo.get("resp_rate")),
            "duration": wo.get("sleep_duration"),
            "awake_pct": wo.get("sleep_awake_pct"),
            "awake_time": wo.get("sleep_awake_time"),
            "light_pct": wo.get("sleep_light_sleep_pct"),
            "light_time": wo.get("sleep_light_sleep_time"),
            "deep_pct": wo.get("sleep_sws_sleep_pct"),
            "deep_time": wo.get("sleep_sws_sleep_time"),
            "rem_pct": wo.get("sleep_rem_sleep_pct"),
            "rem_time": wo.get("sleep_rem_sleep_time"),
            "efficiency": wo.get("efficiency"),
        }

    # YASA summary
    yasa_summary = {}
    if yasa_stats:
        tst = yasa_stats.get("TST", 0)
        yasa_summary = {
            "tst_min": round(tst, 1),
            "tst_hours": f"{int(tst//60)}:{int(tst%60):02d}",
            "se": round(yasa_stats.get("SE", 0), 1),
            "waso": round(yasa_stats.get("WASO", 0), 1),
            "deep_pct": round(yasa_stats.get("%N3", 0), 1),
            "light_pct": round(yasa_stats.get("%N2", 0), 1),
            "rem_pct": round(yasa_stats.get("%REM", 0), 1),
            "deep_min": round(yasa_stats.get("N3", 0), 1),
            "light_min": round(yasa_stats.get("N2", 0), 1),
            "rem_min": round(yasa_stats.get("REM", 0), 1),
            "awake_min": round(yasa_stats.get("WAKE", 0), 1),
            "sol": round(yasa_stats.get("SOL", 0), 1),
            "lat_rem": round(yasa_stats.get("Lat_REM", 0), 1),
            "sfi": round(yasa_stats.get("SFI", 0), 3),
            "n_cycles": 0,
        }
        # Detect cycles from phase sequence
        in_nrem = False
        cycles = 0
        for p in yasa_phases:
            if p["phase"] in ("deep", "light"):
                in_nrem = True
            elif p["phase"] == "rem" and in_nrem:
                cycles += 1
                in_nrem = False
        yasa_summary["n_cycles"] = cycles

    # Compute YASA recovery (simplified)
    yasa_eff = yasa_summary.get("se", 0) if yasa_summary else 0
    if hrv_base > 0:
        ratio = hrv / hrv_base
        hs = 100 / (1 + math.exp(-4 * (ratio - 0.85)))
    else:
        hs = 50
    rs = max(0, min(100, 50 + (rhr_base - rhr) * 8))
    rp = max(0, (resp - 16) * 5) if resp > 16 else 0
    yasa_recovery = max(0, min(100, round(0.60 * hs + 0.25 * rs + 0.15 * yasa_eff - rp)))

    return {
        "date": str(day),
        "physio": {
            "rhr": round(rhr, 1),
            "hrv": round(hrv, 1),
            "resp": round(resp, 1),
            "sleep_samples": len(sleep_df),
            "sleep_hours": round(len(sleep_df) / 3600, 1),
        },
        "whoop": whoop_sleep,
        "whoop_phases": whoop_phases,
        "yasa": yasa_summary,
        "yasa_phases": yasa_phases,
        "yasa_recovery": yasa_recovery,
    }


def generate_html(data):
    dj = json.dumps(data, default=str, separators=(",", ":"))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Open Whoop — YASA vs Whoop</title>
<style>
:root{{--bg:#0a0a0a;--card:#141414;--card2:#1a1a1a;--border:#222;--text:#e0e0e0;--dim:#777;--dim2:#555;--green:#44cf6c;--green2:#44cf6c22;--cyan:#06b6d4;--cyan2:#06b6d422;--pink:#e91e63;--yellow:#f5c542;--red:#e74c3c;--deep:#1a237e;--light-s:#42a5f5;--rem:#ab47bc;--awake:#ff7043}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}}
.container{{max-width:1200px;margin:0 auto;padding:16px 20px}}
.header{{text-align:center;padding:20px 0 16px}}
.header h1{{font-size:18px;font-weight:700;letter-spacing:3px;text-transform:uppercase}}
.header h1 .g{{color:var(--green)}}.header h1 .c{{color:var(--cyan)}}
.header .meta{{color:var(--dim);font-size:11px;margin-top:6px}}
.day-nav{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:16px;justify-content:center}}
.day-btn{{background:var(--card);border:1px solid var(--border);color:var(--dim);padding:6px 14px;border-radius:8px;cursor:pointer;font-size:11px;font-weight:500;transition:all .2s}}
.day-btn:hover{{border-color:var(--dim2);color:var(--text)}}
.day-btn.active{{border-color:var(--cyan);color:var(--cyan);background:var(--cyan2)}}
.day-btn .sub{{display:block;font-size:9px;color:var(--dim2);margin-top:1px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:12px}}
.card h2{{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:12px}}
.sect{{margin-bottom:4px}}
.sect summary{{cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);padding:8px 12px;background:var(--card);border:1px solid var(--border);border-radius:10px;list-style:none;display:flex;align-items:center;gap:6px}}
.sect summary::-webkit-details-marker{{display:none}}
.sect summary::before{{content:'\\25B6';font-size:8px;transition:transform .2s}}
.sect[open] summary::before{{transform:rotate(90deg)}}
.sect .inner{{padding:8px 0 0}}

/* Rings */
.ring-row{{display:flex;gap:20px;justify-content:center;align-items:center;flex-wrap:wrap}}
.ring-item{{text-align:center}}
.ring-item .label{{font-size:9px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;font-weight:600}}
.ring-wrap{{position:relative;width:100px;height:100px;margin:0 auto 6px}}
.ring-wrap svg{{width:100px;height:100px;transform:rotate(-90deg)}}
.ring-wrap .val{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:28px;font-weight:700}}
.ring-wrap .pct{{font-size:12px;font-weight:400;color:var(--dim)}}

/* Scores */
.score-row{{display:flex;gap:4px;flex-wrap:wrap;justify-content:center}}
.sc{{text-align:center;padding:10px 12px;background:var(--card2);border-radius:8px;min-width:90px;flex:1}}
.sc .lbl{{font-size:8px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);margin-bottom:4px}}
.sc .w{{font-size:18px;font-weight:700;color:var(--green)}}.sc .o{{font-size:18px;font-weight:700;color:var(--cyan)}}
.sc .vs{{font-size:8px;color:var(--dim2)}}

/* Sleep bars */
.sleep-bars{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:600px){{.sleep-bars{{grid-template-columns:1fr}}}}
.sb-col .src{{font-size:10px;font-weight:600;letter-spacing:0.5px;margin-bottom:6px}}
.sb{{display:flex;height:18px;border-radius:8px;overflow:hidden;margin-bottom:4px}}
.sb .s{{transition:width .3s}}
.legend{{display:flex;gap:10px;flex-wrap:wrap;font-size:9px;margin-top:2px}}
.legend .d{{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:2px;vertical-align:middle}}
.legend .dur{{color:var(--dim);margin-left:1px}}

/* Hypnogram */
.hypno-container{{position:relative;height:160px;background:var(--card2);border-radius:8px;overflow:hidden;margin-bottom:6px}}
.hypno-canvas{{width:100%;height:100%}}
.hypno-label{{font-size:9px;font-weight:600;letter-spacing:0.5px;margin-bottom:4px}}
.hypno-ylabel{{position:absolute;left:2px;font-size:8px;color:var(--dim2)}}
.time-axis{{display:flex;justify-content:space-between;font-size:9px;color:var(--dim2);padding:0 40px}}

/* Hours comparison */
.hours-table{{width:100%;border-collapse:collapse;font-size:12px}}
.hours-table th{{text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);padding:6px 8px;border-bottom:1px solid var(--border)}}
.hours-table td{{padding:6px 8px;border-bottom:1px solid #1a1a1a}}
.hours-table .bar-cell{{width:50%}}
.hours-bar{{height:14px;border-radius:4px;min-width:2px;transition:width .3s}}
</style></head><body>
<div class="container">
<div class="header">
  <h1><span class="g">WHOOP</span> vs <span class="c">YASA</span></h1>
  <div class="meta" id="meta"></div>
</div>
<div class="day-nav" id="nav"></div>
<div id="content"></div>
</div>
<script>
const D={dj};
const pC={{deep:'#1a237e',light:'#42a5f5',rem:'#ab47bc',awake:'#ff7043'}};
const recCol=v=>v>=67?'#44cf6c':v>=34?'#f5c542':'#e74c3c';

document.getElementById('meta').textContent=D.days.length+' nights with full data';

const nav=document.getElementById('nav');
const content=document.getElementById('content');

function fM(m){{if(!m&&m!==0)return'-';const h=Math.floor(m/60),mm=Math.round(m%60);return h>0?h+'h'+String(mm).padStart(2,'0')+'m':mm+'m';}}
function pT(s){{if(!s)return 0;const p=s.split(':');return parseInt(p[0]||0)*60+parseInt(p[1]||0);}}
function ring(val,color){{
  const r=42,c=2*Math.PI*r,pct=Math.min(Math.max(val||0,0),100),dash=c*pct/100,gap=c-dash;
  return `<div class="ring-wrap"><svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="${{r}}" fill="none" stroke="#222" stroke-width="6"/><circle cx="50" cy="50" r="${{r}}" fill="none" stroke="${{color}}" stroke-width="6" stroke-dasharray="${{dash}} ${{gap}}" stroke-linecap="round"/></svg><div class="val" style="color:${{color}}">${{val||'-'}}<span class="pct">%</span></div></div>`;
}}

function drawHypno(canvasId,phases,color,winMin){{
  const canvas=document.getElementById(canvasId);
  if(!canvas||!phases||!phases.length)return;
  const ctx=canvas.getContext('2d');
  const W=canvas.width=canvas.offsetWidth*2;
  const H=canvas.height=canvas.offsetHeight*2;
  ctx.scale(2,2);
  const w=W/2,h=H/2;

  // Y mapping: awake=top, rem, light, deep=bottom
  const stageY={{awake:0.08,rem:0.30,light:0.58,deep:0.85}};
  const pw=w/phases.length;

  ctx.strokeStyle=color;
  ctx.lineWidth=1.5;
  ctx.beginPath();
  let prevY=null;
  phases.forEach((p,i)=>{{
    const y=h*(stageY[p.phase]||0.5);
    const x=i*pw;
    if(prevY===null){{ctx.moveTo(x,y);}}
    else{{ctx.lineTo(x,prevY);ctx.lineTo(x,y);}}  // step
    prevY=y;
  }});
  if(prevY!==null)ctx.lineTo(phases.length*pw,prevY);
  ctx.stroke();

  // Fill areas with stage colors (subtle)
  phases.forEach((p,i)=>{{
    const y=h*(stageY[p.phase]||0.5);
    ctx.fillStyle=(pC[p.phase]||'#333')+'44';
    ctx.fillRect(i*pw,y,pw,h-y);
  }});
}}

function render(idx){{
  const d=D.days[idx];
  const w=d.whoop||{{}};
  const y=d.yasa||{{}};
  const wRec=w.recovery||0;
  const yRec=d.yasa_recovery||0;

  nav.innerHTML=D.days.map((dy,i)=>{{
    const wr=dy.whoop&&dy.whoop.recovery?dy.whoop.recovery+'%':'';
    return `<button class="day-btn ${{i===idx?'active':''}}" onclick="render(${{i}})">${{dy.date}}<span class="sub">${{dy.physio.sleep_hours}}h ${{wr}}</span></button>`;
  }}).join('');

  let h='';

  // 1. Recovery + Scores
  h+=`<details class="sect" open><summary>Recovery & Scores</summary><div class="inner"><div class="card">
    <div class="ring-row">
      <div class="ring-item"><div class="label" style="color:var(--green)">Whoop</div>${{ring(wRec,recCol(wRec))}}</div>
      <div class="ring-item"><div class="label" style="color:var(--cyan)">YASA</div>${{ring(yRec,recCol(yRec))}}</div>
    </div>
    <div style="margin-top:12px"><div class="score-row">
      <div class="sc"><div class="lbl">Sleep Score</div><div class="w">${{w.sleep_score||'-'}}</div><div class="vs">vs</div><div class="o">${{y.se?Math.round(y.se):'-'}}</div></div>
      <div class="sc"><div class="lbl">HRV</div><div class="w">${{w.hrv_ms||'-'}}</div><div class="vs">vs</div><div class="o">${{d.physio.hrv}}</div></div>
      <div class="sc"><div class="lbl">RHR</div><div class="w">${{w.rhr_bpm||'-'}}</div><div class="vs">vs</div><div class="o">${{d.physio.rhr}}</div></div>
      <div class="sc"><div class="lbl">Resp</div><div class="w">${{w.resp_rate||'-'}}</div><div class="vs">vs</div><div class="o">${{d.physio.resp}}</div></div>
      <div class="sc"><div class="lbl">WASO</div><div class="o">${{y.waso?y.waso+'m':'-'}}</div></div>
      <div class="sc"><div class="lbl">Cycles</div><div class="o">${{y.n_cycles||'-'}}</div></div>
    </div></div>
  </div></div></details>`;

  // 2. Hypnograms
  const wPh=d.whoop_phases||[];
  const yPh=d.yasa_phases||[];
  h+=`<details class="sect" open><summary>Hypnogram — Sleep Timeline</summary><div class="inner"><div class="card">`;
  // Whoop
  if(wPh.length>0){{
    h+=`<div class="hypno-label" style="color:var(--green)">Whoop Official (${{wPh.length}} min)</div>`;
    h+=`<div class="hypno-container"><canvas id="hW" class="hypno-canvas"></canvas>
      <div class="hypno-ylabel" style="top:5%">Awake</div>
      <div class="hypno-ylabel" style="top:25%">REM</div>
      <div class="hypno-ylabel" style="top:52%">Light</div>
      <div class="hypno-ylabel" style="top:80%">Deep</div>
    </div>`;
    h+=`<div class="time-axis"><span>${{wPh[0].time}}</span><span>${{wPh[Math.floor(wPh.length/4)].time}}</span><span>${{wPh[Math.floor(wPh.length/2)].time}}</span><span>${{wPh[Math.floor(wPh.length*3/4)].time}}</span><span>${{wPh[wPh.length-1].time}}</span></div>`;
  }}else{{
    h+=`<div class="hypno-label" style="color:var(--green)">Whoop Official</div><div style="color:var(--dim);font-size:11px;padding:8px">No timeline data for this day</div>`;
  }}
  h+=`<div style="height:12px"></div>`;
  // YASA
  if(yPh.length>0){{
    h+=`<div class="hypno-label" style="color:var(--cyan)">YASA Spectral (${{yPh.length}} min, ${{y.tst_hours||'-'}} sleep)</div>`;
    h+=`<div class="hypno-container"><canvas id="hY" class="hypno-canvas"></canvas>
      <div class="hypno-ylabel" style="top:5%">Awake</div>
      <div class="hypno-ylabel" style="top:25%">REM</div>
      <div class="hypno-ylabel" style="top:52%">Light</div>
      <div class="hypno-ylabel" style="top:80%">Deep</div>
    </div>`;
    h+=`<div class="time-axis"><span>${{yPh[0].time}}</span><span>${{yPh[Math.floor(yPh.length/4)].time}}</span><span>${{yPh[Math.floor(yPh.length/2)].time}}</span><span>${{yPh[Math.floor(yPh.length*3/4)].time}}</span><span>${{yPh[yPh.length-1].time}}</span></div>`;
  }}
  h+=`<div class="legend" style="margin-top:8px;justify-content:center">
    <span><span class="d" style="background:var(--awake)"></span>Awake</span>
    <span><span class="d" style="background:var(--rem)"></span>REM</span>
    <span><span class="d" style="background:var(--light-s)"></span>Light</span>
    <span><span class="d" style="background:var(--deep)"></span>Deep</span>
  </div>`;
  h+=`</div></div></details>`;

  // 3. Sleep Stages Bars
  h+=`<details class="sect" open><summary>Sleep Stages</summary><div class="inner"><div class="card"><div class="sleep-bars">`;
  // Whoop
  h+=`<div class="sb-col"><div class="src" style="color:var(--green)">Whoop${{w.duration?' ('+w.duration+')':''}}</div>`;
  if(w.deep_time){{
    const wd=pT(w.deep_time),wl=pT(w.light_time),wr=pT(w.rem_time),wa=pT(w.awake_time);
    const wt=wd+wl+wr+wa||1;
    h+=`<div class="sb"><div class="s" style="width:${{wd/wt*100}}%;background:var(--deep)"></div><div class="s" style="width:${{wl/wt*100}}%;background:var(--light-s)"></div><div class="s" style="width:${{wr/wt*100}}%;background:var(--rem)"></div><div class="s" style="width:${{wa/wt*100}}%;background:var(--awake)"></div></div>`;
    h+=`<div class="legend"><span><span class="d" style="background:var(--deep)"></span>Deep ${{w.deep_time}} (${{w.deep_pct}})</span><span><span class="d" style="background:var(--light-s)"></span>Light ${{w.light_time}} (${{w.light_pct}})</span><span><span class="d" style="background:var(--rem)"></span>REM ${{w.rem_time}} (${{w.rem_pct}})</span><span><span class="d" style="background:var(--awake)"></span>Awake ${{w.awake_time}} (${{w.awake_pct}})</span></div>`;
  }}else{{h+=`<div style="color:var(--dim);font-size:10px">No data</div>`;}}
  h+=`</div>`;
  // YASA
  h+=`<div class="sb-col"><div class="src" style="color:var(--cyan)">YASA${{y.tst_hours?' ('+y.tst_hours+')':''}}</div>`;
  if(y.deep_min!=null){{
    const yd=y.deep_min,yl=y.light_min,yr=y.rem_min,ya=y.awake_min;
    const yt=yd+yl+yr+ya||1;
    h+=`<div class="sb"><div class="s" style="width:${{yd/yt*100}}%;background:var(--deep)"></div><div class="s" style="width:${{yl/yt*100}}%;background:var(--light-s)"></div><div class="s" style="width:${{yr/yt*100}}%;background:var(--rem)"></div><div class="s" style="width:${{ya/yt*100}}%;background:var(--awake)"></div></div>`;
    h+=`<div class="legend"><span><span class="d" style="background:var(--deep)"></span>Deep ${{fM(yd)}} (${{y.deep_pct}}%)</span><span><span class="d" style="background:var(--light-s)"></span>Light ${{fM(yl)}} (${{y.light_pct}}%)</span><span><span class="d" style="background:var(--rem)"></span>REM ${{fM(yr)}} (${{y.rem_pct}}%)</span><span><span class="d" style="background:var(--awake)"></span>Awake ${{fM(ya)}}</span></div>`;
  }}
  h+=`</div></div></div></div></details>`;

  // 4. Sleep Hours Comparison
  h+=`<details class="sect" open><summary>Sleep Duration Comparison</summary><div class="inner"><div class="card">
    <table class="hours-table"><tr><th>Source</th><th>Total</th><th>Deep</th><th>Light</th><th>REM</th><th>Awake</th><th>Efficiency</th></tr>`;
  if(w.duration){{
    h+=`<tr style="color:var(--green)"><td>Whoop</td><td>${{w.duration}}</td><td>${{w.deep_time||'-'}}</td><td>${{w.light_time||'-'}}</td><td>${{w.rem_time||'-'}}</td><td>${{w.awake_time||'-'}}</td><td>${{w.efficiency||'-'}}</td></tr>`;
  }}
  if(y.tst_hours){{
    h+=`<tr style="color:var(--cyan)"><td>YASA</td><td>${{y.tst_hours}}</td><td>${{fM(y.deep_min)}}</td><td>${{fM(y.light_min)}}</td><td>${{fM(y.rem_min)}}</td><td>${{fM(y.awake_min)}}</td><td>${{y.se}}%</td></tr>`;
  }}
  h+=`<tr style="color:var(--dim)"><td>Raw Data</td><td>${{d.physio.sleep_hours}}h</td><td colspan="5" style="font-size:10px">${{d.physio.sleep_samples.toLocaleString()}} samples in sleep window</td></tr>`;
  h+=`</table></div></div></details>`;

  content.innerHTML=h;

  // Draw hypnograms after DOM update
  setTimeout(()=>{{
    if(wPh.length>0)drawHypno('hW',wPh,'#44cf6c',1);
    if(yPh.length>0)drawHypno('hY',yPh,'#06b6d4',1);
  }},50);
}}

// Start with day that has best whoop data
let defIdx=0;
for(let i=D.days.length-1;i>=0;i--){{if(D.days[i].whoop&&D.days[i].whoop.recovery){{defIdx=i;break;}}}}
render(defIdx);
</script></body></html>"""


def main():
    print("=" * 60)
    print("  WHOOP vs YASA — Focused Comparison")
    print("=" * 60)

    print("\nLoading DB...")
    df = load_from_db()
    if df.empty:
        print("No data!")
        return

    # Filter to real dates only
    df = df[df["date"].apply(lambda d: hasattr(d, "year") and 2025 <= d.year <= 2026)]
    print(f"  {len(df)} records after date filter")

    whoop_official = load_whoop_official()
    print(f"  Whoop official data for: {list(whoop_official.keys())}")

    # Get valid days (>5h sleep data)
    days = sorted(d for d in df["date"].unique() if hasattr(d, "year"))

    # Compute baselines
    daily_hrvs, daily_rhrs = [], []
    for day in days:
        sw = get_sleep_window(df, day)
        if len(sw) < MIN_SLEEP_SAMPLES:
            continue
        day_hrv = compute_hrv_rmssd(sw, method="sws")
        if day_hrv > 10:
            daily_hrvs.append(day_hrv)
        sh = sw["hr"][sw["hr"] > 30].values
        if len(sh) > 100:
            daily_rhrs.append((float(np.percentile(sh, 25)) + float(np.median(sh))) / 2)
    hrv_base = float(np.median(daily_hrvs)) if daily_hrvs else 90
    rhr_base = float(np.median(daily_rhrs)) if daily_rhrs else 55
    print(f"  Baselines: HRV={hrv_base:.1f}ms, RHR={rhr_base:.1f}bpm")

    # Analyze each day
    results = []
    for day in days:
        sw = get_sleep_window(df, day)
        if len(sw) < MIN_SLEEP_SAMPLES:
            continue
        print(f"  Analyzing {day} ({len(sw)} samples, {len(sw)/3600:.1f}h)...")
        result = analyze_day(df, day, hrv_base, rhr_base, whoop_official)
        if result:
            results.append(result)

    print(f"\n{len(results)} days with full data")

    # Generate dashboard
    dashboard = {"days": results}
    html = generate_html(dashboard)
    out = Path(__file__).parent / "full_dashboard.html"
    out.write_text(html)
    print(f"Dashboard: file://{out.resolve()}")


if __name__ == "__main__":
    main()
