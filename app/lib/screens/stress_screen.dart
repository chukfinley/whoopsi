import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/recovery.dart';
import '../models/stress_data.dart';
import '../services/api_service.dart';
import '../widgets/calendar_heatmap.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class StressScreen extends StatefulWidget {
  final String date;
  const StressScreen({super.key, required this.date});

  @override
  State<StressScreen> createState() => _StressScreenState();
}

class _StressScreenState extends State<StressScreen> with TickerProviderStateMixin {
  StressData? _data;
  bool _loading = true;
  String? _error;
  late DateTime _selectedDate;
  late AnimationController _gaugeAnimController;
  late Animation<double> _gaugeAnim;

  // Trends
  int _trendDays = 7;
  List<StressDaySummary> _trendData = [];
  bool _trendLoading = false;
  DateTime _calendarMonth = DateTime(DateTime.now().year, DateTime.now().month);

  @override
  void initState() {
    super.initState();
    _selectedDate = DateTime.tryParse(widget.date) ?? DateTime.now();
    _gaugeAnimController = AnimationController(vsync: this, duration: const Duration(milliseconds: 800));
    _gaugeAnim = Tween<double>(begin: 0, end: 0).animate(
      CurvedAnimation(parent: _gaugeAnimController, curve: Curves.easeOutCubic),
    );
    _fetch();
  }

  @override
  void dispose() {
    _gaugeAnimController.dispose();
    super.dispose();
  }

  String get _dateStr => DateFormat('yyyy-MM-dd').format(_selectedDate);
  bool get _isToday =>
      DateFormat('yyyy-MM-dd').format(_selectedDate) == DateFormat('yyyy-MM-dd').format(DateTime.now());

