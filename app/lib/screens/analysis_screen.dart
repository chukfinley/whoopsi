import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/analysis_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class AnalysisScreen extends StatefulWidget {
  const AnalysisScreen({super.key});

  @override
  State<AnalysisScreen> createState() => _AnalysisScreenState();
}

class _AnalysisScreenState extends State<AnalysisScreen> {
  HrvAnalysis? _hrv;
  RecoveryAnalysis? _recovery;
  SleepAnalysis? _sleep;
  List<Insight> _insights = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _analyze();
  }

  Future<void> _analyze() async {
    setState(() => _loading = true);
    final analysis = context.read<AnalysisService>();

    try {
      final hrv = await analysis.analyzeHrv(7);
      final recovery = analysis.analyzeRecovery(7);
      final sleep = analysis.analyzeSleep(7);
      final insights = await analysis.generateInsights();

      if (mounted) {
        setState(() {
          _hrv = hrv;
          _recovery = recovery;
          _sleep = sleep;
          _insights = insights;
          _loading = false;
        });
      }
    } catch (e) {
      debugPrint('Analysis error: $e');
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        title: const Text('Insights', style: TextStyle(fontWeight: FontWeight.bold)),
      ),
      body: SafeArea(
        child: _loading
            ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
            : RefreshIndicator(
                color: WhoopTheme.primary,
                onRefresh: _analyze,
                child: ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    // HRV Analysis
                    if (_hrv != null && _hrv!.sampleCount > 0) ...[
                      _sectionTitle('HRV Analysis'),
                      const SizedBox(height: 8),
                      GlassCard(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                const Icon(Icons.monitor_heart, color: WhoopTheme.sleepBlue, size: 20),
                                const SizedBox(width: 8),
                                const Text('RMSSD', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600)),
                                const Spacer(),
                                Text('${_hrv!.rmssd.round()} ms', style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 24, fontWeight: FontWeight.w700)),
                              ],
                            ),
                            const SizedBox(height: 8),
                            Row(
                              children: [
                                _miniStat('SDNN', '${_hrv!.sdnn.round()} ms'),
                                const SizedBox(width: 16),
                                _miniStat('Samples', '${_hrv!.sampleCount}'),
                              ],
                            ),
                            if (_hrv!.dailyRmssd.length >= 2) ...[
                              const SizedBox(height: 12),
                              SizedBox(
                                height: 60,
                                child: CustomPaint(
                                  size: const Size(double.infinity, 60),
                                  painter: _TrendLinePainter(_hrv!.dailyRmssd, WhoopTheme.sleepBlue),
                                ),
                              ),
                            ],
                          ],
                        ),
                      ),
                      const SizedBox(height: 20),
                    ],

                    // Recovery Trends
                    if (_recovery != null && _recovery!.daysAnalyzed > 0) ...[
                      _sectionTitle('Recovery Trends'),
                      const SizedBox(height: 8),
                      GlassCard(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Text('${_recovery!.avgScore.round()}%',
                                    style: TextStyle(color: WhoopTheme.recoveryColor(_recovery!.avgScore), fontSize: 32, fontWeight: FontWeight.w700)),
                                const SizedBox(width: 12),
                                const Expanded(child: Text('Average Recovery', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 14))),
                              ],
                            ),
                            const SizedBox(height: 16),
                            Row(
                              mainAxisAlignment: MainAxisAlignment.spaceAround,
                              children: [
                                _dayDist(WhoopTheme.recoveryGreen, '${_recovery!.greenDays}', 'Green'),
                                _dayDist(WhoopTheme.recoveryYellow, '${_recovery!.yellowDays}', 'Yellow'),
                                _dayDist(WhoopTheme.recoveryRed, '${_recovery!.redDays}', 'Red'),
                              ],
                            ),
                            if (_recovery!.dailyScores.length >= 2) ...[
                              const SizedBox(height: 12),
                              SizedBox(
                                height: 60,
                                child: CustomPaint(
                                  size: const Size(double.infinity, 60),
                                  painter: _RecoveryBarPainter(_recovery!.dailyScores),
                                ),
                              ),
                            ],
                          ],
                        ),
                      ),
                      const SizedBox(height: 20),
                    ],

                    // Sleep Quality
                    if (_sleep != null && _sleep!.daysAnalyzed > 0) ...[
                      _sectionTitle('Sleep Quality'),
                      const SizedBox(height: 8),
                      GlassCard(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                const Icon(Icons.bedtime, color: WhoopTheme.sleepBlue, size: 20),
                                const SizedBox(width: 8),
                                Text('${_sleep!.avgPerformance.round()}%',
                                    style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 28, fontWeight: FontWeight.w700)),
                                const SizedBox(width: 8),
                                const Text('avg performance', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
                              ],
                            ),
                            const SizedBox(height: 12),
                            Row(
                              children: [
                                _miniStat('Days', '${_sleep!.daysAnalyzed}'),
                                const SizedBox(width: 16),
                                if (_sleep!.bestNight != null) _miniStat('Best', '${_sleep!.bestNight!.round()}%'),
                                if (_sleep!.bestNight != null) const SizedBox(width: 16),
                                if (_sleep!.worstNight != null) _miniStat('Worst', '${_sleep!.worstNight!.round()}%'),
                              ],
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 20),
                    ],

                    // Insights
                    if (_insights.isNotEmpty) ...[
                      _sectionTitle('Insights'),
                      const SizedBox(height: 8),
                      ..._insights.map((insight) => Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: GlassCard(
                          padding: const EdgeInsets.all(16),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _insightIcon(insight.icon),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(insight.title, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 15, fontWeight: FontWeight.w600)),
                                    const SizedBox(height: 4),
                                    Text(insight.body, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                      )),
                    ],

                    // Empty state
                    if ((_hrv == null || _hrv!.sampleCount == 0) && (_recovery == null || _recovery!.daysAnalyzed == 0))
                      GlassCard(
                        padding: const EdgeInsets.all(24),
                        child: Column(
                          children: [
                            const Icon(Icons.insights, color: WhoopTheme.textSecondary, size: 48),
                            const SizedBox(height: 16),
                            const Text('No Analysis Data', style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600)),
                            const SizedBox(height: 8),
                            const Text(
                              'Sync data from your strap and fetch API data from Settings to see HRV analysis, sleep quality, and recovery insights.',
                              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 13),
                              textAlign: TextAlign.center,
                            ),
                          ],
                        ),
                      ),
                    const SizedBox(height: 80),
                  ],
                ),
              ),
      ),
    );
  }

  Widget _sectionTitle(String text) => Text(text,
      style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600));

  Widget _miniStat(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w600)),
        Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
      ],
    );
  }

  Widget _dayDist(Color color, String count, String label) {
    return Column(
      children: [
        Container(width: 12, height: 12, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(height: 4),
        Text(count, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w700)),
        Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
      ],
    );
  }

  Widget _insightIcon(String icon) {
    IconData iconData;
    Color color;
    switch (icon) {
      case 'heart': iconData = Icons.favorite; color = WhoopTheme.primary; break;
      case 'warning': iconData = Icons.warning_amber; color = WhoopTheme.warning; break;
      case 'streak': iconData = Icons.local_fire_department; color = WhoopTheme.primary; break;
      case 'alert': iconData = Icons.error_outline; color = WhoopTheme.error; break;
      case 'avg': iconData = Icons.analytics; color = WhoopTheme.sleepBlue; break;
      default: iconData = Icons.info_outline; color = WhoopTheme.textSecondary; break;
    }
    return Container(
      width: 36, height: 36,
      decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(10)),
      child: Icon(iconData, color: color, size: 18),
    );
  }
}

