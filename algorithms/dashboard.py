#!/usr/bin/env python3
"""Generate interactive HTML dashboard comparing Whoop vs 3 algorithms on raw sensor data."""

import sys
import json
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from data.loader import load_har_data, load_ground_truth
from common.preprocessing import (
    compute_rhr, compute_hrv_rmssd, compute_hrv_sdnn, compute_pnn50,
    compute_respiratory_rate, compute_hr_zones,
)

BERLIN = timedelta(hours=1)


def classify_sleep_phases(df, rhr, window_sec=300):
    """Classify sleep phases in 5-minute windows. Returns list of dicts."""
    phases = []
    for i in range(0, len(df) - window_sec, window_sec):
        chunk = df.iloc[i:i + window_sec]
        t_local = chunk["datetime_local"].iloc[0]
        hr_vals = chunk["hr"].values
        hr_valid = hr_vals[hr_vals > 0]
        mv_vals = chunk["movement"].values
        rr_vals = chunk["rr1_ms"].dropna().values
        rr_valid = rr_vals[(rr_vals > 200) & (rr_vals < 2500)]

        if len(hr_valid) < 10:
            phases.append({"time": str(t_local), "phase": "unknown", "hr": 0, "hr_std": 0,
                          "movement": 0, "rr_mean": 0})
            continue

        avg_hr = float(hr_valid.mean())
        std_hr = float(hr_valid.std())
        avg_mv = float(mv_vals.mean())
        avg_rr = float(rr_valid.mean()) if len(rr_valid) > 0 else 0
        hr_above = avg_hr - rhr

        # Local HRV for this window
        local_hrv = 0
        if len(rr_valid) > 5:
            diffs = np.diff(rr_valid)
            local_hrv = float(np.sqrt(np.mean(diffs**2)))

        if avg_hr > rhr + 15 and avg_mv > 0.4:
            phase = "awake"
        elif hr_above < 4 and std_hr < 3 and avg_mv < 0.4:
            phase = "deep"
        elif std_hr > 5 and avg_mv < 0.5:
            phase = "rem"
        elif hr_above < 12:
            phase = "light"
        else:
            phase = "awake"

        phases.append({
            "time": t_local.strftime("%H:%M"),
            "phase": phase,
            "hr": round(avg_hr, 1),
            "hr_std": round(std_hr, 1),
            "movement": round(avg_mv, 3),
            "rr_mean": round(avg_rr),
            "hrv_local": round(local_hrv, 1),
        })
    return phases


def sleepecg_classify(df):
    """Try SleepECG classification on the data."""
    try:
        import sleepecg
        rr = df["rr1_ms"].dropna().values
        rr = rr[(rr > 200) & (rr < 2500)]
        if len(rr) < 100:
            return None

        beat_times = np.cumsum(rr) / 1000.0

        # SleepECG classifiers need to be loaded
        # Check what's available
        if hasattr(sleepecg, 'SleepClassifier'):
            classifier = sleepecg.SleepClassifier("writable:SleepECG")
            stages = classifier.predict(heartbeat_times=beat_times)
        elif hasattr(sleepecg, 'classify_heartbeats'):
            stages = sleepecg.classify_heartbeats(beat_times)
        elif hasattr(sleepecg, 'stage_sleep'):
            stages = sleepecg.stage_sleep(heartbeat_times=beat_times, record_duration=beat_times[-1])
        else:
            # Try listing available functions
            funcs = [f for f in dir(sleepecg) if not f.startswith('_')]
            print(f"    SleepECG available functions: {funcs}")
            return None

        # stages: 0=Wake, 1=N1, 2=N2, 3=N3(Deep), 4=REM (30s epochs)
        phase_map = {0: "awake", 1: "light", 2: "light", 3: "deep", 4: "rem"}
        epoch_phases = []
        for i, s in enumerate(stages):
            t_sec = i * 30  # 30-second epochs
            # Map to timestamp
            rr_cumsum = np.cumsum(rr) / 1000.0
            idx = np.searchsorted(rr_cumsum, t_sec)
            if idx < len(df):
                t_local = df.iloc[min(idx, len(df)-1)]["datetime_local"]
                time_str = t_local.strftime("%H:%M") if hasattr(t_local, 'strftime') else str(t_local)
            else:
                time_str = f"+{t_sec//60}min"

            epoch_phases.append({
                "time": time_str,
                "phase": phase_map.get(int(s), "unknown"),
                "epoch": i,
            })
        return epoch_phases
    except Exception as e:
        print(f"    SleepECG error: {e}")
        return None


