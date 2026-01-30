class Sleep {
  final double score;
  final String? hoursVsNeeded;
  final String? consistency;
  final String? efficiency;
  final String? sleepStress;
  final Map<String, dynamic> raw;

  const Sleep({
    this.score = 0,
    this.hoursVsNeeded,
    this.consistency,
    this.efficiency,
    this.sleepStress,
    this.raw = const {},
  });

  factory Sleep.fromDeepDive(Map<String, dynamic> json) {
    double score = 0;
    String? hoursVsNeeded;
    String? consistency;
    String? efficiency;
    String? sleepStress;

    for (final section in json['sections'] as List? ?? []) {
      for (final item in (section as Map)['items'] as List? ?? []) {
        final map = item as Map<String, dynamic>;
        final type = map['type'] as String?;
        final content = map['content'] as Map<String, dynamic>?;
        if (content == null) continue;

        if (type == 'SCORE_GAUGE') {
          score = _parseNum(content['gauge_fill_percentage']) * 100;
          final display = content['score_display'];
          if (display != null) score = _parseNum(display);
        } else if (type == 'CONTRIBUTORS_TILE') {
          for (final m in content['metrics'] as List? ?? []) {
            final metric = m as Map<String, dynamic>;
            final title = (metric['title'] as String? ?? '').toUpperCase();
            final status = metric['status'] as String? ?? '';
            if (title.contains('HOURS') && title.contains('NEEDED')) {
              hoursVsNeeded = status;
            } else if (title.contains('CONSISTENCY')) {
              consistency = status;
            } else if (title.contains('EFFICIENCY')) {
              efficiency = status;
            } else if (title.contains('STRESS')) {
              sleepStress = status;
            }
          }
        }
      }
    }

    return Sleep(
      score: score,
      hoursVsNeeded: hoursVsNeeded,
      consistency: consistency,
      efficiency: efficiency,
      sleepStress: sleepStress,
      raw: json,
    );
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
