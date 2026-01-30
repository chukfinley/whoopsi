class Strain {
  final double score;
  final String? hrZones13;
  final String? hrZones45;
  final String? strengthTime;
  final String? steps;
  final Map<String, dynamic> raw;

  const Strain({
    this.score = 0,
    this.hrZones13,
    this.hrZones45,
    this.strengthTime,
    this.steps,
    this.raw = const {},
  });

  factory Strain.fromDeepDive(Map<String, dynamic> json) {
    double score = 0;
    String? hrZones13;
    String? hrZones45;
    String? strengthTime;
    String? steps;

    for (final section in json['sections'] as List? ?? []) {
      for (final item in (section as Map)['items'] as List? ?? []) {
        final map = item as Map<String, dynamic>;
        final type = map['type'] as String?;
        final content = map['content'] as Map<String, dynamic>?;
        if (content == null) continue;

        if (type == 'SCORE_GAUGE') {
          final display = content['score_display'];
          if (display != null) {
            score = _parseNum(display);
          } else {
            score = _parseNum(content['gauge_fill_percentage']) * 21;
          }
        } else if (type == 'CONTRIBUTORS_TILE') {
          for (final m in content['metrics'] as List? ?? []) {
            final metric = m as Map<String, dynamic>;
            final title = (metric['title'] as String? ?? '').toUpperCase();
            final status = metric['status'] as String? ?? '';
            if (title.contains('ZONES 1-3')) {
              hrZones13 = status;
            } else if (title.contains('ZONES 4-5')) {
              hrZones45 = status;
            } else if (title.contains('STRENGTH')) {
              strengthTime = status;
            } else if (title.contains('STEPS')) {
              steps = status;
            }
          }
        }
      }
    }

    return Strain(
      score: score,
      hrZones13: hrZones13,
      hrZones45: hrZones45,
      strengthTime: strengthTime,
      steps: steps,
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
