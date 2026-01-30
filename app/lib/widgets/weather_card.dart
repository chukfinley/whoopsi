import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/weather_service.dart';
import 'glass_card.dart';

class WeatherCard extends StatefulWidget {
  const WeatherCard({super.key});

  @override
  State<WeatherCard> createState() => _WeatherCardState();
}

class _WeatherCardState extends State<WeatherCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    return Consumer<WeatherService>(
      builder: (context, weather, _) {
        if (!weather.hasLocation || weather.current == null) return const SizedBox.shrink();

        final data = weather.current!;
        return GestureDetector(
          onTap: () => setState(() => _expanded = !_expanded),
          child: GlassCard(
            padding: const EdgeInsets.all(14),
            child: Column(
              children: [
                // Compact view
                Row(
                  children: [
                    Icon(_weatherIcon(data.weatherCode), color: _weatherColor(data.weatherCode), size: 28),
                    const SizedBox(width: 12),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '${data.temperature.round()}°C',
                          style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 20, fontWeight: FontWeight.w700),
                        ),
                        Text(
                          data.weatherDescription,
                          style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12),
                        ),
                      ],
                    ),
                    const Spacer(),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        Text(
                          'H:${data.tempMax.round()}° L:${data.tempMin.round()}°',
                          style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12),
                        ),
                        if (weather.city.isNotEmpty)
                          Text(weather.city, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
                      ],
                    ),
                    const SizedBox(width: 8),
                    Icon(
                      _expanded ? Icons.expand_less : Icons.expand_more,
                      color: WhoopTheme.textSecondary,
                      size: 18,
                    ),
                  ],
                ),

                // Training advice
                if (data.trainingAdvice.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      const Icon(Icons.fitness_center, color: WhoopTheme.warning, size: 14),
                      const SizedBox(width: 6),
                      Text(data.trainingAdvice,
                          style: const TextStyle(color: WhoopTheme.warning, fontSize: 12)),
                    ],
                  ),
                ],

                // Expanded: 7-day forecast
                if (_expanded && data.forecast.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  const Divider(color: WhoopTheme.divider, height: 1),
                  const SizedBox(height: 12),
                  ...data.forecast.map((day) => Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Row(
                          children: [
                            SizedBox(
                              width: 40,
                              child: Text(
                                _dayLabel(day.date),
                                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12),
                              ),
                            ),
                            Icon(_weatherIcon(day.weatherCode), color: _weatherColor(day.weatherCode), size: 18),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(day.description,
                                  style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                            ),
                            Text('${day.tempMax.round()}°',
                                style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 12, fontWeight: FontWeight.w600)),
                            const SizedBox(width: 6),
                            Text('${day.tempMin.round()}°',
                                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                          ],
                        ),
                      )),
                ],
              ],
            ),
          ),
        );
      },
    );
  }

  String _dayLabel(DateTime date) {
    final now = DateTime.now();
    if (date.day == now.day && date.month == now.month) return 'Today';
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    return days[date.weekday - 1];
  }

  IconData _weatherIcon(int code) {
    if (code == 0) return Icons.wb_sunny;
    if (code <= 3) return Icons.wb_cloudy;
    if (code <= 48) return Icons.foggy;
    if (code <= 67) return Icons.water_drop;
    if (code <= 77) return Icons.ac_unit;
    if (code <= 86) return Icons.ac_unit;
    if (code >= 95) return Icons.flash_on;
    return Icons.cloud;
  }

  Color _weatherColor(int code) {
    if (code == 0) return WhoopTheme.warning;
    if (code <= 3) return WhoopTheme.textSecondary;
    if (code <= 48) return WhoopTheme.textSecondary;
    if (code <= 67) return WhoopTheme.sleepBlue;
    if (code <= 77) return Colors.white;
    if (code >= 95) return WhoopTheme.warning;
    return WhoopTheme.textSecondary;
  }
}
