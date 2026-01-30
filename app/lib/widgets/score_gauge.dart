import 'dart:math';
import 'package:flutter/material.dart';

import '../core/theme.dart';

class ScoreGauge extends StatelessWidget {
  final double score;
  final double maxScore;
  final String label;
  final Color color;
  final double size;
  final VoidCallback? onTap;

  const ScoreGauge({
    super.key,
    required this.score,
    required this.maxScore,
    required this.label,
    required this.color,
    this.size = 100,
    this.onTap,
  });

  String get _scoreText {
    if (maxScore == 21) return score.toStringAsFixed(1);
    return '${score.round()}';
  }

  String get _suffix {
    if (maxScore == 21) return '';
    return '%';
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: size,
        height: size + 30,
        child: Column(
          children: [
            SizedBox(
              width: size,
              height: size,
              child: CustomPaint(
                painter: _GaugePainter(
                  progress: (score / maxScore).clamp(0.0, 1.0),
                  color: color,
                  trackColor: WhoopTheme.cardBorder,
                  strokeWidth: size * 0.09,
                ),
                child: Center(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.baseline,
                    textBaseline: TextBaseline.alphabetic,
                    children: [
                      Text(
                        _scoreText,
                        style: TextStyle(
                          color: WhoopTheme.textPrimary,
                          fontSize: size * 0.28,
                          fontWeight: FontWeight.w700,
                          height: 1,
                        ),
                      ),
                      if (_suffix.isNotEmpty)
                        Text(
                          _suffix,
                          style: TextStyle(
                            color: WhoopTheme.textSecondary,
                            fontSize: size * 0.14,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
            const SizedBox(height: 6),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  label,
                  style: const TextStyle(
                    color: WhoopTheme.textSecondary,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                if (onTap != null) ...[
                  const SizedBox(width: 2),
                  const Icon(
                    Icons.chevron_right,
                    color: WhoopTheme.textSecondary,
                    size: 14,
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _GaugePainter extends CustomPainter {
  final double progress;
  final Color color;
  final Color trackColor;
  final double strokeWidth;

  _GaugePainter({
    required this.progress,
    required this.color,
    required this.trackColor,
    required this.strokeWidth,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = min(size.width, size.height) / 2 - strokeWidth;
    const startAngle = 2.356; // 135 degrees
    const sweepTotal = 4.712; // 270 degrees

    // Track
    final trackPaint = Paint()
      ..color = trackColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round;

    canvas.drawArc(
      Rect.fromCircle(center: center, radius: radius),
      startAngle,
      sweepTotal,
      false,
      trackPaint,
    );

    // Progress
    if (progress > 0) {
      final progressPaint = Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..strokeCap = StrokeCap.round;

      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        startAngle,
        sweepTotal * progress,
        false,
        progressPaint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _GaugePainter oldDelegate) =>
      oldDelegate.progress != progress ||
      oldDelegate.color != color ||
      oldDelegate.strokeWidth != strokeWidth;
}