class _TrendLinePainter extends CustomPainter {
  final List<double> values;
  final Color color;
  _TrendLinePainter(this.values, this.color);

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final minV = values.reduce((a, b) => a < b ? a : b) - 5;
    final maxV = values.reduce((a, b) => a > b ? a : b) + 5;
    final range = maxV - minV;
    if (range <= 0) return;

    final paint = Paint()..color = color..strokeWidth = 2..style = PaintingStyle.stroke..strokeCap = StrokeCap.round;
    final path = Path();
    for (var i = 0; i < values.length; i++) {
      final x = i / (values.length - 1) * size.width;
      final y = (1 - (values[i] - minV) / range) * size.height;
      if (i == 0) path.moveTo(x, y); else path.lineTo(x, y);
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _TrendLinePainter old) => old.values != values;
}

class _RecoveryBarPainter extends CustomPainter {
  final List<double> scores;
  _RecoveryBarPainter(this.scores);

  @override
  void paint(Canvas canvas, Size size) {
    if (scores.isEmpty) return;
    final barWidth = size.width / scores.length - 2;
    for (var i = 0; i < scores.length; i++) {
      final h = (scores[i] / 100) * size.height;
      final color = scores[i] >= 67 ? WhoopTheme.recoveryGreen : scores[i] >= 34 ? WhoopTheme.recoveryYellow : WhoopTheme.recoveryRed;
      final rect = RRect.fromRectAndRadius(
        Rect.fromLTWH(i * (barWidth + 2), size.height - h, barWidth, h),
        const Radius.circular(2),
      );
      canvas.drawRRect(rect, Paint()..color = color);
    }
  }

  @override
  bool shouldRepaint(covariant _RecoveryBarPainter old) => old.scores != scores;
}
