#!/usr/bin/env python3
"""Build Whoop Dashboard - extracts HAR data and generates interactive HTML dashboard."""

import json
import base64
import struct
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter

BERLIN = timedelta(hours=1)
RECORD_SIZE = 124


def load_all_data():
    """Load and deduplicate all metrics from all HAR files."""
    all_points = {}
    meta = {"strap": None, "hw": None, "fw": None}

    for f in sorted(Path(".").glob("*.har")):
        har = json.load(open(f))
        for entry in har["log"]["entries"]:
            req = entry["request"]
            if "metrics-service" not in req.get("url", ""):
                continue
            headers = {h["name"]: h["value"] for h in req.get("headers", [])}
            if not meta["strap"]:
                meta["strap"] = headers.get("x-whoop-strap-id")
                meta["hw"] = headers.get("x-whoop-hw-version")
                meta["fw"] = headers.get("x-whoop-fw-version")

            content = req.get("_content") or req.get("postData", {})
            text = content.get("text", "")
            if not text:
                continue
            try:
                data = base64.b64decode(text)
            except Exception:
                continue
            for j in range(len(data) // RECORD_SIZE):
                payload = data[j * RECORD_SIZE + 3 : j * RECORD_SIZE + 119]
                ts = struct.unpack("<I", payload[12:16])[0]
                if ts > 1_000_000_000:
                    all_points[ts] = payload

    return all_points, meta


def extract(payload):
    """Extract all sensor values from 116-byte payload."""
    hr = payload[19]
    rr_count = payload[20]
    rr_intervals = []
    for k in range(min(rr_count, 3)):
        rr = struct.unpack("<H", payload[21 + k * 2 : 23 + k * 2])[0]
        if 200 < rr < 2500:
            rr_intervals.append(rr)

    acc_x = struct.unpack(">f", payload[45:49])[0]
    acc_y = struct.unpack(">f", payload[49:53])[0]
    acc_z = struct.unpack(">f", payload[53:57])[0]

    if all(math.isfinite(v) for v in [acc_x, acc_y, acc_z]):
        movement = abs(math.sqrt(acc_x ** 2 + acc_y ** 2 + acc_z ** 2) - 1.0)
    else:
        movement = 0

    gyro = struct.unpack(">f", payload[29:33])[0]
    if not math.isfinite(gyro) or abs(gyro) > 100:
        gyro = 0

    spo2_raw = payload[55]
    spo2 = spo2_raw + 10 if spo2_raw > 0 else None

    return {
        "hr": hr,
        "rr_count": rr_count,
        "rr_intervals": rr_intervals,
        "acc_x": round(acc_x, 4) if math.isfinite(acc_x) else 0,
        "acc_y": round(acc_y, 4) if math.isfinite(acc_y) else 0,
        "acc_z": round(acc_z, 4) if math.isfinite(acc_z) else 0,
        "movement": round(movement, 4),
        "gyro": round(gyro, 4),
        "spo2": spo2,
        "byte17": payload[17],
        "byte55": spo2_raw,
        "byte66": payload[66],
        "byte68": payload[68],
        "byte105": payload[105],
        "byte106": payload[106],
        "byte16": payload[16],
        "byte70": payload[70],
    }


def classify_phase(avg_hr, hr_std, movement_dev, rhr):
    if avg_hr == 0:
        return "unknown"
    hr_above = avg_hr - rhr
    if avg_hr > rhr + 15 and movement_dev > 0.4:
        return "awake"
    elif hr_above < 4 and hr_std < 3 and movement_dev < 0.4:
        return "deep"
    elif hr_std > 5 and movement_dev < 0.5:
        return "rem"
    elif hr_above < 12:
        return "light"
    else:
        return "awake"


def compute_analytics(sorted_ts, metrics):
    """Compute all analytics for the dashboard."""
    N = len(sorted_ts)
    hrs = [metrics[ts]["hr"] for ts in sorted_ts if metrics[ts]["hr"] > 0]
    all_rr = []
    for ts in sorted_ts:
        all_rr.extend(metrics[ts]["rr_intervals"])

    # Resting HR
    rhr = 999
    rhr_time = None
    W = 300
    for i in range(0, N - W, 30):
        w = [metrics[sorted_ts[j]]["hr"] for j in range(i, min(i + W, N)) if metrics[sorted_ts[j]]["hr"] > 0]
        if w:
            avg = sum(w) / len(w)
            if avg < rhr:
                rhr = avg
                rhr_time = sorted_ts[i]

    # HRV
    rmssd = 0
    if len(all_rr) > 10:
        diffs_sq = [(all_rr[i + 1] - all_rr[i]) ** 2 for i in range(len(all_rr) - 1)]
        rmssd = math.sqrt(sum(diffs_sq) / len(diffs_sq))

    # Recovery score (simplified Whoop-style: based on HRV and RHR)
    # Higher HRV = better, lower RHR = better
    hrv_score = min(100, rmssd / 1.5)  # 150ms RMSSD = 100%
    rhr_score = max(0, 100 - (rhr - 40) * 3)  # 40 BPM = 100%, 73 BPM = 0%
    recovery = int((hrv_score * 0.6 + rhr_score * 0.4))
    recovery = max(0, min(100, recovery))

    # Sleep phases (5-min windows)
    phases = []
    for i in range(0, N - W, W):
        w_ts = sorted_ts[i : i + W]
        w_hr = [metrics[t]["hr"] for t in w_ts if metrics[t]["hr"] > 0]
        w_mv = [metrics[t]["movement"] for t in w_ts]
        w_rr = []
        for t in w_ts:
            w_rr.extend(metrics[t]["rr_intervals"])

        avg_hr = sum(w_hr) / len(w_hr) if w_hr else 0
        std_hr = (sum((h - avg_hr) ** 2 for h in w_hr) / len(w_hr)) ** 0.5 if len(w_hr) > 1 else 0
        avg_mv = sum(w_mv) / len(w_mv) if w_mv else 0
        avg_rr = sum(w_rr) / len(w_rr) if w_rr else 0

        phase = classify_phase(avg_hr, std_hr, avg_mv, rhr)
        t_berlin = datetime.fromtimestamp(w_ts[0], timezone.utc) + BERLIN

        phases.append({
            "time": t_berlin.strftime("%H:%M"),
            "ts": w_ts[0],
            "hr": round(avg_hr, 1),
            "hr_std": round(std_hr, 1),
            "movement": round(avg_mv, 3),
            "rr": round(avg_rr) if avg_rr else None,
            "phase": phase,
        })

    # Phase summary
    phase_mins = defaultdict(int)
    for p in phases:
        phase_mins[p["phase"]] += 5
    sleep_mins = sum(v for k, v in phase_mins.items() if k not in ("awake", "unknown"))

    # Timeseries (downsample to 10s for charts)
    ts_chart = []
    for i in range(0, N, 10):
        chunk = sorted_ts[i : i + 10]
        hrs_c = [metrics[t]["hr"] for t in chunk if metrics[t]["hr"] > 0]
        mvs = [metrics[t]["movement"] for t in chunk]
        spo2s = [metrics[t]["spo2"] for t in chunk if metrics[t]["spo2"]]
        rrs = []
        for t in chunk:
            rrs.extend(metrics[t]["rr_intervals"])

        t_berlin = datetime.fromtimestamp(chunk[0], timezone.utc) + BERLIN
        entry = {
            "t": t_berlin.strftime("%H:%M:%S"),
            "hr": round(sum(hrs_c) / len(hrs_c), 1) if hrs_c else 0,
            "mv": round(sum(mvs) / len(mvs), 3),
            "acc_x": round(metrics[chunk[0]]["acc_x"], 3),
            "acc_y": round(metrics[chunk[0]]["acc_y"], 3),
            "acc_z": round(metrics[chunk[0]]["acc_z"], 3),
        }
        if spo2s:
            entry["spo2"] = round(sum(spo2s) / len(spo2s), 1)
        if rrs:
            entry["rr"] = round(sum(rrs) / len(rrs))
        ts_chart.append(entry)

    # Unknown bytes analysis (1-min averages for chart)
    unknown_chart = []
    for i in range(0, N, 60):
        chunk = sorted_ts[i : i + 60]
        t_berlin = datetime.fromtimestamp(chunk[0], timezone.utc) + BERLIN
        entry = {"t": t_berlin.strftime("%H:%M")}
        for key in ["byte17", "byte55", "byte66", "byte68", "byte105", "byte106", "byte16", "byte70"]:
            vals = [metrics[t][key] for t in chunk]
            entry[key] = round(sum(vals) / len(vals), 1)
        unknown_chart.append(entry)

    # Correlation analysis for unknown bytes
    correlations = {}
    hr_list = [metrics[ts]["hr"] for ts in sorted_ts]
    mv_list = [metrics[ts]["movement"] for ts in sorted_ts]

    def corr(a, b):
        n = len(a)
        if n < 10:
            return 0
        ma, mb = sum(a) / n, sum(b) / n
        cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / n
        sa = (sum((x - ma) ** 2 for x in a) / n) ** 0.5
        sb = (sum((x - mb) ** 2 for x in b) / n) ** 0.5
        return round(cov / (sa * sb), 3) if sa * sb > 0 else 0

    for key in ["byte17", "byte55", "byte66", "byte68", "byte105", "byte106", "byte16", "byte70"]:
        vals = [metrics[ts][key] for ts in sorted_ts]
        correlations[key] = {
            "vs_hr": corr(vals, hr_list),
            "vs_movement": corr(vals, mv_list),
            "min": min(vals),
            "max": max(vals),
            "avg": round(sum(vals) / len(vals), 1),
        }

    first_dt = datetime.fromtimestamp(sorted_ts[0], timezone.utc) + BERLIN
    last_dt = datetime.fromtimestamp(sorted_ts[-1], timezone.utc) + BERLIN

    return {
        "meta": {
            "date": first_dt.strftime("%d.%m.%Y"),
            "start": first_dt.strftime("%H:%M:%S"),
            "end": last_dt.strftime("%H:%M:%S"),
            "duration_min": (sorted_ts[-1] - sorted_ts[0]) // 60,
            "datapoints": N,
        },
        "hr": {
            "avg": round(sum(hrs) / len(hrs), 1),
            "min": min(hrs),
            "max": max(hrs),
            "rhr": round(rhr, 1),
            "rhr_time": datetime.fromtimestamp(rhr_time, timezone.utc).strftime("%H:%M") + " UTC" if rhr_time else None,
        },
        "hrv": {
            "rmssd": round(rmssd, 1),
            "avg_rr": round(sum(all_rr) / len(all_rr)) if all_rr else 0,
            "rr_count": len(all_rr),
        },
        "recovery": recovery,
        "sleep": {
            "total_min": sleep_mins,
            "deep_min": phase_mins.get("deep", 0),
            "light_min": phase_mins.get("light", 0),
            "rem_min": phase_mins.get("rem", 0),
            "awake_min": phase_mins.get("awake", 0),
            "efficiency": round(100 * sleep_mins / (len(phases) * 5)) if phases else 0,
        },
        "phases": phases,
        "timeseries": ts_chart,
        "unknown_bytes": unknown_chart,
        "correlations": correlations,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Whoop Data Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #0a0a0a;
  --card: #141414;
  --card-border: #222;
  --text: #e0e0e0;
  --text-dim: #888;
  --green: #44cf6c;
  --green-dim: #2a7a3f;
  --yellow: #f5c542;
  --red: #e74c3c;
  --blue: #3498db;
  --purple: #9b59b6;
  --cyan: #00bcd4;
  --orange: #ff9800;
  --deep: #1a237e;
  --light-sleep: #42a5f5;
  --rem: #ab47bc;
  --awake: #ff7043;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
.container { max-width: 1200px; margin: 0 auto; padding: 16px; }

/* Header */
.header { text-align: center; padding: 24px 0; border-bottom: 1px solid var(--card-border); margin-bottom: 24px; }
.header h1 { font-size: 28px; font-weight: 700; letter-spacing: 2px; margin-bottom: 8px; }
.header h1 span { color: var(--green); }
.header .meta { color: var(--text-dim); font-size: 14px; }

/* Grid */
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; margin-bottom: 16px; }
.card { background: var(--card); border: 1px solid var(--card-border); border-radius: 12px; padding: 20px; }
.card h2 { font-size: 14px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); margin-bottom: 16px; }
.card .big-number { font-size: 48px; font-weight: 700; line-height: 1; }
.card .unit { font-size: 16px; color: var(--text-dim); margin-left: 4px; }
.card .sub { font-size: 13px; color: var(--text-dim); margin-top: 8px; }

