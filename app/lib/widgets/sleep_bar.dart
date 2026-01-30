import 'package:flutter/material.dart';

class SleepBar extends StatelessWidget {
  final double awakeMin;
  final double lightMin;
  final double remMin;
  final double swsMin;

  const SleepBar({
    super.key,
    required this.awakeMin,
    required this.lightMin,
    required this.remMin,
    required this.swsMin,
  });

  static const _awakeColor = Color(0xFFFF4444);
  static const _lightColor = Color(0xFF5B8DEF);
  static const _remColor = Color(0xFF44D62C);
  static const _swsColor = Color(0xFF9B59B6);

  String _fmt(double mins) {
    final h = (mins / 60).floor();
    final m = (mins % 60).round();
    if (h > 0) return '${h}h ${m}m';
    return '${m}m';
  }

  @override
  Widget build(BuildContext context) {
    final total = awakeMin + lightMin + remMin + swsMin;
    if (total == 0) {
      return const SizedBox(
        height: 48,
        child: Center(
          child: Text('No sleep data', style: TextStyle(color: Color(0xFF9E9E9E))),
        ),
      );
    }

    final segments = [
      _Segment('Awake', awakeMin, _awakeColor),
      _Segment('Light', lightMin, _lightColor),
      _Segment('REM', remMin, _remColor),
      _Segment('SWS', swsMin, _swsColor),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: SizedBox(
            height: 24,
            child: Row(
              children: segments
                  .where((s) => s.minutes > 0)
                  .map((s) => Expanded(
                        flex: (s.minutes * 100 / total).round(),
                        child: Container(color: s.color),
                      ))
                  .toList(),
            ),
          ),
        ),
        const SizedBox(height: 10),
        Wrap(
          spacing: 16,
          runSpacing: 6,
          children: segments
              .where((s) => s.minutes > 0)
              .map((s) => Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Container(
                        width: 10,
                        height: 10,
                        decoration: BoxDecoration(
                          color: s.color,
                          borderRadius: BorderRadius.circular(3),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        '${s.label} ${_fmt(s.minutes)}',
                        style: const TextStyle(
                          color: Color(0xFF9E9E9E),
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ))
              .toList(),
        ),
      ],
    );
  }
}

class _Segment {
  final String label;
  final double minutes;
  final Color color;
  _Segment(this.label, this.minutes, this.color);
}
