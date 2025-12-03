#!/usr/bin/env python3
"""
Generate a comprehensive Whoop-style HTML dashboard from BLE sensor data and API data.
"""
import struct
import sqlite3
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent
MAX_CHART_POINTS = 5000


# ---------------------------------------------------------------------------
# Packet decoding helpers (preserved from original)
# ---------------------------------------------------------------------------

def compute_hr_from_raw(raw_hex):
    """Extract HR from RR intervals in raw packet data.

    For 124-byte AA01 packets (112-byte inner), the HR is not stored directly.
    Instead, RR intervals are at inner[15:21]:
      inner[15] = RR count (0, 1, or 2)
      inner[16:18] = RR interval 1 (uint16 LE, ms)
      inner[18:20] = RR interval 2 (uint16 LE, ms)
    HR = 60000 / avg_RR_ms
    """
    if not raw_hex or raw_hex.startswith('hr_ble:'):
        return 0, 0, 0, 0
    try:
        data = bytes.fromhex(raw_hex)
    except Exception:
        return 0, 0, 0, 0
    if len(data) != 124:
        return 0, 0, 0, 0
    inner = data[8:-4]  # strip AA01 header and CRC32
    if len(inner) < 21:
        return 0, 0, 0, 0
    rr_count = inner[15]
    if rr_count < 1:
        return 0, rr_count, 0, 0
    rr1 = struct.unpack_from('<H', inner, 16)[0]
    rr2 = struct.unpack_from('<H', inner, 18)[0] if rr_count >= 2 and len(inner) > 19 else 0
    if 200 < rr1 < 2000:
        hr = round(60000.0 / rr1)
        return hr, rr_count, rr1, rr2
    return 0, rr_count, rr1, rr2


def load_db_records(db_path):
    """Load sensor records from our companion app's Room database."""
    records = []
    recomputed_hr = 0
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute("""
            SELECT timestamp, heartRate, rrCount, rr1Ms, rr2Ms, rr3Ms,
                   accelX, accelY, accelZ, gyro, spo2Percent, rawHex
            FROM sensor_records ORDER BY timestamp ASC
        """)
        for row in cur:
            hr = row[1]
            rr_count = row[2]
            rr1 = row[3]
            rr2 = row[4]
            rr3 = row[5]
            raw_hex = row[11] if row[11] else ''

            # Recompute HR from RR intervals in raw packet if stored HR is 0
            if hr <= 0 and raw_hex and not raw_hex.startswith('hr_ble:'):
                computed_hr, new_rr_count, new_rr1, new_rr2 = compute_hr_from_raw(raw_hex)
                if computed_hr > 0:
                    hr = computed_hr
                    rr_count = new_rr_count
                    rr1 = new_rr1
                    rr2 = new_rr2
                    recomputed_hr += 1

            records.append({
                'timestamp': row[0],
                'heartRate': hr,
                'rrCount': rr_count,
                'rr1': rr1, 'rr2': rr2, 'rr3': rr3,
                'accelX': round(row[6], 4),
                'accelY': round(row[7], 4),
                'accelZ': round(row[8], 4),
                'gyro': round(row[9], 4),
                'spo2Percent': row[10],
            })
        conn.close()
        if recomputed_hr > 0:
            print(f"  Recomputed HR from RR intervals: {recomputed_hr} records")
    except Exception as e:
        print(f"Error loading DB: {e}")
    return records


# ---------------------------------------------------------------------------
# API data loaders
# ---------------------------------------------------------------------------

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load {path}: {e}")
        return None


def load_cycles(api_dir):
    data = load_json(api_dir / 'cycles.json')
    if not data:
        return []
    cycles = []
    for c in data:
        score = c.get('score') or {}
        start = c.get('start', '')
        cycles.append({
            'id': c.get('id'),
            'start': start,
            'end': c.get('end', ''),
            'strain': round(score.get('strain', 0), 2),
            'kilojoule': round(score.get('kilojoule', 0), 1),
            'avg_hr': score.get('average_heart_rate', 0),
            'max_hr': score.get('max_heart_rate', 0),
            'date': start[:10] if start else '',
        })
    cycles.sort(key=lambda x: x['start'])
    return cycles


