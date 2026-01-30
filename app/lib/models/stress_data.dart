import 'package:flutter/foundation.dart';

class StressPoint {
  final DateTime time;
  final double value; // 0-100

  const StressPoint(this.time, this.value);
}

class StressZoneTime {
  final Duration low;
  final Duration moderate;
  final Duration high;

  const StressZoneTime({
    this.low = Duration.zero,
    this.moderate = Duration.zero,
    this.high = Duration.zero,
  });

  Duration get total => low + moderate + high;
}

class StressData {
  final String state;
  final double meterValue; // 0.0-1.0
  final List<StressPoint> timeline;
  final StressZoneTime zones;
  final double average;
  final double peak;
  final double lowest;
  final String? coachTip;
  final Map<String, dynamic> raw;

  const StressData({
    required this.state,
    required this.meterValue,
    required this.timeline,
    required this.zones,
    required this.average,
    required this.peak,
    required this.lowest,
    this.coachTip,
    this.raw = const {},
  });

  factory StressData.fromApi(Map<String, dynamic> json) {
    // Parse stress state
    String state = 'Unknown';
    final apiState = json['stressState'] as String?;
    if (apiState != null) {
      state = apiState;
    } else {
      // Try nested sections
      for (final section in json['sections'] as List? ?? []) {
        for (final item in (section as Map)['items'] as List? ?? []) {
          final content = (item as Map)['content'] as Map<String, dynamic>?;
          if (content == null) continue;
          final s = content['stress_state'] as String?;
          if (s != null) { state = s; break; }
          final title = content['title'] as String? ?? '';
          if (title.toUpperCase().contains('STRESS')) {
            final sub = content['subtitle'] as String? ?? content['status'] as String? ?? '';
            if (sub.isNotEmpty) { state = sub; break; }
          }
        }
        if (state != 'Unknown') break;
      }
      if (state == 'Unknown') {
        state = json['stress_state'] as String? ?? json['current_state'] as String? ?? 'Unknown';
      }
    }

    // Parse meter value from API
    double meterValue = _stateToLevel(state);
    final meter = json['meter'] as Map<String, dynamic>?;
    if (meter != null) {
      final fill = meter['fill'] as num? ?? meter['value'] as num? ?? meter['percentage'] as num?;
      if (fill != null) {
        meterValue = (fill.toDouble() / 100.0).clamp(0.0, 1.0);
      }
    }

    // Parse timeline
    final timeline = <StressPoint>[];
    _extractPoints(json, timeline);
    timeline.sort((a, b) => a.time.compareTo(b.time));

    // Compute stats
    double avg = 0, peak = 0, low = 100;
    if (timeline.isNotEmpty) {
      double sum = 0;
      for (final p in timeline) {
        sum += p.value;
        if (p.value > peak) peak = p.value;
        if (p.value < low) low = p.value;
      }
      avg = sum / timeline.length;
    } else {
      low = 0;
    }

    // Compute zone times
    final zones = _computeZones(timeline);

    // Parse coaching tip
    String? coachTip;
    final vow = json['vow'] as Map<String, dynamic>?;
    if (vow != null) {
      coachTip = vow['message'] as String? ?? vow['text'] as String? ?? vow['title'] as String?;
    }
    if (coachTip == null) {
      final whoopCoach = json['whoopCoachVow'] as Map<String, dynamic>?;
      if (whoopCoach != null) {
        coachTip = whoopCoach['message'] as String? ?? whoopCoach['text'] as String?;
      }
    }

    return StressData(
      state: state,
      meterValue: meterValue,
      timeline: timeline,
      zones: zones,
      average: avg,
      peak: peak,
      lowest: low,
      coachTip: coachTip,
      raw: json,
    );
  }

  static double _stateToLevel(String state) {
    final n = state.toLowerCase();
    if (n.contains('low') || n.contains('calm') || n.contains('rest')) return 0.25;
    if (n.contains('medium') || n.contains('moderate') || n.contains('normal')) return 0.55;
    if (n.contains('high') || n.contains('elevated')) return 0.85;
    return 0.5;
  }

  static void _extractPoints(Map<String, dynamic> json, List<StressPoint> out) {
    // Try official stressGraph
    final graph = json['stressGraph'] as Map<String, dynamic>?;
    if (graph != null) {
      _parsePointList(graph['data_points'] as List? ?? graph['dataPoints'] as List? ?? [], out);
    }

    // Try nested sections
    if (out.isEmpty) {
      for (final section in json['sections'] as List? ?? []) {
        for (final item in (section as Map)['items'] as List? ?? []) {
          final content = (item as Map)['content'] as Map<String, dynamic>?;
          if (content == null) continue;
          _parsePointList(content['data_points'] as List? ?? content['timeline'] as List? ?? [], out);
        }
      }
    }

    // Top-level fallback
    if (out.isEmpty) {
      _parsePointList(json['data_points'] as List? ?? json['timeline'] as List? ?? [], out);
    }
  }

  static void _parsePointList(List dataPoints, List<StressPoint> out) {
    for (final dp in dataPoints) {
      try {
        final map = dp as Map<String, dynamic>;
        final timestamp = map['timestamp'] as String? ?? map['time'] as String?;
        final value = (map['value'] as num?)?.toDouble() ?? (map['stress_level'] as num?)?.toDouble();
        if (timestamp != null && value != null) {
          final dt = DateTime.tryParse(timestamp)?.toLocal();
          if (dt != null) out.add(StressPoint(dt, value));
        }
      } catch (e) {
        debugPrint('Failed to parse stress point: $e');
      }
    }
  }

  static StressZoneTime _computeZones(List<StressPoint> timeline) {
    if (timeline.length < 2) return const StressZoneTime();

    Duration low = Duration.zero;
    Duration moderate = Duration.zero;
    Duration high = Duration.zero;

    for (var i = 0; i < timeline.length - 1; i++) {
      final gap = timeline[i + 1].time.difference(timeline[i].time);
      // Cap gap to 10 minutes to avoid counting data gaps
      final dt = gap > const Duration(minutes: 10) ? const Duration(minutes: 5) : gap;
      final v = timeline[i].value;
      if (v < 34) {
        low += dt;
      } else if (v < 67) {
        moderate += dt;
      } else {
        high += dt;
      }
    }

    return StressZoneTime(low: low, moderate: moderate, high: high);
  }
}

class StressDaySummary {
  final String date;
  final String state;
  final double avgStress;
  final double peakStress;

  const StressDaySummary({
    required this.date,
    required this.state,
    required this.avgStress,
    required this.peakStress,
  });
}
