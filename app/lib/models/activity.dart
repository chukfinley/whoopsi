class Activity {
  final String id;
  final String name;
  final double? strain;
  final Duration? duration;
  final DateTime? startTime;
  final int? averageHr;
  final int? maxHr;
  final double? kilojoules;

  const Activity({
    required this.id,
    required this.name,
    this.strain,
    this.duration,
    this.startTime,
    this.averageHr,
    this.maxHr,
    this.kilojoules,
  });

  factory Activity.fromJson(Map<String, dynamic> json) {
    final durationMs = json['duration_ms'] as num?;
    final durationS = json['duration_s'] as num?;

    return Activity(
      id: '${json['id'] ?? json['activity_id'] ?? ''}',
      name: json['name'] as String? ?? json['sport_name'] as String? ?? 'Activity',
      strain: _toDouble(json['strain'] ?? json['score']),
      duration: durationMs != null
          ? Duration(milliseconds: durationMs.toInt())
          : durationS != null
              ? Duration(seconds: durationS.toInt())
              : null,
      startTime: json['start'] is String ? DateTime.tryParse(json['start']) : null,
      averageHr: (json['average_heart_rate'] as num?)?.toInt(),
      maxHr: (json['max_heart_rate'] as num?)?.toInt(),
      kilojoules: _toDouble(json['kilojoules']),
    );
  }

  String get durationFormatted {
    if (duration == null) return '--';
    final h = duration!.inHours;
    final m = duration!.inMinutes % 60;
    return h > 0 ? '${h}h ${m}m' : '${m}m';
  }

  static double? _toDouble(dynamic v) => v is num ? v.toDouble() : null;

  static List<Activity> listFromJson(List<dynamic> list) =>
      list.map((e) => Activity.fromJson(e as Map<String, dynamic>)).toList();
}
