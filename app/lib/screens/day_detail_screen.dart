import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/recovery.dart';
import '../models/sleep.dart';
import '../models/strain.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';
import '../widgets/score_gauge.dart';
import '../widgets/metric_card.dart';

class DayDetailScreen extends StatefulWidget {
  final String date;
  const DayDetailScreen({super.key, required this.date});

  @override
  State<DayDetailScreen> createState() => _DayDetailScreenState();
}

class _DayDetailScreenState extends State<DayDetailScreen> {
  Recovery? _recovery;
  Sleep? _sleep;
  Strain? _strain;
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
      Recovery? rec;
      Sleep? slp;
      Strain? str;

      try {
        final data = await api.getDeepDive('recovery', widget.date);
        rec = Recovery.fromDeepDive(data);
        debugPrint('DayDetail Recovery ${widget.date}: score=${rec.score}, hrv=${rec.hrvMs}, rhr=${rec.rhr}');
      } catch (e) {
        debugPrint('DayDetail Recovery fetch failed for ${widget.date}: $e');
      }

      try {
        final data = await api.getDeepDive('sleep', widget.date);
        slp = Sleep.fromDeepDive(data);
        debugPrint('DayDetail Sleep ${widget.date}: score=${slp.score}');
      } catch (e) {
        debugPrint('DayDetail Sleep fetch failed for ${widget.date}: $e');
      }

      try {
        final data = await api.getDeepDive('strain', widget.date);
        str = Strain.fromDeepDive(data);
        debugPrint('DayDetail Strain ${widget.date}: score=${str.score}');
      } catch (e) {
        debugPrint('DayDetail Strain fetch failed for ${widget.date}: $e');
      }

      if (mounted) {
        setState(() {
          _recovery = rec;
          _sleep = slp;
          _strain = str;
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

  Color _recoveryColor(double s) => WhoopTheme.recoveryColor(s);

  @override
  Widget build(BuildContext context) {
    final dateObj = DateTime.tryParse(widget.date);
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: Text(
          dateObj != null ? DateFormat('MMM d, yyyy').format(dateObj) : widget.date,
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        elevation: 0,
      ),
      body: SafeArea(
        child: _loading
            ? const Center(
                child: CircularProgressIndicator(color: WhoopTheme.primary))
            : _error != null
                ? _buildError()
                : _buildContent(),
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
    final rec = _recovery ?? const Recovery();
    final slp = _sleep ?? const Sleep();
    final str = _strain ?? const Strain();

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        // Gauges
        GlassCard(
          padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              ScoreGauge(
                  score: rec.score,
                  maxScore: 100,
                  label: 'Recovery',
                  color: _recoveryColor(rec.score)),
              ScoreGauge(
                  score: str.score,
                  maxScore: 21,
                  label: 'Strain',
                  color: WhoopTheme.strainAmber),
              ScoreGauge(
                  score: slp.score,
                  maxScore: 100,
                  label: 'Sleep',
                  color: WhoopTheme.sleepBlue),
            ],
          ),
        ),
        const SizedBox(height: 24),

        // Recovery contributors
        _sectionTitle('Recovery'),
        const SizedBox(height: 12),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 1.6,
          children: [
            MetricCard(
                title: 'HRV',
                value: rec.hrvMs > 0 ? '${rec.hrvMs.round()}' : '--',
                unit: 'ms',
                statusColor: _recoveryColor(rec.score)),
            MetricCard(
                title: 'Resting HR',
                value: rec.rhr > 0 ? '${rec.rhr}' : '--',
                unit: 'bpm'),
            MetricCard(
                title: 'Resp Rate',
                value: rec.respiratoryRate > 0
                    ? rec.respiratoryRate.toStringAsFixed(1)
                    : '--',
                unit: 'rpm'),
            if (rec.sleepPerformance != null)
              MetricCard(
                  title: 'Sleep Perf',
                  value: rec.sleepPerformance!,
                  unit: ''),
          ],
        ),
        const SizedBox(height: 24),

        // Sleep contributors
        _sectionTitle('Sleep'),
        const SizedBox(height: 12),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 1.6,
          children: [
            if (slp.hoursVsNeeded != null)
              MetricCard(title: 'Hours vs Needed', value: slp.hoursVsNeeded!, unit: ''),
            if (slp.consistency != null)
              MetricCard(title: 'Consistency', value: slp.consistency!, unit: ''),
            if (slp.efficiency != null)
              MetricCard(title: 'Efficiency', value: slp.efficiency!, unit: ''),
            if (slp.sleepStress != null)
              MetricCard(title: 'Sleep Stress', value: slp.sleepStress!, unit: ''),
          ],
        ),
        const SizedBox(height: 24),

        // Strain contributors
        _sectionTitle('Strain'),
        const SizedBox(height: 12),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 1.6,
          children: [
            if (str.hrZones13 != null)
              MetricCard(title: 'HR Zones 1-3', value: str.hrZones13!, unit: ''),
            if (str.hrZones45 != null)
              MetricCard(title: 'HR Zones 4-5', value: str.hrZones45!, unit: ''),
            if (str.steps != null)
              MetricCard(title: 'Steps', value: str.steps!, unit: ''),
            if (str.strengthTime != null)
              MetricCard(title: 'Strength', value: str.strengthTime!, unit: ''),
          ],
        ),
      ],
    );
  }

  Widget _sectionTitle(String text) => Text(text,
      style: const TextStyle(
          color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600));
}
