#!/usr/bin/env python3
"""
Generate a comprehensive Whoop-style interactive dashboard from API backup data.
Reads from data/backup/api/ and outputs data/api_dashboard.html
"""
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "backup" / "api"
OUT = Path(__file__).parent.parent / "api_dashboard.html"


def load(name):
    p = DATA_DIR / name
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def extract_deep_dive_scores(data):
    """Extract SCORE_GAUGE and CONTRIBUTORS_TILE metrics from a deep-dive JSON."""
    result = {"score": None, "score_suffix": None, "gauge_fill": None, "metrics": {}}
    if not data:
        return result
    for section in data.get("sections", []):
        for item in section.get("items", []):
            t = item.get("type", "")
            c = item.get("content", {})
            if t == "SCORE_GAUGE":
                result["score"] = c.get("score_display")
                result["score_suffix"] = c.get("score_display_suffix", "")
                result["gauge_fill"] = c.get("gauge_fill_percentage", 0)
            elif t == "CONTRIBUTORS_TILE":
                for m in c.get("metrics", []):
                    title = m.get("title", "")
                    status = m.get("status", "--")
                    result["metrics"][title] = status
    return result


def extract_sleep_lastnight(data):
    """Extract sleep detail stats from sleep_lastnight.json."""
    result = {}
    if not data:
        return result
    section_map = {
        0: "hours_of_sleep",
        1: "hours_vs_needed",
        2: "sleep_consistency",
        3: "sleep_efficiency",
        4: "sleep_stress",
    }
    for i, section in enumerate(data.get("sections", [])):
        key = section_map.get(i)
        if not key:
            continue
        for item in section.get("items", []):
            c = item.get("content", {})
            arrows = c.get("arrow_stat", [])
            if arrows:
                result[key] = arrows[0].get("current_stat_text", "")
    return result


def parse_float(s):
    if not s or s == "--":
        return None
    s = str(s).replace("%", "").replace(",", "").strip()
    try:
        # Handle time formats like "1:23"
        if ":" in s:
            parts = s.split(":")
            return float(parts[0]) + float(parts[1]) / 60
        return float(s)
    except (ValueError, IndexError):
        return None


