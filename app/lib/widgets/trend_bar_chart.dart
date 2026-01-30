import 'package:flutter/material.dart';

import '../core/theme.dart';
import 'glass_card.dart';

/// Renders a weekly bar chart from the Whoop API bar_groups format.
class TrendBarChart extends StatelessWidget {
  final String title;
  final List<dynamic> barGroups;
  final List<String> xLabels;
  final double height;

  const TrendBarChart({
    super.key,
    required this.title,
    required this.barGroups,
    this.xLabels = const ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'],
    this.height = 140,
  });

  Color _styleColor(String style) {
    final s = style.toUpperCase();
    if (s.contains('RECOVERY_HIGH') || s.contains('GREEN')) return WhoopTheme.recoveryGreen;
    if (s.contains('RECOVERY_MEDIUM') || s.contains('YELLOW')) return WhoopTheme.recoveryYellow;
    if (s.contains('RECOVERY_LOW') || s.contains('RED')) return WhoopTheme.recoveryRed;
    if (s.contains('STRAIN')) return WhoopTheme.sleepBlue;
    if (s.contains('SLEEP')) return WhoopTheme.sleepBlue;
    if (s.contains('ZONE_1') || s.contains('ZONE1')) return const Color(0xFF9E9E9E);
    if (s.contains('ZONE_2') || s.contains('ZONE2')) return WhoopTheme.sleepBlue;
    if (s.contains('ZONE_3') || s.contains('ZONE3')) return WhoopTheme.primary;
    if (s.contains('ZONE_4') || s.contains('ZONE4')) return WhoopTheme.warning;
    if (s.contains('ZONE_5') || s.contains('ZONE5')) return WhoopTheme.error;
    if (s.contains('HIGH') || s.contains('HOCH')) return WhoopTheme.warning;
    if (s.contains('MEDIUM') || s.contains('MITTEL')) return WhoopTheme.primary;
    if (s.contains('LOW') || s.contains('NIEDRIG')) return WhoopTheme.sleepBlue;
    if (s.contains('SWS') || s.contains('DEEP')) return const Color(0xFF9B59B6);
    if (s.contains('REM')) return const Color(0xFFE090E0);
    return WhoopTheme.sleepBlue;
  }

  @override
  Widget build(BuildContext context) {
    if (barGroups.isEmpty) return const SizedBox.shrink();

    return GlassCard(
      padding: const EdgeInsets.all(16),
      radius: 14,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(title,
                    style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
              ),
              const Icon(Icons.chevron_right, color: WhoopTheme.textSecondary, size: 16),
            ],
          ),
          const SizedBox(height: 16),
          SizedBox(
            height: height,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: List.generate(barGroups.length, (i) {
                final group = barGroups[i] as Map<String, dynamic>;
                final bars = group['bars'] as List? ?? [];
                final topLabel = group['top_label'] as Map<String, dynamic>?;
                final labelText = topLabel?['label'] as String? ?? '';
                final labelStyle = topLabel?['label_style'] as String? ?? '';

                return Expanded(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 3),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        if (labelText.isNotEmpty)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 4),
                            child: Text(
                              labelText,
                              style: TextStyle(
                                color: _styleColor(labelStyle.isNotEmpty ? labelStyle : (bars.isNotEmpty ? bars[0]['style'] as String? ?? '' : '')),
                                fontSize: 10,
                                fontWeight: FontWeight.w600,
                              ),
                              textAlign: TextAlign.center,
                            ),
                          ),
                        // Stacked bars
                        ...bars.reversed.map<Widget>((bar) {
                          final barHeight = ((bar['height'] as num?)?.toDouble() ?? 0) * (height - 30);
                          final style = bar['style'] as String? ?? '';
                          return Container(
                            width: double.infinity,
                            height: barHeight.clamp(0, height - 30),
                            decoration: BoxDecoration(
                              color: _styleColor(style),
                              borderRadius: bars.indexOf(bar) == 0
                                  ? const BorderRadius.vertical(top: Radius.circular(3))
                                  : null,
                            ),
                          );
                        }),
                      ],
                    ),
                  ),
                );
              }),
            ),
          ),
          const SizedBox(height: 8),
          // X-axis labels
          Row(
            children: List.generate(barGroups.length, (i) {
              final group = barGroups[i] as Map<String, dynamic>;
              final bottomLabel = group['bottom_label'] as Map<String, dynamic>?;
              final label = bottomLabel?['label'] as String? ?? (i < xLabels.length ? xLabels[i] : '');
              return Expanded(
                child: Text(
                  label,
                  style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10),
                  textAlign: TextAlign.center,
                ),
              );
            }),
          ),
        ],
      ),
    );
  }
}

