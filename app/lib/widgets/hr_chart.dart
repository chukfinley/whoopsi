import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../core/theme.dart';
import 'glass_card.dart';

class HrChart extends StatelessWidget {
  final List<FlSpot> data;
  final double? minY;
  final double? maxY;
  final Color lineColor;
  final String? title;

  const HrChart({
    super.key,
    required this.data,
    this.minY,
    this.maxY,
    this.lineColor = WhoopTheme.primary,
    this.title,
  });

  @override
  Widget build(BuildContext context) {
    if (data.isEmpty) {
      return const SizedBox(
        height: 200,
        child: Center(
          child: Text('No data', style: TextStyle(color: WhoopTheme.textSecondary)),
        ),
      );
    }

    return GlassCard(
      padding: const EdgeInsets.all(16),
      radius: 16,
      child: SizedBox(
        height: 168,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (title != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(
                  title!,
                  style: const TextStyle(
                    color: WhoopTheme.textSecondary,
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
            Expanded(
              child: LineChart(
                LineChartData(
                  gridData: FlGridData(
                    show: true,
                    drawVerticalLine: false,
                    horizontalInterval: _interval,
                    getDrawingHorizontalLine: (_) => FlLine(
                      color: WhoopTheme.divider,
                      strokeWidth: 1,
                    ),
                  ),
                  titlesData: FlTitlesData(
                    topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    bottomTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                    leftTitles: AxisTitles(
                      sideTitles: SideTitles(
                        showTitles: true,
                        reservedSize: 36,
                        getTitlesWidget: (v, _) => Text(
                          v.toInt().toString(),
                          style: const TextStyle(
                            color: WhoopTheme.textSecondary,
                            fontSize: 10,
                          ),
                        ),
                      ),
                    ),
                  ),
                  borderData: FlBorderData(show: false),
                  minY: minY ?? _dataMinY - 5,
                  maxY: maxY ?? _dataMaxY + 5,
                  lineBarsData: [
                    LineChartBarData(
                      spots: data,
                      isCurved: true,
                      curveSmoothness: 0.3,
                      color: lineColor,
                      barWidth: 2.5,
                      dotData: const FlDotData(show: false),
                      belowBarData: BarAreaData(
                        show: true,
                        color: lineColor.withValues(alpha: 0.1),
                      ),
                    ),
                  ],
                  lineTouchData: LineTouchData(
                    touchTooltipData: LineTouchTooltipData(
                      getTooltipColor: (_) => WhoopTheme.surface,
                      getTooltipItems: (spots) => spots
                          .map((s) => LineTooltipItem(
                                s.y.toStringAsFixed(1),
                                const TextStyle(
                                  color: WhoopTheme.textPrimary,
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                ),
                              ))
                          .toList(),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  double get _dataMinY => data.map((s) => s.y).reduce((a, b) => a < b ? a : b);
  double get _dataMaxY => data.map((s) => s.y).reduce((a, b) => a > b ? a : b);
  double get _interval {
    final range = _dataMaxY - _dataMinY;
    if (range <= 20) return 5;
    if (range <= 50) return 10;
    return 20;
  }
}