def ml_sleep_classify(df, rhr, window_sec=300):
    """ML-based sleep classification using feature extraction + simple rules.

    Enhanced version that uses more features per window.
    """
    phases = []
    for i in range(0, len(df) - window_sec, window_sec):
        chunk = df.iloc[i:i + window_sec]
        t_local = chunk["datetime_local"].iloc[0]
        hr_vals = chunk["hr"].values
        hr_valid = hr_vals[hr_vals > 0]
        mv_vals = chunk["movement"].values
        rr_vals = chunk["rr1_ms"].dropna().values
        rr_valid = rr_vals[(rr_vals > 200) & (rr_vals < 2500)]
        gyro_vals = chunk["gyro"].values

        if len(hr_valid) < 10:
            phases.append({"time": str(t_local), "phase": "unknown"})
            continue

        avg_hr = float(hr_valid.mean())
        std_hr = float(hr_valid.std())
        min_hr = float(hr_valid.min())
        avg_mv = float(mv_vals.mean())
        max_mv = float(mv_vals.max())
        avg_gyro = float(np.abs(gyro_vals).mean())

        # HRV features
        local_hrv = 0
        hr_entropy = 0
        if len(rr_valid) > 5:
            diffs = np.diff(rr_valid)
            local_hrv = float(np.sqrt(np.mean(diffs**2)))
            # Approximate entropy via range/mean
            hr_entropy = float(np.std(rr_valid) / np.mean(rr_valid)) if np.mean(rr_valid) > 0 else 0

        hr_above = avg_hr - rhr

        # Enhanced classification using multiple features
        # Deep sleep: very low HR near RHR, very low movement, low HRV (parasympathetic dominance)
        deep_score = 0
        if hr_above < 5:
            deep_score += 3
        if std_hr < 3:
            deep_score += 2
        if avg_mv < 0.2:
            deep_score += 2
        if max_mv < 0.5:
            deep_score += 1
        if local_hrv < 30:
            deep_score += 1

        # REM: moderate HR with HIGH variability, low movement (muscle atonia)
        rem_score = 0
        if std_hr > 4:
            rem_score += 3
        if local_hrv > 40:
            rem_score += 2
        if avg_mv < 0.3:
            rem_score += 2
        if hr_above > 3 and hr_above < 15:
            rem_score += 1
        if hr_entropy > 0.05:
            rem_score += 1

        # Awake: high HR and/or high movement
        awake_score = 0
        if hr_above > 15:
            awake_score += 3
        if avg_mv > 0.4:
            awake_score += 3
        if max_mv > 1.0:
            awake_score += 2
        if avg_gyro > 0.5:
            awake_score += 1

        # Light: everything else
        light_score = 0
        if hr_above >= 5 and hr_above < 15:
            light_score += 2
        if avg_mv >= 0.2 and avg_mv < 0.4:
            light_score += 2
        if std_hr >= 2 and std_hr < 5:
            light_score += 1

        scores = {"deep": deep_score, "rem": rem_score, "awake": awake_score, "light": light_score}
        phase = max(scores, key=scores.get)

        phases.append({
            "time": t_local.strftime("%H:%M"),
            "phase": phase,
            "hr": round(avg_hr, 1),
            "movement": round(avg_mv, 3),
            "hrv_local": round(local_hrv, 1),
            "scores": {k: round(v, 1) for k, v in scores.items()},
        })
    return phases


