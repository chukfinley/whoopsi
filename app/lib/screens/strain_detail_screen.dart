import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/strain.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';
import '../widgets/score_gauge.dart';
import '../widgets/trend_bar_chart.dart';

class StrainDetailScreen extends StatefulWidget {
  final String date;
  const StrainDetailScreen({super.key, required this.date});

  @override
  State<StrainDetailScreen> createState() => _StrainDetailScreenState();
}

class _StrainDetailScreenState extends State<StrainDetailScreen> {
  Strain? _strain;
  Map<String, dynamic>? _trends;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() => _loading = true);
    final api = context.read<ApiService>();

    try {
      final data = await api.getDeepDive('strain', widget.date);
      _strain = Strain.fromDeepDive(data);
    } catch (e) {
      debugPrint('Strain fetch failed: $e');
    }

    try {
      _trends = await api.getDeepDiveTrends('strain', widget.date);
    } catch (e) {
      debugPrint('Strain trends failed: $e');
    }

    if (mounted) setState(() => _loading = false);
  }

  List<Map<String, dynamic>> _parseActivities() {
    if (_strain == null) return [];
    final raw = _strain!.raw;
    final activities = <Map<String, dynamic>>[];
    for (final section in raw['sections'] as List? ?? []) {
      for (final item in (section as Map)['items'] as List? ?? []) {
        final map = item as Map<String, dynamic>;
        final type = (map['type'] as String? ?? '').toUpperCase();
        if (type.contains('ACTIVITY')) {
          final content = map['content'] as Map<String, dynamic>? ?? {};
          activities.add({
            'name': content['title'] ?? content['name'] ?? 'Activity',
            'score': content['score_display'] ?? content['strain'] ?? '',
            'time': content['subtitle'] ?? content['time_range'] ?? '',
            'id': content['activity_id'] ?? content['id'] ?? '',
          });
        }
      }
    }
    return activities;
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Strain', style: TextStyle(fontWeight: FontWeight.bold)),
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
    final str = _strain ?? const Strain();
    final activities = _parseActivities();

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // Score gauge
        Center(
          child: ScoreGauge(
            score: str.score,
            maxScore: 21,
            label: 'Strain',
            color: WhoopTheme.strainAmber,
            size: 140,
          ),
        ),
        const SizedBox(height: 24),

        // Contributors
        _buildContributors(str),
        const SizedBox(height: 16),

        // Activities
        if (activities.isNotEmpty) ...[
          const Text('Activities', style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          ...activities.map(_buildActivityCard),
        ],

        // Weekly Trends
        ..._buildTrends(),
        const SizedBox(height: 24),
      ],
    );
  }

  Widget _buildContributors(Strain str) {
    final metrics = <_Metric>[];
    if (str.hrZones13 != null) metrics.add(_Metric(Icons.monitor_heart, 'Heart Rate Zones 1-3', str.hrZones13!));
    if (str.hrZones45 != null) metrics.add(_Metric(Icons.monitor_heart, 'Heart Rate Zones 4-5', str.hrZones45!));
    if (str.strengthTime != null) metrics.add(_Metric(Icons.fitness_center, 'Strength Activity Time', str.strengthTime!));
    if (str.steps != null) metrics.add(_Metric(Icons.directions_walk, 'Steps', str.steps!));

    return GlassCard(
      padding: const EdgeInsets.all(16),
      radius: 14,
      child: Column(
        children: metrics.map((m) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Row(
            children: [
              Icon(m.icon, color: WhoopTheme.textSecondary, size: 16),
              const SizedBox(width: 10),
              Expanded(child: Text(m.label, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
              Text(m.value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w700)),
            ],
          ),
        )).toList(),
      ),
    );
  }

  Widget _buildActivityCard(Map<String, dynamic> activity) {
    final name = activity['name'] as String;
    final scoreStr = activity['score']?.toString() ?? '';
    final time = activity['time'] as String;
    final id = activity['id']?.toString() ?? '';

    final scoreNum = double.tryParse(scoreStr.replaceAll(RegExp(r'[^0-9.]'), '')) ?? 0;
    Color badgeColor;
    if (scoreNum < 7) badgeColor = WhoopTheme.sleepBlue;
    else if (scoreNum < 14) badgeColor = WhoopTheme.warning;
    else badgeColor = WhoopTheme.error;

    return GestureDetector(
      onTap: id.isNotEmpty ? () => context.push('/activity/$id') : null,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: GlassCard(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          radius: 14,
          child: Row(
            children: [
              Container(
                width: 36, height: 36,
                decoration: BoxDecoration(color: WhoopTheme.divider, borderRadius: BorderRadius.circular(8)),
                child: const Icon(Icons.fitness_center, color: WhoopTheme.textSecondary, size: 18),
              ),
              const SizedBox(width: 12),
              if (scoreStr.isNotEmpty)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(color: badgeColor.withValues(alpha: 0.2), borderRadius: BorderRadius.circular(6)),
                  child: Text(scoreStr, style: TextStyle(color: badgeColor, fontSize: 13, fontWeight: FontWeight.w700)),
                ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(name, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w500), overflow: TextOverflow.ellipsis),
                    if (time.isNotEmpty)
                      Text(time, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right, color: WhoopTheme.textSecondary, size: 18),
            ],
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
          continue;
        }

        if (type == 'GRAPHING_CARD' && content['graph'] != null) {
          final graph = content['graph'] as Map<String, dynamic>;
          final plots = graph['plots'] as List? ?? [];
          if (plots.isEmpty) continue;

          final plot = plots[0]['plot'] as Map<String, dynamic>? ?? {};
          final barGroups = plot['bar_groups'] as List?;

          if (barGroups != null && barGroups.isNotEmpty) {
            widgets.add(TrendBarChart(title: title, barGroups: barGroups));
            widgets.add(const SizedBox(height: 12));
          }
        }
      }
    }

    return widgets;
  }
}

class _Metric {
  final IconData icon;
  final String label;
  final String value;
  _Metric(this.icon, this.label, this.value);
}
