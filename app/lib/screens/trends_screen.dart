import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';
import '../widgets/hr_chart.dart';

class TrendsScreen extends StatefulWidget {
  const TrendsScreen({super.key});

  @override
  State<TrendsScreen> createState() => _TrendsScreenState();
}

class _TrendsScreenState extends State<TrendsScreen> {
  int _days = 30;
  List<_DayData> _data = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = context.read<ApiService>();
      String? nextToken;
      final allRecords = <Map<String, dynamic>>[];
      var pages = 0;

      do {
        final res = await api.getCycles(limit: 25, nextToken: nextToken);
        final records = res['records'] as List<dynamic>? ?? [];
        for (final r in records) {
          allRecords.add(r as Map<String, dynamic>);
        }
        nextToken = res['next_token'] as String?;
        pages++;
      } while (nextToken != null && allRecords.length < _days + 5 && pages < 8);

      final parsed = <_DayData>[];
      for (final r in allRecords) {
        final start = r['start'] as String?;
        final score = r['score'] as Map<String, dynamic>?;
        if (start == null || score == null) continue;
        final date = DateTime.tryParse(start);
        if (date == null) continue;

        parsed.add(_DayData(
          date: date,
          strain: (score['strain'] as num?)?.toDouble() ?? 0,
          kilojoules: (score['kilojoule'] as num?)?.toDouble() ?? 0,
          avgHr: (score['average_heart_rate'] as num?)?.toDouble() ?? 0,
          maxHr: (score['max_heart_rate'] as num?)?.toDouble() ?? 0,
        ));
      }

      parsed.sort((a, b) => a.date.compareTo(b.date));
      final cutoff = DateTime.now().subtract(Duration(days: _days));
      final filtered = parsed.where((d) => d.date.isAfter(cutoff)).toList();

      if (mounted) {
        setState(() {
          _data = filtered;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  List<FlSpot> _spots(double Function(_DayData) selector) {
    return _data
        .asMap()
        .entries
        .map((e) => FlSpot(e.key.toDouble(), selector(e.value)))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Trends',
            style: TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: SafeArea(
        child: _loading
            ? const Center(
                child: CircularProgressIndicator(color: WhoopTheme.primary))
            : _error != null
                ? _buildError()
                : RefreshIndicator(
                    color: WhoopTheme.primary,
                    onRefresh: _fetch,
                    child: _buildContent(),
                  ),
      ),
    );
  }

  Widget _buildError() {
    return Center(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        const Icon(Icons.cloud_off, color: WhoopTheme.textSecondary, size: 48),
        const SizedBox(height: 16),
        Text(_error!,
            style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: _fetch,
          icon: const Icon(Icons.refresh),
          label: const Text('Retry'),
          style: OutlinedButton.styleFrom(
            foregroundColor: WhoopTheme.primary,
            side: const BorderSide(color: WhoopTheme.primary),
          ),
        ),
      ]),
    );
  }

  Widget _buildContent() {
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(20),
      children: [
        // Period selector
        GlassCard(
          padding: const EdgeInsets.all(4),
          radius: 12,
          child: Row(
            children: [7, 30, 90].map((d) {
              final selected = _days == d;
              return Expanded(
                child: GestureDetector(
                  onTap: () {
                    if (_days != d) {
                      _days = d;
                      _fetch();
                    }
                  },
                  child: Container(
                    padding: const EdgeInsets.symmetric(vertical: 10),
                    decoration: BoxDecoration(
                      color: selected
                          ? WhoopTheme.primary
                          : Colors.transparent,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Center(
                      child: Text('${d}d',
                          style: TextStyle(
                            color: selected
                                ? Colors.black
                                : WhoopTheme.textSecondary,
                            fontWeight: FontWeight.w600,
                            fontSize: 14,
                          )),
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
        ),
        const SizedBox(height: 24),

        if (_data.isEmpty)
          const Padding(
            padding: EdgeInsets.all(48),
            child: Center(
                child: Text('No data for this period',
                    style: TextStyle(color: WhoopTheme.textSecondary))),
          )
        else ...[
          HrChart(
            title: 'Strain',
            data: _spots((d) => d.strain),
            lineColor: const Color(0xFF5B8DEF),
            minY: 0,
            maxY: 21,
          ),
          const SizedBox(height: 16),
          HrChart(
            title: 'Avg Heart Rate',
            data: _spots((d) => d.avgHr),
            lineColor: const Color(0xFFFF4444),
            minY: 40,
          ),
          const SizedBox(height: 16),
          HrChart(
            title: 'Calories (kJ)',
            data: _spots((d) => d.kilojoules),
            lineColor: const Color(0xFFFFBE0B),
            minY: 0,
          ),

          // Summary stats
          const SizedBox(height: 24),
          GlassCard(
            padding: const EdgeInsets.all(16),
            radius: 16,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                _stat('Avg Strain',
                    (_data.map((d) => d.strain).reduce((a, b) => a + b) / _data.length).toStringAsFixed(1)),
                _stat('Avg HR',
                    '${(_data.map((d) => d.avgHr).reduce((a, b) => a + b) / _data.length).round()}'),
                _stat('Max HR',
                    '${_data.map((d) => d.maxHr).reduce((a, b) => a > b ? a : b).round()}'),
              ],
            ),
          ),
        ],
      ],
    );
  }

  Widget _stat(String label, String value) {
    return Column(children: [
      Text(value,
          style: const TextStyle(
              color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.bold)),
      const SizedBox(height: 4),
      Text(label,
          style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
    ]);
  }
}

class _DayData {
  final DateTime date;
  final double strain;
  final double kilojoules;
  final double avgHr;
  final double maxHr;
  _DayData({
    required this.date,
    required this.strain,
    required this.kilojoules,
    required this.avgHr,
    required this.maxHr,
  });
}
