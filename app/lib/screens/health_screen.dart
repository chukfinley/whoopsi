import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/api_service.dart';
import '../services/ble_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class HealthScreen extends StatefulWidget {
  const HealthScreen({super.key});

  @override
  State<HealthScreen> createState() => _HealthScreenState();
}

class _HealthScreenState extends State<HealthScreen> {
  Map<String, dynamic>? _healthTab;
  Map<String, dynamic>? _healthMonitor;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _fetch();
    // Auto-connect BLE if not already connected
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final ble = context.read<BleService>();
      if (!ble.connected) ble.connect();
    });
  }

  Future<void> _fetch() async {
    final api = context.read<ApiService>();
    // Load cached data immediately
    final cachedTab = api.cache.get<Map<String, dynamic>>('health_tab');
    final cachedMonitor = api.cache.get<Map<String, dynamic>>('health_monitor');
    if (cachedTab != null || cachedMonitor != null) {
      _healthTab = cachedTab;
      _healthMonitor = cachedMonitor;
      if (mounted) setState(() => _loading = false);
    }
    // Try to refresh
    try { _healthTab = await api.getHealthTab(forceRefresh: cachedTab != null); } catch (e) { debugPrint('Health tab: $e'); }
    try { _healthMonitor = await api.getHealthMonitor(forceRefresh: cachedMonitor != null); } catch (e) { debugPrint('Health monitor: $e'); }
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Health', style: TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
          : RefreshIndicator(
              color: WhoopTheme.primary,
              backgroundColor: WhoopTheme.surface,
              onRefresh: _fetch,
              child: _buildContent(),
            ),
    );
  }

  Widget _buildContent() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _buildWhoopAge(),
        const SizedBox(height: 20),
        _buildLiveHrCard(),
        const SizedBox(height: 20),
        _buildHealthMonitor(),
        const SizedBox(height: 20),
        _buildStressMonitor(),
        const SizedBox(height: 24),
      ],
    );
  }

  // === LIVE HEART RATE (BLE) ===
  Widget _buildLiveHrCard() {
    return Consumer<BleService>(
      builder: (context, ble, _) {
        final hr = ble.heartRate;
        final battery = ble.batteryLevel;
        final history = ble.hrHistory;

        return GlassCard(
          padding: const EdgeInsets.all(16),
          radius: 14,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header row
              Row(
                children: [
                  const Icon(Icons.favorite, color: WhoopTheme.error, size: 18),
                  const SizedBox(width: 8),
                  const Text('LIVE HEART RATE',
                      style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                          fontWeight: FontWeight.w600, letterSpacing: 0.5)),
                  const Spacer(),
                  if (battery >= 0) ...[
                    Icon(
                      battery > 60 ? Icons.battery_full :
                      battery > 20 ? Icons.battery_3_bar : Icons.battery_1_bar,
                      color: battery > 20 ? WhoopTheme.primary : WhoopTheme.error,
                      size: 16,
                    ),
                    const SizedBox(width: 4),
                    Text('$battery%', style: const TextStyle(
                        color: WhoopTheme.textSecondary, fontSize: 11)),
                  ],
                  if (!ble.connected) ...[
                    const SizedBox(width: 8),
                    GestureDetector(
                      onTap: () => ble.connect(),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                        decoration: BoxDecoration(
                          color: WhoopTheme.primary.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: const Text('Connect', style: TextStyle(
                            color: WhoopTheme.primary, fontSize: 11, fontWeight: FontWeight.w600)),
                      ),
                    ),
                  ],
                ],
              ),
              const SizedBox(height: 12),
              // Big HR number
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    hr > 0 ? '$hr' : '--',
                    style: TextStyle(
                      color: hr > 0 ? WhoopTheme.textPrimary : WhoopTheme.textSecondary,
                      fontSize: 48,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const Padding(
                    padding: EdgeInsets.only(bottom: 8, left: 4),
                    child: Text('BPM', style: TextStyle(
                        color: WhoopTheme.textSecondary, fontSize: 14)),
                  ),
                  const Spacer(),
                  // Zone indicator
                  if (hr > 0) _buildZoneChip(hr),
                ],
              ),
              // Mini HR graph
              if (history.length >= 2) ...[
                const SizedBox(height: 12),
                SizedBox(
                  height: 40,
                  child: CustomPaint(
                    size: const Size(double.infinity, 40),
                    painter: _MiniHrPainter(history),
                  ),
                ),
              ],
              // Status
              if (!ble.connected || hr == 0)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    ble.status,
                    style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11),
                  ),
                ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildZoneChip(int hr) {
    // Simple zone calculation based on typical max HR
    final zone = hr < 100 ? 0 : hr < 120 ? 1 : hr < 140 ? 2 : hr < 160 ? 3 : hr < 180 ? 4 : 5;
    final zoneColors = [
      WhoopTheme.textSecondary,
      WhoopTheme.sleepBlue,
      WhoopTheme.primary,
      WhoopTheme.recoveryYellow,
      WhoopTheme.warning,
      WhoopTheme.error,
    ];
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: zoneColors[zone].withValues(alpha: 0.2),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        'Zone $zone',
        style: TextStyle(color: zoneColors[zone], fontSize: 12, fontWeight: FontWeight.w600),
      ),
    );
  }

  // === WHOOP AGE ===
  Widget _buildWhoopAge() {
    if (_healthTab == null) return const SizedBox.shrink();
    final sections = _healthTab!['sections'] as List? ?? [];
    if (sections.isEmpty) return const SizedBox.shrink();

    final heroItems = (sections[0] as Map)['items'] as List? ?? [];
    if (heroItems.isEmpty) return const SizedBox.shrink();

    final heroContent = heroItems[0]['content'] as Map<String, dynamic>? ?? {};
    final subItems = heroContent['items'] as List? ?? [];

    Map<String, dynamic>? ageData;
    Map<String, dynamic>? paceMeter;
    for (final item in subItems) {
      if (item['type'] == 'WHOOP_AGE_AMOEBA') ageData = item['content'] as Map<String, dynamic>?;
      if (item['type'] == 'PACE_OF_AGING_METER') paceMeter = item['content'] as Map<String, dynamic>?;
    }

    if (ageData == null) return const SizedBox.shrink();

    final ageDisplay = ageData['age_value_display'] as String? ?? '';
    final ageTitle = ageData['age_title_display'] as String? ?? 'WHOOP AGE';
    final subtitle = ageData['age_subtitle_display'] as String? ?? '';
    final subtitleStyle = ageData['age_subtitle_style'] as String? ?? '';
    final yearsDiff = ageData['years_difference_value_display'] as String? ?? '';
    final yearsDiffSub = ageData['years_difference_subtitle_display'] as String? ?? '';
    final paceDisplay = ageData['pace_of_aging_display'] as String? ?? '';
    final paceSub = ageData['pace_of_aging_subtitle_display'] as String? ?? '';

    Color subtitleColor;
    if (subtitleStyle.contains('POSITIVE') || subtitleStyle.contains('GREEN')) {
      subtitleColor = WhoopTheme.primary;
    } else if (subtitleStyle.contains('NEGATIVE') || subtitleStyle.contains('RED')) {
      subtitleColor = WhoopTheme.error;
    } else {
      subtitleColor = WhoopTheme.textSecondary;
    }

    return GlassCard(
      padding: const EdgeInsets.all(20),
      radius: 14,
      child: Column(
        children: [
          Text(ageTitle, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          const SizedBox(height: 8),
          Text(ageDisplay, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 48, fontWeight: FontWeight.w700)),
          const SizedBox(height: 4),
          Text(subtitle, style: TextStyle(color: subtitleColor, fontSize: 14)),
          const SizedBox(height: 20),
          Row(
            children: [
              Expanded(child: _ageMetric(yearsDiff, yearsDiffSub, subtitleColor)),
              Container(width: 1, height: 40, color: WhoopTheme.divider),
              Expanded(child: _ageMetric(paceDisplay, paceSub, WhoopTheme.textSecondary)),
            ],
          ),
          if (paceMeter != null) ...[
            const SizedBox(height: 16),
            _buildPaceMeter(paceMeter),
          ],
        ],
      ),
    );
  }

  Widget _ageMetric(String value, String label, Color color) {
    return Column(
      children: [
        Text(value, style: TextStyle(color: color, fontSize: 22, fontWeight: FontWeight.w700)),
        const SizedBox(height: 4),
        Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10, fontWeight: FontWeight.w600, letterSpacing: 0.3), textAlign: TextAlign.center),
      ],
    );
  }

  Widget _buildPaceMeter(Map<String, dynamic> meter) {
    final ticks = meter['ticks'] as List? ?? [];
    if (ticks.isEmpty) return const SizedBox.shrink();

    int currentIdx = ticks.length ~/ 2;
    for (var i = 0; i < ticks.length; i++) {
      if (ticks[i]['is_current_value'] == true) {
        currentIdx = i;
        break;
      }
    }

    final progress = currentIdx / (ticks.length - 1);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(meter['title_display'] as String? ?? '', style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10, fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        LayoutBuilder(
          builder: (context, constraints) {
            final barWidth = constraints.maxWidth;
            return SizedBox(
              height: 20,
              child: Stack(
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: Container(
                      height: 8,
                      margin: const EdgeInsets.only(top: 6),
                      decoration: const BoxDecoration(
                        gradient: LinearGradient(colors: [
                          WhoopTheme.primary,
                          WhoopTheme.recoveryYellow,
                          WhoopTheme.error,
                        ]),
                      ),
                    ),
                  ),
                  Positioned(
                    left: progress * (barWidth - 3),
                    top: 0,
                    child: Container(
                      width: 3,
                      height: 20,
                      decoration: BoxDecoration(
                        color: WhoopTheme.textPrimary,
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                ],
              ),
            );
          },
        ),
        const SizedBox(height: 4),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(ticks.first['top_value_display'] as String? ?? '', style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 9)),
            Text(ticks.last['top_value_display'] as String? ?? '', style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 9)),
          ],
        ),
      ],
    );
  }

  // === HEALTH MONITOR ===
  Widget _buildHealthMonitor() {
    if (_healthMonitor == null) return const SizedBox.shrink();
    final items = _healthMonitor!['items'] as List? ?? [];
    final title = _healthMonitor!['title'] as String? ?? 'Health Monitor';

    final metrics = items.where((i) => i['type'] == 'KEY_METRIC_TILE').toList();
    if (metrics.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600)),
        const SizedBox(height: 12),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 10,
          crossAxisSpacing: 10,
          childAspectRatio: 1.5,
          children: metrics.map<Widget>((item) {
            final c = item['content'] as Map<String, dynamic>;
            return _buildMetricTile(c);
          }).toList(),
        ),
      ],
    );
  }

  Widget _buildMetricTile(Map<String, dynamic> c) {
    final title = c['key_metric_tile_title_display'] as String? ?? '';
    final value = c['key_metric_tile_stat_value_display'] as String? ?? '';
    final suffix = c['key_metric_tile_suffix_display'] as String? ?? '';
    final trend = c['key_metric_tile_trend_display'] as String? ?? '';
    final trendType = c['key_metric_tile_trend_type'] as String? ?? '';
    final icon = c['key_metric_tile_icon'] as String? ?? '';

    Color trendColor;
    if (trendType.contains('POSITIVE')) {
      trendColor = WhoopTheme.primary;
    } else if (trendType.contains('NEGATIVE') || trendType.contains('WARNING')) {
      trendColor = WhoopTheme.warning;
    } else if (trendType.contains('CRITICAL')) {
      trendColor = WhoopTheme.error;
    } else {
      trendColor = WhoopTheme.textSecondary;
    }

    IconData iconData;
    switch (icon) {
      case 'RESPIRATORY_RATE': iconData = Icons.air; break;
      case 'BLOOD_OXYGEN': iconData = Icons.water_drop; break;
      case 'RHR': iconData = Icons.favorite; break;
      case 'HRV': iconData = Icons.monitor_heart; break;
      case 'SKIN_TEMPERATURE_CELSIUS': iconData = Icons.thermostat; break;
      default: iconData = Icons.health_and_safety; break;
    }

    return GlassCard(
      padding: const EdgeInsets.all(12),
      radius: 14,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(iconData, color: WhoopTheme.sleepBlue, size: 16),
              const SizedBox(width: 6),
              Expanded(child: Text(title, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10, fontWeight: FontWeight.w600), maxLines: 2, overflow: TextOverflow.ellipsis)),
            ],
          ),
          const Spacer(),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 24, fontWeight: FontWeight.w700)),
              if (suffix.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(bottom: 3, left: 2),
                  child: Text(suffix, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                ),
            ],
          ),
          if (trend.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Row(
                children: [
                  Icon(Icons.check_circle, color: trendColor, size: 12),
                  const SizedBox(width: 4),
                  Expanded(child: Text(trend, style: TextStyle(color: trendColor, fontSize: 10), maxLines: 1, overflow: TextOverflow.ellipsis)),
                ],
              ),
            ),
        ],
      ),
    );
  }

  // === STRESS MONITOR ===
  Widget _buildStressMonitor() {
    if (_healthTab == null) return const SizedBox.shrink();
    final sections = _healthTab!['sections'] as List? ?? [];
    if (sections.isEmpty) return const SizedBox.shrink();

    final items = (sections[0] as Map)['items'] as List? ?? [];
    Map<String, dynamic>? stressCard;

    for (final item in items) {
      final type = item['type'] as String? ?? '';
      if (type == 'GRAPH_DESCRIPTION_CARD') stressCard = item['content'] as Map<String, dynamic>?;
    }

    if (stressCard == null) return const SizedBox.shrink();

    final title = stressCard['title'] as String? ?? 'STRESS MONITOR';
    final body = stressCard['body'] as Map<String, dynamic>? ?? {};
    final stressTitle = body['title'] as String? ?? '';
    final magnitude = body['magnitude'] as String? ?? '';
    final magnitudeSuffix = body['magnitude_suffix'] as String? ?? '';
    final trendData = body['trend'] as Map<String, dynamic>?;

    return GlassCard(
      padding: const EdgeInsets.all(16),
      radius: 14,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          const SizedBox(height: 8),
          Text(stressTitle, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14)),
          const SizedBox(height: 4),
          Row(
            children: [
              Text(magnitude, style: const TextStyle(color: WhoopTheme.warning, fontSize: 28, fontWeight: FontWeight.w700)),
              const SizedBox(width: 4),
              Text(magnitudeSuffix, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 14)),
              if (trendData != null) ...[
                const SizedBox(width: 8),
                Text(trendData['title_display'] as String? ?? '', style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
              ],
            ],
          ),
          if (stressCard['graph'] != null)
            _buildStressGraph(stressCard['graph'] as Map<String, dynamic>),
        ],
      ),
    );
  }

  Widget _buildStressGraph(Map<String, dynamic> graph) {
    final plots = graph['plots'] as List? ?? [];
    if (plots.isEmpty) return const SizedBox.shrink();

    final segs = (plots[0]['plot'] as Map?)?['segments'] as List? ?? [];
    if (segs.isEmpty) return const SizedBox.shrink();
    final points = segs[0]['points'] as List? ?? [];
    if (points.length < 2) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: SizedBox(
        height: 60,
        child: CustomPaint(
          size: const Size(double.infinity, 60),
          painter: _StressLinePainter(points),
        ),
      ),
    );
  }
}

