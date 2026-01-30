import 'dart:math';
import 'package:flutter/material.dart';

import '../core/theme.dart';

class ActivityRings extends StatelessWidget {
  final double steps;
  final double stepsGoal;
  final double calories;
  final double caloriesGoal;
  final double strain;
  final double strainMax;

  const ActivityRings({
    super.key,
    required this.steps,
    this.stepsGoal = 10000,
    required this.calories,
    this.caloriesGoal = 2000,
    required this.strain,
    this.strainMax = 21,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: WhoopTheme.cardDecoration(),
      child: Row(
        children: [
          // Left side: metrics
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _metricRow(Icons.directions_walk, WhoopTheme.stepsBlue, _formatNumber(steps.round()), 'STEPS'),
                const SizedBox(height: 16),
                _metricRow(Icons.local_fire_department, WhoopTheme.caloriesOrange, '${calories.round()}', 'CALORIES'),
                const SizedBox(height: 16),
                _metricRow(Icons.flash_on, WhoopTheme.exertionGold, strain.toStringAsFixed(1), 'STRAIN'),
              ],
            ),
          ),
          // Right side: rings
          SizedBox(
            width: 140,
            height: 140,
            child: CustomPaint(
              painter: _RingsPainter(
                stepsProgress: (steps / stepsGoal).clamp(0.0, 1.0),
                caloriesProgress: (calories / caloriesGoal).clamp(0.0, 1.0),
                strainProgress: (strain / strainMax).clamp(0.0, 1.0),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _metricRow(IconData icon, Color color, String value, String label) {
    return Row(
      children: [
        Container(
          width: 32,
          height: 32,
          decoration: BoxDecoration(
            color: color.withOpacity(0.12),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(icon, color: color, size: 18),
        ),
        const SizedBox(width: 12),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 20, fontWeight: FontWeight.w700)),
            Text(label, style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          ],
        ),
      ],
    );
  }

  String _formatNumber(int n) {
    if (n >= 1000) {
      return '${(n / 1000).toStringAsFixed(n % 1000 == 0 ? 0 : 1)}k';
    }
    return '$n';
  }
}

class _RingsPainter extends CustomPainter {
  final double stepsProgress;
  final double caloriesProgress;
  final double strainProgress;

  _RingsPainter({
    required this.stepsProgress,
    required this.caloriesProgress,
    required this.strainProgress,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    const strokeWidth = 12.0;
    const gap = 6.0;
    final outerRadius = min(size.width, size.height) / 2 - strokeWidth / 2;
    final middleRadius = outerRadius - strokeWidth - gap;
    final innerRadius = middleRadius - strokeWidth - gap;

    // Draw three rings
    _drawRing(canvas, center, outerRadius, strokeWidth, stepsProgress, WhoopTheme.stepsBlue);
    _drawRing(canvas, center, middleRadius, strokeWidth, caloriesProgress, WhoopTheme.caloriesOrange);
    _drawRing(canvas, center, innerRadius, strokeWidth, strainProgress, WhoopTheme.exertionGold);
  }

  void _drawRing(Canvas canvas, Offset center, double radius, double strokeWidth, double progress, Color color) {
    // Track
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      -pi / 2,
      2 * pi,
      false,
      Paint()
        ..color = color.withOpacity(0.12)
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..strokeCap = StrokeCap.round,
    );

    // Progress
    if (progress > 0) {
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        -pi / 2,
        2 * pi * progress,
        false,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = strokeWidth
          ..strokeCap = StrokeCap.round,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _RingsPainter old) =>
      old.stepsProgress != stepsProgress ||
      old.caloriesProgress != caloriesProgress ||
      old.strainProgress != strainProgress;
}
