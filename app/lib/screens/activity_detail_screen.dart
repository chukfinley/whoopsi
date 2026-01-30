import 'dart:math';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';
import '../widgets/score_gauge.dart';

class ActivityDetailScreen extends StatefulWidget {
  final String activityId;
  const ActivityDetailScreen({super.key, required this.activityId});

  @override
  State<ActivityDetailScreen> createState() => _ActivityDetailScreenState();
}

class _ActivityDetailScreenState extends State<ActivityDetailScreen> {
  Map<String, dynamic>? _workout; // From developer API
  Map<String, dynamic>? _cardioDetails; // From cardio-details BFF
  bool _loading = true;
  double? _scrubX;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    final api = context.read<ApiService>();

    // 1. Load cached workout data (from home screen tap)
    final cached = api.cache.get<Map<String, dynamic>>('workout:${widget.activityId}');
    if (cached != null) {
      _workout = cached;
      if (mounted) setState(() => _loading = false);
    }

    // 2. Try cardio-details BFF
    try {
      final details = await api.getActivityDetails(widget.activityId);
      if (mounted) setState(() { _cardioDetails = details; _loading = false; });
    } catch (e) {
      debugPrint('Cardio details failed: $e');
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: Text(_title(), style: const TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
          : (_workout == null && _cardioDetails == null)
              ? const Center(child: Text('No activity data', style: TextStyle(color: WhoopTheme.textSecondary)))
              : RefreshIndicator(
                  color: WhoopTheme.primary,
                  onRefresh: _fetch,
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      if (_cardioDetails != null) ..._buildCardioContent(),
                      if (_cardioDetails == null && _workout != null) ..._buildWorkoutFallback(),
                      const SizedBox(height: 24),
                    ],
                  ),
                ),
    );
  }

  String _title() {
    if (_cardioDetails != null) {
      final titleBar = _cardioDetails!['title_bar'] as Map<String, dynamic>?;
      if (titleBar != null) {
        return titleBar['title_display'] as String? ?? 'Activity';
      }
    }
    if (_workout != null) {
      final sportId = _workout!['sport_id'] as int? ?? 0;
      return _sportName(sportId);
    }
    return 'Activity';
  }

  // === Full cardio-details BFF rendering (matches official app) ===

  List<Widget> _buildCardioContent() {
    final cd = _cardioDetails!;
    final titleBar = cd['title_bar'] as Map<String, dynamic>? ?? {};
    final subtitle = titleBar['subtitle_display'] as String? ?? '';

    // Horizontal stats (Strain, Steps, etc.)
    final horizontalStats = cd['horizontal_stats'] as List? ?? [];
    final mainStat = horizontalStats.isNotEmpty ? horizontalStats[0] as Map<String, dynamic> : null;

    // Key metrics carousel
    final carousel = cd['key_metric_carousel'] as Map<String, dynamic>? ?? {};
    final keyMetrics = carousel['key_metric_tile'] as List? ?? [];

    // Graph
    final graphResponse = cd['graph_response'] as Map<String, dynamic>?;

    // HR Zones bar graph
    final barGraphContainer = cd['bar_graph_container'] as Map<String, dynamic>?;

    // VOW (coach insight)
    final vow = cd['vow_response_string'] as String?;

    final widgets = <Widget>[];

    // === Strain score header ===
    if (mainStat != null) {
      final strainVal = double.tryParse(mainStat['stat_main_value_display']?.toString().replaceAll(',', '') ?? '') ?? 0;
      final strainColor = strainVal < 7 ? WhoopTheme.sleepBlue : strainVal < 14 ? WhoopTheme.warning : WhoopTheme.error;

      widgets.add(Center(
        child: ScoreGauge(
          score: strainVal,
          maxScore: 21,
          label: 'Strain',
          color: strainColor,
          size: 130,
        ),
      ));
      widgets.add(const SizedBox(height: 4));
      if (subtitle.isNotEmpty) {
        widgets.add(Center(
          child: Text(subtitle, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
        ));
      }
      widgets.add(const SizedBox(height: 16));
    }

    // === Horizontal stats row ===
    if (horizontalStats.length > 1) {
      widgets.add(GlassCard(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: horizontalStats.map<Widget>((stat) {
            final s = stat as Map<String, dynamic>;
            final value = s['stat_main_value_display'] as String? ?? '';
            final title = s['stat_title_display'] as String? ?? '';
            final comparison = s['stat_comparison_display'] as String? ?? '';
            final trend = s['stat_trend_type'] as String? ?? '';
            final trendColor = trend == 'POSITIVE' ? WhoopTheme.primary : trend == 'NEGATIVE' ? WhoopTheme.error : WhoopTheme.textSecondary;

            return Expanded(
              child: Column(
                children: [
                  Text(title, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10, fontWeight: FontWeight.w600, letterSpacing: 0.3)),
                  const SizedBox(height: 4),
                  Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 22, fontWeight: FontWeight.w700)),
                  if (comparison.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (trend == 'POSITIVE')
                          Icon(Icons.arrow_upward, color: trendColor, size: 12)
                        else if (trend == 'NEGATIVE')
                          Icon(Icons.arrow_downward, color: trendColor, size: 12),
                        Text(comparison, style: TextStyle(color: trendColor, fontSize: 11)),
                      ],
                    ),
                  ],
                ],
              ),
            );
          }).toList(),
        ),
      ));
      widgets.add(const SizedBox(height: 16));
    }

    // === Key metrics carousel ===
    if (keyMetrics.isNotEmpty) {
      widgets.add(GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              carousel['title'] as String? ?? 'KEY STATISTICS',
              style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600, letterSpacing: 0.5),
            ),
            if (carousel['subtitle'] != null)
              Text(carousel['subtitle'] as String, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10)),
            const SizedBox(height: 12),
            ...keyMetrics.map<Widget>((m) {
              final tile = m as Map<String, dynamic>;
              final title = tile['key_metric_tile_title_display'] as String? ?? '';
              final value = tile['key_metric_tile_stat_value_display'] as String? ?? '';
              final suffix = tile['key_metric_tile_suffix_display'] as String? ?? '';
              final trendVal = tile['key_metric_tile_trend_display'] as String? ?? '';
              final trendType = tile['key_metric_tile_trend_type'] as String? ?? '';
              final trendColor = trendType == 'POSITIVE' ? WhoopTheme.primary : trendType == 'NEGATIVE' ? WhoopTheme.error : WhoopTheme.textSecondary;

              return Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(title, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
                    ),
                    Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w700)),
                    if (suffix.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(left: 2),
                        child: Text(suffix, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                      ),
                    if (trendVal.isNotEmpty) ...[
                      const SizedBox(width: 8),
                      if (trendType == 'POSITIVE')
                        Icon(Icons.arrow_upward, color: trendColor, size: 12)
                      else if (trendType == 'NEGATIVE')
                        Icon(Icons.arrow_downward, color: trendColor, size: 12),
                      Text(trendVal, style: TextStyle(color: trendColor, fontSize: 11)),
                    ],
                  ],
                ),
              );
            }),
          ],
        ),
      ));
      widgets.add(const SizedBox(height: 16));
    }

    // === HR Graph with zone coloring ===
    if (graphResponse != null) {
      widgets.addAll(_buildHrGraph(graphResponse));
      widgets.add(const SizedBox(height: 16));
    }

    // === HR Zone breakdown ===
    if (barGraphContainer != null) {
      widgets.add(_buildZoneBreakdown(barGraphContainer));
      widgets.add(const SizedBox(height: 16));
    }

    // === Coach insight ===
    if (vow != null && vow.isNotEmpty) {
      widgets.add(GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.auto_awesome, color: WhoopTheme.primary, size: 16),
                SizedBox(width: 6),
                Text('ACTIVITY INSIGHT', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
              ],
            ),
            const SizedBox(height: 8),
            Text(vow, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 13, height: 1.4)),
          ],
        ),
      ));
    }

    return widgets;
  }

  // === HR graph (same pattern as sleep detail) ===

  List<Widget> _buildHrGraph(Map<String, dynamic> graphResponse) {
    final plots = graphResponse['plots'] as List? ?? [];
    if (plots.isEmpty) return [];

    final hrPlot = plots[0];
    final hrSegments = (hrPlot['plot'] as Map)['segments'] as List? ?? [];
    final hrPoints = hrSegments.isNotEmpty ? hrSegments[0]['points'] as List? ?? [] : [];

    final plane = graphResponse['plane'] as Map<String, dynamic>? ?? {};
    final startYAxis = plane['start_yaxis'] as Map<String, dynamic>? ?? {};
    final endYAxis = plane['end_yaxis'] as Map<String, dynamic>? ?? {};
    final yLabels = startYAxis['labels'] as List? ?? [];
    final boundaryOffset = (startYAxis['offset'] as num?)?.toDouble() ?? 0;
    final leftBoundaryX = boundaryOffset;
    final rightBoundaryX = 1.0 - ((endYAxis['offset'] as num?)?.toDouble() ?? boundaryOffset);
    final leftTimeLabel = startYAxis['axis_description'] as String? ?? '';
    final rightTimeLabel = endYAxis['axis_description'] as String? ?? '';

    // Parse zone plots (plots 1+)
    final zonePlots = <_ZonePlot>[];
    for (var i = 1; i < plots.length; i++) {
      final plotMap = plots[i]['plot'] as Map<String, dynamic>? ?? {};
      final style = plotMap['style'] as String? ?? '';
      final segs = plotMap['segments'] as List? ?? [];
      if (segs.isEmpty) continue;

      Color color;
      if (style.contains('HARD') || style.contains('ZONE_5')) {
        color = WhoopTheme.error;
      } else if (style.contains('MODERATE') || style.contains('ZONE_4')) {
        color = WhoopTheme.warning;
      } else if (style.contains('LIGHT') && !style.contains('VERY')) {
        color = WhoopTheme.strainAmber;
      } else if (style.contains('VERY_LIGHT') || style.contains('ZONE_2')) {
        color = WhoopTheme.primary;
      } else if (style.contains('RESTORATIVE') || style.contains('ZONE_1') || style.contains('ZONE_0')) {
        color = WhoopTheme.sleepBlue;
      } else {
        color = WhoopTheme.textSecondary;
      }

      final ranges = <_XRange>[];
      for (final seg in segs) {
        final pts = (seg as Map)['points'] as List? ?? [];
        if (pts.isEmpty) continue;
        double minX = double.infinity, maxX = double.negativeInfinity;
        for (final p in pts) {
          final x = ((p as Map)['position_x'] as num?)?.toDouble() ?? 0;
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
        }
        ranges.add(_XRange(minX, maxX));
      }
      zonePlots.add(_ZonePlot(style, ranges, color));
    }

    // Scrub info
    String? scrubTime;
    String? scrubHr;
    String? scrubUnit;
    if (_scrubX != null && hrPoints.isNotEmpty) {
      Map<String, dynamic>? closest;
      double closestDist = double.infinity;
      for (final p in hrPoints) {
        final px = ((p as Map)['position_x'] as num?)?.toDouble() ?? 0;
        final dist = (px - _scrubX!).abs();
        if (dist < closestDist) {
          closestDist = dist;
          closest = p as Map<String, dynamic>;
        }
      }
      if (closest != null) {
        final details = closest['data_scrubber_details'] as Map<String, dynamic>?;
        if (details != null) {
          scrubTime = details['secondary_contextual_display'] as String?;
          scrubHr = details['value_display'] as String?;
          scrubUnit = details['unit_display'] as String?;
        }
      }
    }

    final yLabelData = yLabels.map((l) {
      final label = (l as Map)['label'] as String? ?? '';
      final pos = (l['position'] as num?)?.toDouble() ?? 0;
      return _YLabel(label, pos);
    }).toList();

    return [
      GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
              child: Row(
                children: [
                  Text(
                    graphResponse['graph_title_display'] as String? ?? 'HEART RATE',
                    style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600),
                  ),
                  const Spacer(),
                  if (scrubHr != null) ...[
                    Text('$scrubHr ', style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w700)),
                    Text(scrubUnit ?? 'bpm', style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
                  ],
                ],
              ),
            ),
            LayoutBuilder(builder: (context, constraints) {
              const graphHeight = 150.0;
              const yAxisWidth = 30.0;
              final graphWidth = constraints.maxWidth - yAxisWidth - 8;
              return GestureDetector(
                onHorizontalDragStart: (d) {
                  final x = (d.localPosition.dx - yAxisWidth) / graphWidth;
                  setState(() => _scrubX = x.clamp(0, 1));
                },
                onHorizontalDragUpdate: (d) {
                  final x = (d.localPosition.dx - yAxisWidth) / graphWidth;
                  setState(() => _scrubX = x.clamp(0, 1));
                },
                onHorizontalDragEnd: (_) => setState(() => _scrubX = null),
                onTapDown: (d) {
                  final x = (d.localPosition.dx - yAxisWidth) / graphWidth;
                  setState(() => _scrubX = x.clamp(0, 1));
                },
                onTapUp: (_) => setState(() => _scrubX = null),
                child: SizedBox(
                  height: graphHeight,
                  child: Row(
                    children: [
                      SizedBox(
                        width: yAxisWidth,
                        child: Stack(
                          children: yLabelData.map((yl) {
                            final y = (1 - yl.position) * graphHeight;
                            return Positioned(
                              left: 0,
                              top: y - 6,
                              child: Text(yl.label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 9)),
                            );
                          }).toList(),
                        ),
                      ),
                      Expanded(
                        child: CustomPaint(
                          size: Size(graphWidth, graphHeight),
                          painter: _ActivityHrPainter(
                            hrPoints: hrPoints,
                            zonePlots: zonePlots,
                            scrubX: _scrubX,
                            leftBoundaryX: leftBoundaryX,
                            rightBoundaryX: rightBoundaryX,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              );
            }),
            Padding(
              padding: const EdgeInsets.fromLTRB(30, 4, 8, 8),
              child: scrubTime != null
                  ? Center(child: Text(scrubTime, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 12, fontWeight: FontWeight.w600)))
                  : Row(
                      children: [
                        SizedBox(width: 60, child: Text(leftTimeLabel, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10))),
                        const Spacer(),
                        SizedBox(width: 60, child: Text(rightTimeLabel, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10), textAlign: TextAlign.right)),
                      ],
                    ),
            ),
          ],
        ),
      ),
    ];
  }

  // === HR Zone breakdown (bar_graph_container) ===

  Widget _buildZoneBreakdown(Map<String, dynamic> container) {
    final zones = container['heart_rate_zones'] as List? ?? [];
    final duration = container['duration_display'] as String? ?? '';

    // Calculate max % for bar scaling
    double maxPct = 1;
    for (final z in zones) {
      final pctStr = (z as Map)['bar_graph_tile_percentage_display'] as String? ?? '0%';
      final pct = double.tryParse(pctStr.replaceAll('%', '').replaceAll('<', '')) ?? 0;
      if (pct > maxPct) maxPct = pct;
    }

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (duration.isNotEmpty)
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(container['duration_title_display'] as String? ?? 'DURATION', style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600)),
                Text(duration, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
              ],
            ),
          const SizedBox(height: 12),
          // Stacked zone bar
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: SizedBox(
              height: 24,
              child: Row(
                children: zones.reversed.map<Widget>((z) {
                  final pctStr = (z as Map)['bar_graph_tile_percentage_display'] as String? ?? '0%';
                  final pctNum = double.tryParse(pctStr.replaceAll('%', '').replaceAll('<', '')) ?? 0;
                  final title = z['bar_graph_tile_title_display'] as String? ?? '';
                  return Expanded(
                    flex: max(1, (pctNum * 10).round()),
                    child: Container(color: _zoneColor(title)),
                  );
                }).toList(),
              ),
            ),
          ),
          const SizedBox(height: 12),
          // Zone rows
          ...zones.map<Widget>((z) {
            final title = (z as Map)['bar_graph_tile_title_display'] as String? ?? '';
            final pct = z['bar_graph_tile_percentage_display'] as String? ?? '';
            final time = z['bar_graph_tile_time_display'] as String? ?? '';
            return Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Container(width: 12, height: 12, decoration: BoxDecoration(color: _zoneColor(title), shape: BoxShape.circle)),
                  const SizedBox(width: 8),
                  SizedBox(width: 60, child: Text(title, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 13, fontWeight: FontWeight.w500))),
                  const SizedBox(width: 4),
                  Text(pct, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                  const Spacer(),
                  Text(time, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 13, fontWeight: FontWeight.w600)),
                ],
              ),
            );
          }),
        ],
      ),
    );
  }

  Color _zoneColor(String title) {
    final t = title.toUpperCase();
    if (t.contains('5')) return WhoopTheme.error;
    if (t.contains('4')) return const Color(0xFFE67E22);
    if (t.contains('3')) return WhoopTheme.warning;
    if (t.contains('2')) return WhoopTheme.primary;
    if (t.contains('1')) return WhoopTheme.sleepBlue;
    if (t.contains('0')) return const Color(0xFF6C7B8A);
    return WhoopTheme.textSecondary;
  }

  // === Fallback: basic workout data when cardio-details unavailable ===

  List<Widget> _buildWorkoutFallback() {
    final w = _workout!;
    final score = w['score'] as Map<String, dynamic>? ?? {};
    final strain = (score['strain'] as num?)?.toDouble();
    final avgHr = (score['average_heart_rate'] as num?)?.toInt();
    final maxHr = (score['max_heart_rate'] as num?)?.toInt();
    final kilojoule = (score['kilojoule'] as num?)?.toDouble();
    final distance = (score['distance_meter'] as num?)?.toDouble();
    final sportId = w['sport_id'] as int? ?? 0;

    final startStr = w['start'] as String?;
    final endStr = w['end'] as String?;
    String timeRange = '';
    String duration = '';
    if (startStr != null && endStr != null) {
      final start = DateTime.tryParse(startStr)?.toLocal();
      final end = DateTime.tryParse(endStr)?.toLocal();
      if (start != null && end != null) {
        timeRange = '${DateFormat('HH:mm').format(start)} - ${DateFormat('HH:mm').format(end)}';
        final dur = end.difference(start);
        final h = dur.inHours;
        final m = dur.inMinutes % 60;
        duration = h > 0 ? '${h}h ${m}m' : '${m}m';
      }
    }

    final strainColor = strain != null
        ? (strain < 7 ? WhoopTheme.sleepBlue : strain < 14 ? WhoopTheme.warning : WhoopTheme.error)
        : WhoopTheme.textSecondary;

    return [
      Center(
        child: ScoreGauge(
          score: strain ?? 0,
          maxScore: 21,
          label: 'Strain',
          color: strainColor,
          size: 130,
        ),
      ),
      const SizedBox(height: 4),
      Center(child: Text(_sportName(sportId), style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13))),
      if (timeRange.isNotEmpty)
        Center(child: Text(timeRange, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13))),
      const SizedBox(height: 16),

      // Metrics
      GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            if (avgHr != null) _metricRow('Avg HR', '$avgHr BPM'),
            if (maxHr != null) _metricRow('Max HR', '$maxHr BPM'),
            if (kilojoule != null) _metricRow('Calories', '${(kilojoule / 4.184).round()} kcal'),
            if (duration.isNotEmpty) _metricRow('Duration', duration),
            if (distance != null && distance > 0) _metricRow('Distance', '${(distance / 1000).toStringAsFixed(2)} km'),
          ],
        ),
      ),
      const SizedBox(height: 16),

      // HR zones from developer API
      ..._buildDevApiZones(score),
    ];
  }

  Widget _metricRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 14)),
          Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  List<Widget> _buildDevApiZones(Map<String, dynamic> score) {
    final zoneD = score['zone_duration'] as Map<String, dynamic>?;
    if (zoneD == null || zoneD.isEmpty) return [];

    final zones = <MapEntry<String, double>>[];
    for (final e in zoneD.entries) {
      final ms = (e.value as num?)?.toDouble() ?? 0;
      if (ms > 0) zones.add(MapEntry(e.key, ms / 60000));
    }
    if (zones.isEmpty) return [];

    return [
      GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('HR ZONES', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
            const SizedBox(height: 12),
            ...zones.map((e) => Padding(
              padding: const EdgeInsets.symmetric(vertical: 3),
              child: Row(
                children: [
                  SizedBox(width: 80, child: Text(e.key, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12))),
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(3),
                      child: LinearProgressIndicator(
                        value: (e.value / 60).clamp(0, 1),
                        backgroundColor: WhoopTheme.divider,
                        color: WhoopTheme.primary,
                        minHeight: 8,
                      ),
                    ),
                  ),
                  SizedBox(width: 50, child: Text('${e.value.round()}m', style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 12), textAlign: TextAlign.right)),
                ],
              ),
            )),
          ],
        ),
      ),
    ];
  }

  static String _sportName(int id) {
    const sports = {
      -1: 'Activity', 0: 'Running', 1: 'Cycling', 16: 'Baseball',
      17: 'Basketball', 18: 'Rowing', 19: 'Fencing', 20: 'Field Hockey',
      21: 'Football', 22: 'Golf', 24: 'Ice Hockey', 25: 'Lacrosse',
      27: 'Rugby', 28: 'Sailing', 29: 'Skiing', 30: 'Soccer',
      31: 'Softball', 32: 'Squash', 33: 'Swimming', 34: 'Tennis',
      35: 'Track & Field', 36: 'Volleyball', 37: 'Water Polo',
      38: 'Wrestling', 39: 'Boxing', 42: 'Dance', 43: 'Pilates',
      44: 'Yoga', 45: 'Weightlifting', 47: 'Cross Country Skiing',
      48: 'Functional Fitness', 49: 'Duathlon', 51: 'Gymnastics',
      52: 'Hiking', 53: 'Horseback Riding', 55: 'Kayaking',
      56: 'Martial Arts', 57: 'Mountain Biking', 59: 'Powerlifting',
      60: 'Rock Climbing', 61: 'Paddleboarding', 62: 'Triathlon',
      63: 'Walking', 64: 'Surfing', 65: 'Elliptical', 66: 'Stairmaster',
      70: 'Meditation', 71: 'Other', 73: 'Diving', 82: 'Ultimate',
      83: 'Climber', 84: 'Jumping Rope', 85: 'Australian Football',
      86: 'Skateboarding', 87: 'Coaching', 88: 'Ice Bath',
      89: 'Commuting', 90: 'Gaming', 91: 'Snowboarding',
      92: 'Motocross', 93: 'Caddying', 94: 'Obstacle Course Racing',
      95: 'Motor Racing', 96: 'HIIT', 97: 'Spin', 98: 'Jiu Jitsu',
      99: 'Manual Labor', 100: 'Cricket', 101: 'Pickleball',
      102: 'Inline Skating', 103: 'Box Fitness', 104: 'Spikeball',
      105: 'Wheelchair Pushing', 106: 'Paddle Tennis', 107: 'Barre',
      108: 'Stage Performance', 109: 'High Stress Work', 110: 'Parkour',
      111: 'Gaelic Football', 112: 'Hurling', 113: 'Circus Arts',
      121: 'Massage Therapy', 123: 'Strength Trainer',
      125: 'Watching Sports', 126: 'Assault Bike', 127: 'Kickboxing',
      128: 'Stretching', 230: 'Table Tennis', 231: 'Badminton',
      232: 'Netball', 233: 'Sauna', 234: 'Disc Golf',
      235: 'Yard Work', 236: 'Air Compression',
      237: 'Percussive Massage', 238: 'Paintball', 239: 'Ice Skating',
      240: 'Handball', 248: 'F45 Training', 249: 'Padel',
    };
    return sports[id] ?? 'Activity ($id)';
  }
}