/* Recovery circle */
.recovery-circle { width: 160px; height: 160px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 16px; position: relative; }
.recovery-circle .value { font-size: 56px; font-weight: 700; }
.recovery-circle .pct { font-size: 18px; color: var(--text-dim); }

/* Sleep bar */
.sleep-bar { display: flex; height: 24px; border-radius: 12px; overflow: hidden; margin: 12px 0; }
.sleep-bar .seg { transition: width 0.5s; }
.sleep-legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; margin-top: 8px; }
.sleep-legend .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 4px; vertical-align: middle; }

/* Hypnogram */
.hypno-row { display: flex; align-items: center; font-size: 11px; height: 18px; }
.hypno-row .time { width: 44px; color: var(--text-dim); text-align: right; margin-right: 8px; flex-shrink: 0; }
.hypno-row .bar { height: 14px; border-radius: 2px; }

/* Stat row */
.stat-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #1a1a1a; }
.stat-row:last-child { border: none; }
.stat-label { color: var(--text-dim); font-size: 13px; }
.stat-value { font-weight: 600; font-size: 14px; }

/* Chart */
.chart-container { position: relative; height: 250px; margin-top: 12px; }
.full-width { grid-column: 1 / -1; }

/* Correlation table */
.corr-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.corr-table th, .corr-table td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #1a1a1a; }
.corr-table th { color: var(--text-dim); font-weight: 500; }
.corr-bar { height: 8px; border-radius: 4px; display: inline-block; vertical-align: middle; }
</style>
</head>
<body>
<div class="container">