def main():
    print("Loading data...")
    cycles_raw = load("all_cycles.json") or []
    health = load("health_tab.json")
    profile = load("user_profile.json")
    body = load("user_body_measurement.json")

    # Extract user info
    user_name = "Whoop User"
    user_email = ""
    if profile:
        user_name = profile.get("fullName") or profile.get("firstName", "User")
        user_email = profile.get("emailAddress", "")

    created_at = ""
    if profile:
        created_at = profile.get("createdAt", "")[:10]

    whoop_age = ""
    whoop_age_subtitle = ""
    if health:
        for section in health.get("sections", []):
            for item in section.get("items", []):
                c = item.get("content", {})
                for sub in c.get("items", []):
                    sc = sub.get("content", {})
                    if sc.get("age_value_display"):
                        whoop_age = sc["age_value_display"]
                        whoop_age_subtitle = sc.get("age_subtitle_display", "")

    height = body.get("height_meter", 0) if body else 0
    weight = body.get("weight_kilogram", 0) if body else 0

    # Build per-day data from cycles
    days = {}
    for c in cycles_raw:
        if c.get("score_state") != "SCORED" or not c.get("score"):
            continue
        date = c["start"][:10]
        s = c["score"]
        days[date] = {
            "date": date,
            "strain": round(s.get("strain", 0), 1),
            "kj": round(s.get("kilojoule", 0), 0),
            "kcal": round(s.get("kilojoule", 0) / 4.184, 0),
            "avg_hr": s.get("average_heart_rate", 0),
            "max_hr": s.get("max_heart_rate", 0),
            # Deep dive data to be filled
            "recovery": None,
            "recovery_fill": 0,
            "sleep_score": None,
            "sleep_fill": 0,
            "strain_score": None,
            "hrv": None,
            "rhr": None,
            "resp_rate": None,
            "sleep_perf": None,
            "sleep_hours": None,
            "sleep_efficiency": None,
            "sleep_consistency": None,
            "sleep_stress": None,
            "hours_vs_needed": None,
            "activities": [],
        }

    # Load deep-dive data for each date
    dd_dir = DATA_DIR / "deep_dive"
    date_dirs = sorted([
        d for d in os.listdir(dd_dir)
        if os.path.isdir(dd_dir / d) and re.match(r"\d{4}-\d{2}-\d{2}", d)
    ])
    print(f"Processing {len(date_dirs)} deep-dive dates...")

    for date in date_dirs:
        if date not in days:
            days[date] = {
                "date": date, "strain": None, "kj": None, "kcal": None,
                "avg_hr": None, "max_hr": None, "recovery": None, "recovery_fill": 0,
                "sleep_score": None, "sleep_fill": 0, "strain_score": None,
                "hrv": None, "rhr": None, "resp_rate": None, "sleep_perf": None,
                "sleep_hours": None, "sleep_efficiency": None, "sleep_consistency": None,
                "sleep_stress": None, "hours_vs_needed": None, "activities": [],
            }

        day = days[date]
        dpath = dd_dir / date

        def safe_load_json(path):
            try:
                return json.load(open(path))
            except (json.JSONDecodeError, Exception):
                return None

        # Recovery
        rec_file = dpath / "recovery.json"
        if rec_file.exists():
            rec = extract_deep_dive_scores(safe_load_json(rec_file))
            if rec["score"] and rec["score"] != "--":
                day["recovery"] = parse_float(rec["score"])
                day["recovery_fill"] = rec["gauge_fill"] or 0
            metrics = rec["metrics"]
            for k, v in metrics.items():
                kl = k.lower()
                if "heart rate variability" in kl or "hrv" in kl:
                    day["hrv"] = parse_float(v)
                elif "resting heart rate" in kl or k == "Resting Heart Rate":
                    day["rhr"] = parse_float(v)
                elif "respiratory" in kl:
                    day["resp_rate"] = parse_float(v)
                elif "sleep performance" in kl:
                    day["sleep_perf"] = parse_float(v)

        # Sleep
        sleep_file = dpath / "sleep.json"
        if sleep_file.exists():
            slp = extract_deep_dive_scores(safe_load_json(sleep_file))
            if slp["score"] and slp["score"] != "--":
                day["sleep_score"] = parse_float(slp["score"])
                day["sleep_fill"] = slp["gauge_fill"] or 0
            for k, v in slp["metrics"].items():
                kl = k.upper()
                if "HOURS" in kl and "NEEDED" in kl:
                    day["hours_vs_needed"] = v
                elif "CONSISTENCY" in kl:
                    day["sleep_consistency"] = v
                elif "EFFICIENCY" in kl:
                    day["sleep_efficiency"] = v
                elif "STRESS" in kl:
                    day["sleep_stress"] = v

        # Sleep lastnight (for hours of sleep)
        sln_file = dpath / "sleep_lastnight.json"
        if sln_file.exists():
            sln_data = safe_load_json(sln_file)
            sln = extract_sleep_lastnight(sln_data)
            if sln.get("hours_of_sleep"):
                day["sleep_hours"] = sln["hours_of_sleep"]
            if sln.get("sleep_efficiency") and not day["sleep_efficiency"]:
                day["sleep_efficiency"] = sln["sleep_efficiency"]
            if sln.get("sleep_consistency") and not day["sleep_consistency"]:
                day["sleep_consistency"] = sln["sleep_consistency"]
            if sln.get("sleep_stress") and not day["sleep_stress"]:
                day["sleep_stress"] = sln["sleep_stress"]
            if sln.get("hours_vs_needed") and not day["hours_vs_needed"]:
                day["hours_vs_needed"] = sln["hours_vs_needed"]

        # Strain
        str_file = dpath / "strain.json"
        if str_file.exists():
            strn = extract_deep_dive_scores(safe_load_json(str_file))
            if strn["score"] and strn["score"] != "--":
                day["strain_score"] = parse_float(strn["score"])

    # Load activities and map to dates
    activities_dir = dd_dir / "_activities"
    activities_by_date = defaultdict(list)
    if activities_dir.exists():
        for fname in os.listdir(activities_dir):
            if not fname.endswith(".json"):
                continue
            fpath = activities_dir / fname
            try:
                adata = json.load(open(fpath))
                tb = adata.get("title_bar") or {}
                title = tb.get("title_display", "Activity")
                subtitle = tb.get("subtitle_display", "")
                hs = adata.get("horizontal_stats") or []
                strain_val = None
                for h in hs:
                    if "STRAIN" in (h.get("stat_title_display") or "").upper():
                        strain_val = h.get("stat_main_value_display")

                # Find date from JSON content
                raw = json.dumps(adata)
                date_matches = re.findall(r"(20\d\d-\d\d-\d\d)", raw)
                act_date = date_matches[0] if date_matches else None

                act = {
                    "title": title,
                    "subtitle": subtitle,
                    "strain": strain_val,
                    "id": fname.replace(".json", ""),
                }
                if act_date:
                    activities_by_date[act_date].append(act)
            except Exception:
                pass

    for date, acts in activities_by_date.items():
        if date in days:
            days[date]["activities"] = acts

    # Sort days
    sorted_dates = sorted(days.keys())
    sorted_days = [days[d] for d in sorted_dates]

    # Compute summary stats
    def avg_metric(key):
        vals = [d[key] for d in sorted_days if d[key] is not None]
        return round(statistics.mean(vals), 1) if vals else 0

    def safe_hours(s):
        if not s:
            return None
        return parse_float(s)

    total_days = len(sorted_days)
    avg_strain = avg_metric("strain")
    avg_recovery = avg_metric("recovery")
    avg_sleep = avg_metric("sleep_score")
    avg_hrv = avg_metric("hrv")
    avg_rhr = avg_metric("rhr")
    total_kcal = sum(d["kcal"] for d in sorted_days if d["kcal"])

    # Best/worst days
    rec_days = [(d["date"], d["recovery"]) for d in sorted_days if d["recovery"] is not None]
    best_rec = max(rec_days, key=lambda x: x[1]) if rec_days else ("N/A", 0)
    worst_rec = min(rec_days, key=lambda x: x[1]) if rec_days else ("N/A", 0)

    strain_days = [(d["date"], d["strain"]) for d in sorted_days if d["strain"] is not None]
    best_strain = max(strain_days, key=lambda x: x[1]) if strain_days else ("N/A", 0)

    hrv_days = [(d["date"], d["hrv"]) for d in sorted_days if d["hrv"] is not None]
    best_hrv = max(hrv_days, key=lambda x: x[1]) if hrv_days else ("N/A", 0)

    # Weekly patterns
    dow_data = defaultdict(lambda: {"strain": [], "recovery": [], "sleep": [], "hrv": []})
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for d in sorted_days:
        try:
            dt = datetime.strptime(d["date"], "%Y-%m-%d")
            dow = dt.weekday()
            if d["strain"] is not None:
                dow_data[dow]["strain"].append(d["strain"])
            if d["recovery"] is not None:
                dow_data[dow]["recovery"].append(d["recovery"])
            if d["sleep_score"] is not None:
                dow_data[dow]["sleep"].append(d["sleep_score"])
            if d["hrv"] is not None:
                dow_data[dow]["hrv"].append(d["hrv"])
        except Exception:
            pass

    weekly_patterns = {}
    for dow in range(7):
        dd = dow_data[dow]
        weekly_patterns[dow_names[dow]] = {
            "strain": round(statistics.mean(dd["strain"]), 1) if dd["strain"] else 0,
            "recovery": round(statistics.mean(dd["recovery"]), 1) if dd["recovery"] else 0,
            "sleep": round(statistics.mean(dd["sleep"]), 1) if dd["sleep"] else 0,
            "hrv": round(statistics.mean(dd["hrv"]), 1) if dd["hrv"] else 0,
        }

    # Monthly averages
    monthly = defaultdict(lambda: {"strain": [], "recovery": [], "sleep": [], "hrv": [], "rhr": []})
    for d in sorted_days:
        month = d["date"][:7]
        if d["strain"] is not None:
            monthly[month]["strain"].append(d["strain"])
        if d["recovery"] is not None:
            monthly[month]["recovery"].append(d["recovery"])
        if d["sleep_score"] is not None:
            monthly[month]["sleep"].append(d["sleep_score"])
        if d["hrv"] is not None:
            monthly[month]["hrv"].append(d["hrv"])
        if d["rhr"] is not None:
            monthly[month]["rhr"].append(d["rhr"])

    monthly_avg = {}
    for month in sorted(monthly.keys()):
        md = monthly[month]
        monthly_avg[month] = {
            "strain": round(statistics.mean(md["strain"]), 1) if md["strain"] else None,
            "recovery": round(statistics.mean(md["recovery"]), 1) if md["recovery"] else None,
            "sleep": round(statistics.mean(md["sleep"]), 1) if md["sleep"] else None,
            "hrv": round(statistics.mean(md["hrv"]), 1) if md["hrv"] else None,
            "rhr": round(statistics.mean(md["rhr"]), 1) if md["rhr"] else None,
            "days": len(md["strain"]) or len(md["recovery"]),
        }

    # Correlation: sleep hours vs next-day recovery
    corr_sleep_rec = []
    for i in range(len(sorted_days) - 1):
        sh = safe_hours(sorted_days[i].get("sleep_hours"))
        nr = sorted_days[i + 1].get("recovery")
        if sh is not None and nr is not None:
            corr_sleep_rec.append({"sleep": sh, "recovery": nr})

    # Correlation: strain vs next-day recovery
    corr_strain_rec = []
    for i in range(len(sorted_days) - 1):
        st = sorted_days[i].get("strain")
        nr = sorted_days[i + 1].get("recovery")
        if st is not None and nr is not None:
            corr_strain_rec.append({"strain": st, "recovery": nr})

    # Scatter: strain vs recovery (same day)
    scatter_strain_rec = []
    for d in sorted_days:
        if d["strain"] is not None and d["recovery"] is not None:
            scatter_strain_rec.append({"x": d["strain"], "y": d["recovery"], "date": d["date"]})

    # Scatter: HRV vs sleep hours
    scatter_hrv_sleep = []
    for d in sorted_days:
        sh = safe_hours(d.get("sleep_hours"))
        if d["hrv"] is not None and sh is not None:
            scatter_hrv_sleep.append({"x": sh, "y": d["hrv"], "date": d["date"]})

    # Compute simple correlation coefficient
    def pearson(pairs, xkey, ykey):
        if len(pairs) < 5:
            return None
        xs = [p[xkey] for p in pairs]
        ys = [p[ykey] for p in pairs]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        dx = sum((x - mx) ** 2 for x in xs) ** 0.5
        dy = sum((y - my) ** 2 for y in ys) ** 0.5
        if dx == 0 or dy == 0:
            return 0
        return round(num / (dx * dy), 3)

    r_sleep_rec = pearson(corr_sleep_rec, "sleep", "recovery")
    r_strain_rec = pearson(corr_strain_rec, "strain", "recovery")
    r_strain_rec_same = pearson(scatter_strain_rec, "x", "y")

    # Insights
    insights = []
    insights.append(f"Tracking {total_days} days from {sorted_dates[0]} to {sorted_dates[-1]}.")
    insights.append(f"Average strain: {avg_strain}, recovery: {avg_recovery}%, sleep: {avg_sleep}%.")
    insights.append(f"Average HRV: {avg_hrv} ms, RHR: {avg_rhr} bpm.")
    insights.append(f"Total calories burned: {total_kcal:,.0f} kcal.")
    if best_rec[0] != "N/A":
        insights.append(f"Best recovery: {best_rec[1]}% on {best_rec[0]}.")
        insights.append(f"Worst recovery: {worst_rec[1]}% on {worst_rec[0]}.")
    if best_strain[0] != "N/A":
        insights.append(f"Highest strain day: {best_strain[1]} on {best_strain[0]}.")
    if best_hrv[0] != "N/A":
        insights.append(f"Best HRV: {best_hrv[1]} ms on {best_hrv[0]}.")

    if r_sleep_rec is not None:
        direction = "positive" if r_sleep_rec > 0 else "negative"
        strength = "strong" if abs(r_sleep_rec) > 0.5 else "moderate" if abs(r_sleep_rec) > 0.3 else "weak"
        insights.append(f"Sleep hours vs next-day recovery: {strength} {direction} correlation (r={r_sleep_rec}).")
    if r_strain_rec is not None:
        direction = "positive" if r_strain_rec > 0 else "negative"
        strength = "strong" if abs(r_strain_rec) > 0.5 else "moderate" if abs(r_strain_rec) > 0.3 else "weak"
        insights.append(f"Strain vs next-day recovery: {strength} {direction} correlation (r={r_strain_rec}).")

    # Best day of week
    best_dow_strain = max(weekly_patterns.items(), key=lambda x: x[1]["strain"]) if weekly_patterns else None
    best_dow_sleep = max(weekly_patterns.items(), key=lambda x: x[1]["sleep"]) if weekly_patterns else None
    best_dow_rec = max(weekly_patterns.items(), key=lambda x: x[1]["recovery"]) if weekly_patterns else None
    if best_dow_strain:
        insights.append(f"Highest avg strain on {best_dow_strain[0]}s ({best_dow_strain[1]['strain']}).")
    if best_dow_sleep:
        insights.append(f"Best sleep on {best_dow_sleep[0]}s ({best_dow_sleep[1]['sleep']}%).")
    if best_dow_rec:
        insights.append(f"Best recovery on {best_dow_rec[0]}s ({best_dow_rec[1]['recovery']}%).")

    # Build the data blob for JS
    dashboard_data = {
        "user": {
            "name": user_name,
            "email": user_email,
            "created": created_at,
            "whoop_age": whoop_age,
            "whoop_age_subtitle": whoop_age_subtitle,
            "height": height,
            "weight": weight,
        },
        "summary": {
            "total_days": total_days,
            "avg_strain": avg_strain,
            "avg_recovery": avg_recovery,
            "avg_sleep": avg_sleep,
            "avg_hrv": avg_hrv,
            "avg_rhr": avg_rhr,
            "total_kcal": total_kcal,
        },
        "days": sorted_days,
        "monthly_avg": monthly_avg,
        "weekly_patterns": weekly_patterns,
        "scatter_strain_rec": scatter_strain_rec,
        "scatter_hrv_sleep": scatter_hrv_sleep,
        "insights": insights,
        "correlations": {
            "sleep_vs_next_recovery": r_sleep_rec,
            "strain_vs_next_recovery": r_strain_rec,
            "strain_vs_same_recovery": r_strain_rec_same,
        },
    }

    data_json = json.dumps(dashboard_data, default=str)

    print("Generating HTML...")
    html = generate_html(data_json)

    OUT.write_text(html, encoding="utf-8")
    print(f"Dashboard written to {OUT}")
    print(f"  {total_days} days, {len(date_dirs)} deep-dive dates, {sum(len(v) for v in activities_by_date.values())} activities")