def load_health_tab(api_dir):
    data = load_json(api_dir / 'health_tab.json')
    if not data:
        return {}
    # Extract whoop age
    info = {}
    try:
        sections = data.get('sections', [])
        for sec in sections:
            for item in sec.get('items', []):
                content = item.get('content', {})
                for sub in content.get('items', []):
                    if sub.get('type') == 'WHOOP_AGE_AMOEBA':
                        sc = sub['content']['style_values']
                        info['whoop_age'] = sc.get('age', 0)
                        info['pace_of_aging'] = sc.get('pace_of_aging', 0)
                        info['years_difference'] = sc.get('years_difference', 0)
                        info['age_display'] = sub['content'].get('age_value_display', '')
                        info['age_subtitle'] = sub['content'].get('age_subtitle_display', '')
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def downsample(records, max_points):
    if len(records) <= max_points:
        return records
    step = len(records) / max_points
    return [records[int(i * step)] for i in range(max_points)]


def compute_hr_zones(hr_values, max_hr=189):
    """Compute time in each HR zone."""
    zones = {
        'Zone 1 (Rest)': {'min': 0, 'max': int(max_hr * 0.50), 'color': '#888888', 'count': 0},
        'Zone 2 (Easy)': {'min': int(max_hr * 0.50), 'max': int(max_hr * 0.60), 'color': '#4488FF', 'count': 0},
        'Zone 3 (Moderate)': {'min': int(max_hr * 0.60), 'max': int(max_hr * 0.70), 'color': '#44D62C', 'count': 0},
        'Zone 4 (Hard)': {'min': int(max_hr * 0.70), 'max': int(max_hr * 0.80), 'color': '#FFD700', 'count': 0},
        'Zone 5 (Max)': {'min': int(max_hr * 0.80), 'max': 999, 'color': '#FF4444', 'count': 0},
    }
    for hr in hr_values:
        if hr < zones['Zone 1 (Rest)']['max']:
            zones['Zone 1 (Rest)']['count'] += 1
        elif hr < zones['Zone 2 (Easy)']['max']:
            zones['Zone 2 (Easy)']['count'] += 1
        elif hr < zones['Zone 3 (Moderate)']['max']:
            zones['Zone 3 (Moderate)']['count'] += 1
        elif hr < zones['Zone 4 (Hard)']['max']:
            zones['Zone 4 (Hard)']['count'] += 1
        else:
            zones['Zone 5 (Max)']['count'] += 1
    return zones


def compute_daily_hrv(records):
    """Compute daily RMSSD from consecutive valid RR intervals."""
    daily_rr = defaultdict(list)
    for r in records:
        rr1 = r.get('rr1', 0)
        if 200 < rr1 < 2000:
            day = datetime.fromtimestamp(r['timestamp'], tz=timezone.utc).strftime('%Y-%m-%d')
            daily_rr[day].append(rr1)

    daily_hrv = {}
    for day, rrs in sorted(daily_rr.items()):
        if len(rrs) < 10:
            continue
        diffs_sq = []
        for i in range(1, len(rrs)):
            diff = rrs[i] - rrs[i - 1]
            diffs_sq.append(diff * diff)
        if diffs_sq:
            rmssd = math.sqrt(sum(diffs_sq) / len(diffs_sq))
            daily_hrv[day] = round(rmssd, 1)
    return daily_hrv