<!-- Header -->
<div class="header">
  <h1><span>WHOOP</span> DATA DASHBOARD</h1>
  <div class="meta" id="meta-info"></div>
</div>

<!-- Top cards -->
<div class="grid">
  <!-- Recovery -->
  <div class="card" style="text-align:center">
    <h2>Recovery</h2>
    <div class="recovery-circle" id="recovery-circle">
      <div><span class="value" id="recovery-val"></span><span class="pct">%</span></div>
    </div>
    <div class="sub" id="recovery-sub"></div>
  </div>

  <!-- Heart Rate -->
  <div class="card">
    <h2>Herzfrequenz</h2>
    <div class="big-number" id="hr-avg"></div>
    <div class="sub">Durchschnitt BPM</div>
    <div style="margin-top:16px">
      <div class="stat-row"><span class="stat-label">Minimum</span><span class="stat-value" id="hr-min"></span></div>
      <div class="stat-row"><span class="stat-label">Maximum</span><span class="stat-value" id="hr-max"></span></div>
      <div class="stat-row"><span class="stat-label">Ruhe-HR</span><span class="stat-value" id="hr-rhr"></span></div>
    </div>
  </div>

  <!-- HRV -->
  <div class="card">
    <h2>HRV</h2>
    <div class="big-number" style="color:var(--green)" id="hrv-rmssd"></div>
    <div class="sub">RMSSD (ms)</div>
    <div style="margin-top:16px">
      <div class="stat-row"><span class="stat-label">RR-Intervall Ø</span><span class="stat-value" id="hrv-rr"></span></div>
      <div class="stat-row"><span class="stat-label">Gültige RR</span><span class="stat-value" id="hrv-count"></span></div>
    </div>
  </div>