/// Renders a weekly line chart from Whoop API segments/points format.
class TrendLineChart extends StatelessWidget {
  final String title;
  final List<dynamic> points;
  final List<String> xLabels;
  final Color lineColor;
  final double height;

  const TrendLineChart({
    super.key,
    required this.title,
    required this.points,
    this.xLabels = const ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'],
    this.lineColor = WhoopTheme.sleepBlue,
    this.height = 120,
  });

  @override
  Widget build(BuildContext context) {
    if (points.isEmpty) return const SizedBox.shrink();

    return GlassCard(
      padding: const EdgeInsets.all(16),
      radius: 14,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(title,
                    style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
              ),
              const Icon(Icons.chevron_right, color: WhoopTheme.textSecondary, size: 16),
            ],
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: height,
            child: CustomPaint(
              size: Size(double.infinity, height),
              painter: _LineChartPainter(points, lineColor),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: List.generate(points.length.clamp(0, xLabels.length), (i) {
              return Expanded(
                child: Text(
                  i < xLabels.length ? xLabels[i] : '',
                  style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 10),
                  textAlign: TextAlign.center,
                ),
              );
            }),
          ),
        ],
      ),
    );
  }
}

class _LineChartPainter extends CustomPainter {
  final List<dynamic> points;
  final Color lineColor;

  _LineChartPainter(this.points, this.lineColor);

  @override
  void paint(Canvas canvas, Size size) {
    if (points.length < 2) return;

    final paint = Paint()
      ..color = lineColor
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final dotPaint = Paint()..color = lineColor;

    final path = Path();
    final positions = <Offset>[];

    for (var i = 0; i < points.length; i++) {
      final p = points[i] as Map<String, dynamic>;
      final x = (p['position_x'] as num?)?.toDouble() ?? (i / (points.length - 1));
      final y = (p['position_y'] as num?)?.toDouble() ?? 0;
      final px = x * size.width;
      final py = (1 - y) * size.height;
      positions.add(Offset(px, py));
      if (i == 0) {
        path.moveTo(px, py);
      } else {
        path.lineTo(px, py);
      }
    }

    canvas.drawPath(path, paint);

    // Draw dots and labels
    final labelStyle = TextStyle(color: lineColor, fontSize: 10, fontWeight: FontWeight.w600);
    for (var i = 0; i < positions.length; i++) {
      canvas.drawCircle(positions[i], 3, dotPaint);

      // Draw value label
      final p = points[i] as Map<String, dynamic>;
      final details = p['data_scrubber_details'] as Map<String, dynamic>?;
      final primary = details?['primary_content'] as Map<String, dynamic>?;
      final label = primary?['primary_display'] as String? ?? '';
      if (label.isNotEmpty) {
        final tp = TextPainter(
          text: TextSpan(text: label, style: labelStyle),
          textDirection: TextDirection.ltr,
        )..layout();
        tp.paint(canvas, Offset(positions[i].dx - tp.width / 2, positions[i].dy - tp.height - 6));
      }
    }
  }

  @override
  bool shouldRepaint(covariant _LineChartPainter old) => old.points != points;
}
