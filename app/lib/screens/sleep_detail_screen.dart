import 'dart:math';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/sleep.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';
import '../widgets/score_gauge.dart';
import '../widgets/trend_bar_chart.dart';

class SleepDetailScreen extends StatefulWidget {
  final String date;
  final String? activityId;
  const SleepDetailScreen({super.key, required this.date, this.activityId});

  @override
  State<SleepDetailScreen> createState() => _SleepDetailScreenState();
}

class _SleepDetailScreenState extends State<SleepDetailScreen> {
  Sleep? _sleep;
  Map<String, dynamic>? _lastNight;
  Map<String, dynamic>? _trends;
  bool _loading = true;
  String? _selectedStage;
  double? _scrubX;

  // Pre-parsed graph data (avoids re-parsing JSON every frame)
  _ParsedGraphData? _graphData;

  bool get _isNap => widget.activityId != null;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() => _loading = true);
    final api = context.read<ApiService>();

    try {
      final data = await api.getDeepDive('sleep', widget.date);
      _sleep = Sleep.fromDeepDive(data);
    } catch (e) {
      debugPrint('Sleep deep-dive failed: $e');
    }

    try {
      _lastNight = await api.getSleepLastNight(widget.date);
    } catch (e) {
      debugPrint('Sleep last-night failed: $e');
    }

    try {
      _trends = await api.getDeepDiveTrends('sleep', widget.date);
    } catch (e) {
      debugPrint('Sleep trends failed: $e');
    }

    // Pre-parse graph data once
    _graphData = _parseGraphData();

    if (mounted) setState(() => _loading = false);
  }

  // === Pre-parse all graph data from JSON into typed structures ===
  _ParsedGraphData? _parseGraphData() {
    if (_lastNight == null) return null;
    final sections = _lastNight!['sections'] as List? ?? [];
    if (sections.isEmpty) return null;

    final card0 = sections[0];
    final item0 = (card0 as Map)['items']?[0] as Map<String, dynamic>?;
    if (item0 == null) return null;
    final content = item0['content'] as Map<String, dynamic>? ?? {};
    final cardContent = content['card_content'] as List? ?? [];

    Map<String, dynamic>? graphContent;
    for (final sub in cardContent) {
      if ((sub as Map)['type'] == 'GRAPH') {
        graphContent = sub['content'] as Map<String, dynamic>?;
        break;
      }
    }
    if (graphContent == null) return null;

    final plots = graphContent['plots'] as List? ?? [];
    if (plots.isEmpty) return null;

    final hrPlot = plots[0];
    final hrSegments = (hrPlot['plot'] as Map)['segments'] as List? ?? [];
    final rawPoints = hrSegments.isNotEmpty ? hrSegments[0]['points'] as List? ?? [] : [];

    // Downsample if too many points (keep every Nth for display, but keep all scrub data)
    final step = rawPoints.length > 600 ? 3 : rawPoints.length > 300 ? 2 : 1;

    final hrPoints = <_HrPoint>[];
    for (var i = 0; i < rawPoints.length; i += step) {
      final p = rawPoints[i] as Map<String, dynamic>;
      final x = (p['position_x'] as num?)?.toDouble() ?? (i / max(1, rawPoints.length - 1));
      final y = (p['position_y'] as num?)?.toDouble() ?? 0;
      String? scrubTime, scrubHr, scrubUnit;
      final details = p['data_scrubber_details'] as Map<String, dynamic>?;
      if (details != null) {
        scrubTime = details['secondary_contextual_display'] as String?;
        scrubHr = details['value_display'] as String?;
        scrubUnit = details['unit_display'] as String?;
      }
      hrPoints.add(_HrPoint(x, y, scrubTime, scrubHr, scrubUnit));
    }

    final plane = graphContent['plane'] as Map<String, dynamic>? ?? {};
    final startYAxis = plane['start_yaxis'] as Map<String, dynamic>? ?? {};
    final endYAxis = plane['end_yaxis'] as Map<String, dynamic>? ?? {};
    final boundaryOffset = (startYAxis['offset'] as num?)?.toDouble() ?? 0;

    final yLabels = <_YLabel>[];
    for (final l in startYAxis['labels'] as List? ?? []) {
      yLabels.add(_YLabel((l as Map)['label'] as String? ?? '', (l['position'] as num?)?.toDouble() ?? 0));
    }

    final stagePlots = <_StagePlot>[];
    for (var i = 1; i < plots.length; i++) {
      final plotMap = plots[i]['plot'] as Map<String, dynamic>? ?? {};
      final style = plotMap['style'] as String? ?? '';
      final segs = plotMap['segments'] as List? ?? [];
      if (segs.isEmpty) continue;

      String id;
      Color color;
      if (style.contains('REM')) { id = 'REM_SLEEP'; color = WhoopTheme.primary; }
      else if (style.contains('LIGHT')) { id = 'LIGHT_SLEEP'; color = WhoopTheme.sleepBlue; }
      else if (style.contains('SWS')) { id = 'SWS_SLEEP'; color = const Color(0xFF9B59B6); }
      else if (style.contains('AWAKE')) { id = 'AWAKE'; color = WhoopTheme.error; }
      else { continue; }

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
      stagePlots.add(_StagePlot(id, ranges, color));
    }

    return _ParsedGraphData(
      hrPoints: hrPoints,
      yLabels: yLabels,
      stagePlots: stagePlots,
      leftBoundaryX: boundaryOffset,
      rightBoundaryX: 1.0 - ((endYAxis['offset'] as num?)?.toDouble() ?? boundaryOffset),
      leftTimeLabel: startYAxis['axis_description'] as String? ?? '',
      rightTimeLabel: endYAxis['axis_description'] as String? ?? '',
    );
  }

  String _buildTitle() {
    if (_isNap) return 'Nap';
    final apiTitle = _lastNight?['header_section']?['title'] as String?;
    if (apiTitle != null && apiTitle.isNotEmpty) {
      final now = DateTime.now();
      final today = DateTime(now.year, now.month, now.day);
      final parsed = DateTime.tryParse(widget.date);
      if (parsed != null) {
        final diff = today.difference(DateTime(parsed.year, parsed.month, parsed.day)).inDays;
        if (diff <= 1) return apiTitle;
        final months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return 'Sleep ${months[parsed.month - 1]} ${parsed.day}';
      }
      return apiTitle;
    }
    return 'Sleep';
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: Text(_buildTitle(), style: const TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
          : RefreshIndicator(
              color: WhoopTheme.primary,
              onRefresh: _fetch,
              child: _buildContent(),
            ),
    );
  }

  Widget _buildContent() {
    final slp = _sleep ?? const Sleep();

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (_lastNight?['sub_header_section'] != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(
              '${_lastNight!['sub_header_section']['sub_header'] ?? ''}${_lastNight!['sub_header_section']['sub_header_end'] ?? ''}',
              style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13),
              textAlign: TextAlign.center,
            ),
          ),

        Center(
          child: ScoreGauge(
            score: slp.score,
            maxScore: 100,
            label: 'Sleep',
            color: WhoopTheme.sleepBlue,
            size: 130,
          ),
        ),
        const SizedBox(height: 8),

        _buildContributorsSummary(slp),
        const SizedBox(height: 20),

        if (_lastNight != null)
          ..._buildHrAndStages(),

        if (_lastNight != null)
          ..._buildDetailCards(),

        ..._buildTrends(),

        const SizedBox(height: 24),
      ],
    );
  }

  Widget _buildContributorsSummary(Sleep slp) {
    final items = <_ContItem>[];
    if (slp.hoursVsNeeded != null) items.add(_ContItem('Hours vs. Needed', slp.hoursVsNeeded!, _pct(slp.hoursVsNeeded)));
    if (slp.consistency != null) items.add(_ContItem('Sleep Consistency', slp.consistency!, _pct(slp.consistency)));
    if (slp.efficiency != null) items.add(_ContItem('Sleep Efficiency', slp.efficiency!, _pct(slp.efficiency)));
    if (slp.sleepStress != null) items.add(_ContItem('High Sleep Stress', slp.sleepStress!, 1.0 - _pct(slp.sleepStress)));

    if (items.isEmpty) return const SizedBox.shrink();

    return GlassCard(
      padding: const EdgeInsets.all(16),
      radius: 14,
      child: Column(
        children: items.map((item) {
          final color = item.score >= 0.8 ? WhoopTheme.primary : item.score >= 0.5 ? WhoopTheme.warning : WhoopTheme.error;
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(
              children: [
                Container(width: 4, height: 20, decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(2))),
                const SizedBox(width: 12),
                Expanded(child: Text(item.label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 14))),
                Text(item.value, style: TextStyle(color: color, fontSize: 14, fontWeight: FontWeight.w600)),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }

  double _pct(String? s) {
    if (s == null) return 0;
    return (double.tryParse(s.replaceAll(RegExp(r'[^0-9.]'), '')) ?? 0) / 100;
  }

  List<Widget> _buildHrAndStages() {
    final sections = _lastNight!['sections'] as List? ?? [];
    if (sections.isEmpty) return [];

    final card0 = sections[0];
    final item0 = (card0 as Map)['items']?[0] as Map<String, dynamic>?;
    if (item0 == null) return [];
    final content = item0['content'] as Map<String, dynamic>? ?? {};
    final arrowStats = content['arrow_stat'] as List? ?? [];
    final cardContent = content['card_content'] as List? ?? [];

    Map<String, dynamic>? stagesContent;
    Map<String, dynamic>? restorativeContent;

    for (final sub in cardContent) {
      final type = (sub as Map)['type'] as String? ?? '';
      if (type == 'BAR_GRAPH_CARD') stagesContent = sub['content'] as Map<String, dynamic>?;
      if (type == 'DETAILS_METRIC_TILES') restorativeContent = sub['content'] as Map<String, dynamic>?;
    }

    final widgets = <Widget>[];
    widgets.add(_buildCardHeader(content['card_title'] as String? ?? 'HOURS OF SLEEP', arrowStats));

    if (_graphData != null) widgets.add(_buildSleepHrGraph());
    if (stagesContent != null) widgets.add(_buildTappableSleepStages(stagesContent));
    if (restorativeContent != null) widgets.add(_buildRestorativeTile(restorativeContent));

    return widgets;
  }

  Widget _buildCardHeader(String title, List arrowStats) {
    String? current, historic, trend;
    if (arrowStats.isNotEmpty) {
      final s = arrowStats[0] as Map<String, dynamic>;
      current = s['current_stat_text'] as String?;
      historic = s['historic_stat_text'] as String?;
      trend = s['trend_state'] as String?;
    }

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          if (current != null) ...[
            const SizedBox(height: 4),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(current, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 28, fontWeight: FontWeight.w700)),
                if (trend != null) ...[
                  const SizedBox(width: 6),
                  Icon(
                    trend.contains('HIGHER') ? Icons.arrow_upward : Icons.arrow_downward,
                    color: trend.contains('POSITIVE') ? WhoopTheme.primary : WhoopTheme.error,
                    size: 16,
                  ),
                ],
                if (historic != null) ...[
                  const SizedBox(width: 4),
                  Padding(padding: const EdgeInsets.only(bottom: 4),
                    child: Text(historic, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13))),
                ],
              ],
            ),
          ],
        ],
      ),
    );
  }

  // Uses pre-parsed _graphData — no JSON parsing during build/scrub
  Widget _buildSleepHrGraph() {
    final gd = _graphData!;

    // Scrub lookup via binary search on pre-sorted x values
    String? scrubTime;
    String? scrubHr;
    String? scrubUnit;
    if (_scrubX != null && gd.hrPoints.isNotEmpty) {
      final closest = _findClosestPoint(gd.hrPoints, _scrubX!);
      scrubTime = closest.scrubTime;
      scrubHr = closest.scrubHr;
      scrubUnit = closest.scrubUnit;
    }

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Row(
              children: [
                const Text('HEART RATE', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600)),
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
                        children: gd.yLabels.map((yl) {
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
                      child: RepaintBoundary(
                        child: CustomPaint(
                          size: Size(graphWidth, graphHeight),
                          painter: _SleepHrPainter(
                            hrPoints: gd.hrPoints,
                            stagePlots: gd.stagePlots,
                            selectedStage: _selectedStage,
                            scrubX: _scrubX,
                            leftBoundaryX: gd.leftBoundaryX,
                            rightBoundaryX: gd.rightBoundaryX,
                          ),
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
                      SizedBox(width: 50, child: Text(gd.leftTimeLabel, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10), textAlign: TextAlign.left)),
                      const Spacer(),
                      SizedBox(width: 50, child: Text(gd.rightTimeLabel, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10), textAlign: TextAlign.right)),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  // Binary search for closest point
  _HrPoint _findClosestPoint(List<_HrPoint> pts, double x) {
    var lo = 0, hi = pts.length - 1;
    while (lo < hi - 1) {
      final mid = (lo + hi) ~/ 2;
      if (pts[mid].x <= x) lo = mid; else hi = mid;
    }
    return (x - pts[lo].x).abs() <= (x - pts[hi].x).abs() ? pts[lo] : pts[hi];
  }

  Widget _buildTappableSleepStages(Map<String, dynamic> content) {
    final zones = content['heart_rate_zones'] as List? ?? [];
    final duration = content['duration_display'] as String? ?? '';

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (duration.isNotEmpty)
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text('DURATION', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600)),
                Text(duration, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
              ],
            ),
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: SizedBox(
              height: 24,
              child: Row(
                children: zones.map<Widget>((z) {
                  final pct = z['bar_graph_tile_percentage_display'] as String? ?? '0%';
                  final pctNum = double.tryParse(pct.replaceAll('%', '')) ?? 0;
                  final id = z['id'] as String? ?? '';
                  final isSelected = _selectedStage == null || _selectedStage == id;
                  return Expanded(
                    flex: (pctNum * 10).round().clamp(1, 1000),
                    child: GestureDetector(
                      onTap: () => setState(() => _selectedStage = _selectedStage == id ? null : id),
                      child: Opacity(
                        opacity: isSelected ? 1.0 : 0.3,
                        child: Container(color: _stageColor(id)),
                      ),
                    ),
                  );
                }).toList(),
              ),
            ),
          ),
          const SizedBox(height: 12),
          ...zones.map<Widget>((z) {
            final title = z['bar_graph_tile_title_display'] as String? ?? '';
            final pct = z['bar_graph_tile_percentage_display'] as String? ?? '';
            final time = z['bar_graph_tile_time_display'] as String? ?? '';
            final id = z['id'] as String? ?? '';
            final isSelected = _selectedStage == null || _selectedStage == id;

            return GestureDetector(
              onTap: () => setState(() => _selectedStage = _selectedStage == id ? null : id),
              child: Opacity(
                opacity: isSelected ? 1.0 : 0.4,
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Row(
                    children: [
                      Container(
                        width: 12, height: 12,
                        decoration: BoxDecoration(
                          color: _selectedStage == id ? _stageColor(id) : Colors.transparent,
                          border: Border.all(color: _stageColor(id), width: 2),
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Text(title, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w500)),
                      const SizedBox(width: 6),
                      Text(pct, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
                      const Spacer(),
                      Text(time, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
                    ],
                  ),
                ),
              ),
            );
          }),
        ],
      ),
    );
  }

  Widget _buildRestorativeTile(Map<String, dynamic> content) {
    final title = content['title'] as String? ?? '';
    final arrowStats = content['arrow_stat'] as List? ?? [];

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Row(
        children: [
          const Icon(Icons.square_rounded, color: WhoopTheme.primary, size: 14),
          const SizedBox(width: 6),
          Text(title, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600)),
          const Spacer(),
          if (arrowStats.isNotEmpty) ...[
            Text(arrowStats[0]['current_stat_text'] as String? ?? '', style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w700)),
            const SizedBox(width: 8),
            Text(arrowStats[0]['historic_stat_text'] as String? ?? '', style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
          ],
        ],
      ),
    );
  }

  Color _stageColor(String id) {
    if (id == 'AWAKE') return WhoopTheme.error;
    if (id == 'LIGHT_SLEEP') return WhoopTheme.sleepBlue;
    if (id == 'SWS_SLEEP') return const Color(0xFF9B59B6);
    if (id == 'REM_SLEEP') return WhoopTheme.primary;
    return WhoopTheme.textSecondary;
  }

  List<Widget> _buildDetailCards() {
    final sections = _lastNight!['sections'] as List? ?? [];
    final widgets = <Widget>[];

    for (var i = 1; i < sections.length; i++) {
      final item = (sections[i] as Map)['items']?[0] as Map<String, dynamic>?;
      if (item == null) continue;
      final content = item['content'] as Map<String, dynamic>? ?? {};
      final cardTitle = content['card_title'] as String? ?? '';
      final arrowStats = content['arrow_stat'] as List? ?? [];
      final cardContent = content['card_content'] as List? ?? [];

      widgets.add(const SizedBox(height: 20));
      widgets.add(_buildCardHeader(cardTitle, arrowStats));

      for (final sub in cardContent) {
        final type = (sub as Map)['type'] as String? ?? '';
        final subContent = sub['content'] as Map<String, dynamic>? ?? {};

        if (type == 'COMPARISON_BARS') {
          widgets.add(_buildComparisonBars(subContent));
        } else if (type == 'BAR_GRAPH_CARD') {
          widgets.add(_buildBarGraphCard(subContent));
        } else if (type == 'GRAPH') {
          widgets.add(_buildMiniGraph(subContent));
        } else if (type == 'DETAILS_METRIC_TILES') {
          widgets.add(_buildRestorativeTile(subContent));
        }
      }
    }

    return widgets;
  }

  Widget _buildComparisonBars(Map<String, dynamic> content) {
    final bars = content['bars'] as List? ?? [];
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        children: bars.map<Widget>((bar) {
          final title = bar['title_display'] as String? ?? '';
          final value = bar['value_display'] as String? ?? '';
          final segments = bar['segments'] as List? ?? [];
          final totalFill = segments.fold<double>(0, (s, seg) => s + ((seg['fill_percent'] as num?)?.toDouble() ?? 0));
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                  Text(title, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                  Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w700)),
                ]),
                const SizedBox(height: 6),
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: SizedBox(
                    height: 14,
                    child: Stack(children: [
                      Container(color: WhoopTheme.divider),
                      FractionallySizedBox(
                        widthFactor: totalFill.clamp(0, 1),
                        child: Container(color: WhoopTheme.sleepBlue),
                      ),
                    ]),
                  ),
                ),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildBarGraphCard(Map<String, dynamic> content) {
    final zones = content['heart_rate_zones'] as List? ?? [];
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        children: zones.map<Widget>((z) {
          final title = z['bar_graph_tile_title_display'] as String? ?? '';
          final pct = z['bar_graph_tile_percentage_display'] as String? ?? '';
          final time = z['bar_graph_tile_time_display'] as String? ?? '';
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(children: [
              Expanded(child: Text('$title $pct', style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
              Text(time, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
            ]),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildMiniGraph(Map<String, dynamic> content) {
    final plots = content['plots'] as List? ?? [];
    if (plots.isEmpty) return const SizedBox.shrink();

    final segs = (plots[0]['plot'] as Map?)?['segments'] as List? ?? [];
    if (segs.isEmpty) return const SizedBox.shrink();
    final rawPoints = segs[0]['points'] as List? ?? [];

    // Downsample mini graphs too
    final step = rawPoints.length > 200 ? 3 : 1;
    final points = <Map<String, dynamic>>[];
    for (var i = 0; i < rawPoints.length; i += step) {
      points.add(rawPoints[i] as Map<String, dynamic>);
    }

    return GlassCard(
      child: SizedBox(
        height: 100,
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: RepaintBoundary(
            child: CustomPaint(
              size: const Size(double.infinity, 84),
              painter: _SimpleLinePainter(points, WhoopTheme.primary),
            ),
          ),
        ),
      ),
    );
  }

  List<Widget> _buildTrends() {
    if (_trends == null) return [];
    final sections = _trends!['sections'] as List? ?? [];
    final widgets = <Widget>[];

    for (final sec in sections) {
      final items = (sec as Map)['items'] as List? ?? [];
      for (final item in items) {
        final content = (item as Map)['content'] as Map<String, dynamic>? ?? {};
        final type = item['type'] as String? ?? '';
        final title = content['title'] as String? ?? '';

        if (type == 'HEADER') {
          widgets.add(const SizedBox(height: 24));
          widgets.add(Text(title, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600)));
          widgets.add(const SizedBox(height: 12));
        } else if (type == 'GRAPHING_CARD' && content['graph'] != null) {
          final graph = content['graph'] as Map<String, dynamic>;
          final graphPlots = graph['plots'] as List? ?? [];
          if (graphPlots.isEmpty) continue;

          final plot = graphPlots[0]['plot'] as Map<String, dynamic>? ?? {};
          final barGroups = plot['bar_groups'] as List?;
          final segments = plot['segments'] as List?;

          if (barGroups != null && barGroups.isNotEmpty) {
            widgets.add(TrendBarChart(title: title, barGroups: barGroups));
            widgets.add(const SizedBox(height: 12));
          } else if (segments != null && segments.isNotEmpty) {
            final pts = segments[0]['points'] as List? ?? [];
            widgets.add(TrendLineChart(title: title, points: pts, lineColor: WhoopTheme.sleepBlue));
            widgets.add(const SizedBox(height: 12));
          }
        }
      }
    }
    return widgets;
  }
}

// === Pre-parsed data structures ===

class _HrPoint {
  final double x, y;
  final String? scrubTime, scrubHr, scrubUnit;
  const _HrPoint(this.x, this.y, this.scrubTime, this.scrubHr, this.scrubUnit);
}

class _ParsedGraphData {
  final List<_HrPoint> hrPoints;
  final List<_YLabel> yLabels;
  final List<_StagePlot> stagePlots;
  final double leftBoundaryX, rightBoundaryX;
  final String leftTimeLabel, rightTimeLabel;

  const _ParsedGraphData({
    required this.hrPoints,
    required this.yLabels,
    required this.stagePlots,
    required this.leftBoundaryX,
    required this.rightBoundaryX,
    required this.leftTimeLabel,
    required this.rightTimeLabel,
  });
}

class _ContItem {
  final String label, value;
  final double score;
  _ContItem(this.label, this.value, this.score);
}

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

class _StagePlot {
  final String id;
  final List<_XRange> ranges;
  final Color color;
  _StagePlot(this.id, this.ranges, this.color);
  bool containsX(double x) => ranges.any((r) => r.contains(x));
}

// === CustomPainters — work with pre-parsed typed data, no JSON ===

class _SleepHrPainter extends CustomPainter {
  final List<_HrPoint> hrPoints;
  final List<_StagePlot> stagePlots;
  final String? selectedStage;
  final double? scrubX;
  final double leftBoundaryX;
  final double rightBoundaryX;

  _SleepHrPainter({
    required this.hrPoints,
    required this.stagePlots,
    this.selectedStage,
    this.scrubX,
    this.leftBoundaryX = 0,
    this.rightBoundaryX = 1,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (hrPoints.isEmpty) return;

    // Pre-compute pixel coords
    final pxCoords = hrPoints.map((p) => Offset(p.x * size.width, (1 - p.y) * size.height)).toList();

    // Scrim for outside sleep boundaries
    if (leftBoundaryX > 0 || rightBoundaryX < 1) {
      final scrimPaint = Paint()..color = WhoopTheme.background.withValues(alpha: 0.5);
      if (leftBoundaryX > 0) {
        canvas.drawRect(Rect.fromLTRB(0, 0, leftBoundaryX * size.width, size.height), scrimPaint);
      }
      if (rightBoundaryX < 1) {
        canvas.drawRect(Rect.fromLTRB(rightBoundaryX * size.width, 0, size.width, size.height), scrimPaint);
      }

      final dashPaint = Paint()
        ..color = WhoopTheme.textSecondary.withValues(alpha: 0.3)
        ..strokeWidth = 1;
      const dashHeight = 4.0;
      const dashGap = 4.0;
      for (final bx in [leftBoundaryX, rightBoundaryX]) {
        final px = bx * size.width;
        var y = 0.0;
        while (y < size.height) {
          canvas.drawLine(Offset(px, y), Offset(px, (y + dashHeight).clamp(0, size.height)), dashPaint);
          y += dashHeight + dashGap;
        }
      }
    }

    // Stage highlight regions
    if (selectedStage != null) {
      final stage = stagePlots.where((s) => s.id == selectedStage).firstOrNull;
      if (stage != null) {
        final highlightPaint = Paint()..color = stage.color.withValues(alpha: 0.1);
        for (final range in stage.ranges) {
          canvas.drawRect(Rect.fromLTRB(range.start * size.width, 0, range.end * size.width, size.height), highlightPaint);
        }
      }
    }

    // HR line
    final hrPaint = Paint()
      ..color = selectedStage == null ? WhoopTheme.sleepBlue.withValues(alpha: 0.8) : WhoopTheme.sleepBlue.withValues(alpha: 0.2)
      ..strokeWidth = 0.8
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final path = Path();
    for (var i = 0; i < pxCoords.length; i++) {
      if (i == 0) path.moveTo(pxCoords[i].dx, pxCoords[i].dy);
      else path.lineTo(pxCoords[i].dx, pxCoords[i].dy);
    }
    canvas.drawPath(path, hrPaint);

    // Highlighted HR segments for selected stage
    if (selectedStage != null) {
      final stage = stagePlots.where((s) => s.id == selectedStage).firstOrNull;
      if (stage != null) {
        final highlightPaint = Paint()
          ..color = stage.color
          ..strokeWidth = 1.2
          ..style = PaintingStyle.stroke
          ..strokeCap = StrokeCap.round;

        Path? currentPath;
        for (var i = 0; i < hrPoints.length; i++) {
          if (stage.containsX(hrPoints[i].x)) {
            if (currentPath == null) {
              currentPath = Path()..moveTo(pxCoords[i].dx, pxCoords[i].dy);
            } else {
              currentPath.lineTo(pxCoords[i].dx, pxCoords[i].dy);
            }
          } else {
            if (currentPath != null) {
              canvas.drawPath(currentPath, highlightPaint);
              currentPath = null;
            }
          }
        }
        if (currentPath != null) canvas.drawPath(currentPath, highlightPaint);
      }
    }

    // Scrub line
    if (scrubX != null) {
      final sx = scrubX! * size.width;
      canvas.drawLine(Offset(sx, 0), Offset(sx, size.height),
          Paint()..color = WhoopTheme.textSecondary.withValues(alpha: 0.5)..strokeWidth = 1);

      // Binary search for closest point
      var lo = 0, hi = hrPoints.length - 1;
      while (lo < hi - 1) {
        final mid = (lo + hi) ~/ 2;
        if (hrPoints[mid].x <= scrubX!) lo = mid; else hi = mid;
      }
      final closest = (scrubX! - hrPoints[lo].x).abs() <= (scrubX! - hrPoints[hi].x).abs() ? lo : hi;
      final dotY = pxCoords[closest].dy;
      canvas.drawCircle(Offset(sx, dotY), 4, Paint()..color = WhoopTheme.textPrimary);
      canvas.drawCircle(Offset(sx, dotY), 2.5, Paint()..color = WhoopTheme.sleepBlue);
    }
  }

  @override
  bool shouldRepaint(covariant _SleepHrPainter old) =>
      old.selectedStage != selectedStage || old.hrPoints != hrPoints || old.scrubX != scrubX;
}

class _SimpleLinePainter extends CustomPainter {
  final List<Map<String, dynamic>> points;
  final Color color;
  _SimpleLinePainter(this.points, this.color);

  @override
  void paint(Canvas canvas, Size size) {
    if (points.length < 2) return;
    final paint = Paint()..color = color..strokeWidth = 0.8..style = PaintingStyle.stroke;
    final path = Path();
    for (var i = 0; i < points.length; i++) {
      final p = points[i];
      final x = (p['position_x'] as num?)?.toDouble() ?? (i / (points.length - 1));
      final y = (p['position_y'] as num?)?.toDouble() ?? 0;
      final px = x * size.width;
      final py = (1 - y) * size.height;
      if (i == 0) path.moveTo(px, py); else path.lineTo(px, py);
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _SimpleLinePainter old) => old.points != points;
}