</div>

<!-- Sleep -->
<div class="grid">
  <div class="card full-width">
    <h2>Schlaf</h2>
    <div style="display:flex;gap:32px;flex-wrap:wrap;margin-bottom:16px">
      <div><span class="big-number" id="sleep-total"></span><span class="unit">min</span><div class="sub">Schlafzeit</div></div>
      <div><span class="big-number" style="font-size:36px" id="sleep-eff"></span><span class="unit">%</span><div class="sub">Effizienz</div></div>
    </div>
    <div class="sleep-bar" id="sleep-bar"></div>
    <div class="sleep-legend" id="sleep-legend"></div>
    <div style="margin-top:16px;display:flex;gap:24px;flex-wrap:wrap">
      <div class="stat-row" style="flex:1;min-width:120px"><span class="stat-label">Tiefschlaf</span><span class="stat-value" id="sleep-deep"></span></div>
      <div class="stat-row" style="flex:1;min-width:120px"><span class="stat-label">Leichtschlaf</span><span class="stat-value" id="sleep-light"></span></div>
      <div class="stat-row" style="flex:1;min-width:120px"><span class="stat-label">REM</span><span class="stat-value" id="sleep-rem"></span></div>
      <div class="stat-row" style="flex:1;min-width:120px"><span class="stat-label">Wach</span><span class="stat-value" id="sleep-awake"></span></div>
    </div>
  </div>
