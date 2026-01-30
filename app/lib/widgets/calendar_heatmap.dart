import 'package:flutter/material.dart';
import '../core/theme.dart';

class CalendarHeatmap extends StatelessWidget {
  final Map<DateTime, double> scores;
  final ValueChanged<DateTime>? onDayTap;
  final DateTime month;
  final double maxScore;

  const CalendarHeatmap({
    super.key,
    required this.scores,
    required this.month,
    this.onDayTap,
    this.maxScore = 21,
  });

  static const _dayLabels = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

  Color _colorForScore(double? score) {
    if (score == null) return WhoopTheme.divider.withValues(alpha: 0.3);
    final pct = (score / maxScore).clamp(0.0, 1.0);
    if (pct < 0.33) return const Color(0xFF44D62C).withValues(alpha: 0.2 + pct);
    if (pct < 0.66) return const Color(0xFFFFBE0B).withValues(alpha: 0.5 + pct * 0.5);
    return const Color(0xFFFF4444).withValues(alpha: 0.6 + pct * 0.4);
  }

  @override
  Widget build(BuildContext context) {
    final firstDay = DateTime(month.year, month.month, 1);
    final daysInMonth = DateTime(month.year, month.month + 1, 0).day;
    final startWeekday = firstDay.weekday;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: _dayLabels
              .map((d) => Expanded(
                    child: Center(
                      child: Text(d,
                          style: const TextStyle(
                              color: WhoopTheme.textSecondary,
                              fontSize: 11,
                              fontWeight: FontWeight.w600)),
                    ),
                  ))
              .toList(),
        ),
        const SizedBox(height: 6),
        ...List.generate(_rowCount(startWeekday, daysInMonth), (row) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              children: List.generate(7, (col) {
                final dayNum = row * 7 + col - (startWeekday - 2);
                if (dayNum < 1 || dayNum > daysInMonth) {
                  return const Expanded(child: SizedBox(height: 32));
                }
                final date = DateTime(month.year, month.month, dayNum);
                final score = _findScore(date);
                final hasData = score != null;

                return Expanded(
                  child: GestureDetector(
                    onTap: onDayTap != null ? () => onDayTap!(date) : null,
                    child: Container(
                      height: 32,
                      margin: const EdgeInsets.symmetric(horizontal: 2),
                      decoration: BoxDecoration(
                        color: _colorForScore(score),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Center(
                        child: Text(
                          '$dayNum',
                          style: TextStyle(
                            color: hasData
                                ? WhoopTheme.textPrimary
                                : WhoopTheme.textSecondary,
                            fontSize: 11,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                    ),
                  ),
                );
              }),
            ),
          );
        }),
      ],
    );
  }

  double? _findScore(DateTime date) {
    for (final entry in scores.entries) {
      final d = entry.key;
      if (d.year == date.year && d.month == date.month && d.day == date.day) {
        return entry.value;
      }
    }
    return null;
  }

  int _rowCount(int startWeekday, int daysInMonth) {
    final totalSlots = (startWeekday - 1) + daysInMonth;
    return (totalSlots / 7).ceil();
  }
}
