import 'dart:math';
import '../models/sensor_record.dart';
import '../models/daily_score.dart';

/// Port of algorithms/algo4_calibrated/engine.py to Dart.
/// Whoop-calibrated scoring with optimized parameters (MAE 2.76).
class ScoringEngine {
  // Whoop HR zones (from hr-zones-service/v1/bff/zones)
  static const whoopZones = [
    [0, 108],   // Zone 0
    [109, 135], // Zone 1
    [136, 149], // Zone 2
    [150, 162], // Zone 3
    [163, 176], // Zone 4
    [177, 189], // Zone 5
  ];

  /// Compute all scores for a day's worth of sensor records.
  static DailyScore? computeDaily({
    required List<SensorRecord> records,
    required DateTime date,
    double hrvBaseline = 90,
    double rhrBaseline = 55,
  }) {
    if (records.isEmpty) return null;

    // Split into sleep (midnight-10am, 10pm-midnight) and day (all for strain)
    final sleepRecords = records.where((r) {
      final h = r.dateTime.hour;
      return h < 10 || h >= 22;
    }).toList();

    final rhr = computeSleepRhr(sleepRecords);
    final hrv = computeSwsHrv(sleepRecords);
    final respRate = computeRespiratoryRate(sleepRecords);

    final sleepResult = classifySleepPhases(sleepRecords, rhr);
    final summary = sleepResult.summary;

    final sleepScore = computeSleepScore(summary);
    final recovery = computeRecovery(
      hrv: hrv, rhr: rhr, sleepScore: sleepScore, respRate: respRate,
      hrvBaseline: hrvBaseline, rhrBaseline: rhrBaseline,
    );
    final strain = computeStrain(records);

    return DailyScore(
      date: date,
      recoveryScore: recovery,
      sleepScore: sleepScore,
      strainScore: strain,
      hrv: hrv,
      rhr: rhr,
      respiratoryRate: respRate,
      totalSleepMin: summary['sleep_min']?.toInt() ?? 0,
      sleepEfficiency: summary['efficiency'] ?? 0,
      sleepPhases: {
        'deep_pct': summary['deep_pct'] ?? 0,
        'light_pct': summary['light_pct'] ?? 0,
        'rem_pct': summary['rem_pct'] ?? 0,
        'awake_pct': summary['awake_pct'] ?? 0,
      },
      recordCount: records.length,
    );
  }

  /// RHR: average of P25 and median HR during sleep.
  static double computeSleepRhr(List<SensorRecord> sleepRecords) {
    final hrs = sleepRecords
        .where((r) => r.heartRate > 30)
        .map((r) => r.heartRate.toDouble())
        .toList();
    if (hrs.length < 300) {
      return hrs.isNotEmpty ? _median(hrs) : 55.0;
    }
    hrs.sort();
    final p25 = hrs[(hrs.length * 0.25).floor()];
    final med = _median(hrs);
    return ((p25 + med) / 2 * 10).roundToDouble() / 10;
  }

  /// HRV (RMSSD) during the slow-wave sleep window.
  static double computeSwsHrv(List<SensorRecord> sleepRecords) {
    final rrs = sleepRecords
        .where((r) => r.rr1Ms > 200 && r.rr1Ms < 2500)
        .map((r) => r.rr1Ms.toDouble())
        .toList();
    if (rrs.length < 30) return 0;

    final win = min(300, rrs.length ~/ 2);
    if (win < 30) {
      final diffs = _diffs(rrs).where((d) => d.abs() < 200).toList();
      return diffs.length > 5 ? _rmssd(diffs) : 0;
    }

    double? bestRmssd;
    double bestScore = double.infinity;

    for (int i = 0; i <= rrs.length - win; i += max(1, win ~/ 4)) {
      final chunk = rrs.sublist(i, i + win);
      final diffs = _diffs(chunk).where((d) => d.abs() < 200).toList();
      if (diffs.length < 10) continue;

      final rmssd = _rmssd(diffs);
      final meanRr = _mean(chunk);
      final stdRr = _std(chunk);
      final score = -meanRr + stdRr * 2;

      if (score < bestScore) {
        bestScore = score;
        bestRmssd = rmssd;
      }
    }
    return bestRmssd ?? 0;
  }