</div>

<!-- Hypnogram -->
<div class="grid">
  <div class="card full-width">
    <h2>Hypnogramm</h2>
    <div id="hypnogram"></div>
  </div>
</div>

<!-- HR Chart -->
<div class="grid">
  <div class="card full-width">
    <h2>Herzfrequenz-Verlauf</h2>
    <div class="chart-container"><canvas id="hrChart"></canvas></div>
  </div>
</div>

<!-- Movement + SpO2 -->
<div class="grid">
  <div class="card">
    <h2>Bewegung</h2>
    <div class="chart-container"><canvas id="movChart"></canvas></div>
  </div>
  <div class="card">
    <h2>SpO2 (Blutsauerstoff)</h2>
    <div class="chart-container"><canvas id="spo2Chart"></canvas></div>
  </div>
</div>

<!-- Accelerometer -->
<div class="grid">
  <div class="card full-width">
    <h2>Beschleunigungsmesser (3 Achsen)</h2>
    <div class="chart-container"><canvas id="accelChart"></canvas></div>
  </div>
</div>

<!-- Unknown bytes -->
<div class="grid">
  <div class="card full-width">
    <h2>Unbekannte Byte-Felder</h2>
    <div class="chart-container" style="height:300px"><canvas id="unknownChart"></canvas></div>
  </div>
</div>

<!-- Correlation table -->
<div class="grid">
  <div class="card full-width">
    <h2>Byte-Feld Korrelationsanalyse</h2>
    <table class="corr-table" id="corr-table">
      <thead><tr><th>Feld</th><th>Min</th><th>Max</th><th>Avg</th><th>Korr. HR</th><th></th><th>Korr. Bewegung</th><th></th><th>Vermutung</th></tr></thead>
      <tbody id="corr-body"></tbody>
    </table>
  </div>
</div>

</div>

<script>
const DATA = __DATA_PLACEHOLDER__;

// Helper
const $ = id => document.getElementById(id);
const fmt = (n, d=0) => n != null ? n.toFixed(d) : '-';

// Meta
$('meta-info').innerHTML = `${DATA.meta.date} &middot; ${DATA.meta.start} – ${DATA.meta.end} &middot; ${DATA.meta.duration_min} min &middot; ${DATA.meta.datapoints} Datenpunkte`;

// Recovery
const rec = DATA.recovery;
const recColor = rec >= 67 ? '#44cf6c' : rec >= 34 ? '#f5c542' : '#e74c3c';
$('recovery-val').textContent = rec;
$('recovery-val').style.color = recColor;
$('recovery-circle').style.background = `conic-gradient(${recColor} ${rec*3.6}deg, #222 0)`;
$('recovery-sub').textContent = `HRV ${DATA.hrv.rmssd}ms · RHR ${DATA.hr.rhr} BPM`;