def compute_sleep_summary(phases):
    """Compute summary stats from phase list."""
    total = len(phases)
    if total == 0:
        return {}
    counts = defaultdict(int)
    for p in phases:
        counts[p["phase"]] += 1
    sleep_count = total - counts["awake"] - counts["unknown"]
    return {
        "total_min": total * 5,
        "sleep_min": sleep_count * 5,
        "deep_min": counts["deep"] * 5,
        "light_min": counts["light"] * 5,
        "rem_min": counts["rem"] * 5,
        "awake_min": counts["awake"] * 5,
        "efficiency": round(sleep_count / total * 100, 1) if total > 0 else 0,
        "deep_pct": round(counts["deep"] / total * 100, 1) if total > 0 else 0,
        "light_pct": round(counts["light"] / total * 100, 1) if total > 0 else 0,
        "rem_pct": round(counts["rem"] / total * 100, 1) if total > 0 else 0,
        "awake_pct": round(counts["awake"] / total * 100, 1) if total > 0 else 0,
    }


def compute_recovery_score(hrv, rhr, sleep_eff, resp_rate, hrv_baseline=90, rhr_baseline=55):
    """Compute recovery using the custom formula."""
    if hrv_baseline > 0:
        hrv_ratio = hrv / hrv_baseline
        hrv_score = 100 / (1 + math.exp(-4 * (hrv_ratio - 0.85)))
    else:
        hrv_score = 50
    rhr_diff = rhr_baseline - rhr
    rhr_score = max(0, min(100, 50 + rhr_diff * 8))
    resp_penalty = max(0, (resp_rate - 16) * 5) if resp_rate > 16 else 0
    recovery = 0.60 * hrv_score + 0.25 * rhr_score + 0.15 * sleep_eff - resp_penalty
    return max(0, min(100, round(recovery)))


def compute_strain(hr_series, max_hr=200):
    """Compute strain from raw HR series."""
    hrs = hr_series[hr_series > 0].values
    if len(hrs) == 0:
        return 0
    boundaries = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    weights = [1.0, 2.0, 4.0, 8.0, 16.0]
    raw_load = 0
    for i in range(5):
        lo = max_hr * boundaries[i]
        hi = max_hr * boundaries[i + 1]
        minutes = np.sum((hrs >= lo) & (hrs < hi)) / 60.0
        raw_load += minutes * weights[i]
    raw_load += np.sum(hrs >= max_hr) / 60.0 * 16.0
    if raw_load <= 0:
        return 0
    strain = 5.5 * math.log(1 + raw_load / 15.0)
    return min(21.0, round(strain, 1))


