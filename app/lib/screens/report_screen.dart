import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/recovery.dart';
import '../models/sleep.dart';
import '../models/strain.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class ReportScreen extends StatefulWidget {
  const ReportScreen({super.key});

  @override
  State<ReportScreen> createState() => _ReportScreenState();
}

class _ReportScreenState extends State<ReportScreen> {
  bool _loading = true;
  final List<_DayData> _days = [];
  DateTime _weekStart = DateTime.now().subtract(const Duration(days: 6));

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() { _loading = true; });
    final api = context.read<ApiService>();
    final days = <_DayData>[];

    for (var i = 6; i >= 0; i--) {
      final date = DateTime.now().subtract(Duration(days: i));
      final dateStr = DateFormat('yyyy-MM-dd').format(date);
      double rec = 0, slp = 0, str = 0;

      try {
        final data = await api.getDeepDive('recovery', dateStr);
        rec = Recovery.fromDeepDive(data).score;
      } catch (_) {}
      try {
        final data = await api.getDeepDive('sleep', dateStr);
        slp = Sleep.fromDeepDive(data).score;
      } catch (_) {}
      try {
        final data = await api.getDeepDive('strain', dateStr);
        str = Strain.fromDeepDive(data).score;
      } catch (_) {}

      days.add(_DayData(date: date, recovery: rec, sleep: slp, strain: str));
    }

    if (mounted) {
      setState(() {
        _days.clear();
        _days.addAll(days);
        _weekStart = DateTime.now().subtract(const Duration(days: 6));
        _loading = false;
      });
    }
  }

  double get _avgRecovery => _days.isEmpty ? 0 : _days.map((d) => d.recovery).reduce((a, b) => a + b) / _days.length;
  double get _avgSleep => _days.isEmpty ? 0 : _days.map((d) => d.sleep).reduce((a, b) => a + b) / _days.length;
  double get _avgStrain => _days.isEmpty ? 0 : _days.map((d) => d.strain).reduce((a, b) => a + b) / _days.length;

  _DayData? get _bestDay {
    if (_days.isEmpty) return null;
    return _days.reduce((a, b) => a.recovery > b.recovery ? a : b);
  }

  _DayData? get _worstDay {
    if (_days.isEmpty) return null;
    return _days.where((d) => d.recovery > 0).fold<_DayData?>(null,
        (prev, d) => prev == null || d.recovery < prev.recovery ? d : prev);
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Weekly Report', style: TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: SafeArea(
        child: _loading
            ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
            : RefreshIndicator(
                color: WhoopTheme.primary,
                onRefresh: _fetch,
                child: _buildContent(),
              ),
      ),
    );
  }

  Widget _buildContent() {
    final dateRange = '${DateFormat('MMM d').format(_weekStart)} - ${DateFormat('MMM d').format(DateTime.now())}';
    final best = _bestDay;
    final worst = _worstDay;

    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      children: [
        // Header
        Center(
          child: Text(dateRange,
              style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 14, fontWeight: FontWeight.w500)),
        ),
        const SizedBox(height: 20),

        // Averages
        GlassCard(
          padding: const EdgeInsets.all(20),
          child: Row(
            children: [
              _avgCircle('Recovery', _avgRecovery, WhoopTheme.recoveryColor(_avgRecovery), '%'),
              _avgCircle('Sleep', _avgSleep, WhoopTheme.sleepBlue, '%'),
              _avgCircle('Strain', _avgStrain, WhoopTheme.strainAmber, ''),
            ],
          ),
        ),
        const SizedBox(height: 16),

        // Recovery chart
        _buildBarChart('Recovery', _days.map((d) => d.recovery).toList(), 100, WhoopTheme.recoveryGreen),
        const SizedBox(height: 12),

        // Sleep chart
        _buildBarChart('Sleep', _days.map((d) => d.sleep).toList(), 100, WhoopTheme.sleepBlue),
        const SizedBox(height: 12),

        // Strain chart
        _buildBarChart('Strain', _days.map((d) => d.strain).toList(), 21, WhoopTheme.strainAmber),
        const SizedBox(height: 16),

        // Highlights
        if (best != null || worst != null)
          GlassCard(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('HIGHLIGHTS', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                    fontWeight: FontWeight.w600, letterSpacing: 0.5)),
                const SizedBox(height: 12),
                if (best != null)
                  _highlightRow(Icons.arrow_upward, WhoopTheme.recoveryGreen,
                      'Best recovery: ${best.recovery.round()}%', DateFormat('EEEE').format(best.date)),
                if (worst != null) ...[
                  const SizedBox(height: 8),
                  _highlightRow(Icons.arrow_downward, WhoopTheme.error,
                      'Lowest recovery: ${worst.recovery.round()}%', DateFormat('EEEE').format(worst.date)),
                ],
              ],
            ),
          ),
        const SizedBox(height: 80),
      ],
    );
  }

  Widget _avgCircle(String label, double value, Color color, String suffix) {
    final display = suffix == '%' ? '${value.round()}$suffix' : value.toStringAsFixed(1);
    return Expanded(
      child: Column(
        children: [
          Container(
            width: 64, height: 64,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(color: color, width: 3),
            ),
            child: Center(
              child: Text(display,
                  style: TextStyle(color: color, fontSize: 16, fontWeight: FontWeight.w700)),
            ),
          ),
          const SizedBox(height: 8),
          Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
        ],
      ),
    );
  }

  Widget _buildBarChart(String label, List<double> values, double maxY, Color color) {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          SizedBox(
            height: 100,
            child: BarChart(
              BarChartData(
                alignment: BarChartAlignment.spaceAround,
                maxY: maxY,
                barGroups: List.generate(values.length, (i) => BarChartGroupData(
                  x: i,
                  barRods: [
                    BarChartRodData(
                      toY: values[i],
                      color: color,
                      width: 16,
                      borderRadius: const BorderRadius.vertical(top: Radius.circular(4)),
                    ),
                  ],
                )),
                gridData: const FlGridData(show: false),
                borderData: FlBorderData(show: false),
                titlesData: FlTitlesData(
                  topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  leftTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      getTitlesWidget: (v, _) {
                        final idx = v.round();
                        if (idx < 0 || idx >= _days.length) return const SizedBox.shrink();
                        return Text(
                          DateFormat('E').format(_days[idx].date).substring(0, 1),
                          style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11),
                        );
                      },
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _highlightRow(IconData icon, Color color, String text, String day) {
    return Row(
      children: [
        Icon(icon, color: color, size: 18),
        const SizedBox(width: 10),
        Expanded(
          child: Text(text, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 13)),
        ),
        Text(day, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
      ],
    );
  }
}

class _DayData {
  final DateTime date;
  final double recovery;
  final double sleep;
  final double strain;
  const _DayData({required this.date, required this.recovery, required this.sleep, required this.strain});
}