  /// Strain using Whoop HR zone boundaries + log-scale EPOC model.
  static double computeStrain(List<SensorRecord> records) {
    final hrs = records.where((r) => r.heartRate > 30).map((r) => r.heartRate).toList();
    if (hrs.isEmpty) return 0;

    final coverage = hrs.length / max(1, records.length);
    final scale = coverage > 0.1 ? min(3.0, 1.0 / coverage) : 1.0;

    const zoneWeights = [0.0, 1.0, 2.5, 5.0, 10.0, 20.0];
    double load = 0;
    for (int zi = 0; zi < whoopZones.length; zi++) {
      final lo = whoopZones[zi][0];
      final hi = whoopZones[zi][1];
      final minutesInZone = hrs.where((h) => h >= lo && h <= hi).length / 60.0;
      load += minutesInZone * zoneWeights[zi];
    }
    load *= scale;
    if (load <= 0) return 0;

    final strain = 2.32 * log(1 + load / 6.3);
    return min(21.0, (strain * 10).roundToDouble() / 10);
  }

  /// Sleep phase classification.
  static _SleepResult classifySleepPhases(List<SensorRecord> records, double rhr) {
    if (records.isEmpty) return _SleepResult([], {});

    const window = 600; // 10 min windows
    final phases = <Map<String, dynamic>>[];

    for (int i = 0; i < records.length - window; i += window) {
      final chunk = records.sublist(i, min(i + window, records.length));
      final hrs = chunk.where((r) => r.heartRate > 30).map((r) => r.heartRate.toDouble()).toList();
      final movements = chunk.map((r) => r.movement).toList();

      if (hrs.length < 5) {
        phases.add({'phase': 'unknown', 'hr': 0.0});
        continue;
      }

      final avgHr = _median(hrs);
      final hrIqr = hrs.length > 10
          ? _percentile(hrs, 75) - _percentile(hrs, 25)
          : _std(hrs);
      final avgMv = _mean(movements);
      final maxMv = movements.reduce(max);
      final ha = avgHr - rhr;

      final rrs = chunk
          .where((r) => r.rr1Ms > 200 && r.rr1Ms < 2500)
          .map((r) => r.rr1Ms.toDouble())
          .toList();
      double localHrv = 0;
      if (rrs.length > 5) {
        final diffs = _diffs(rrs).where((d) => d.abs() < 300).toList();
        if (diffs.length > 3) localHrv = _rmssd(diffs);
      }

      final isMoving = avgMv > 0.6 || maxMv > 1.7;
      String phase;
      if (isMoving && ha > 16.3) {
        phase = 'awake';
      } else if (ha <= 0.9 && hrIqr < 9.9 && avgMv < 1.4) {
        phase = 'deep';
      } else if (hrIqr > 12.8 && avgMv < 0.4) {
        phase = 'rem';
      } else if (localHrv > 84.1 && avgMv < 0.4) {
        phase = 'rem';
      } else {
        phase = 'light';
      }
      phases.add({'phase': phase, 'hr': avgHr});
    }

    final total = phases.length;
    if (total == 0) {
      return _SleepResult(phases, {
        'total_min': 0.0, 'sleep_min': 0.0, 'efficiency': 0.0,
        'deep_pct': 0.0, 'light_pct': 0.0, 'rem_pct': 0.0, 'awake_pct': 0.0,
      });
    }

    int deepCount = 0, lightCount = 0, remCount = 0, awakeCount = 0, unknownCount = 0;
    for (final p in phases) {
      switch (p['phase']) {
        case 'deep': deepCount++; break;
        case 'light': lightCount++; break;
        case 'rem': remCount++; break;
        case 'awake': awakeCount++; break;
        default: unknownCount++; break;
      }
    }
    final sleepCount = total - awakeCount - unknownCount;

    return _SleepResult(phases, {
      'total_min': (total * 10).toDouble(),
      'sleep_min': (sleepCount * 10).toDouble(),
      'efficiency': total > 0 ? (sleepCount / total * 100) : 0.0,
      'deep_min': (deepCount * 10).toDouble(),
      'light_min': (lightCount * 10).toDouble(),
      'rem_min': (remCount * 10).toDouble(),
      'awake_min': (awakeCount * 10).toDouble(),
      'deep_pct': total > 0 ? (deepCount / total * 100) : 0.0,
      'light_pct': total > 0 ? (lightCount / total * 100) : 0.0,
      'rem_pct': total > 0 ? (remCount / total * 100) : 0.0,
      'awake_pct': total > 0 ? (awakeCount / total * 100) : 0.0,
    });
  }