class _MiniHrPainter extends CustomPainter {
  final List<int> values;
  _MiniHrPainter(this.values);

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final minHr = values.reduce((a, b) => a < b ? a : b).toDouble() - 5;
    final maxHr = values.reduce((a, b) => a > b ? a : b).toDouble() + 5;
    final range = maxHr - minHr;
    if (range <= 0) return;

    final paint = Paint()
      ..color = WhoopTheme.error.withValues(alpha: 0.8)
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    final path = Path();
    for (var i = 0; i < values.length; i++) {
      final x = i / (values.length - 1) * size.width;
      final y = (1 - (values[i] - minHr) / range) * size.height;
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _MiniHrPainter old) => true;
}

class _StressLinePainter extends CustomPainter {
  final List points;
  _StressLinePainter(this.points);

  @override
  void paint(Canvas canvas, Size size) {
    if (points.length < 2) return;
    final paint = Paint()
      ..strokeWidth = 1
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final path = Path();
    for (var i = 0; i < points.length; i++) {
      final p = points[i] as Map<String, dynamic>;
      final x = (p['position_x'] as num?)?.toDouble() ?? (i / (points.length - 1));
      final y = (p['position_y'] as num?)?.toDouble() ?? 0;
      final px = x * size.width;
      final py = (1 - y) * size.height;

      if (i == 0) {
        path.moveTo(px, py);
      } else {
        path.lineTo(px, py);
      }
    }

    paint.color = WhoopTheme.warning.withValues(alpha: 0.8);
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _StressLinePainter old) => old.points != points;
}
