import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class CalendarScreen extends StatefulWidget {
  const CalendarScreen({super.key});

  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  // Store recovery + strain + sleep scores per day
  final Map<DateTime, _DayScores> _dayScores = {};
  bool _loading = true;
  String? _error;
  late DateTime _currentMonth;

  @override
  void initState() {
    super.initState();
    _currentMonth = DateTime(DateTime.now().year, DateTime.now().month);
    _loadCachedAndFetch();
  }

  /// Populate calendar scores from existing deep-dive cache files
  void _populateFromDeepDiveCache() {
    final api = context.read<ApiService>();
    // Scan recent 365 days for cached deep-dive data
    final now = DateTime.now();
    for (var d = 0; d < 365; d++) {
      final date = now.subtract(Duration(days: d));
      final dateStr = '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
      final normalized = DateTime(date.year, date.month, date.day);
      if (_dayScores.containsKey(normalized)) continue;

      final cachedRec = api.cache.get<Map<String, dynamic>>('deep_dive:recovery:$dateStr');
      final cachedStr = api.cache.get<Map<String, dynamic>>('deep_dive:strain:$dateStr');
      final cachedSlp = api.cache.get<Map<String, dynamic>>('deep_dive:sleep:$dateStr');
      if (cachedRec == null && cachedStr == null && cachedSlp == null) continue;

      double recovery = 0, strain = 0, sleep = 0;
      if (cachedRec != null) {
        try {
          for (final section in cachedRec['sections'] as List? ?? []) {
            for (final item in (section as Map)['items'] as List? ?? []) {
              if ((item as Map)['type'] == 'SCORE_GAUGE') {
                recovery = (item['content']?['gauge_fill_percentage'] as num?)?.toDouble() ?? 0;
                recovery *= 100;
              }
            }
          }
        } catch (_) {}
      }
      if (cachedStr != null) {
        try {
          for (final section in cachedStr['sections'] as List? ?? []) {
            for (final item in (section as Map)['items'] as List? ?? []) {
              if ((item as Map)['type'] == 'SCORE_GAUGE') {
                final scoreStr = item['content']?['score_display'] as String? ?? '0';
                strain = double.tryParse(scoreStr.replaceAll(RegExp(r'[^0-9.]'), '')) ?? 0;
              }
            }
          }
        } catch (_) {}
      }
      if (cachedSlp != null) {
        try {
          for (final section in cachedSlp['sections'] as List? ?? []) {
            for (final item in (section as Map)['items'] as List? ?? []) {
              if ((item as Map)['type'] == 'SCORE_GAUGE') {
                sleep = (item['content']?['gauge_fill_percentage'] as num?)?.toDouble() ?? 0;
                sleep *= 100;
              }
            }
          }
        } catch (_) {}
      }

      if (recovery > 0 || strain > 0 || sleep > 0) {
        _dayScores[normalized] = _DayScores(recovery: recovery, strain: strain, sleep: sleep);
      }
    }
  }

  void _parseCycleRecords(List<dynamic> records) {
    for (final r in records) {
      final cycle = r as Map<String, dynamic>;
      final start = cycle['start'] as String?;
      final score = cycle['score'] as Map<String, dynamic>?;
      if (start != null && score != null) {
        final date = DateTime.parse(start).toLocal();
        final normalized = DateTime(date.year, date.month, date.day);
        // Keep existing recovery if we already have it from deep-dive
        final existing = _dayScores[normalized];
        _dayScores[normalized] = _DayScores(
          recovery: existing?.recovery ?? 0,
          strain: (score['strain'] as num?)?.toDouble() ?? 0,
          sleep: (score['sleep_performance'] as num?)?.toDouble() ?? 0,
        );
      }
    }
  }

  Future<void> _loadCachedAndFetch() async {
    final api = context.read<ApiService>();
    // Load cached calendar scores
    final cached = api.cache.get<Map<String, dynamic>>('calendar_scores');
    if (cached != null) {
      final entries = cached['entries'] as List? ?? [];
      for (final e in entries) {
        final m = e as Map<String, dynamic>;
        final date = DateTime.tryParse(m['date'] as String? ?? '');
        if (date != null) {
          _dayScores[date] = _DayScores(
            recovery: (m['recovery'] as num?)?.toDouble() ?? 0,
            strain: (m['strain'] as num?)?.toDouble() ?? 0,
            sleep: (m['sleep'] as num?)?.toDouble() ?? 0,
          );
        }
      }
      if (_dayScores.isNotEmpty && mounted) {
        setState(() => _loading = false);
      }
    }
    // Also populate from deep-dive cache files
    _populateFromDeepDiveCache();
    if (_dayScores.isNotEmpty && mounted) {
      setState(() => _loading = false);
      _saveCalendarCache();
    }
    _fetchCycles();
  }

  void _saveCalendarCache() {
    final api = context.read<ApiService>();
    final entries = _dayScores.entries.map((e) => {
      'date': e.key.toIso8601String(),
      'recovery': e.value.recovery,
      'strain': e.value.strain,
      'sleep': e.value.sleep,
    }).toList();
    api.cache.setPermanent('calendar_scores', {'entries': entries});
  }

  Future<void> _fetchCycles() async {
    if (_dayScores.isEmpty) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final api = context.read<ApiService>();

      // Fetch cycles (strain + sleep data)
      String? nextToken;
      var pages = 0;
      do {
        final data = await api.getCycles(limit: 25, nextToken: nextToken);
        final records = data['records'] as List<dynamic>? ?? [];
        _parseCycleRecords(records);
        nextToken = data['next_token'] as String?;
        pages++;
      } while (nextToken != null && pages < 12);

      // Fetch recovery scores in bulk from developer/v1/recovery
      String? recToken;
      var recPages = 0;
      do {
        final data = await api.getRecoveryCollection(limit: 25, nextToken: recToken);
        final records = data['records'] as List<dynamic>? ?? [];
        for (final r in records) {
          final rec = r as Map<String, dynamic>;
          final createdAt = rec['created_at'] as String?;
          final score = rec['score'] as Map<String, dynamic>?;
          if (createdAt != null && score != null) {
            final date = DateTime.parse(createdAt).toLocal();
            final normalized = DateTime(date.year, date.month, date.day);
            final recoveryScore = (score['recovery_score'] as num?)?.toDouble() ?? 0;
            final existing = _dayScores[normalized];
            _dayScores[normalized] = _DayScores(
              recovery: recoveryScore,
              strain: existing?.strain ?? 0,
              sleep: existing?.sleep ?? 0,
            );
          }
        }
        recToken = data['next_token'] as String?;
        recPages++;
      } while (recToken != null && recPages < 12);

      _saveCalendarCache();
      if (mounted) setState(() => _loading = false);
    } catch (e) {
      if (mounted) {
        setState(() {
          if (_dayScores.isEmpty) _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  void _changeMonth(int delta) {
    setState(() {
      _currentMonth = DateTime(_currentMonth.year, _currentMonth.month + delta);
    });
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Calendar', style: TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: SafeArea(
        child: _loading
            ? const Center(child: CircularProgressIndicator(color: WhoopTheme.primary))
            : _error != null
                ? _buildError()
                : RefreshIndicator(
                    color: WhoopTheme.primary,
                    backgroundColor: WhoopTheme.surface,
                    onRefresh: _fetchCycles,
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
        Text(_error!, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: _fetchCycles,
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
      padding: const EdgeInsets.symmetric(horizontal: 16),
      children: [
        // Month navigation
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            IconButton(
              onPressed: () => _changeMonth(-1),
              icon: const Icon(Icons.chevron_left, color: WhoopTheme.textPrimary),
            ),
            Text(
              DateFormat('MMMM yyyy').format(_currentMonth),
              style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600),
            ),
            IconButton(
              onPressed: () => _changeMonth(1),
              icon: const Icon(Icons.chevron_right, color: WhoopTheme.textPrimary),
            ),
          ],
        ),
        const SizedBox(height: 8),
        // Legend — recovery colors
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            _legendDot(WhoopTheme.divider, 'No data'),
            const SizedBox(width: 12),
            _legendDot(WhoopTheme.recoveryGreen, 'Green'),
            const SizedBox(width: 12),
            _legendDot(WhoopTheme.recoveryYellow, 'Yellow'),
            const SizedBox(width: 12),
            _legendDot(WhoopTheme.recoveryRed, 'Red'),
          ],
        ),
        const SizedBox(height: 16),
        // Calendar grid
        _buildCalendarGrid(),
        const SizedBox(height: 20),
        // Stats for current month
        _buildMonthStats(),
        const SizedBox(height: 24),
      ],
    );
  }

  Widget _buildCalendarGrid() {
    const dayLabels = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
    final firstDay = DateTime(_currentMonth.year, _currentMonth.month, 1);
    final daysInMonth = DateTime(_currentMonth.year, _currentMonth.month + 1, 0).day;
    final startWeekday = firstDay.weekday; // 1=Mon
    final totalSlots = (startWeekday - 1) + daysInMonth;
    final rows = (totalSlots / 7).ceil();
    final today = DateTime.now();

    return Column(
      children: [
        // Day labels
        Row(
          children: dayLabels.map((d) => Expanded(
            child: Center(
              child: Text(d, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600)),
            ),
          )).toList(),
        ),
        const SizedBox(height: 6),
        // Grid
        ...List.generate(rows, (row) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              children: List.generate(7, (col) {
                final dayNum = row * 7 + col - (startWeekday - 2);
                if (dayNum < 1 || dayNum > daysInMonth) {
                  return const Expanded(child: SizedBox(height: 40));
                }
                final date = DateTime(_currentMonth.year, _currentMonth.month, dayNum);
                final isToday = date.year == today.year && date.month == today.month && date.day == today.day;
                final isFuture = date.isAfter(today);
                final scores = _findScores(date);

                Color bgColor;
                Color textColor;
                if (isFuture) {
                  bgColor = Colors.transparent;
                  textColor = WhoopTheme.textSecondary.withValues(alpha: 0.3);
                } else if (scores == null) {
                  bgColor = WhoopTheme.surface;
                  textColor = WhoopTheme.textSecondary;
                } else {
                  bgColor = WhoopTheme.recoveryColor(scores.recovery).withValues(alpha: 0.25);
                  textColor = WhoopTheme.textPrimary;
                }

                return Expanded(
                  child: GestureDetector(
                    onTap: isFuture ? null : () {
                      final formatted = DateFormat('yyyy-MM-dd').format(date);
                      context.push('/day/$formatted');
                    },
                    child: Container(
                      height: 40,
                      margin: const EdgeInsets.symmetric(horizontal: 2),
                      decoration: BoxDecoration(
                        color: bgColor,
                        borderRadius: BorderRadius.circular(8),
                        border: isToday
                            ? Border.all(color: WhoopTheme.primary, width: 1.5)
                            : null,
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(
                            '$dayNum',
                            style: TextStyle(color: textColor, fontSize: 12, fontWeight: FontWeight.w500),
                          ),
                          if (scores != null)
                            Container(
                              width: 4, height: 4,
                              margin: const EdgeInsets.only(top: 2),
                              decoration: BoxDecoration(
                                color: WhoopTheme.recoveryColor(scores.recovery),
                                shape: BoxShape.circle,
                              ),
                            ),
                        ],
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

  Widget _buildMonthStats() {
    // Filter scores for current month
    final monthScores = _dayScores.entries.where((e) =>
        e.key.year == _currentMonth.year && e.key.month == _currentMonth.month).toList();

    final daysWithData = monthScores.length;
    final avgRecovery = daysWithData > 0
        ? (monthScores.map((e) => e.value.recovery).reduce((a, b) => a + b) / daysWithData)
        : 0.0;
    final avgStrain = daysWithData > 0
        ? (monthScores.map((e) => e.value.strain).reduce((a, b) => a + b) / daysWithData)
        : 0.0;

    return GlassCard(
      padding: const EdgeInsets.all(16),
      radius: 14,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _stat('Days', '$daysWithData'),
          _stat('Avg Recovery', daysWithData > 0 ? '${avgRecovery.round()}%' : '--'),
          _stat('Avg Strain', daysWithData > 0 ? avgStrain.toStringAsFixed(1) : '--'),
        ],
      ),
    );
  }

  _DayScores? _findScores(DateTime date) {
    for (final entry in _dayScores.entries) {
      final d = entry.key;
      if (d.year == date.year && d.month == date.month && d.day == date.day) {
        return entry.value;
      }
    }
    return null;
  }

  Widget _stat(String label, String value) {
    return Column(children: [
      Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 20, fontWeight: FontWeight.bold)),
      const SizedBox(height: 4),
      Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
    ]);
  }

  Widget _legendDot(Color color, String label) {
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Container(width: 10, height: 10, decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(3))),
      const SizedBox(width: 4),
      Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
    ]);
  }
}

class _DayScores {
  final double recovery;
  final double strain;
  final double sleep;
  const _DayScores({required this.recovery, required this.strain, required this.sleep});
}