def generate_html(data_json):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Whoop Data Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0a0a0a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.5; }}
a {{ color: #44D62C; }}

.top-bar {{ background: #111; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #222; position: sticky; top: 0; z-index: 100; }}
.top-bar h1 {{ font-size: 20px; font-weight: 700; color: #fff; }}
.top-bar .meta {{ font-size: 13px; color: #888; }}
.top-bar .whoop-age {{ color: #44D62C; font-weight: 600; }}

.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}

.cards-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.card {{ background: #161616; border: 1px solid #222; border-radius: 12px; padding: 20px; text-align: center; }}
.card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 8px; }}
.card .value {{ font-size: 28px; font-weight: 700; color: #fff; }}
.card .value.green {{ color: #44D62C; }}
.card .value.blue {{ color: #4A9EFF; }}
.card .value.purple {{ color: #A855F7; }}
.card .value.red {{ color: #EF4444; }}
.card .value.teal {{ color: #2DD4BF; }}

.section {{ margin-bottom: 32px; }}
.section-title {{ font-size: 18px; font-weight: 700; color: #fff; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid #222; }}

/* Calendar Heatmap */
.calendar-container {{ background: #161616; border: 1px solid #222; border-radius: 12px; padding: 20px; overflow-x: auto; }}
.calendar-grid {{ display: flex; gap: 2px; flex-wrap: wrap; }}
.cal-month-label {{ font-size: 11px; color: #666; width: 100%; margin-top: 8px; margin-bottom: 4px; }}
.cal-day {{ width: 16px; height: 16px; border-radius: 3px; cursor: pointer; transition: transform 0.15s, box-shadow 0.15s; }}
.cal-day:hover {{ transform: scale(1.5); box-shadow: 0 0 8px rgba(68, 214, 44, 0.4); z-index: 10; position: relative; }}
.cal-day-label {{ font-size: 9px; color: #555; width: 24px; text-align: right; margin-right: 4px; flex-shrink: 0; }}
.cal-row {{ display: flex; align-items: center; gap: 2px; }}
.cal-legend {{ display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 11px; color: #888; }}
.cal-legend-box {{ width: 12px; height: 12px; border-radius: 2px; }}

/* Day Detail Panel */
.day-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 200; display: none; justify-content: center; align-items: center; }}
.day-overlay.active {{ display: flex; }}
.day-panel {{ background: #161616; border: 1px solid #333; border-radius: 16px; padding: 32px; max-width: 700px; width: 95%; max-height: 90vh; overflow-y: auto; position: relative; animation: slideUp 0.25s ease; }}
@keyframes slideUp {{ from {{ opacity: 0; transform: translateY(30px); }} to {{ opacity: 1; transform: translateY(0); }} }}
.day-panel .close-btn {{ position: absolute; top: 12px; right: 16px; font-size: 24px; color: #888; cursor: pointer; background: none; border: none; }}
.day-panel .close-btn:hover {{ color: #fff; }}
.day-panel h2 {{ font-size: 22px; color: #fff; margin-bottom: 4px; }}
.day-panel .nav-arrows {{ display: flex; gap: 12px; margin-bottom: 16px; }}
.day-panel .nav-arrows button {{ background: #222; border: 1px solid #333; color: #ccc; padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 14px; }}
.day-panel .nav-arrows button:hover {{ background: #333; color: #fff; }}

.gauges-row {{ display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }}
.gauge {{ text-align: center; flex: 1; min-width: 120px; }}
.gauge-circle {{ width: 90px; height: 90px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 8px; font-size: 24px; font-weight: 700; color: #fff; }}
.gauge-label {{ font-size: 11px; text-transform: uppercase; color: #888; letter-spacing: 1px; }}

.detail-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px; }}
.detail-item {{ background: #1a1a1a; border-radius: 8px; padding: 12px; text-align: center; }}
.detail-item .di-label {{ font-size: 10px; text-transform: uppercase; color: #666; letter-spacing: 0.5px; }}
.detail-item .di-value {{ font-size: 18px; font-weight: 600; color: #fff; margin-top: 4px; }}

.activity-list {{ margin-top: 12px; }}
.activity-item {{ background: #1a1a1a; border-radius: 8px; padding: 10px 14px; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; }}
.activity-item .act-title {{ font-weight: 600; color: #fff; }}
.activity-item .act-sub {{ font-size: 12px; color: #888; }}
.activity-item .act-strain {{ color: #4A9EFF; font-weight: 600; }}

/* Charts */
.charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; }}
.chart-card {{ background: #161616; border: 1px solid #222; border-radius: 12px; padding: 20px; }}
.chart-card h3 {{ font-size: 14px; color: #aaa; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }}
.chart-card canvas {{ width: 100% !important; }}

/* Stats table */
.stats-table {{ width: 100%; border-collapse: collapse; background: #161616; border-radius: 12px; overflow: hidden; }}
.stats-table th {{ background: #1a1a1a; padding: 12px 16px; text-align: left; font-size: 11px; text-transform: uppercase; color: #888; letter-spacing: 1px; border-bottom: 1px solid #222; }}
.stats-table td {{ padding: 10px 16px; border-bottom: 1px solid #1a1a1a; font-size: 14px; }}
.stats-table tr:hover {{ background: #1c1c1c; }}

/* Insights */
.insights {{ background: #161616; border: 1px solid #222; border-radius: 12px; padding: 24px; }}
.insight {{ padding: 8px 0; border-bottom: 1px solid #1a1a1a; font-size: 14px; color: #ccc; }}
.insight:last-child {{ border-bottom: none; }}
.insight strong {{ color: #44D62C; }}

/* Scatter */
.scatter-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }}

@media (max-width: 768px) {{
  .charts-grid {{ grid-template-columns: 1fr; }}
  .scatter-grid {{ grid-template-columns: 1fr; }}
  .gauges-row {{ flex-direction: column; align-items: center; }}
  .top-bar {{ flex-direction: column; gap: 8px; text-align: center; }}
}}
</style>
</head>
<body>

<div class="top-bar">
  <div>
    <h1 id="userName"></h1>
    <div class="meta" id="userMeta"></div>
  </div>
  <div style="text-align:right">
    <div class="whoop-age" id="whoopAge"></div>
    <div class="meta" id="whoopAgeSub"></div>
  </div>
</div>

<div class="container">
  <!-- Overview Cards -->
  <div class="cards-row" id="overviewCards"></div>

  <!-- Calendar Heatmap -->
  <div class="section">
    <div class="section-title">Recovery Calendar</div>
    <div class="calendar-container" id="calendarContainer"></div>
  </div>

  <!-- Day Detail Overlay -->
  <div class="day-overlay" id="dayOverlay">
    <div class="day-panel" id="dayPanel"></div>
  </div>

  <!-- Time Series Charts -->
  <div class="section">
    <div class="section-title">Trends Over Time</div>
    <div class="charts-grid">
      <div class="chart-card"><h3>Recovery %</h3><canvas id="chartRecovery"></canvas></div>
      <div class="chart-card"><h3>Strain</h3><canvas id="chartStrain"></canvas></div>
      <div class="chart-card"><h3>HRV (ms)</h3><canvas id="chartHRV"></canvas></div>
      <div class="chart-card"><h3>Resting Heart Rate (bpm)</h3><canvas id="chartRHR"></canvas></div>
      <div class="chart-card"><h3>Sleep Performance %</h3><canvas id="chartSleep"></canvas></div>
      <div class="chart-card"><h3>Avg HR vs Max HR</h3><canvas id="chartHR"></canvas></div>
    </div>
  </div>

  <!-- Scatter Plots -->
  <div class="section">
    <div class="section-title">Correlations</div>
    <div class="scatter-grid">
      <div class="chart-card"><h3>Strain vs Recovery</h3><canvas id="scatterStrainRec"></canvas></div>
      <div class="chart-card"><h3>HRV vs Sleep Hours</h3><canvas id="scatterHRVSleep"></canvas></div>
    </div>
  </div>

  <!-- Weekly Patterns -->
  <div class="section">
    <div class="section-title">Weekly Patterns</div>
    <div class="charts-grid">
      <div class="chart-card"><h3>Average by Day of Week</h3><canvas id="chartWeekly"></canvas></div>
    </div>
  </div>

  <!-- Monthly Stats Table -->
  <div class="section">
    <div class="section-title">Monthly Averages</div>
    <div style="overflow-x:auto; border-radius:12px; border:1px solid #222;">
      <table class="stats-table" id="monthlyTable"></table>
    </div>
  </div>

  <!-- Insights -->
  <div class="section">
    <div class="section-title">Personal Analysis</div>
    <div class="insights" id="insightsContainer"></div>
  </div>
</div>

<script>
const D = {data_json};

// --- Helpers ---
function recColor(v) {{
  if (v == null) return '#333';
  if (v >= 67) return '#44D62C';
  if (v >= 34) return '#F5A623';
  return '#EF4444';
}}
function recBg(v) {{
  if (v == null) return 'rgba(50,50,50,0.5)';
  if (v >= 67) return 'rgba(68,214,44,0.2)';
  if (v >= 34) return 'rgba(245,166,35,0.2)';
  return 'rgba(239,68,68,0.2)';
}}
function fmt(v, suffix) {{ return v != null ? v + (suffix || '') : '--'; }}

// --- Top Bar ---
document.getElementById('userName').textContent = D.user.name;
document.getElementById('userMeta').textContent = `Member since ${{D.user.created}} | ${{D.summary.total_days}} days tracked`;
document.getElementById('whoopAge').textContent = D.user.whoop_age ? `Whoop Age: ${{D.user.whoop_age}}` : '';
document.getElementById('whoopAgeSub').textContent = D.user.whoop_age_subtitle || '';

// --- Overview Cards ---
const cards = [
  {{ label: 'Days Tracked', value: D.summary.total_days, cls: '' }},
  {{ label: 'Avg Strain', value: D.summary.avg_strain, cls: 'blue' }},
  {{ label: 'Avg Recovery', value: D.summary.avg_recovery + '%', cls: 'green' }},
  {{ label: 'Avg Sleep Score', value: D.summary.avg_sleep + '%', cls: 'teal' }},
  {{ label: 'Avg HRV', value: D.summary.avg_hrv + ' ms', cls: 'purple' }},
  {{ label: 'Total Calories', value: Math.round(D.summary.total_kcal).toLocaleString(), cls: 'red' }},
];
document.getElementById('overviewCards').innerHTML = cards.map(c =>
  `<div class="card"><div class="label">${{c.label}}</div><div class="value ${{c.cls}}">${{c.value}}</div></div>`
).join('');

// --- Calendar Heatmap ---
(function() {{
  const container = document.getElementById('calendarContainer');
  if (!D.days.length) return;
  const firstDate = new Date(D.days[0].date + 'T00:00:00');
  const lastDate = new Date(D.days[D.days.length-1].date + 'T00:00:00');
  const dayMap = {{}};
  D.days.forEach(d => dayMap[d.date] = d);

  let html = '<div style="display:flex;flex-direction:column;gap:2px;">';
  let current = new Date(firstDate);
  // Align to start of week (Monday)
  while (current.getDay() !== 1) current.setDate(current.getDate() - 1);

  let lastMonth = -1;
  const rows = [[], [], [], [], [], [], []]; // 7 rows for days of week
  const monthLabels = [];
  let col = 0;

  while (current <= lastDate || current.getDay() !== 1) {{
    const ds = current.toISOString().slice(0, 10);
    const dow = (current.getDay() + 6) % 7; // Mon=0
    const d = dayMap[ds];
    const rec = d ? d.recovery : null;
    const color = rec != null ? recColor(rec) : '#1a1a1a';
    const opacity = rec != null ? Math.max(0.3, rec / 100) : 0.15;
    const title = ds + (rec != null ? ` - Recovery: ${{rec}}%` : '');

    if (dow === 0 && current.getMonth() !== lastMonth) {{
      monthLabels.push({{ col, label: current.toLocaleString('en', {{ month: 'short' }}) }});
      lastMonth = current.getMonth();
    }}

    rows[dow].push({{ ds, color, opacity, title, hasData: !!d }});
    if (dow === 6) col++;
    current.setDate(current.getDate() + 1);
    if (current > new Date(lastDate.getTime() + 7 * 86400000)) break;
  }}

  // Month labels row
  html += '<div style="display:flex;gap:2px;margin-left:28px;margin-bottom:2px;">';
  let labelCol = 0;
  for (const ml of monthLabels) {{
    const gap = ml.col - labelCol;
    if (gap > 0) html += `<div style="width:${{gap * 18}}px"></div>`;
    html += `<div style="font-size:11px;color:#666;width:36px">${{ml.label}}</div>`;
    labelCol = ml.col + 2;
  }}
  html += '</div>';

  const dowLabels = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  for (let r = 0; r < 7; r++) {{
    html += '<div class="cal-row">';
    html += `<div class="cal-day-label">${{dowLabels[r]}}</div>`;
    for (const cell of rows[r]) {{
      const style = `background:${{cell.color}};opacity:${{cell.opacity}}`;
      html += `<div class="cal-day" style="${{style}}" title="${{cell.title}}" data-date="${{cell.ds}}" onclick="showDay('${{cell.ds}}')"></div>`;
    }}
    html += '</div>';
  }}

  html += '</div>';
  html += '<div class="cal-legend"><span>Low</span>';
  html += '<div class="cal-legend-box" style="background:#EF4444"></div>';
  html += '<div class="cal-legend-box" style="background:#F5A623"></div>';
  html += '<div class="cal-legend-box" style="background:#44D62C"></div>';
  html += '<span>High Recovery</span></div>';
  container.innerHTML = html;
}})();

// --- Day Detail Panel ---
let currentDayIndex = -1;
const dayDates = D.days.map(d => d.date);

function showDay(date) {{
  const idx = dayDates.indexOf(date);
  if (idx === -1) return;
  currentDayIndex = idx;
  renderDayPanel(D.days[idx]);
  document.getElementById('dayOverlay').classList.add('active');
}}

function closeDay() {{
  document.getElementById('dayOverlay').classList.remove('active');
  currentDayIndex = -1;
}}

function navDay(delta) {{
  const next = currentDayIndex + delta;
  if (next >= 0 && next < D.days.length) {{
    currentDayIndex = next;
    renderDayPanel(D.days[next]);
  }}
}}

function renderDayPanel(d) {{
  const p = document.getElementById('dayPanel');
  const dayName = new Date(d.date + 'T12:00:00').toLocaleDateString('en', {{ weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }});

  let html = `<button class="close-btn" onclick="closeDay()">&times;</button>`;
  html += `<h2>${{dayName}}</h2>`;
  html += `<div class="nav-arrows">
    <button onclick="navDay(-1)">&larr; Prev</button>
    <button onclick="navDay(1)">Next &rarr;</button>
  </div>`;

  // Gauges
  html += '<div class="gauges-row">';
  const gauges = [
    {{ val: d.recovery, label: 'Recovery', suffix: '%', color: recColor(d.recovery) }},
    {{ val: d.strain || d.strain_score, label: 'Strain', suffix: '', color: '#4A9EFF' }},
    {{ val: d.sleep_score, label: 'Sleep', suffix: '%', color: '#2DD4BF' }},
  ];
  for (const g of gauges) {{
    const bg = g.val != null ? g.color + '33' : '#22222266';
    const border = g.val != null ? g.color : '#333';
    html += `<div class="gauge">
      <div class="gauge-circle" style="border:4px solid ${{border}};background:${{bg}}">
        ${{g.val != null ? g.val + g.suffix : '--'}}
      </div>
      <div class="gauge-label">${{g.label}}</div>
    </div>`;
  }}
  html += '</div>';

  // Detail metrics
  html += '<div class="detail-grid">';
  const details = [
    ['HRV', fmt(d.hrv, ' ms')],
    ['RHR', fmt(d.rhr, ' bpm')],
    ['Resp Rate', fmt(d.resp_rate)],
    ['Avg HR', fmt(d.avg_hr, ' bpm')],
    ['Max HR', fmt(d.max_hr, ' bpm')],
    ['Calories', fmt(d.kcal, ' kcal')],
    ['Sleep Hours', d.sleep_hours || '--'],
    ['Efficiency', d.sleep_efficiency || '--'],
    ['Consistency', d.sleep_consistency || '--'],
    ['Sleep Stress', d.sleep_stress || '--'],
    ['Hrs vs Needed', d.hours_vs_needed || '--'],
    ['Strain', fmt(d.strain)],
  ];
  for (const [label, val] of details) {{
    html += `<div class="detail-item"><div class="di-label">${{label}}</div><div class="di-value">${{val}}</div></div>`;
  }}
  html += '</div>';

  // Activities
  if (d.activities && d.activities.length) {{
    html += '<div class="activity-list"><div style="font-size:12px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Activities</div>';
    for (const a of d.activities) {{
      html += `<div class="activity-item">
        <div><div class="act-title">${{a.title}}</div><div class="act-sub">${{a.subtitle}}</div></div>
        ${{a.strain ? `<div class="act-strain">Strain: ${{a.strain}}</div>` : ''}}
      </div>`;
    }}
    html += '</div>';
  }}

  p.innerHTML = html;
}}

document.getElementById('dayOverlay').addEventListener('click', function(e) {{
  if (e.target === this) closeDay();
}});
document.addEventListener('keydown', function(e) {{
  if (currentDayIndex === -1) return;
  if (e.key === 'Escape') closeDay();
  if (e.key === 'ArrowLeft') navDay(-1);
  if (e.key === 'ArrowRight') navDay(1);
}});

// --- Charts ---
const chartDefaults = {{
  responsive: true,
  maintainAspectRatio: true,
  aspectRatio: 2.2,
  plugins: {{
    legend: {{ display: false }},
    tooltip: {{ backgroundColor: '#222', titleColor: '#fff', bodyColor: '#ccc', borderColor: '#444', borderWidth: 1 }},
  }},
  scales: {{
    x: {{ ticks: {{ color: '#555', maxTicksLimit: 12 }}, grid: {{ color: '#1a1a1a' }} }},
    y: {{ ticks: {{ color: '#555' }}, grid: {{ color: '#1a1a1a' }} }},
  }},
}};

function tsLabels() {{ return D.days.map(d => d.date); }}
function tsData(key) {{ return D.days.map(d => d[key]); }}

function makeLine(id, data, color, label) {{
  new Chart(document.getElementById(id), {{
    type: 'line',
    data: {{
      labels: tsLabels(),
      datasets: [{{ label, data, borderColor: color, backgroundColor: color + '22', borderWidth: 1.5, pointRadius: 1.5, pointHoverRadius: 4, fill: true, tension: 0.3, spanGaps: true }}],
    }},
    options: {{ ...chartDefaults }},
  }});
}}

makeLine('chartRecovery', tsData('recovery'), '#44D62C', 'Recovery %');
makeLine('chartStrain', tsData('strain'), '#4A9EFF', 'Strain');
makeLine('chartHRV', tsData('hrv'), '#A855F7', 'HRV (ms)');
makeLine('chartRHR', tsData('rhr'), '#EF4444', 'RHR (bpm)');
makeLine('chartSleep', tsData('sleep_score'), '#2DD4BF', 'Sleep %');

// Dual line: avg HR vs max HR
new Chart(document.getElementById('chartHR'), {{
  type: 'line',
  data: {{
    labels: tsLabels(),
    datasets: [
      {{ label: 'Avg HR', data: tsData('avg_hr'), borderColor: '#F59E0B', borderWidth: 1.5, pointRadius: 1, tension: 0.3, spanGaps: true }},
      {{ label: 'Max HR', data: tsData('max_hr'), borderColor: '#EF4444', borderWidth: 1.5, pointRadius: 1, tension: 0.3, spanGaps: true }},
    ],
  }},
  options: {{ ...chartDefaults, plugins: {{ ...chartDefaults.plugins, legend: {{ display: true, labels: {{ color: '#888' }} }} }} }},
}});

// Scatter plots
function makeScatter(id, data, color, xLabel, yLabel) {{
  new Chart(document.getElementById(id), {{
    type: 'scatter',
    data: {{
      datasets: [{{
        data: data,
        backgroundColor: color + '88',
        borderColor: color,
        borderWidth: 1,
        pointRadius: 4,
        pointHoverRadius: 6,
      }}],
    }},
    options: {{
      ...chartDefaults,
      plugins: {{
        ...chartDefaults.plugins,
        tooltip: {{
          ...chartDefaults.plugins.tooltip,
          callbacks: {{ label: (ctx) => `${{ctx.raw.date}}: ${{xLabel}}=${{ctx.raw.x}}, ${{yLabel}}=${{ctx.raw.y}}` }},
        }},
      }},
      scales: {{
        x: {{ title: {{ display: true, text: xLabel, color: '#888' }}, ticks: {{ color: '#555' }}, grid: {{ color: '#1a1a1a' }} }},
        y: {{ title: {{ display: true, text: yLabel, color: '#888' }}, ticks: {{ color: '#555' }}, grid: {{ color: '#1a1a1a' }} }},
      }},
    }},
  }});
}}

makeScatter('scatterStrainRec', D.scatter_strain_rec, '#44D62C', 'Strain', 'Recovery %');
makeScatter('scatterHRVSleep', D.scatter_hrv_sleep, '#A855F7', 'Sleep Hours', 'HRV (ms)');

// Weekly patterns chart
(function() {{
  const wp = D.weekly_patterns;
  const labels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const ds = (key, color, label) => ({{
    label, data: labels.map(l => wp[l] ? wp[l][key] : 0),
    backgroundColor: color + '88', borderColor: color, borderWidth: 1,
  }});
  new Chart(document.getElementById('chartWeekly'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [
        ds('strain', '#4A9EFF', 'Strain'),
        ds('recovery', '#44D62C', 'Recovery'),
        ds('sleep', '#2DD4BF', 'Sleep'),
      ],
    }},
    options: {{
      ...chartDefaults,
      plugins: {{ ...chartDefaults.plugins, legend: {{ display: true, labels: {{ color: '#888' }} }} }},
    }},
  }});
}})();

// --- Monthly Table ---
(function() {{
  const ma = D.monthly_avg;
  const months = Object.keys(ma).sort();
  let html = '<thead><tr><th>Month</th><th>Days</th><th>Strain</th><th>Recovery</th><th>Sleep</th><th>HRV</th><th>RHR</th></tr></thead><tbody>';
  for (const m of months) {{
    const r = ma[m];
    html += `<tr>
      <td style="color:#fff;font-weight:600">${{m}}</td>
      <td>${{r.days}}</td>
      <td style="color:#4A9EFF">${{r.strain ?? '--'}}</td>
      <td style="color:${{recColor(r.recovery)}}">${{r.recovery != null ? r.recovery + '%' : '--'}}</td>
      <td style="color:#2DD4BF">${{r.sleep != null ? r.sleep + '%' : '--'}}</td>
      <td style="color:#A855F7">${{r.hrv ?? '--'}}</td>
      <td style="color:#EF4444">${{r.rhr ?? '--'}}</td>
    </tr>`;
  }}
  html += '</tbody>';
  document.getElementById('monthlyTable').innerHTML = html;
}})();

// --- Insights ---
document.getElementById('insightsContainer').innerHTML = D.insights.map(i =>
  `<div class="insight">${{i.replace(/(\\d+\\.?\\d*%?)/g, '<strong>$1</strong>')}}</div>`
).join('');
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
