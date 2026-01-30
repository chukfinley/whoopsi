class Recovery {
  final double score;
  final double hrvMs;
  final int rhr;
  final double respiratoryRate;
  final String? sleepPerformance;
  final Map<String, dynamic> raw;

  const Recovery({
    this.score = 0,
    this.hrvMs = 0,
    this.rhr = 0,
    this.respiratoryRate = 0,
    this.sleepPerformance,
    this.raw = const {},
  });

  factory Recovery.fromDeepDive(Map<String, dynamic> json) {
    double score = 0;
    double hrv = 0;
    int rhr = 0;
    double respRate = 0;
    String? sleepPerf;

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
            if (title.contains('HEART RATE VARIABILITY') || title.contains('HRV')) {
              hrv = _parseNum(status);
            } else if (title.contains('RESTING HEART RATE') || title.contains('RHR')) {
              rhr = _parseNum(status).round();
            } else if (title.contains('RESPIRATORY')) {
              respRate = _parseNum(status);
            } else if (title.contains('SLEEP PERFORMANCE')) {
              sleepPerf = status;
            }
          }
        }
      }
    }

    return Recovery(
      score: score,
      hrvMs: hrv,
      rhr: rhr,
      respiratoryRate: respRate,
      sleepPerformance: sleepPerf,
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
