import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/recovery.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';
import '../widgets/score_gauge.dart';
import '../widgets/trend_bar_chart.dart';

class RecoveryDetailScreen extends StatefulWidget {
  final String date;
  const RecoveryDetailScreen({super.key, required this.date});

  @override
  State<RecoveryDetailScreen> createState() => _RecoveryDetailScreenState();
}

class _RecoveryDetailScreenState extends State<RecoveryDetailScreen> {
  Recovery? _recovery;
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
      final data = await api.getDeepDive('recovery', widget.date);
      _recovery = Recovery.fromDeepDive(data);
    } catch (e) {
      debugPrint('Recovery fetch failed: $e');
    }

    try {
      _trends = await api.getDeepDiveTrends('recovery', widget.date);
    } catch (e) {
      debugPrint('Recovery trends failed: $e');
    }

    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Recovery', style: TextStyle(fontWeight: FontWeight.bold)),
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
    final rec = _recovery ?? const Recovery();
    final color = WhoopTheme.recoveryColor(rec.score);

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // Score gauge
        Center(
          child: ScoreGauge(
            score: rec.score,
            maxScore: 100,
            label: 'Recovery',
            color: color,
            size: 140,
          ),
        ),
        const SizedBox(height: 24),

        // Contributors
        _buildContributors(rec, color),
        const SizedBox(height: 16),

        // Summary text from raw data
        ..._buildInsights(),

        // Weekly Trends
        ..._buildTrends(),
        const SizedBox(height: 24),
      ],
    );
  }

  Widget _buildContributors(Recovery rec, Color color) {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      radius: 14,
      child: Column(
        children: [
          _contributorRow('Heart Rate Variability', rec.hrvMs > 0 ? '${rec.hrvMs.round()}' : '--', color),
          _contributorRow('Resting Heart Rate', rec.rhr > 0 ? '${rec.rhr}' : '--', null),
          _contributorRow('Respiratory Rate', rec.respiratoryRate > 0 ? rec.respiratoryRate.toStringAsFixed(1) : '--', null),
          if (rec.sleepPerformance != null)
            _contributorRow('Sleep Performance', rec.sleepPerformance!, null),
        ],
      ),
    );
  }

  Widget _contributorRow(String label, String value, Color? statusColor) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: [
          Icon(Icons.favorite, color: statusColor ?? WhoopTheme.textSecondary, size: 16),
          const SizedBox(width: 10),
          Expanded(child: Text(label, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
          Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }

  List<Widget> _buildInsights() {
    // Extract insights from ARCH_MINI_RECOVERY_IMPACTS if present
    if (_recovery == null) return [];
    final raw = _recovery!.raw;
    final sections = raw['sections'] as List? ?? [];
    for (final sec in sections) {
      final items = (sec as Map)['items'] as List? ?? [];
      for (final item in items) {
        final type = (item as Map)['type'] as String? ?? '';
        if (type.contains('IMPACTS') || type.contains('INSIGHT')) {
          // Could have insight text
        }
      }
    }
    return [];
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
          final segments = plot['segments'] as List?;

          if (barGroups != null && barGroups.isNotEmpty) {
            widgets.add(TrendBarChart(title: title, barGroups: barGroups));
            widgets.add(const SizedBox(height: 12));
          } else if (segments != null && segments.isNotEmpty) {
            final points = segments[0]['points'] as List? ?? [];
            widgets.add(TrendLineChart(title: title, points: points, lineColor: WhoopTheme.sleepBlue));
            widgets.add(const SizedBox(height: 12));
          }
        }
      }
    }

    return widgets;
  }
}