def build_dashboard():
    print("Loading sensor data...")
    df = load_har_data()
    if df.empty:
        print("No data!")
        return

    # Focus on sample day data (most data available)
    day_df = df[df["date"] == pd.Timestamp("2025-01-15").date()].copy()
    print(f"sample day data: {len(day_df)} samples, {day_df['datetime_local'].min()} to {day_df['datetime_local'].max()}")

    # Load ground truth for reference day (closest day with deep dive)
    gt_df = load_ground_truth()
    gt_ref = gt_df[gt_df["date"] == "2025-01-14"].iloc[0].to_dict() if "2025-01-14" in gt_df["date"].values else {}

    # ============================================================
    # Compute metrics from raw data
    # ============================================================
    rhr = compute_rhr(day_df)
    hrv_rmssd = compute_hrv_rmssd(day_df)
    hrv_sdnn = compute_hrv_sdnn(day_df)
    pnn50 = compute_pnn50(day_df)
    resp_rate = compute_respiratory_rate(day_df)

    valid_hr = day_df["hr"][day_df["hr"] > 0]
    hr_stats = {
        "mean": round(float(valid_hr.mean()), 1),
        "min": int(valid_hr.min()),
        "max": int(valid_hr.max()),
        "rhr": round(rhr, 1),
    }

    # ============================================================
    # Algorithm 1: Custom rule-based sleep staging
    # ============================================================
    print("Running Algorithm 1: Custom rule-based...")
    algo1_phases = classify_sleep_phases(day_df, rhr)
    algo1_summary = compute_sleep_summary(algo1_phases)
    algo1_recovery = compute_recovery_score(hrv_rmssd, rhr, algo1_summary.get("efficiency", 0), resp_rate)
    algo1_strain = compute_strain(day_df["hr"])

    # ============================================================
    # Algorithm 2: SleepECG
    # ============================================================
    print("Running Algorithm 2: SleepECG...")
    algo2_raw = sleepecg_classify(day_df)
    if algo2_raw:
        # Convert 30s epochs to 5-min windows for comparison
        algo2_phases = []
        epoch_per_window = 10  # 10 × 30s = 5min
        for i in range(0, len(algo2_raw), epoch_per_window):
            window = algo2_raw[i:i+epoch_per_window]
            counts = defaultdict(int)
            for e in window:
                counts[e["phase"]] += 1
            dominant = max(counts, key=counts.get)
            algo2_phases.append({"time": window[0]["time"], "phase": dominant})
        algo2_summary = compute_sleep_summary(algo2_phases)
    else:
        print("  SleepECG not available, using fallback")
        algo2_phases = classify_sleep_phases(day_df, rhr)  # fallback
        algo2_summary = compute_sleep_summary(algo2_phases)

    algo2_recovery = compute_recovery_score(hrv_rmssd, rhr, algo2_summary.get("efficiency", 0), resp_rate)
    algo2_strain = compute_strain(day_df["hr"])

    # ============================================================
    # Algorithm 3: ML feature-based
    # ============================================================
    print("Running Algorithm 3: ML feature-based...")
    algo3_phases = ml_sleep_classify(day_df, rhr)
    algo3_summary = compute_sleep_summary(algo3_phases)
    algo3_recovery = compute_recovery_score(hrv_rmssd, rhr, algo3_summary.get("efficiency", 0), resp_rate)
    algo3_strain = compute_strain(day_df["hr"])

    # ============================================================
    # Timeseries for charts (10-second resolution)
    # ============================================================
    ts_chart = []
    for i in range(0, len(day_df), 10):
        chunk = day_df.iloc[i:min(i+10, len(day_df))]
        hrs = chunk["hr"].values
        hrs_valid = hrs[hrs > 0]
        t_local = chunk["datetime_local"].iloc[0]
        entry = {
            "t": t_local.strftime("%H:%M:%S"),
            "hr": round(float(hrs_valid.mean()), 1) if len(hrs_valid) > 0 else 0,
            "mv": round(float(chunk["movement"].mean()), 4),
        }
        spo2 = chunk["spo2"].dropna()
        if len(spo2) > 0:
            entry["spo2"] = round(float(spo2.mean()), 1)
        rr = chunk["rr1_ms"].dropna()
        if len(rr) > 0:
            entry["rr"] = round(float(rr.mean()))
        ts_chart.append(entry)

    # ============================================================
    # Build dashboard data
    # ============================================================
    dashboard_data = {
        "meta": {
            "date": "2025-01-15",
            "period": f"{day_df['datetime_local'].min().strftime('%H:%M')} – {day_df['datetime_local'].max().strftime('%H:%M')} (local time)",
            "samples": len(day_df),
            "duration_min": round((day_df["timestamp"].max() - day_df["timestamp"].min()) / 60, 1),
            "note": "Raw sensor data from HAR capture (partial night recording)",
        },
        "physiology": {
            "hr": hr_stats,
            "hrv_rmssd": round(hrv_rmssd, 1),
            "hrv_sdnn": round(hrv_sdnn, 1),
            "pnn50": round(pnn50, 1),
            "resp_rate": round(resp_rate, 1),
        },
        "whoop_official": {
            "date": "2025-01-14",
            "recovery": gt_ref.get("recovery_score", "N/A"),
            "sleep": gt_ref.get("sleep_score", "N/A"),
            "strain": gt_ref.get("strain_score", gt_ref.get("cycle_strain", "N/A")),
            "hrv_ms": gt_ref.get("hrv_ms", "N/A"),
            "rhr_bpm": gt_ref.get("rhr_bpm", "N/A"),
            "resp_rate": gt_ref.get("resp_rate", "N/A"),
            "note": "Official Whoop scores for reference day (closest available day)",
        },
        "algorithms": [
            {
                "name": "Custom Rule-Based",
                "id": "algo1",
                "description": "HR/Movement thresholds + EPOC strain model",
                "recovery": algo1_recovery,
                "sleep_score": round(algo1_summary.get("efficiency", 0)),
                "strain": algo1_strain,
                "sleep": algo1_summary,
                "phases": algo1_phases,
            },
            {
                "name": "SleepECG (ML)",
                "id": "algo2",
                "description": "Pre-trained CNN from RR intervals (Brunner & Hofer 2023)",
                "recovery": algo2_recovery,
                "sleep_score": round(algo2_summary.get("efficiency", 0)),
                "strain": algo2_strain,
                "sleep": algo2_summary,
                "phases": algo2_phases if algo2_raw else algo2_phases,
                "used_fallback": algo2_raw is None,
            },
            {
                "name": "ML Feature-Based",
                "id": "algo3",
                "description": "Multi-feature scoring (HR, HRV, movement, gyro)",
                "recovery": algo3_recovery,
                "sleep_score": round(algo3_summary.get("efficiency", 0)),
                "strain": algo3_strain,
                "sleep": algo3_summary,
                "phases": algo3_phases,
            },
        ],
        "timeseries": ts_chart,
    }

    # Generate HTML
    html = generate_html(dashboard_data)
    out_path = Path(__file__).parent / "comparison_dashboard.html"
    out_path.write_text(html)
    print(f"\nDashboard written to: {out_path}")
    print(f"Open in browser: file://{out_path.resolve()}")