  /// Sleep score matching Whoop's contributor weights.
  static int computeSleepScore(Map<String, double> summary, {double sleepNeedMin = 480}) {
    final totalMin = summary['sleep_min'] ?? 0;
    final efficiency = summary['efficiency'] ?? 0;
    final awakePct = summary['awake_pct'] ?? 0;

    final hoursScore = min(100.0, (totalMin / sleepNeedMin) * 100);
    final effScore = min(100.0, efficiency);
    const consistencyScore = 41.5;
    final stressScore = max(0.0, 100 - awakePct * 5);

    final score = 0.23 * hoursScore + 0.34 * consistencyScore +
        0.26 * effScore + 0.18 * stressScore;
    return max(0, min(100, score.round()));
  }

  /// Recovery score -- HRV-driven sigmoid.
  static int computeRecovery({
    required double hrv,
    required double rhr,
    required int sleepScore,
    required double respRate,
    required double hrvBaseline,
    required double rhrBaseline,
  }) {
    if (hrvBaseline <= 0) hrvBaseline = 90;

    final hrvRatio = hrv / hrvBaseline;
    final hrvScore = 100 / (1 + exp(-11.6 * (hrvRatio - 1.107)));

    double rhrScore;
    if (rhrBaseline > 0) {
      final rhrDiff = rhrBaseline - rhr;
      rhrScore = max(0, min(100, 50 + rhrDiff * 8.1));
    } else {
      rhrScore = max(0, min(100, 100 - (rhr - 45) * 2));
    }

    final respPenalty = respRate > 16 ? max(0.0, (respRate - 16) * 3) : 0.0;
    final sleepContrib = min(100.0, sleepScore.toDouble());

    final recovery = 0.09 * hrvScore + 0.43 * rhrScore +
        0.33 * sleepContrib + 0.14 * (100 - respPenalty);
    return max(0, min(100, recovery.round()));
  }

  /// Respiratory rate from RR interval frequency analysis (simplified).
  static double computeRespiratoryRate(List<SensorRecord> sleepRecords) {
    final rrs = sleepRecords
        .where((r) => r.rr1Ms > 200 && r.rr1Ms < 2500)
        .map((r) => r.rr1Ms.toDouble())
        .toList();
    if (rrs.length < 60) return 14.0;

    final diffs = _diffs(rrs);
    if (diffs.isEmpty) return 14.0;

    int crossings = 0;
    for (int i = 1; i < diffs.length; i++) {
      if ((diffs[i] > 0 && diffs[i - 1] < 0) || (diffs[i] < 0 && diffs[i - 1] > 0)) {
        crossings++;
      }
    }

    double totalMs = 0;
    for (final rr in rrs) totalMs += rr;
    final totalSec = totalMs / 1000;

    final breathsPerSec = crossings / 2 / totalSec;
    final bpm = breathsPerSec * 60;
    return max(8, min(25, (bpm * 10).roundToDouble() / 10));
  }

  // --- Utility functions ---

  static double _mean(List<double> vals) {
    if (vals.isEmpty) return 0;
    double sum = 0;
    for (final v in vals) sum += v;
    return sum / vals.length;
  }

  static double _median(List<double> vals) {
    if (vals.isEmpty) return 0;
    final sorted = List<double>.from(vals)..sort();
    final mid = sorted.length ~/ 2;
    return sorted.length.isOdd ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  static double _std(List<double> vals) {
    if (vals.length < 2) return 0;
    final m = _mean(vals);
    double sumSq = 0;
    for (final v in vals) sumSq += (v - m) * (v - m);
    return sqrt(sumSq / vals.length);
  }

  static double _percentile(List<double> vals, double pct) {
    final sorted = List<double>.from(vals)..sort();
    final idx = (sorted.length * pct / 100).floor().clamp(0, sorted.length - 1);
    return sorted[idx];
  }

  static List<double> _diffs(List<double> vals) {
    if (vals.length < 2) return [];
    return List.generate(vals.length - 1, (i) => vals[i + 1] - vals[i]);
  }

  static double _rmssd(List<double> diffs) {
    if (diffs.isEmpty) return 0;
    double sumSq = 0;
    for (final d in diffs) sumSq += d * d;
    return sqrt(sumSq / diffs.length);
  }
}

class _SleepResult {
  final List<Map<String, dynamic>> phases;
  final Map<String, double> summary;
  _SleepResult(this.phases, this.summary);
}
