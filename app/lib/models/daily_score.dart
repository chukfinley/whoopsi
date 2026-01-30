class DailyScore {
  final DateTime date;
  final int recoveryScore;
  final int sleepScore;
  final double strainScore;
  final double hrv;
  final double rhr;
  final double respiratoryRate;
  final int totalSleepMin;
  final double sleepEfficiency;
  final Map<String, double> sleepPhases;
  final int recordCount;

  DailyScore({
    required this.date,
    required this.recoveryScore,
    required this.sleepScore,
    required this.strainScore,
    required this.hrv,
    required this.rhr,
    required this.respiratoryRate,
    required this.totalSleepMin,
    required this.sleepEfficiency,
    required this.sleepPhases,
    required this.recordCount,
  });

  String get recoveryColor {
    if (recoveryScore >= 67) return 'green';
    if (recoveryScore >= 34) return 'yellow';
    return 'red';
  }

  String get sleepHours {
    final h = totalSleepMin ~/ 60;
    final m = totalSleepMin % 60;
    return '${h}h ${m}m';
  }
}
