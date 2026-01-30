import 'dart:math';

import '../models/daily_score.dart';
import '../models/sensor_record.dart';
import 'scoring_engine.dart';
import 'sensor_db_service.dart';
import 'storage_service.dart';

class HrvAnalysis {
  final double rmssd;
  final double sdnn;
  final int sampleCount;
  final List<double> dailyRmssd;
  HrvAnalysis({required this.rmssd, required this.sdnn, required this.sampleCount, required this.dailyRmssd});
}

class SleepAnalysis {
  final double avgPerformance;
  final int daysAnalyzed;
  final double? bestNight;
  final double? worstNight;
  final Map<String, double> avgStages; // stage -> avg minutes
  SleepAnalysis({required this.avgPerformance, required this.daysAnalyzed, this.bestNight, this.worstNight, required this.avgStages});
}

class RecoveryAnalysis {
  final double avgScore;
  final int daysAnalyzed;
  final int greenDays;
  final int yellowDays;
  final int redDays;
  final List<double> dailyScores;
  final int streak; // consecutive green days
  RecoveryAnalysis({required this.avgScore, required this.daysAnalyzed, required this.greenDays, required this.yellowDays, required this.redDays, required this.dailyScores, required this.streak});
}

class Insight {
  final String icon;
  final String title;
  final String body;
  Insight({required this.icon, required this.title, required this.body});
}

class AnalysisService {
  final SensorDbService sensorDb;
  final StorageService storage;

  AnalysisService({required this.sensorDb, required this.storage});

  Future<HrvAnalysis> analyzeHrv(int days) async {
    final now = DateTime.now().millisecondsSinceEpoch;
    final start = now - (days * 86400000);
    final records = await sensorDb.getHrvRecords(start, now);

    if (records.isEmpty) {
      return HrvAnalysis(rmssd: 0, sdnn: 0, sampleCount: 0, dailyRmssd: []);
    }

    // Collect all RR intervals
    final rrIntervals = <double>[];
    for (final r in records) {
      for (final key in ['rr1', 'rr2', 'rr3']) {
        final v = (r[key] as int?) ?? 0;
        if (v > 200 && v < 2000) rrIntervals.add(v.toDouble());
      }
    }

    if (rrIntervals.length < 2) {
      return HrvAnalysis(rmssd: 0, sdnn: 0, sampleCount: rrIntervals.length, dailyRmssd: []);
    }

    // RMSSD
    double sumSqDiff = 0;
    for (var i = 1; i < rrIntervals.length; i++) {
      final diff = rrIntervals[i] - rrIntervals[i - 1];
      sumSqDiff += diff * diff;
    }
    final rmssd = sqrt(sumSqDiff / (rrIntervals.length - 1));

    // SDNN
    final mean = rrIntervals.reduce((a, b) => a + b) / rrIntervals.length;
    double sumSqDev = 0;
    for (final rr in rrIntervals) {
      sumSqDev += (rr - mean) * (rr - mean);
    }
    final sdnn = sqrt(sumSqDev / rrIntervals.length);

    // Daily RMSSD
    final dailyRmssd = <double>[];
    for (var d = 0; d < days; d++) {
      final dayStart = now - ((d + 1) * 86400000);
      final dayEnd = now - (d * 86400000);
      final dayRecords = records.where((r) {
        final ts = r['timestamp'] as int;
        return ts >= dayStart && ts < dayEnd;
      }).toList();

      final dayRr = <double>[];
      for (final r in dayRecords) {
        for (final key in ['rr1', 'rr2', 'rr3']) {
          final v = (r[key] as int?) ?? 0;
          if (v > 200 && v < 2000) dayRr.add(v.toDouble());
        }
      }

      if (dayRr.length >= 2) {
        double daySumSq = 0;
        for (var i = 1; i < dayRr.length; i++) {
          final diff = dayRr[i] - dayRr[i - 1];
          daySumSq += diff * diff;
        }
        dailyRmssd.add(sqrt(daySumSq / (dayRr.length - 1)));
      }
    }

    return HrvAnalysis(rmssd: rmssd, sdnn: sdnn, sampleCount: rrIntervals.length, dailyRmssd: dailyRmssd.reversed.toList());
  }

  SleepAnalysis analyzeSleep(int days) {
    final scores = <double>[];
    final now = DateTime.now();

    for (var d = 0; d < days; d++) {
      final date = now.subtract(Duration(days: d));
      final dateStr = '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
      final cached = storage.get<Map<String, dynamic>>('deep_dive_sleep_$dateStr');
      if (cached == null) continue;

      // Try to extract score
      final sections = cached['sections'] as List? ?? [];
      for (final sec in sections) {
        final items = (sec as Map)['items'] as List? ?? [];
        for (final item in items) {
          final content = (item as Map)['content'] as Map<String, dynamic>? ?? {};
          final scoreDisplay = content['score_display'] as String?;
          if (scoreDisplay != null) {
            final val = double.tryParse(scoreDisplay.replaceAll(RegExp(r'[^0-9.]'), ''));
            if (val != null) scores.add(val);
          }
        }
      }
    }

    if (scores.isEmpty) {
      return SleepAnalysis(avgPerformance: 0, daysAnalyzed: 0, avgStages: {});
    }

    return SleepAnalysis(
      avgPerformance: scores.reduce((a, b) => a + b) / scores.length,
      daysAnalyzed: scores.length,
      bestNight: scores.reduce(max),
      worstNight: scores.reduce(min),
      avgStages: {},
    );
  }

