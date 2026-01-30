/// Strain and Sleep coaching logic based on recovery score and sleep data.

class StrainCoach {
  final double recoveryScore;

  const StrainCoach(this.recoveryScore);

  String get zone {
    if (recoveryScore >= 67) return 'green';
    if (recoveryScore >= 34) return 'yellow';
    return 'red';
  }

  double get targetMin {
    if (recoveryScore >= 67) return 14;
    if (recoveryScore >= 34) return 8;
    return 0;
  }

  double get targetMax {
    if (recoveryScore >= 67) return 18;
    if (recoveryScore >= 34) return 14;
    return 8;
  }

  String get headline {
    if (recoveryScore >= 67) return 'Your body is ready';
    if (recoveryScore >= 34) return 'Moderate day';
    return 'Focus on rest';
  }

  String get recommendation {
    if (recoveryScore >= 67) {
      return 'Target strain ${targetMin.round()}-${targetMax.round()}. Push yourself today.';
    }
    if (recoveryScore >= 34) {
      return 'Target strain ${targetMin.round()}-${targetMax.round()}. Keep it moderate.';
    }
    return 'Keep strain under ${targetMax.round()}. Prioritize recovery.';
  }
}

class SleepCoach {
  /// Sleep needed in hours (from API or default 7.5)
  final double sleepNeededHours;

  /// Recent average wake time hour (0-23)
  final int avgWakeHour;
  final int avgWakeMinute;

  /// Last night's sleep performance (0-100)
  final double sleepPerformance;

  /// Sleep debt in hours (positive = deficit)
  final double sleepDebt;

  const SleepCoach({
    this.sleepNeededHours = 7.5,
    this.avgWakeHour = 7,
    this.avgWakeMinute = 0,
    this.sleepPerformance = 0,
    this.sleepDebt = 0,
  });

  /// Recommended bedtime as hour:minute
  String get recommendedBedtime {
    final totalMinutes = (sleepNeededHours * 60).round() + 15; // 15min to fall asleep
    final wakeMinutes = avgWakeHour * 60 + avgWakeMinute;
    var bedMinutes = wakeMinutes - totalMinutes;
    if (bedMinutes < 0) bedMinutes += 24 * 60;
    final h = bedMinutes ~/ 60;
    final m = bedMinutes % 60;
    return '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}';
  }

  String get sleepNeededDisplay {
    final h = sleepNeededHours.floor();
    final m = ((sleepNeededHours - h) * 60).round();
    if (m == 0) return '${h}h';
    return '${h}h ${m}m';
  }

  String get debtDisplay {
    if (sleepDebt <= 0) return 'No sleep debt';
    final h = sleepDebt.floor();
    final m = ((sleepDebt - h) * 60).round();
    if (h == 0) return '${m}m debt';
    return '${h}h ${m}m debt';
  }

  /// Parse sleep coach data from getSleepLastNight() API response.
  static SleepCoach fromSleepData(Map<String, dynamic>? sleepLastNight) {
    if (sleepLastNight == null) return const SleepCoach();

    double sleepNeeded = 7.5;
    double sleepPerf = 0;
    double debt = 0;
    int wakeH = 7;
    int wakeM = 0;

    // Parse from header section
    final header = sleepLastNight['header_section'] as Map<String, dynamic>?;
    final dest = header?['destination'] as Map<String, dynamic>?;
    final params = dest?['parameters'] as Map<String, dynamic>?;
    final endStr = params?['end_time'] as String?;
    if (endStr != null) {
      final end = DateTime.tryParse(endStr)?.toLocal();
      if (end != null) {
        wakeH = end.hour;
        wakeM = end.minute;
      }
    }

    // Parse sections for sleep needed and performance
    for (final section in sleepLastNight['sections'] as List? ?? []) {
      for (final item in (section as Map)['items'] as List? ?? []) {
        final map = item as Map<String, dynamic>;
        final content = map['content'] as Map<String, dynamic>?;
        if (content == null) continue;

        final metrics = content['metrics'] as List?;
        if (metrics != null) {
          for (final m in metrics) {
            final metric = m as Map<String, dynamic>;
            final title = (metric['title'] as String? ?? '').toUpperCase();
            final status = metric['status'] as String? ?? '';
            if (title.contains('NEEDED') || title.contains('SLEEP NEED')) {
              sleepNeeded = _parseHours(status);
            } else if (title.contains('PERFORMANCE')) {
              sleepPerf = _parseNum(status);
            } else if (title.contains('DEBT')) {
              debt = _parseHours(status);
            }
          }
        }
      }
    }

    return SleepCoach(
      sleepNeededHours: sleepNeeded > 0 ? sleepNeeded : 7.5,
      avgWakeHour: wakeH,
      avgWakeMinute: wakeM,
      sleepPerformance: sleepPerf,
      sleepDebt: debt,
    );
  }

  static double _parseHours(String s) {
    // "7h 45m" or "7.5" or "7:45"
    final hm = RegExp(r'(\d+)h\s*(\d+)m').firstMatch(s);
    if (hm != null) {
      return int.parse(hm.group(1)!) + int.parse(hm.group(2)!) / 60.0;
    }
    final colonMatch = RegExp(r'(\d+):(\d+)').firstMatch(s);
    if (colonMatch != null) {
      return int.parse(colonMatch.group(1)!) + int.parse(colonMatch.group(2)!) / 60.0;
    }
    return _parseNum(s);
  }

  static double _parseNum(dynamic v) {
    if (v is num) return v.toDouble();
    if (v is String) {
      final cleaned = v.replaceAll(RegExp(r'[^0-9.]'), '');
      return double.tryParse(cleaned) ?? 0;
    }
    return 0;
  }
}