  void _changeDate(int days) {
    HapticFeedback.lightImpact();
    setState(() { _selectedDate = _selectedDate.add(Duration(days: days)); });
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() { _loading = true; _error = null; });
    try {
      final api = context.read<ApiService>();
      final raw = await api.getStressMonitor(_dateStr, forceRefresh: true);
      final data = StressData.fromApi(raw);
      if (mounted) {
        setState(() { _data = data; _loading = false; });
        _gaugeAnim = Tween<double>(begin: 0, end: data.meterValue).animate(
          CurvedAnimation(parent: _gaugeAnimController, curve: Curves.easeOutCubic),
        );
        _gaugeAnimController.forward(from: 0);
      }
    } catch (e) {
      if (mounted) setState(() { _loading = false; _error = e.toString(); });
    }
  }

  Future<void> _fetchTrend() async {
    setState(() => _trendLoading = true);
    final api = context.read<ApiService>();
    final now = DateTime.now();
    final results = <StressDaySummary>[];
    for (var i = 0; i < _trendDays; i++) {
      final date = now.subtract(Duration(days: i));
      final dateStr = DateFormat('yyyy-MM-dd').format(date);
      try {
        final cached = api.cache.get<Map<String, dynamic>>('stress:$dateStr');
        if (cached == null) continue;
        final d = StressData.fromApi(cached);
        if (d.timeline.isNotEmpty) {
          results.add(StressDaySummary(
            date: dateStr,
            state: d.state,
            avgStress: d.average,
            peakStress: d.peak,
          ));
        }
      } catch (_) {}
    }
    results.sort((a, b) => a.date.compareTo(b.date));
    if (mounted) setState(() { _trendData = results; _trendLoading = false; });
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: GradientScaffold(
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          title: const Text('Stress Monitor', style: TextStyle(fontWeight: FontWeight.bold)),
          elevation: 0,
          bottom: const TabBar(
            indicatorColor: WhoopTheme.primary,
            labelColor: WhoopTheme.primary,
            unselectedLabelColor: WhoopTheme.textSecondary,
            tabs: [
              Tab(text: 'Today'),
              Tab(text: 'Trends'),
            ],
          ),
        ),
        body: SafeArea(
          child: TabBarView(
            children: [
              _buildTodayTab(),
              _buildTrendsTab(),
            ],
          ),
        ),
      ),
    );
  }

  // ─── TODAY TAB ──────────────────────────────────────────────

  Widget _buildTodayTab() {
    return Column(
      children: [
        _buildDateNav(),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
              : _error != null
                  ? _buildError()
                  : RefreshIndicator(
                      color: WhoopTheme.primary,
                      onRefresh: _fetch,
                      child: _buildTodayContent(),
                    ),
        ),
      ],
    );
  }

  Widget _buildDateNav() {
    final label = _isToday
        ? 'TODAY'
        : DateFormat('MMM d').format(_selectedDate).toUpperCase();
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          IconButton(
            icon: const Icon(Icons.chevron_left, color: WhoopTheme.textSecondary),
            onPressed: () => _changeDate(-1),
          ),
          GestureDetector(
            onTap: () {
              if (!_isToday) {
                setState(() => _selectedDate = DateTime.now());
                _fetch();
              }
            },
            child: Text(label,
                style: const TextStyle(color: WhoopTheme.textPrimary,
                    fontSize: 14, fontWeight: FontWeight.w600, letterSpacing: 1)),
          ),
          IconButton(
            icon: Icon(Icons.chevron_right,
                color: _isToday ? WhoopTheme.divider : WhoopTheme.textSecondary),
            onPressed: _isToday ? null : () => _changeDate(1),
          ),
        ],
      ),
    );
  }

  Widget _buildError() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.error_outline, color: WhoopTheme.textSecondary, size: 48),
          const SizedBox(height: 16),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(_error!, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13),
                textAlign: TextAlign.center),
          ),
          const SizedBox(height: 16),
          OutlinedButton(onPressed: _fetch, child: const Text('Retry')),
        ],
      ),
    );
  }

  Widget _buildTodayContent() {
    if (_data == null) {
      return const Center(child: Text('No stress data available',
          style: TextStyle(color: WhoopTheme.textSecondary)));
    }
    final d = _data!;

    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      children: [
        _buildGauge(d),
        const SizedBox(height: 16),
        if (d.zones.total > Duration.zero) ...[
          _buildZoneBar(d.zones),
          const SizedBox(height: 16),
        ],
        _buildStatsRow(d),
        const SizedBox(height: 16),
        if (d.timeline.isNotEmpty) ...[
          _buildTimelineChart(d.timeline),
          const SizedBox(height: 16),
        ],
        if (d.coachTip != null) ...[
          _buildCoachCard(d.coachTip!),
          const SizedBox(height: 16),
        ],
        _buildRecoveryCorrelation(d),
        const SizedBox(height: 16),
        _buildTipsCard(d.state),
        const SizedBox(height: 80),
      ],
    );
  }

  Widget _buildGauge(StressData d) {
    final color = _stateColor(d.state);
    final label = _stateLabel(d.state);

    return GlassCard(
      padding: const EdgeInsets.all(24),
      child: Column(
        children: [
          SizedBox(
            width: 180, height: 110,
            child: AnimatedBuilder(
              animation: _gaugeAnim,
              builder: (_, __) => CustomPaint(
                painter: _StressGaugePainter(level: _gaugeAnim.value, color: color),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Text(label, style: TextStyle(color: color, fontSize: 26, fontWeight: FontWeight.bold)),
          if (d.timeline.isNotEmpty)
            Text('Score: ${d.average.round()}',
                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
          const SizedBox(height: 2),
          const Text('Stress Level',
              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
        ],
      ),
    );
  }

  Widget _buildZoneBar(StressZoneTime zones) {
    final total = zones.total.inSeconds.toDouble();
    if (total <= 0) return const SizedBox.shrink();
    final lowPct = zones.low.inSeconds / total;
    final modPct = zones.moderate.inSeconds / total;
    final highPct = zones.high.inSeconds / total;

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Time in Zones',
              style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: SizedBox(
              height: 20,
              child: Row(
                children: [
                  if (lowPct > 0) Expanded(flex: (lowPct * 1000).round(),
                      child: Container(color: WhoopTheme.recoveryGreen)),
                  if (modPct > 0) Expanded(flex: (modPct * 1000).round(),
                      child: Container(color: WhoopTheme.warning)),
                  if (highPct > 0) Expanded(flex: (highPct * 1000).round(),
                      child: Container(color: WhoopTheme.error)),
                ],
              ),
            ),
          ),
          const SizedBox(height: 10),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              _zoneLabel(WhoopTheme.recoveryGreen, 'Low', zones.low),
              _zoneLabel(WhoopTheme.warning, 'Moderate', zones.moderate),
              _zoneLabel(WhoopTheme.error, 'High', zones.high),
            ],
          ),
        ],
      ),
    );
  }

  Widget _zoneLabel(Color color, String label, Duration dur) {
    final h = dur.inHours;
    final m = dur.inMinutes % 60;
    final text = h > 0 ? '${h}h ${m}m' : '${m}m';
    return Column(
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
            const SizedBox(width: 4),
            Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
          ],
        ),
        const SizedBox(height: 2),
        Text(text, style: TextStyle(color: color, fontSize: 13, fontWeight: FontWeight.w600)),
      ],
    );
  }

  Widget _buildStatsRow(StressData d) {
    return Row(
      children: [
        _statCard('Average', d.timeline.isEmpty ? '--' : d.average.round().toString(), WhoopTheme.warning),
        const SizedBox(width: 8),
        _statCard('Peak', d.timeline.isEmpty ? '--' : d.peak.round().toString(), WhoopTheme.error),
        const SizedBox(width: 8),
        _statCard('Lowest', d.timeline.isEmpty ? '--' : d.lowest.round().toString(), WhoopTheme.recoveryGreen),
      ],
    );
  }

  Widget _statCard(String label, String value, Color color) {
    return Expanded(
      child: GlassCard(
        padding: const EdgeInsets.all(12),
        child: Column(
          children: [
            Text(value, style: TextStyle(color: color, fontSize: 22, fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
          ],
        ),
      ),
    );
  }

  Widget _buildTimelineChart(List<StressPoint> timeline) {
    // Convert to hours-based x-axis
    final spots = timeline.map((p) {
      final hours = p.time.hour + p.time.minute / 60.0;
      return FlSpot(hours, p.value);
    }).toList();

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('24-Hour Timeline',
              style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
          const SizedBox(height: 16),
          SizedBox(
            height: 200,
            child: LineChart(
              LineChartData(
                gridData: FlGridData(
                  show: true,
                  drawVerticalLine: false,
                  horizontalInterval: 33,
                  getDrawingHorizontalLine: (v) => FlLine(
                    color: WhoopTheme.divider.withValues(alpha: 0.3),
                    strokeWidth: 0.5,
                  ),
                ),
                titlesData: FlTitlesData(
                  topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  leftTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 32,
                      interval: 25,
                      getTitlesWidget: (v, _) => Text(
                        '${v.round()}',
                        style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10),
                      ),
                    ),
                  ),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 28,
                      interval: 6,
                      getTitlesWidget: (v, _) {
                        final hour = v.round();
                        if (hour < 0 || hour > 24) return const SizedBox.shrink();
                        final labels = {0: '12AM', 6: '6AM', 12: '12PM', 18: '6PM', 24: '12AM'};
                        return Text(
                          labels[hour] ?? '',
                          style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 9),
                        );
                      },
                    ),
                  ),
                ),
                borderData: FlBorderData(show: false),
                rangeAnnotations: RangeAnnotations(
                  horizontalRangeAnnotations: [
                    HorizontalRangeAnnotation(y1: 0, y2: 33, color: WhoopTheme.recoveryGreen.withValues(alpha: 0.06)),
                    HorizontalRangeAnnotation(y1: 33, y2: 66, color: WhoopTheme.warning.withValues(alpha: 0.06)),
                    HorizontalRangeAnnotation(y1: 66, y2: 100, color: WhoopTheme.error.withValues(alpha: 0.06)),
                  ],
                ),
                lineTouchData: LineTouchData(
                  touchTooltipData: LineTouchTooltipData(
                    getTooltipItems: (spots) => spots.map((s) {
                      final hour = s.x.floor();
                      final min = ((s.x - hour) * 60).round();
                      return LineTooltipItem(
                        '${hour.toString().padLeft(2, '0')}:${min.toString().padLeft(2, '0')}\nStress: ${s.y.round()}',
                        const TextStyle(color: WhoopTheme.textPrimary, fontSize: 12),
                      );
                    }).toList(),
                  ),
                ),
                lineBarsData: [
                  LineChartBarData(
                    spots: spots,
                    isCurved: true,
                    curveSmoothness: 0.2,
                    gradient: const LinearGradient(
                      colors: [WhoopTheme.recoveryGreen, WhoopTheme.warning, WhoopTheme.error],
                    ),
                    barWidth: 2.5,
                    dotData: const FlDotData(show: false),
                    belowBarData: BarAreaData(
                      show: true,
                      gradient: LinearGradient(
                        begin: Alignment.topCenter,
                        end: Alignment.bottomCenter,
                        colors: [
                          WhoopTheme.warning.withValues(alpha: 0.12),
                          WhoopTheme.warning.withValues(alpha: 0.01),
                        ],
                      ),
                    ),
                  ),
                ],
                minX: 0,
                maxX: 24,
                minY: 0,
                maxY: 100,
              ),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              _zoneDot(WhoopTheme.recoveryGreen, 'Low (0-33)'),
              _zoneDot(WhoopTheme.warning, 'Moderate (34-66)'),
              _zoneDot(WhoopTheme.error, 'High (67-100)'),
            ],
          ),
        ],
      ),
    );
  }

  Widget _zoneDot(Color color, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(width: 4),
        Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10)),
      ],
    );
  }

  Widget _buildCoachCard(String tip) {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.auto_awesome, color: WhoopTheme.primary.withValues(alpha: 0.7), size: 24),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Stress Coach',
                    style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
                const SizedBox(height: 6),
                Text(tip, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13, height: 1.4)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildRecoveryCorrelation(StressData d) {
    final api = context.read<ApiService>();
    final recCached = api.cache.get<Map<String, dynamic>>('deep_dive:recovery:$_dateStr');
    if (recCached == null) return const SizedBox.shrink();

    try {
      final rec = Recovery.fromDeepDive(recCached);
      final avgStress = d.average.round();
      final recScore = rec.score.round();

      String insight;
      if (avgStress > 66 && recScore < 50) {
        insight = 'High stress is likely impacting your recovery. Consider active recovery today.';
      } else if (avgStress < 34 && recScore > 66) {
        insight = 'Low stress is supporting your recovery. Great balance!';
      } else {
        insight = 'Monitor how stress changes affect your next recovery score.';
      }

      return GlassCard(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Stress vs Recovery',
                style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(child: _metricPill('Recovery', '$recScore%', _recoveryColor(recScore))),
                const SizedBox(width: 8),
                Expanded(child: _metricPill('Avg Stress', '$avgStress', _stateColorFromValue(avgStress.toDouble()))),
              ],
            ),
            const SizedBox(height: 10),
            Text(insight, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, height: 1.4)),
          ],
        ),
      );
    } catch (_) {
      return const SizedBox.shrink();
    }
  }

  Widget _metricPill(String label, String value, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 12),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: color.withValues(alpha: 0.3), width: 0.5),
      ),
      child: Column(
        children: [
          Text(value, style: TextStyle(color: color, fontSize: 20, fontWeight: FontWeight.bold)),
          const SizedBox(height: 2),
          Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
        ],
      ),
    );
  }

  Widget _buildTipsCard(String state) {
    final tips = _getTips(state);
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Recommendations',
              style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          ...tips.map((tip) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.lightbulb_outline, color: WhoopTheme.warning, size: 18),
                    const SizedBox(width: 10),
                    Expanded(child: Text(tip,
                        style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13, height: 1.4))),
                  ],
                ),
              )),
        ],
      ),
    );
  }

  List<String> _getTips(String state) {
    final n = state.toLowerCase();
    if (n.contains('high') || n.contains('elevated')) {
      return [
        'Try box breathing: inhale 4s, hold 4s, exhale 4s, hold 4s.',
        'Take a 10-minute walk outside to reset your nervous system.',
        'Avoid caffeine for the rest of the day.',
        'Consider a cold shower or face splash to activate the dive reflex.',
      ];
    }
    if (n.contains('medium') || n.contains('moderate')) {
      return [
        'Practice 5 minutes of deep breathing between tasks.',
        'Stay hydrated — dehydration amplifies stress hormones.',
        'A short meditation session can help lower cortisol.',
      ];
    }
    return [
      'Your stress levels look good. Keep up your current routine.',
      'Regular exercise and good sleep help maintain low stress.',
      'Mindfulness practice can help you stay in this zone.',
    ];
  }

  // ─── TRENDS TAB ──────────────────────────────────────────────

  Widget _buildTrendsTab() {
    return StatefulBuilder(
      builder: (ctx, setTabState) {
        // Fetch trend data on first open or period change
        if (_trendData.isEmpty && !_trendLoading) {
          Future.microtask(() => _fetchTrend());
        }

        return ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // Period selector
            _buildPeriodSelector(setTabState),
            const SizedBox(height: 16),

            if (_trendLoading)
              const Center(child: Padding(
                padding: EdgeInsets.all(32),
                child: CircularProgressIndicator(color: WhoopTheme.primary),
              ))
            else if (_trendData.isEmpty)
              const Center(child: Padding(
                padding: EdgeInsets.all(32),
                child: Text('No trend data. Prefetch stress data in Settings to populate.',
                    style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 13),
                    textAlign: TextAlign.center),
              ))
            else ...[
              _buildTrendChart(),
              const SizedBox(height: 16),
              _buildTrendSummary(),
              const SizedBox(height: 16),
              _buildStressCalendar(setTabState),
            ],
            const SizedBox(height: 80),
          ],
        );
      },
    );
  }

  Widget _buildPeriodSelector(StateSetter setTabState) {
    return Row(
      children: [
        for (final days in [7, 14, 30])
          Expanded(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4),
              child: GestureDetector(
                onTap: () {
                  _trendDays = days;
                  _trendData = [];
                  setTabState(() {});
                  _fetchTrend().then((_) { if (mounted) setTabState(() {}); });
                },
                child: Container(
                  padding: const EdgeInsets.symmetric(vertical: 10),
                  decoration: BoxDecoration(
                    color: _trendDays == days
                        ? WhoopTheme.primary.withValues(alpha: 0.15)
                        : WhoopTheme.surface,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                      color: _trendDays == days ? WhoopTheme.primary : WhoopTheme.cardBorder,
                      width: _trendDays == days ? 1.5 : 0.5,
                    ),
                  ),
                  child: Center(
                    child: Text('${days}D',
                        style: TextStyle(
                          color: _trendDays == days ? WhoopTheme.primary : WhoopTheme.textSecondary,
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                        )),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  Widget _buildTrendChart() {
    if (_trendData.isEmpty) return const SizedBox.shrink();

    final spots = <FlSpot>[];
    for (var i = 0; i < _trendData.length; i++) {
      spots.add(FlSpot(i.toDouble(), _trendData[i].avgStress));
    }

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Average Daily Stress',
              style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
          const SizedBox(height: 16),
          SizedBox(
            height: 180,
            child: LineChart(
              LineChartData(
                gridData: FlGridData(
                  show: true,
                  drawVerticalLine: false,
                  horizontalInterval: 25,
                  getDrawingHorizontalLine: (v) => FlLine(
                    color: WhoopTheme.divider.withValues(alpha: 0.3),
                    strokeWidth: 0.5,
                  ),
                ),
                titlesData: FlTitlesData(
                  topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  leftTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 32,
                      interval: 25,
                      getTitlesWidget: (v, _) => Text('${v.round()}',
                          style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10)),
                    ),
                  ),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 28,
                      interval: _trendData.length > 14 ? (_trendData.length / 5).ceilToDouble() : 2,
                      getTitlesWidget: (v, _) {
                        final idx = v.round();
                        if (idx < 0 || idx >= _trendData.length) return const SizedBox.shrink();
                        final date = DateTime.parse(_trendData[idx].date);
                        return Text(DateFormat('d/M').format(date),
                            style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 9));
                      },
                    ),
                  ),
                ),
                borderData: FlBorderData(show: false),
                lineBarsData: [
                  LineChartBarData(
                    spots: spots,
                    isCurved: true,
                    curveSmoothness: 0.3,
                    color: WhoopTheme.primary,
                    barWidth: 2.5,
                    dotData: FlDotData(
                      show: true,
                      getDotPainter: (spot, _, __, ___) {
                        final color = _stateColorFromValue(spot.y);
                        return FlDotCirclePainter(radius: 4, color: color, strokeWidth: 0);
                      },
                    ),
                    belowBarData: BarAreaData(
                      show: true,
                      gradient: LinearGradient(
                        begin: Alignment.topCenter,
                        end: Alignment.bottomCenter,
                        colors: [
                          WhoopTheme.primary.withValues(alpha: 0.15),
                          WhoopTheme.primary.withValues(alpha: 0.01),
                        ],
                      ),
                    ),
                  ),
                ],
                minY: 0,
                maxY: 100,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTrendSummary() {
    if (_trendData.isEmpty) return const SizedBox.shrink();

    final avgAll = _trendData.map((d) => d.avgStress).reduce((a, b) => a + b) / _trendData.length;
    final bestDay = _trendData.reduce((a, b) => a.avgStress < b.avgStress ? a : b);
    final worstDay = _trendData.reduce((a, b) => a.avgStress > b.avgStress ? a : b);

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${_trendDays}-Day Summary',
              style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          _summaryRow('Period Average', avgAll.round().toString(), _stateColorFromValue(avgAll)),
          const SizedBox(height: 8),
          _summaryRow('Lowest Day', '${DateFormat('MMM d').format(DateTime.parse(bestDay.date))} — ${bestDay.avgStress.round()}',
              WhoopTheme.recoveryGreen),
          const SizedBox(height: 8),
          _summaryRow('Highest Day', '${DateFormat('MMM d').format(DateTime.parse(worstDay.date))} — ${worstDay.avgStress.round()}',
              WhoopTheme.error),
        ],
      ),
    );
  }

  Widget _summaryRow(String label, String value, Color color) {
    return Row(
      children: [
        Container(width: 10, height: 10, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(width: 10),
        Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
        const Spacer(),
        Text(value, style: TextStyle(color: color, fontSize: 13, fontWeight: FontWeight.w600)),
      ],
    );
  }

  Widget _buildStressCalendar(StateSetter setTabState) {
    // Build scores map from cached stress data
    final api = context.read<ApiService>();
    final scores = <DateTime, double>{};
    final daysInMonth = DateTime(_calendarMonth.year, _calendarMonth.month + 1, 0).day;

    for (var d = 1; d <= daysInMonth; d++) {
      final date = DateTime(_calendarMonth.year, _calendarMonth.month, d);
      final dateStr = DateFormat('yyyy-MM-dd').format(date);
      final cached = api.cache.get<Map<String, dynamic>>('stress:$dateStr');
      if (cached != null) {
        try {
          final data = StressData.fromApi(cached);
          if (data.timeline.isNotEmpty) scores[date] = data.average;
        } catch (_) {}
      }
    }

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              IconButton(
                icon: const Icon(Icons.chevron_left, color: WhoopTheme.textSecondary, size: 20),
                onPressed: () {
                  _calendarMonth = DateTime(_calendarMonth.year, _calendarMonth.month - 1);
                  setTabState(() {});
                },
              ),
              Expanded(
                child: Center(
                  child: Text(DateFormat('MMMM yyyy').format(_calendarMonth),
                      style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.chevron_right, color: WhoopTheme.textSecondary, size: 20),
                onPressed: () {
                  final next = DateTime(_calendarMonth.year, _calendarMonth.month + 1);
                  if (!next.isAfter(DateTime.now())) {
                    _calendarMonth = next;
                    setTabState(() {});
                  }
                },
              ),
            ],
          ),
          const SizedBox(height: 8),
          CalendarHeatmap(
            scores: scores,
            month: _calendarMonth,
            maxScore: 100,
            onDayTap: (date) {
              setState(() => _selectedDate = date);
              DefaultTabController.of(context).animateTo(0);
              _fetch();
            },
          ),
        ],
      ),
    );
  }

  // ─── HELPERS ───────────────────────────────────────────────

  Color _stateColor(String state) {
    final n = state.toLowerCase();
    if (n.contains('low') || n.contains('calm') || n.contains('rest')) return WhoopTheme.recoveryGreen;
    if (n.contains('medium') || n.contains('moderate') || n.contains('normal')) return WhoopTheme.warning;
    if (n.contains('high') || n.contains('elevated')) return WhoopTheme.error;
    return WhoopTheme.textSecondary;
  }

  String _stateLabel(String state) {
    final n = state.toLowerCase();
    if (n.contains('low') || n.contains('calm') || n.contains('rest')) return 'Low';
    if (n.contains('medium') || n.contains('moderate') || n.contains('normal')) return 'Moderate';
    if (n.contains('high') || n.contains('elevated')) return 'High';
    return state;
  }

  Color _stateColorFromValue(double value) {
    if (value < 34) return WhoopTheme.recoveryGreen;
    if (value < 67) return WhoopTheme.warning;
    return WhoopTheme.error;
  }

  Color _recoveryColor(int score) {
    if (score >= 67) return WhoopTheme.recoveryGreen;
    if (score >= 34) return WhoopTheme.warning;
    return WhoopTheme.error;
  }
}