def compute_daily_resting_hr(records):
    """Compute resting HR as lowest 5-minute rolling average per day."""
    daily_hrs = defaultdict(list)
    for r in records:
        hr = r.get('heartRate', 0)
        if hr > 30:
            day = datetime.fromtimestamp(r['timestamp'], tz=timezone.utc).strftime('%Y-%m-%d')
            daily_hrs[day].append((r['timestamp'], hr))

    resting = {}
    for day, pairs in sorted(daily_hrs.items()):
        pairs.sort()
        if len(pairs) < 300:
            # Not enough for 5-min window, just use lowest 10% avg
            hrs_sorted = sorted([p[1] for p in pairs])
            n = max(1, len(hrs_sorted) // 10)
            resting[day] = round(sum(hrs_sorted[:n]) / n, 1)
            continue
        # 5 min rolling avg (assume ~1 record/sec, window=300)
        window = 300
        best = 999
        hr_vals = [p[1] for p in pairs]
        running = sum(hr_vals[:window])
        best = running / window
        for i in range(window, len(hr_vals)):
            running += hr_vals[i] - hr_vals[i - window]
            avg = running / window
            if avg < best:
                best = avg
        resting[day] = round(best, 1)
    return resting


def estimate_sleep_periods(records):
    """Detect sleep: low accel variance + low HR for consecutive periods."""
    daily_records = defaultdict(list)
    for r in records:
        day = datetime.fromtimestamp(r['timestamp'], tz=timezone.utc).strftime('%Y-%m-%d')
        daily_records[day].append(r)

    sleep_periods = {}
    for day, recs in sorted(daily_records.items()):
        recs.sort(key=lambda x: x['timestamp'])
        # Sliding window: 60 records (~1 min)
        window = 60
        if len(recs) < window:
            continue
        sleeping = []
        for i in range(0, len(recs) - window, window):
            chunk = recs[i:i + window]
            hrs = [c['heartRate'] for c in chunk if c.get('heartRate', 0) > 30]
            accels = [math.sqrt(c['accelX'] ** 2 + c['accelY'] ** 2 + c['accelZ'] ** 2) for c in chunk]
            if not hrs or not accels:
                continue
            avg_hr = sum(hrs) / len(hrs)
            accel_var = sum((a - sum(accels) / len(accels)) ** 2 for a in accels) / len(accels)
            is_sleep = avg_hr < 70 and accel_var < 0.01
            sleeping.append({
                'start': chunk[0]['timestamp'],
                'end': chunk[-1]['timestamp'],
                'is_sleep': is_sleep,
                'avg_hr': round(avg_hr, 1),
            })

        # Merge consecutive sleep chunks
        periods = []
        current = None
        for s in sleeping:
            if s['is_sleep']:
                if current is None:
                    current = {'start': s['start'], 'end': s['end'], 'hrs': [s['avg_hr']]}
                else:
                    current['end'] = s['end']
                    current['hrs'].append(s['avg_hr'])
            else:
                if current and len(current['hrs']) >= 5:  # at least 5 min
                    duration_min = (current['end'] - current['start']) / 60
                    periods.append({
                        'start': datetime.fromtimestamp(current['start'], tz=timezone.utc).strftime('%H:%M'),
                        'end': datetime.fromtimestamp(current['end'], tz=timezone.utc).strftime('%H:%M'),
                        'duration_min': round(duration_min),
                        'avg_hr': round(sum(current['hrs']) / len(current['hrs']), 1),
                    })
                current = None
        # Close last
        if current and len(current['hrs']) >= 5:
            duration_min = (current['end'] - current['start']) / 60
            periods.append({
                'start': datetime.fromtimestamp(current['start'], tz=timezone.utc).strftime('%H:%M'),
                'end': datetime.fromtimestamp(current['end'], tz=timezone.utc).strftime('%H:%M'),
                'duration_min': round(duration_min),
                'avg_hr': round(sum(current['hrs']) / len(current['hrs']), 1),
            })

        if periods:
            total_sleep = sum(p['duration_min'] for p in periods)
            sleep_periods[day] = {'periods': periods, 'total_min': total_sleep}

    return sleep_periods


def compute_ble_daily_avg_hr(records):
    """Compute BLE average HR per day for comparison."""
    daily = defaultdict(list)
    for r in records:
        hr = r.get('heartRate', 0)
        if hr > 30:
            day = datetime.fromtimestamp(r['timestamp'], tz=timezone.utc).strftime('%Y-%m-%d')
            daily[day].append(hr)
    return {d: round(sum(v) / len(v), 1) for d, v in sorted(daily.items())}


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(records, cycles, health_info, console_logs):
    """Generate the full dashboard HTML."""

    # De-duplicate by timestamp
    seen = set()
    unique = []
    for r in records:
        if r['timestamp'] not in seen:
            seen.add(r['timestamp'])
            unique.append(r)
    unique.sort(key=lambda r: r['timestamp'])

    chart_records = downsample(unique, MAX_CHART_POINTS)

    # Summary stats
    hr_vals = [r['heartRate'] for r in unique if r.get('heartRate', 0) > 30]
    spo2_vals = [r['spo2Percent'] for r in unique if 50 < r.get('spo2Percent', 0) <= 100]

    summary = {
        'total': len(unique),
        'chartPoints': len(chart_records),
        'hrCount': len(hr_vals),
        'hrAvg': round(sum(hr_vals) / len(hr_vals), 1) if hr_vals else 0,
        'hrMin': min(hr_vals) if hr_vals else 0,
        'hrMax': max(hr_vals) if hr_vals else 0,
        'spo2Count': len(spo2_vals),
        'spo2Avg': round(sum(spo2_vals) / len(spo2_vals), 1) if spo2_vals else 0,
        'firstTs': datetime.fromtimestamp(unique[0]['timestamp'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M') if unique else '?',
        'lastTs': datetime.fromtimestamp(unique[-1]['timestamp'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M') if unique else '?',
        'days': round((unique[-1]['timestamp'] - unique[0]['timestamp']) / 86400, 1) if len(unique) > 1 else 0,
    }

    # HR zones
    zones = compute_hr_zones(hr_vals)
    zone_labels = list(zones.keys())
    zone_counts = [zones[z]['count'] for z in zone_labels]
    zone_colors = [zones[z]['color'] for z in zone_labels]

    # HRV / Recovery
    daily_hrv = compute_daily_hrv(unique)
    daily_resting = compute_daily_resting_hr(unique)

    # Sleep
    sleep_data = estimate_sleep_periods(unique)

    # BLE daily avg HR for comparison
    ble_daily_hr = compute_ble_daily_avg_hr(unique)

    # API comparison for overlapping dates
    api_daily_hr = {}
    for c in cycles:
        if c['avg_hr'] > 0 and c['date']:
            api_daily_hr[c['date']] = {'avg_hr': c['avg_hr'], 'max_hr': c['max_hr'], 'strain': c['strain']}
    overlap_dates = sorted(set(ble_daily_hr.keys()) & set(api_daily_hr.keys()))

    # Chart data for HR over time
    chart_ts = [datetime.fromtimestamp(r['timestamp'], tz=timezone.utc).strftime('%m-%d %H:%M') for r in chart_records]
    chart_hr = [r['heartRate'] if r.get('heartRate', 0) > 30 else None for r in chart_records]
    chart_spo2 = [r['spo2Percent'] if 50 < r.get('spo2Percent', 0) <= 100 else None for r in chart_records]
    chart_ax = [r['accelX'] for r in chart_records]
    chart_ay = [r['accelY'] for r in chart_records]
    chart_az = [r['accelZ'] for r in chart_records]

    # Strain chart data
    strain_dates = [c['date'] for c in cycles if c['strain'] > 0]
    strain_vals = [c['strain'] for c in cycles if c['strain'] > 0]
    strain_colors = []
    for s in strain_vals:
        if s < 10:
            strain_colors.append('#44D62C')
        elif s < 14:
            strain_colors.append('#FFD700')
        elif s < 18:
            strain_colors.append('#FF8C00')
        else:
            strain_colors.append('#FF4444')

    # HRV chart data
    hrv_days = list(daily_hrv.keys())
    hrv_vals = list(daily_hrv.values())
    resting_days = list(daily_resting.keys())
    resting_vals = list(daily_resting.values())

    # Whoop age
    whoop_age = health_info.get('age_display', '')
    age_subtitle = health_info.get('age_subtitle', '')

    # Cycles table (last 30)
    recent_cycles = [c for c in cycles if c['strain'] > 0][-30:]

    # Sleep table
    sleep_days = sorted(sleep_data.keys())

    # Comparison table
    comparison_rows = []
    for d in overlap_dates:
        comparison_rows.append({
            'date': d,
            'ble_hr': ble_daily_hr[d],
            'api_hr': api_daily_hr[d]['avg_hr'],
            'api_max': api_daily_hr[d]['max_hr'],
            'strain': api_daily_hr[d]['strain'],
        })

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    data_json = json.dumps({
        'chartTs': chart_ts,
        'chartHr': chart_hr,
        'chartSpo2': chart_spo2,
        'chartAx': chart_ax,
        'chartAy': chart_ay,
        'chartAz': chart_az,
        'zoneLabels': zone_labels,
        'zoneCounts': zone_counts,
        'zoneColors': zone_colors,
        'strainDates': strain_dates,
        'strainVals': strain_vals,
        'strainColors': strain_colors,
        'hrvDays': hrv_days,
        'hrvVals': hrv_vals,
        'restingDays': resting_days,
        'restingVals': resting_vals,
        'consoleLogs': console_logs[:500],
    })

    # Build cycles table HTML
    cycles_html = ''
    for c in reversed(recent_cycles):
        sc = '#44D62C' if c['strain'] < 10 else '#FFD700' if c['strain'] < 14 else '#FF8C00' if c['strain'] < 18 else '#FF4444'
        cycles_html += f'<tr><td>{c["date"]}</td><td style="color:{sc};font-weight:bold">{c["strain"]}</td><td>{c["avg_hr"]}</td><td>{c["max_hr"]}</td><td>{c["kilojoule"]}</td></tr>\n'

    # Sleep table HTML
    sleep_html = ''
    for day in sleep_days:
        sd = sleep_data[day]
        hours = sd['total_min'] // 60
        mins = sd['total_min'] % 60
        periods_str = ', '.join(f'{p["start"]}-{p["end"]} ({p["duration_min"]}m)' for p in sd['periods'])
        sleep_html += f'<tr><td>{day}</td><td style="color:#44D62C;font-weight:bold">{hours}h {mins}m</td><td style="font-size:11px">{periods_str}</td></tr>\n'

    # Comparison table HTML
    comp_html = ''
    for row in comparison_rows:
        diff = round(row['ble_hr'] - row['api_hr'], 1)
        diff_color = '#44D62C' if abs(diff) < 5 else '#FFD700'
        comp_html += f'<tr><td>{row["date"]}</td><td>{row["ble_hr"]}</td><td>{row["api_hr"]}</td><td style="color:{diff_color}">{diff:+.1f}</td><td>{row["api_max"]}</td><td>{row["strain"]}</td></tr>\n'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WHOOP COMPANION Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0a0a0a; color: #e0e0e0; font-family: -apple-system, 'SF Pro Display', 'Helvetica Neue', sans-serif; }}
  .header {{
    background: linear-gradient(135deg, #111 0%, #1a1a2e 100%);
    padding: 40px 32px; text-align: center; border-bottom: 2px solid #44D62C;
  }}
  .header h1 {{ color: #44D62C; font-size: 32px; letter-spacing: 6px; font-weight: 800; }}
  .header p {{ color: #888; margin-top: 8px; font-size: 13px; }}
  .header .age-badge {{
    display: inline-block; margin-top: 12px; padding: 6px 20px;
    border: 1px solid #44D62C; border-radius: 20px; color: #44D62C; font-size: 14px;
  }}
  .stats-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px; padding: 20px; max-width: 1400px; margin: 0 auto;
  }}
  .stat-card {{
    background: #1a1a1a; border-radius: 12px; padding: 20px; text-align: center;
    border: 1px solid #2a2a2a; transition: border-color 0.2s;
  }}
  .stat-card:hover {{ border-color: #44D62C; }}
  .stat-card .label {{ color: #888; font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; }}
  .stat-card .value {{ color: #44D62C; font-size: 32px; font-weight: 700; margin-top: 4px; }}
  .stat-card .sub {{ color: #666; font-size: 11px; margin-top: 4px; }}
  .section {{
    max-width: 1400px; margin: 24px auto; padding: 0 20px;
  }}
  .section h2 {{
    color: #44D62C; font-size: 15px; margin-bottom: 12px; letter-spacing: 2px;
    text-transform: uppercase; border-bottom: 1px solid #2a2a2a; padding-bottom: 8px;
  }}
  .chart-container {{
    background: #1a1a1a; border-radius: 12px; padding: 20px;
    margin-bottom: 16px; border: 1px solid #2a2a2a;
  }}
  .chart-container canvas {{ max-height: 320px; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 800px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 12px;
    background: #1a1a1a; border-radius: 8px; overflow: hidden;
  }}
  th {{ background: #222; color: #44D62C; padding: 10px 12px; text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; }}
  td {{ padding: 8px 12px; border-top: 1px solid #222; color: #ccc; }}
  tr:hover td {{ background: #1e1e1e; }}
  .console-log {{
    background: #0d0d0d; border: 1px solid #2a2a2a; border-radius: 8px;
    padding: 12px; max-height: 400px; overflow-y: auto; font-size: 11px;
    font-family: 'SF Mono', 'Fira Code', monospace; line-height: 1.6;
  }}
  .console-log .entry {{ padding: 2px 0; }}
  .console-log .ts {{ color: #555; }}
  .console-log .msg {{ color: #44D62C; }}
</style>
</head>
<body>

<div class="header">
  <h1>WHOOP COMPANION</h1>
  <p>BLE Sensor Data + API Analytics Dashboard</p>
  <p style="color:#555;margin-top:4px;">Device: <STRAP_MAC> | Serial: <STRAP_SERIAL> | {summary['firstTs']} to {summary['lastTs']} | Generated: {now}</p>
  {"<div class='age-badge'>WHOOP AGE: " + whoop_age + " &mdash; " + age_subtitle + "</div>" if whoop_age else ""}
</div>

<!-- Summary Stats -->
<div class="stats-grid">
  <div class="stat-card"><div class="label">BLE Records</div><div class="value">{summary['total']:,}</div><div class="sub">{summary['days']} days span</div></div>
  <div class="stat-card"><div class="label">HR Records</div><div class="value">{summary['hrCount']:,}</div><div class="sub">With valid heartbeat</div></div>
  <div class="stat-card"><div class="label">Avg Heart Rate</div><div class="value">{summary['hrAvg']}</div><div class="sub">Min {summary['hrMin']} / Max {summary['hrMax']} BPM</div></div>
  <div class="stat-card"><div class="label">Avg SpO2</div><div class="value">{summary['spo2Avg']}%</div><div class="sub">{summary['spo2Count']:,} readings</div></div>
  <div class="stat-card"><div class="label">API Cycles</div><div class="value">{len(cycles)}</div><div class="sub">{cycles[0]['date'] if cycles else '?'} to {cycles[-1]['date'] if cycles else '?'}</div></div>
  <div class="stat-card"><div class="label">Chart Points</div><div class="value">{summary['chartPoints']:,}</div><div class="sub">Downsampled for render</div></div>
</div>

<!-- Strain Chart -->
<div class="section">
  <h2>Daily Strain (API Cycles)</h2>
  <div class="chart-container"><canvas id="strainChart"></canvas></div>
</div>

<!-- HR Over Time -->
<div class="section">
  <h2>Heart Rate Over Time (BLE)</h2>
  <div class="chart-container"><canvas id="hrChart"></canvas></div>
</div>

<!-- Two column: HR Zones + HRV -->
<div class="section">
  <div class="two-col">
    <div>
      <h2 style="color:#44D62C;font-size:15px;letter-spacing:2px;text-transform:uppercase;border-bottom:1px solid #2a2a2a;padding-bottom:8px;margin-bottom:12px;">HR Zones Distribution</h2>
      <div class="chart-container" style="margin-bottom:0"><canvas id="zonesChart"></canvas></div>
    </div>
    <div>
      <h2 style="color:#44D62C;font-size:15px;letter-spacing:2px;text-transform:uppercase;border-bottom:1px solid #2a2a2a;padding-bottom:8px;margin-bottom:12px;">HRV / Recovery (RMSSD)</h2>
      <div class="chart-container" style="margin-bottom:0"><canvas id="hrvChart"></canvas></div>
    </div>
  </div>
</div>

<!-- Sleep -->
<div class="section">
  <h2>Sleep Estimation (BLE)</h2>
  <div class="chart-container">
    {f'<table><thead><tr><th>Date</th><th>Total Sleep</th><th>Periods (UTC)</th></tr></thead><tbody>{sleep_html}</tbody></table>' if sleep_html else '<p style="color:#666">No sleep periods detected</p>'}
  </div>
</div>

<!-- Resting HR -->
<div class="section">
  <h2>Resting Heart Rate (BLE)</h2>
  <div class="chart-container"><canvas id="restingChart"></canvas></div>
</div>

<!-- SpO2 -->
<div class="section">
  <h2>SpO2 (Blood Oxygen)</h2>
  <div class="chart-container"><canvas id="spo2Chart"></canvas></div>
</div>

<!-- Accelerometer -->
<div class="section">
  <h2>Accelerometer</h2>
  <div class="chart-container"><canvas id="accelChart"></canvas></div>
</div>

<!-- API Cycles Table -->
<div class="section">
  <h2>API Cycles (Recent 30)</h2>
  <div class="chart-container" style="overflow-x:auto">
    <table>
      <thead><tr><th>Date</th><th>Strain</th><th>Avg HR</th><th>Max HR</th><th>kJ</th></tr></thead>
      <tbody>{cycles_html}</tbody>
    </table>
  </div>
</div>

<!-- BLE vs API Comparison -->
<div class="section">
  <h2>BLE vs API Comparison</h2>
  <div class="chart-container" style="overflow-x:auto">
    {f'<table><thead><tr><th>Date</th><th>BLE Avg HR</th><th>API Avg HR</th><th>Diff</th><th>API Max HR</th><th>Strain</th></tr></thead><tbody>{comp_html}</tbody></table>' if comp_html else '<p style="color:#666">No overlapping dates between BLE and API data</p>'}
  </div>
</div>

<!-- Console Logs -->
<div class="section" style="padding-bottom:40px;">
  <h2>Console Logs (Firmware Debug)</h2>
  <div class="console-log" id="consoleLogs"></div>
</div>

<script>
const D = {data_json};

Chart.defaults.color = '#888';
Chart.defaults.borderColor = '#2a2a2a';
const baseOpts = {{
  responsive: true,
  animation: false,
  plugins: {{ legend: {{ labels: {{ color: '#aaa', font: {{ size: 11 }} }} }} }},
  scales: {{
    x: {{ ticks: {{ color: '#555', font: {{ size: 9 }}, maxTicksLimit: 12 }}, grid: {{ color: '#1a1a1a' }} }},
    y: {{ ticks: {{ color: '#555', font: {{ size: 10 }} }}, grid: {{ color: '#1e1e1e' }} }}
  }}
}};

// Strain
new Chart(document.getElementById('strainChart'), {{
  type: 'bar',
  data: {{
    labels: D.strainDates,
    datasets: [{{ label: 'Strain', data: D.strainVals, backgroundColor: D.strainColors, borderRadius: 3 }}]
  }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, y: {{ ...baseOpts.scales.y, min: 0, max: 21 }} }} }}
}});

// HR
new Chart(document.getElementById('hrChart'), {{
  type: 'line',
  data: {{
    labels: D.chartTs,
    datasets: [{{ label: 'Heart Rate (BPM)', data: D.chartHr, borderColor: '#FF4444', backgroundColor: 'rgba(255,68,68,0.05)', fill: true, pointRadius: 0, borderWidth: 1.2, tension: 0.3 }}]
  }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, y: {{ ...baseOpts.scales.y, min: 30 }} }} }}
}});

// Zones doughnut
new Chart(document.getElementById('zonesChart'), {{
  type: 'doughnut',
  data: {{
    labels: D.zoneLabels,
    datasets: [{{ data: D.zoneCounts, backgroundColor: D.zoneColors, borderColor: '#0a0a0a', borderWidth: 2 }}]
  }},
  options: {{
    responsive: true, animation: false,
    plugins: {{
      legend: {{ position: 'right', labels: {{ color: '#aaa', font: {{ size: 11 }}, padding: 12 }} }}
    }}
  }}
}});

// HRV
new Chart(document.getElementById('hrvChart'), {{
  type: 'bar',
  data: {{
    labels: D.hrvDays,
    datasets: [{{ label: 'RMSSD (ms)', data: D.hrvVals, backgroundColor: '#44D62C', borderRadius: 4 }}]
  }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, y: {{ ...baseOpts.scales.y, min: 0 }} }} }}
}});

// Resting HR
new Chart(document.getElementById('restingChart'), {{
  type: 'bar',
  data: {{
    labels: D.restingDays,
    datasets: [{{ label: 'Resting HR (BPM)', data: D.restingVals, backgroundColor: '#FF6666', borderRadius: 4 }}]
  }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, y: {{ ...baseOpts.scales.y, min: 30 }} }} }}
}});

// SpO2
new Chart(document.getElementById('spo2Chart'), {{
  type: 'line',
  data: {{
    labels: D.chartTs,
    datasets: [{{ label: 'SpO2 (%)', data: D.chartSpo2, borderColor: '#44AAFF', backgroundColor: 'rgba(68,170,255,0.05)', fill: true, pointRadius: 0, borderWidth: 1.2, tension: 0.3 }}]
  }},
  options: {{ ...baseOpts, scales: {{ ...baseOpts.scales, y: {{ ...baseOpts.scales.y, min: 85, max: 100 }} }} }}
}});

// Accel
new Chart(document.getElementById('accelChart'), {{
  type: 'line',
  data: {{
    labels: D.chartTs,
    datasets: [
      {{ label: 'X', data: D.chartAx, borderColor: '#FF6666', pointRadius: 0, borderWidth: 1, tension: 0.3 }},
      {{ label: 'Y', data: D.chartAy, borderColor: '#66FF66', pointRadius: 0, borderWidth: 1, tension: 0.3 }},
      {{ label: 'Z', data: D.chartAz, borderColor: '#6666FF', pointRadius: 0, borderWidth: 1, tension: 0.3 }},
    ]
  }},
  options: baseOpts
}});

// Console logs
const cl = document.getElementById('consoleLogs');
if (D.consoleLogs.length > 0) {{
  cl.innerHTML = D.consoleLogs.map(l =>
    `<div class="entry"><span class="ts">${{l.ts}}</span> <span class="msg">${{l.text}}</span></div>`
  ).join('');
}} else {{
  cl.innerHTML = '<div style="color:#666;">No console logs captured</div>';
}}
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== Whoop Companion Dashboard Generator ===\n")

    # 1. Load BLE sensor data from Room DB
    records = []
    db_path = DATA_DIR / 'backup' / 'companion_db' / 'whoop_capture.db'
    if db_path.exists():
        print(f"Loading Room DB: {db_path}")
        records = load_db_records(db_path)
        print(f"  Records: {len(records)}")
    else:
        print(f"DB not found: {db_path}")

    # 2. Load API data
    api_dir = DATA_DIR / 'backup' / 'api'
    cycles = load_cycles(api_dir) if api_dir.exists() else []
    print(f"API cycles: {len(cycles)}")

    health_info = load_health_tab(api_dir) if api_dir.exists() else {}
    if health_info:
        print(f"Health tab: Whoop Age = {health_info.get('age_display', '?')}")

    # 3. Load console logs from logcat if present
    console_logs = []
    log_path = DATA_DIR / 'ble_log.txt'
    if log_path.exists():
        import re
        with open(log_path) as f:
            for line in f:
                m = re.search(r'CONSOLE:\s*(.*)', line)
                if m:
                    console_logs.append({'ts': line[:19], 'text': m.group(1)})
        print(f"Console logs from logcat: {len(console_logs)}")

    # 4. Generate
    print(f"\nTotal BLE records: {len(records)}")
    html = generate_html(records, cycles, health_info, console_logs)

    out_path = DATA_DIR / 'dashboard.html'
    with open(out_path, 'w') as f:
        f.write(html)
    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"\nDashboard written to: {out_path} ({size_mb:.1f} MB)")
    print(f"Open in browser: file://{out_path}")


if __name__ == '__main__':
    main()