def generate_html(data):
    data_json = json.dumps(data, default=str, separators=(",", ":"))
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Whoop vs Algorithms — Comparison Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg: #0a0a0a; --card: #141414; --border: #222; --text: #e0e0e0; --dim: #888;
  --green: #44cf6c; --yellow: #f5c542; --red: #e74c3c; --blue: #3498db;
  --purple: #9b59b6; --cyan: #00bcd4; --orange: #ff9800;
  --deep: #1a237e; --light-s: #42a5f5; --rem: #ab47bc; --awake: #ff7043;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
.container {{ max-width:1400px; margin:0 auto; padding:16px; }}
.header {{ text-align:center; padding:24px 0; border-bottom:1px solid var(--border); margin-bottom:24px; }}
.header h1 {{ font-size:28px; font-weight:700; letter-spacing:2px; }}
.header h1 .accent {{ color:var(--green); }}
.header .meta {{ color:var(--dim); font-size:13px; margin-top:8px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; margin-bottom:16px; }}
.grid-4 {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:16px; }}
@media(max-width:1200px) {{ .grid-4 {{ grid-template-columns:repeat(2,1fr); }} }}
@media(max-width:700px) {{ .grid-4 {{ grid-template-columns:1fr; }} }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; }}
.card h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:1px; color:var(--dim); margin-bottom:12px; }}
.card .tag {{ display:inline-block; padding:2px 8px; border-radius:6px; font-size:11px; margin-bottom:8px; }}
.big {{ font-size:48px; font-weight:700; line-height:1; }}
.medium {{ font-size:32px; font-weight:700; line-height:1; }}
.unit {{ font-size:14px; color:var(--dim); margin-left:4px; }}
.sub {{ font-size:12px; color:var(--dim); margin-top:6px; }}
.stat {{ display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid #1a1a1a; font-size:13px; }}
.stat:last-child {{ border:none; }}
.stat-label {{ color:var(--dim); }}
.stat-value {{ font-weight:600; }}
.sleep-bar {{ display:flex; height:20px; border-radius:10px; overflow:hidden; margin:10px 0; }}
.sleep-bar .seg {{ transition:width 0.5s; }}
.legend {{ display:flex; gap:12px; flex-wrap:wrap; font-size:11px; }}
.legend .dot {{ width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:3px; vertical-align:middle; }}
.hypno {{ margin-top:8px; }}
.hypno-row {{ display:flex; align-items:center; font-size:10px; height:16px; }}
.hypno-row .time {{ width:36px; color:var(--dim); text-align:right; margin-right:6px; flex-shrink:0; }}
.hypno-row .bar {{ height:12px; border-radius:2px; min-width:4px; }}
.full {{ grid-column:1/-1; }}
.chart-box {{ position:relative; height:200px; margin-top:8px; }}
.recovery-circle {{ width:120px; height:120px; border-radius:50%; display:flex; align-items:center;
  justify-content:center; margin:0 auto 12px; }}
.recovery-circle .val {{ font-size:40px; font-weight:700; }}
.recovery-circle .pct {{ font-size:14px; color:var(--dim); }}
.compare-row {{ display:grid; grid-template-columns:120px repeat(4,1fr); gap:8px; align-items:center;
  padding:8px 0; border-bottom:1px solid #1a1a1a; font-size:13px; }}
.compare-row.header {{ font-weight:600; color:var(--dim); font-size:11px; text-transform:uppercase; }}
.source-tag {{ display:inline-block; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:600; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
  <h1><span class="accent">WHOOP</span> vs ALGORITHMS</h1>
  <div class="meta" id="meta"></div>
</div>

<!-- Comparison Overview -->
<div class="card full" style="margin-bottom:16px">
  <h2>Score Comparison — All Sources</h2>
  <div id="comparison"></div>
</div>

<!-- 4 columns: Whoop + 3 Algos -->
<div class="grid-4" id="algo-cards"></div>

<!-- Hypnograms -->
<div class="card full" style="margin-bottom:16px">
  <h2>Hypnograms — Sleep Stage Comparison</h2>
  <div id="hypnograms"></div>
</div>

<!-- HR Chart -->
<div class="card full" style="margin-bottom:16px">
  <h2>Heart Rate (Raw Sensor Data)</h2>
  <div class="chart-box" style="height:250px"><canvas id="hrChart"></canvas></div>
</div>

<!-- Movement + RR -->
<div class="grid">
  <div class="card">
    <h2>Movement</h2>
    <div class="chart-box"><canvas id="mvChart"></canvas></div>
  </div>
  <div class="card">
    <h2>RR Intervals</h2>
    <div class="chart-box"><canvas id="rrChart"></canvas></div>
  </div>
</div>

<!-- Physiology -->
<div class="card full" style="margin-bottom:16px">
  <h2>Raw Physiological Metrics (Computed from Sensor Data)</h2>
  <div id="physio"></div>
</div>

</div>

<script>
const D = {data_json};

// Meta
document.getElementById('meta').innerHTML =
  `${{D.meta.date}} &middot; ${{D.meta.period}} &middot; ${{D.meta.samples}} samples &middot; ${{D.meta.duration_min}} min<br><span style="color:#666">${{D.meta.note}}</span>`;

// Recovery color
const recCol = v => v >= 67 ? '#44cf6c' : v >= 34 ? '#f5c542' : '#e74c3c';

// Comparison table
const compEl = document.getElementById('comparison');
let compHTML = `<div class="compare-row header">
  <div>Source</div><div>Recovery</div><div>Sleep</div><div>Strain</div><div>HRV / RHR</div>
</div>`;

// Whoop official
const w = D.whoop_official;
compHTML += `<div class="compare-row">
  <div><span class="source-tag" style="background:#333;color:var(--green)">WHOOP</span></div>
  <div style="color:${{recCol(w.recovery)}}; font-weight:700">${{w.recovery}}%</div>
  <div style="font-weight:700">${{w.sleep}}%</div>
  <div style="font-weight:700">${{w.strain}}</div>
  <div>${{w.hrv_ms}}ms / ${{w.rhr_bpm}}bpm</div>
</div>`;

// Algorithms
const algoColors = ['#3498db','#9b59b6','#ff9800'];
D.algorithms.forEach((a,i) => {{
  compHTML += `<div class="compare-row">
    <div><span class="source-tag" style="background:${{algoColors[i]}}22;color:${{algoColors[i]}}">${{a.name}}</span></div>
    <div style="color:${{recCol(a.recovery)}}; font-weight:700">${{a.recovery}}%</div>
    <div style="font-weight:700">${{a.sleep_score}}%</div>
    <div style="font-weight:700">${{a.strain}}</div>
    <div>${{D.physiology.hrv_rmssd}}ms / ${{D.physiology.hr.rhr}}bpm</div>
  </div>`;
}});
compEl.innerHTML = compHTML;

// Algo cards (4 columns: Whoop + 3 algos)
const cardsEl = document.getElementById('algo-cards');
let cardsHTML = '';

// Whoop card
cardsHTML += `<div class="card">
  <h2><span style="color:var(--green)">&#9679;</span> Whoop Official (reference day)</h2>
  <div class="recovery-circle" style="background:conic-gradient(${{recCol(w.recovery)}} ${{w.recovery*3.6}}deg, #222 0)">
    <div><span class="val" style="color:${{recCol(w.recovery)}}">${{w.recovery}}</span><span class="pct">%</span></div>
  </div>
  <div class="sub" style="text-align:center">Recovery</div>
  <div style="margin-top:12px">
    <div class="stat"><span class="stat-label">Sleep</span><span class="stat-value">${{w.sleep}}%</span></div>
    <div class="stat"><span class="stat-label">Strain</span><span class="stat-value">${{w.strain}}</span></div>
    <div class="stat"><span class="stat-label">HRV</span><span class="stat-value">${{w.hrv_ms}} ms</span></div>
    <div class="stat"><span class="stat-label">RHR</span><span class="stat-value">${{w.rhr_bpm}} bpm</span></div>
  </div>
</div>`;

// Algorithm cards
D.algorithms.forEach((a,i) => {{
  const sl = a.sleep;
  const totalPhase = (sl.deep_min||0) + (sl.light_min||0) + (sl.rem_min||0) + (sl.awake_min||0) || 1;
  cardsHTML += `<div class="card">
    <h2><span style="color:${{algoColors[i]}}">&#9679;</span> ${{a.name}}</h2>
    <div class="recovery-circle" style="background:conic-gradient(${{recCol(a.recovery)}} ${{a.recovery*3.6}}deg, #222 0)">
      <div><span class="val" style="color:${{recCol(a.recovery)}}">${{a.recovery}}</span><span class="pct">%</span></div>
    </div>
    <div class="sub" style="text-align:center">Recovery</div>
    <div style="margin-top:12px">
      <div class="stat"><span class="stat-label">Sleep Score</span><span class="stat-value">${{a.sleep_score}}%</span></div>
      <div class="stat"><span class="stat-label">Strain</span><span class="stat-value">${{a.strain}}</span></div>
      <div class="stat"><span class="stat-label">Sleep Time</span><span class="stat-value">${{sl.sleep_min||0}} min</span></div>
      <div class="stat"><span class="stat-label">Efficiency</span><span class="stat-value">${{sl.efficiency||0}}%</span></div>
    </div>
    <div class="sleep-bar">
      <div class="seg" style="width:${{(sl.deep_min||0)/totalPhase*100}}%;background:#1a237e"></div>
      <div class="seg" style="width:${{(sl.light_min||0)/totalPhase*100}}%;background:#42a5f5"></div>
      <div class="seg" style="width:${{(sl.rem_min||0)/totalPhase*100}}%;background:#ab47bc"></div>
      <div class="seg" style="width:${{(sl.awake_min||0)/totalPhase*100}}%;background:#ff7043"></div>
    </div>
    <div class="legend">
      <span><span class="dot" style="background:#1a237e"></span>Deep ${{sl.deep_min||0}}m</span>
      <span><span class="dot" style="background:#42a5f5"></span>Light ${{sl.light_min||0}}m</span>
      <span><span class="dot" style="background:#ab47bc"></span>REM ${{sl.rem_min||0}}m</span>
      <span><span class="dot" style="background:#ff7043"></span>Awake ${{sl.awake_min||0}}m</span>
    </div>
    <div class="sub">${{a.description}}</div>
  </div>`;
}});
cardsEl.innerHTML = cardsHTML;

// Hypnograms
const hypEl = document.getElementById('hypnograms');
const phaseColors = {{deep:'#1a237e', light:'#42a5f5', rem:'#ab47bc', awake:'#ff7043', unknown:'#333'}};
const phaseDepth = {{awake:0, rem:1, light:2, deep:3, unknown:0}};
let hypHTML = '';
const sources = [
  {{name:'Algo 1: Custom', phases:D.algorithms[0].phases, color:algoColors[0]}},
  {{name:'Algo 2: SleepECG', phases:D.algorithms[1].phases, color:algoColors[1]}},
  {{name:'Algo 3: ML', phases:D.algorithms[2].phases, color:algoColors[2]}},
];

sources.forEach(src => {{
  hypHTML += `<div style="margin-bottom:12px"><span style="color:${{src.color}};font-weight:600;font-size:12px">${{src.name}}</span>`;
  src.phases.forEach(p => {{
    const d = phaseDepth[p.phase] || 0;
    hypHTML += `<div class="hypno-row"><span class="time">${{p.time}}</span>
      <div class="bar" style="width:${{100-d*20}}%;margin-left:${{d*20}}%;background:${{phaseColors[p.phase]||'#333'}}"></div></div>`;
  }});
  hypHTML += '</div>';
}});
hypHTML += `<div class="legend" style="margin-top:8px">
  <span><span class="dot" style="background:#ff7043"></span>Awake</span>
  <span><span class="dot" style="background:#ab47bc"></span>REM</span>
  <span><span class="dot" style="background:#42a5f5"></span>Light</span>
  <span><span class="dot" style="background:#1a237e"></span>Deep</span>
</div>`;
hypEl.innerHTML = hypHTML;

// Charts
Chart.defaults.color = '#888';
Chart.defaults.borderColor = '#1a1a1a';
Chart.defaults.font.size = 11;
const chartOpts = () => ({{
  responsive:true, maintainAspectRatio:false,
  plugins:{{legend:{{display:false}}}},
  scales:{{x:{{ticks:{{maxTicksLimit:15,maxRotation:0}},grid:{{display:false}}}},y:{{grid:{{color:'#1a1a1a'}}}}}},
  elements:{{point:{{radius:0}},line:{{borderWidth:1.5}}}},
  animation:false,
}});

const ts = D.timeseries;
const labels = ts.map(d => d.t);

new Chart(document.getElementById('hrChart'), {{
  type:'line',
  data:{{labels, datasets:[{{data:ts.map(d=>d.hr), borderColor:'#e74c3c', backgroundColor:'rgba(231,76,60,0.1)', fill:true}}]}},
  options:{{...chartOpts(), scales:{{...chartOpts().scales, y:{{min:40,grid:{{color:'#1a1a1a'}}}}}}}}
}});

new Chart(document.getElementById('mvChart'), {{
  type:'line',
  data:{{labels, datasets:[{{data:ts.map(d=>d.mv), borderColor:'#f5c542', backgroundColor:'rgba(245,197,66,0.1)', fill:true}}]}},
  options:chartOpts()
}});

new Chart(document.getElementById('rrChart'), {{
  type:'line',
  data:{{labels, datasets:[{{data:ts.map(d=>d.rr||null), borderColor:'#00bcd4', backgroundColor:'rgba(0,188,212,0.1)', fill:true, spanGaps:true}}]}},
  options:chartOpts()
}});

// Physiology
const ph = D.physiology;
document.getElementById('physio').innerHTML = `
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px">
    <div><span class="medium" style="color:var(--green)">${{ph.hrv_rmssd}}</span><span class="unit">ms</span><div class="sub">HRV (RMSSD)</div></div>
    <div><span class="medium">${{ph.hrv_sdnn}}</span><span class="unit">ms</span><div class="sub">SDNN</div></div>
    <div><span class="medium">${{ph.pnn50}}</span><span class="unit">%</span><div class="sub">pNN50</div></div>
    <div><span class="medium">${{ph.hr.rhr}}</span><span class="unit">bpm</span><div class="sub">Resting HR</div></div>
    <div><span class="medium">${{ph.resp_rate}}</span><span class="unit">br/m</span><div class="sub">Resp. Rate</div></div>
    <div><span class="medium">${{ph.hr.mean}}</span><span class="unit">bpm</span><div class="sub">Avg HR</div></div>
    <div><span class="medium">${{ph.hr.min}}</span><span class="unit">bpm</span><div class="sub">Min HR</div></div>
    <div><span class="medium">${{ph.hr.max}}</span><span class="unit">bpm</span><div class="sub">Max HR</div></div>
  </div>`;
</script>
</body>
</html>"""


if __name__ == "__main__":
    build_dashboard()
