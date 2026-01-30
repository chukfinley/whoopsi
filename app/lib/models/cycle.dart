class Cycle {
  final String id;
  final DateTime? start;
  final DateTime? end;
  final double? strainScore;
  final double? kilojoules;
  final double? recoveryScore;
  final double? hrvMs;
  final int? rhr;
  final double? sleepScore;
  final double? sleepHours;

  const Cycle({
    required this.id,
    this.start,
    this.end,
    this.strainScore,
    this.kilojoules,
    this.recoveryScore,
    this.hrvMs,
    this.rhr,
    this.sleepScore,
    this.sleepHours,
  });

  factory Cycle.fromJson(Map<String, dynamic> json) {
    final strain = json['strain'] as Map<String, dynamic>?;
    final recovery = json['recovery'] as Map<String, dynamic>?;
    final sleep = json['sleep'] as Map<String, dynamic>?;

    return Cycle(
      id: '${json['id'] ?? json['cycle_id'] ?? ''}',
      start: _tryParse(json['start']),
      end: _tryParse(json['end']),
      strainScore: _toDouble(strain?['score']),
      kilojoules: _toDouble(strain?['kilojoules']),
      recoveryScore: _toDouble(recovery?['score']),
      hrvMs: _toDouble(recovery?['hrv_rmssd_milli']),
      rhr: (recovery?['resting_heart_rate'] as num?)?.toInt(),
      sleepScore: _toDouble(sleep?['score']),
      sleepHours: _toDouble(sleep?['quality_duration_s'] != null
          ? (sleep!['quality_duration_s'] as num) / 3600.0
          : sleep?['hours']),
    );
  }

  static DateTime? _tryParse(dynamic v) =>
      v is String ? DateTime.tryParse(v) : null;

  static double? _toDouble(dynamic v) => v is num ? v.toDouble() : null;

  static List<Cycle> listFromJson(List<dynamic> list) =>
      list.map((e) => Cycle.fromJson(e as Map<String, dynamic>)).toList();
}