  RecoveryAnalysis analyzeRecovery(int days) {
    final scores = <double>[];
    final now = DateTime.now();

    for (var d = 0; d < days; d++) {
      final date = now.subtract(Duration(days: d));
      final dateStr = '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
      final cached = storage.get<Map<String, dynamic>>('deep_dive_recovery_$dateStr');
      if (cached == null) continue;

      final sections = cached['sections'] as List? ?? [];
      for (final sec in sections) {
        final items = (sec as Map)['items'] as List? ?? [];
        for (final item in items) {
          final content = (item as Map)['content'] as Map<String, dynamic>? ?? {};
          final scoreDisplay = content['score_display'] as String?;
          if (scoreDisplay != null) {
            final val = double.tryParse(scoreDisplay.replaceAll(RegExp(r'[^0-9.]'), ''));
            if (val != null) scores.add(val);
          }
        }
      }
    }

    if (scores.isEmpty) {
      return RecoveryAnalysis(avgScore: 0, daysAnalyzed: 0, greenDays: 0, yellowDays: 0, redDays: 0, dailyScores: [], streak: 0);
    }

    int green = 0, yellow = 0, red = 0;
    for (final s in scores) {
      if (s >= 67) green++;
      else if (s >= 34) yellow++;
      else red++;
    }

    // Streak from most recent
    int streak = 0;
    for (final s in scores) {
      if (s >= 67) streak++;
      else break;
    }

    return RecoveryAnalysis(
      avgScore: scores.reduce((a, b) => a + b) / scores.length,
      daysAnalyzed: scores.length,
      greenDays: green,
      yellowDays: yellow,
      redDays: red,
      dailyScores: scores.reversed.toList(),
      streak: streak,
    );
  }

  /// Compute local scores for the last N days using the scoring engine.
  Future<List<DailyScore>> computeLocalScores(int days) async {
    final scores = <DailyScore>[];
    final now = DateTime.now();

    for (var d = 0; d < days; d++) {
      final date = DateTime(now.year, now.month, now.day).subtract(Duration(days: d));
      final rows = await sensorDb.getRecordsForDay(date);
      if (rows.isEmpty) continue;

      final records = rows.map((r) => SensorRecord.fromDb(r)).toList();
      final score = ScoringEngine.computeDaily(
        records: records,
        date: date,
      );
      if (score != null) scores.add(score);
    }

    return scores.reversed.toList(); // Oldest first
  }

  Future<List<Insight>> generateInsights() async {
    final insights = <Insight>[];
    final hrv = await analyzeHrv(7);
    final recovery = analyzeRecovery(7);

    if (hrv.rmssd > 0) {
      if (hrv.rmssd > 60) {
        insights.add(Insight(icon: 'heart', title: 'Strong HRV', body: 'Your RMSSD of ${hrv.rmssd.round()}ms indicates good autonomic balance. Keep up your recovery routine.'));
      } else if (hrv.rmssd < 30) {
        insights.add(Insight(icon: 'warning', title: 'Low HRV', body: 'Your RMSSD of ${hrv.rmssd.round()}ms is below typical. Consider prioritizing sleep and reducing stress.'));
      }
    }

    if (recovery.daysAnalyzed > 0) {
      if (recovery.streak >= 3) {
        insights.add(Insight(icon: 'streak', title: '${recovery.streak}-Day Green Streak', body: 'You\'ve been in the green zone for ${recovery.streak} days. Your body is well recovered.'));
      }
      if (recovery.redDays > recovery.greenDays && recovery.daysAnalyzed >= 5) {
        insights.add(Insight(icon: 'alert', title: 'Recovery Deficit', body: 'You had ${recovery.redDays} red days vs ${recovery.greenDays} green days this week. Consider more rest.'));
      }
      if (recovery.avgScore > 0) {
        insights.add(Insight(icon: 'avg', title: 'Weekly Avg: ${recovery.avgScore.round()}%', body: 'Your average recovery this week across ${recovery.daysAnalyzed} days.'));
      }
    }

    if (insights.isEmpty) {
      insights.add(Insight(icon: 'info', title: 'Sync More Data', body: 'Sync more days from Settings to see personalized insights based on your HRV, sleep, and recovery trends.'));
    }

    return insights;
  }
}