// HR
$('hr-avg').innerHTML = `${fmt(DATA.hr.avg)}<span class="unit">BPM</span>`;
$('hr-min').textContent = `${DATA.hr.min} BPM`;
$('hr-max').textContent = `${DATA.hr.max} BPM`;
$('hr-rhr').textContent = `${fmt(DATA.hr.rhr)} BPM`;

// HRV
$('hrv-rmssd').textContent = fmt(DATA.hrv.rmssd, 1);
$('hrv-rr').textContent = `${DATA.hrv.avg_rr} ms`;
$('hrv-count').textContent = DATA.hrv.rr_count.toLocaleString();

// Sleep
const sl = DATA.sleep;
$('sleep-total').textContent = sl.total_min;
$('sleep-eff').textContent = sl.efficiency;
$('sleep-deep').textContent = `${sl.deep_min} min`;
$('sleep-light').textContent = `${sl.light_min} min`;
$('sleep-rem').textContent = `${sl.rem_min} min`;
$('sleep-awake').textContent = `${sl.awake_min} min`;

const totalPhase = sl.deep_min + sl.light_min + sl.rem_min + sl.awake_min || 1;
const phases = [
  {name:'Tiefschlaf', min:sl.deep_min, color:'#1a237e'},
  {name:'Leichtschlaf', min:sl.light_min, color:'#42a5f5'},
  {name:'REM', min:sl.rem_min, color:'#ab47bc'},
  {name:'Wach', min:sl.awake_min, color:'#ff7043'},
];
$('sleep-bar').innerHTML = phases.map(p => `<div class="seg" style="width:${p.min/totalPhase*100}%;background:${p.color}"></div>`).join('');
$('sleep-legend').innerHTML = phases.map(p => `<span><span class="dot" style="background:${p.color}"></span>${p.name} ${p.min}min</span>`).join('');

// Hypnogram
const phaseMap = {deep:3, light:2, rem:1, awake:0, unknown:0};
const phaseColors = {deep:'#1a237e', light:'#42a5f5', rem:'#ab47bc', awake:'#ff7043', unknown:'#333'};
const phaseLabels = {0:'Wach', 1:'REM', 2:'Leicht', 3:'Tief'};
$('hypnogram').innerHTML = DATA.phases.map(p => {
  const d = phaseMap[p.phase];
  const w = 100 - d * 20;
  return `<div class="hypno-row"><span class="time">${p.time}</span><div class="bar" style="width:${w}%;background:${phaseColors[p.phase]};margin-left:${d*20}%"></div></div>`;
}).join('');

// Chart defaults
Chart.defaults.color = '#888';
Chart.defaults.borderColor = '#1a1a1a';
Chart.defaults.font.size = 11;
const chartOpts = (title) => ({
  responsive: true, maintainAspectRatio: false,
  plugins: { legend: { display: false }, title: { display: false } },
  scales: {
    x: { ticks: { maxTicksLimit: 15, maxRotation: 0 }, grid: { display: false } },
    y: { grid: { color: '#1a1a1a' } }
  },
  elements: { point: { radius: 0 }, line: { borderWidth: 1.5 } },
  animation: false,
});

const ts = DATA.timeseries;
const labels = ts.map(d => d.t);

// HR Chart with phase background
const hrCtx = $('hrChart').getContext('2d');
new Chart(hrCtx, {
  type: 'line',
  data: {
    labels,
    datasets: [{
      data: ts.map(d => d.hr),
      borderColor: '#e74c3c',
      backgroundColor: 'rgba(231,76,60,0.1)',
      fill: true,
    }]
  },
  options: {...chartOpts(), scales: {...chartOpts().scales, y: {min: 40, grid:{color:'#1a1a1a'}}}}
});

// Movement
new Chart($('movChart'), {
  type: 'line',
  data: { labels, datasets: [{
    data: ts.map(d => d.mv), borderColor: '#f5c542', backgroundColor: 'rgba(245,197,66,0.1)', fill: true
  }]},
  options: chartOpts()
});