// === Painter classes ===

class _YLabel {
  final String label;
  final double position;
  _YLabel(this.label, this.position);
}

class _XRange {
  final double start, end;
  _XRange(this.start, this.end);
  bool contains(double x) => x >= start && x <= end;
}

class _ZonePlot {
  final String style;
  final List<_XRange> ranges;
  final Color color;
  _ZonePlot(this.style, this.ranges, this.color);
}

class _ActivityHrPainter extends CustomPainter {
  final List hrPoints;
  final List<_ZonePlot> zonePlots;
  final double? scrubX;
  final double leftBoundaryX;
  final double rightBoundaryX;

  _ActivityHrPainter({
    required this.hrPoints,
    required this.zonePlots,
    this.scrubX,
    this.leftBoundaryX = 0,
    this.rightBoundaryX = 1,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (hrPoints.isEmpty) return;

    // Zone background fills
    for (final zone in zonePlots) {
      final fillPaint = Paint()..color = zone.color.withValues(alpha: 0.15);
      for (final range in zone.ranges) {
        canvas.drawRect(
          Rect.fromLTRB(range.start * size.width, 0, range.end * size.width, size.height),
          fillPaint,
        );
      }
    }

    // HR line with zone coloring
    final coords = <_HrCoord>[];
    for (var i = 0; i < hrPoints.length; i++) {
      final p = hrPoints[i] as Map<String, dynamic>;
      final nx = (p['position_x'] as num?)?.toDouble() ?? (i / max(1, hrPoints.length - 1));
      final ny = (p['position_y'] as num?)?.toDouble() ?? 0;
      coords.add(_HrCoord(nx, nx * size.width, (1 - ny) * size.height));
    }

    // Draw colored segments based on zones
    for (var i = 0; i < coords.length - 1; i++) {
      Color segColor = WhoopTheme.sleepBlue;
      final midX = (coords[i].nx + coords[i + 1].nx) / 2;
      for (final zone in zonePlots) {
        if (zone.ranges.any((r) => r.contains(midX))) {
          segColor = zone.color;
          break;
        }
      }
      canvas.drawLine(
        Offset(coords[i].px, coords[i].py),
        Offset(coords[i + 1].px, coords[i + 1].py),
        Paint()..color = segColor..strokeWidth = 1.5..strokeCap = StrokeCap.round,
      );
    }

    // Scrub line
    if (scrubX != null) {
      final sx = scrubX! * size.width;
      canvas.drawLine(Offset(sx, 0), Offset(sx, size.height),
          Paint()..color = WhoopTheme.textSecondary.withValues(alpha: 0.5)..strokeWidth = 1);

      double? dotY;
      double closestDist = double.infinity;
      for (final c in coords) {
        final dist = (c.nx - scrubX!).abs();
        if (dist < closestDist) {
          closestDist = dist;
          dotY = c.py;
        }
      }
      if (dotY != null) {
        canvas.drawCircle(Offset(sx, dotY), 4, Paint()..color = WhoopTheme.textPrimary);
        canvas.drawCircle(Offset(sx, dotY), 2.5, Paint()..color = WhoopTheme.primary);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _ActivityHrPainter old) =>
      old.hrPoints != hrPoints || old.scrubX != scrubX;
}

class _HrCoord {
  final double nx, px, py;
  _HrCoord(this.nx, this.px, this.py);
}