class _StressGaugePainter extends CustomPainter {
  final double level;
  final Color color;

  _StressGaugePainter({required this.level, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height);
    final radius = size.width / 2 - 10;

    // Background arc
    final bgPaint = Paint()
      ..color = WhoopTheme.divider
      ..style = PaintingStyle.stroke
      ..strokeWidth = 14
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      math.pi, math.pi, false, bgPaint,
    );

    // Zone arcs
    final zones = [
      (WhoopTheme.recoveryGreen, 0.0, 0.33),
      (WhoopTheme.warning, 0.33, 0.66),
      (WhoopTheme.error, 0.66, 1.0),
    ];
    for (final (zoneColor, start, end) in zones) {
      final zonePaint = Paint()
        ..color = zoneColor.withValues(alpha: 0.2)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 14
        ..strokeCap = StrokeCap.round;
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        math.pi + (math.pi * start),
        math.pi * (end - start),
        false,
        zonePaint,
      );
    }

    // Active arc
    if (level > 0) {
      final activePaint = Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 14
        ..strokeCap = StrokeCap.round;
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        math.pi, math.pi * level, false, activePaint,
      );
    }

    // Needle dot
    final angle = math.pi + (math.pi * level);
    final dotX = center.dx + radius * math.cos(angle);
    final dotY = center.dy + radius * math.sin(angle);
    canvas.drawCircle(Offset(dotX, dotY), 7, Paint()..color = color);
    canvas.drawCircle(Offset(dotX, dotY), 3.5, Paint()..color = WhoopTheme.background);
  }

  @override
  bool shouldRepaint(covariant _StressGaugePainter old) =>
      old.level != level || old.color != color;
}