// SpO2
const spo2Data = ts.map(d => d.spo2 || null);
new Chart($('spo2Chart'), {
  type: 'line',
  data: { labels, datasets: [{
    data: spo2Data, borderColor: '#00bcd4', backgroundColor: 'rgba(0,188,212,0.1)', fill: true, spanGaps: true
  }]},
  options: {...chartOpts(), scales: {...chartOpts().scales, y: {min: 90, max: 100, grid:{color:'#1a1a1a'}}}}
});

// Accelerometer
new Chart($('accelChart'), {
  type: 'line',
  data: { labels, datasets: [
    { label: 'X', data: ts.map(d => d.acc_x), borderColor: '#e74c3c' },
    { label: 'Y', data: ts.map(d => d.acc_y), borderColor: '#44cf6c' },
    { label: 'Z', data: ts.map(d => d.acc_z), borderColor: '#3498db' },
  ]},
  options: {...chartOpts(), plugins: { legend: { display: true, labels: { boxWidth: 12 }}}}
});

// Unknown bytes
const ub = DATA.unknown_bytes;
const ubLabels = ub.map(d => d.t);
const byteKeys = ['byte17','byte55','byte66','byte68','byte105','byte106'];
const byteColors = ['#e74c3c','#00bcd4','#f5c542','#ff9800','#9b59b6','#44cf6c'];
const byteNames = {byte17:'Byte 17 (?)', byte55:'Byte 55 (SpO2?)', byte66:'Byte 66', byte68:'Byte 68', byte105:'Byte 105', byte106:'Byte 106'};
new Chart($('unknownChart'), {
  type: 'line',
  data: { labels: ubLabels, datasets: byteKeys.map((k,i) => ({
    label: byteNames[k], data: ub.map(d => d[k]), borderColor: byteColors[i], borderWidth: 1.5
  }))},
  options: {...chartOpts(), plugins: { legend: { display: true, labels: { boxWidth: 12 }}}}
});

// Correlation table
const guesses = {
  byte17: 'Atemfrequenz (encoded)?',
  byte55: 'SpO2 raw (+10 = %)',
  byte66: 'Hauttemp / PPG?',
  byte68: 'Hauttemp #2?',
  byte105: 'PPG-Amplitude (r=0.67 HR)',
  byte106: 'PPG-Amplitude #2',
  byte16: 'Aktivitätstyp / Flags',
  byte70: 'Unklar',
};
const corrBody = $('corr-body');
Object.entries(DATA.correlations).forEach(([key, c]) => {
  const corrHR = c.vs_hr;
  const corrMV = c.vs_movement;
  const barHR = `<span class="corr-bar" style="width:${Math.abs(corrHR)*80}px;background:${corrHR>0?'#44cf6c':'#e74c3c'}"></span>`;
  const barMV = `<span class="corr-bar" style="width:${Math.abs(corrMV)*80}px;background:${corrMV>0?'#3498db':'#ff9800'}"></span>`;
  corrBody.innerHTML += `<tr>
    <td style="font-weight:600">${key}</td>
    <td>${c.min}</td><td>${c.max}</td><td>${c.avg}</td>
    <td>${corrHR.toFixed(3)}</td><td>${barHR}</td>
    <td>${corrMV.toFixed(3)}</td><td>${barMV}</td>
    <td style="color:var(--text-dim)">${guesses[key]||'?'}</td>
  </tr>`;
});
</script>
</body>
</html>"""


def main():
    print("Loading HAR files...")
    all_points, device_meta = load_all_data()

    # Filter to main data block (sample date)
    jan31 = {ts: p for ts, p in all_points.items() if ts > 1769800000}
    sorted_ts = sorted(jan31.keys())
    print(f"  {len(sorted_ts)} datapoints")

    print("Extracting metrics...")
    metrics = {ts: extract(jan31[ts]) for ts in sorted_ts}

    print("Computing analytics...")
    analytics = compute_analytics(sorted_ts, metrics)
    analytics["device"] = device_meta

    print("Generating dashboard...")
    data_json = json.dumps(analytics, separators=(",", ":"))
    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", data_json)

    out = Path("dashboard.html")
    out.write_text(html)
    print(f"  Written: {out} ({len(html)//1024} KB)")
    print("Done!")


if __name__ == "__main__":
    main()
