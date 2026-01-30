import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/sport_types.dart';
import '../services/activity_tracker_service.dart';
import '../services/api_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class ActivitySummaryScreen extends StatefulWidget {
  final String activityId;
  const ActivitySummaryScreen({super.key, required this.activityId});

  @override
  State<ActivitySummaryScreen> createState() => _ActivitySummaryScreenState();
}

class _ActivitySummaryScreenState extends State<ActivitySummaryScreen> {
  Map<String, dynamic>? _apiWorkout;
  bool _loading = true;
  bool _notFound = false;

  @override
  void initState() {
    super.initState();
    _fetchApiData();
  }

  Future<void> _fetchApiData() async {
    setState(() { _loading = true; _notFound = false; });

    try {
      final tracker = context.read<ActivityTrackerService>();
      final local = tracker.history.where((a) => a.id == widget.activityId).firstOrNull;
      if (local == null) {
        setState(() { _loading = false; _notFound = true; });
        return;
      }

      final api = context.read<ApiService>();
      final dateStr = DateFormat('yyyy-MM-dd').format(local.startTime);

      final data = await api.getWorkoutActivities(dateStr, forceRefresh: true);
      final records = data['records'] as List? ?? [];

      // Match by time overlap
      Map<String, dynamic>? match;
      for (final r in records) {
        final startStr = r['start'] as String?;
        final endStr = r['end'] as String?;
        if (startStr == null) continue;
        final apiStart = DateTime.tryParse(startStr);
        final apiEnd = endStr != null ? DateTime.tryParse(endStr) : null;
        if (apiStart == null) continue;

        // Check overlap: API activity overlaps with local activity within 15 min tolerance
        final diff = apiStart.difference(local.startTime).inMinutes.abs();
        if (diff < 15 || (apiEnd != null && local.endTime != null &&
            apiEnd.difference(local.endTime!).inMinutes.abs() < 15)) {
          match = r as Map<String, dynamic>;
          break;
        }
      }

      if (mounted) {
        setState(() {
          _apiWorkout = match;
          _loading = false;
          _notFound = match == null;
        });
      }
    } catch (e) {
      debugPrint('ActivitySummary: API fetch error: $e');
      if (mounted) setState(() { _loading = false; _notFound = true; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final tracker = context.read<ActivityTrackerService>();
    final local = tracker.history.where((a) => a.id == widget.activityId).firstOrNull;

    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Activity Summary', style: TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.close),
          onPressed: () => context.go('/'),
        ),
      ),
      body: local == null
          ? const Center(child: Text('Activity not found',
              style: TextStyle(color: WhoopTheme.textSecondary)))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _buildLocalSummary(local),
                const SizedBox(height: 16),
                if (_loading)
                  _buildLoadingCard()
                else if (_apiWorkout != null)
                  _buildApiCard(_apiWorkout!)
                else
                  _buildNotFoundCard(),
                const SizedBox(height: 80),
              ],
            ),
    );
  }

  Widget _buildLocalSummary(TrackedActivity activity) {
    final sportId = activity.sportId ?? -1;
    final duration = activity.elapsed;
    final h = duration.inHours;
    final m = duration.inMinutes % 60;
    final s = duration.inSeconds % 60;
    final durationStr = h > 0 ? '${h}h ${m}m ${s}s' : '${m}m ${s}s';

    return GlassCard(
      padding: const EdgeInsets.all(20),
      child: Column(
        children: [
          // Sport icon + name
          Icon(SportTypes.icon(sportId), color: WhoopTheme.primary, size: 40),
          const SizedBox(height: 12),
          Text(
            activity.sportName ?? 'Activity',
            style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 22, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 20),

          // Duration
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.timer_outlined, color: WhoopTheme.textSecondary, size: 18),
              const SizedBox(width: 6),
              Text(durationStr, style: const TextStyle(
                color: WhoopTheme.textPrimary, fontSize: 28, fontWeight: FontWeight.w300,
              )),
            ],
          ),
          const SizedBox(height: 16),

          // Time range
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              _timeColumn('Start', activity.startTime),
              Container(width: 1, height: 30, color: WhoopTheme.divider),
              _timeColumn('End', activity.endTime ?? DateTime.now()),
            ],
          ),
        ],
      ),
    );
  }

  Widget _timeColumn(String label, DateTime dt) {
    final local = dt.toLocal();
    return Column(
      children: [
        Text(label.toUpperCase(), style: const TextStyle(
          color: WhoopTheme.textSecondary, fontSize: 10, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
        const SizedBox(height: 4),
        Text(
          '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}',
          style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.w600),
        ),
      ],
    );
  }

  Widget _buildLoadingCard() {
    return GlassCard(
      padding: const EdgeInsets.all(24),
      child: Column(
        children: [
          const CircularProgressIndicator(color: WhoopTheme.primary, strokeWidth: 2),
          const SizedBox(height: 16),
          const Text('Fetching activity analysis...',
              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 14)),
          const SizedBox(height: 6),
          Text('Cloud may need time to process',
              style: TextStyle(color: WhoopTheme.textSecondary.withOpacity(0.6), fontSize: 12)),
        ],
      ),
    );
  }

  Widget _buildApiCard(Map<String, dynamic> workout) {
    final score = workout['score'] as Map<String, dynamic>?;
    final strain = (score?['strain'] as num?)?.toDouble() ?? 0;
    final avgHr = (score?['average_heart_rate'] as num?)?.toInt();
    final maxHr = (score?['max_heart_rate'] as num?)?.toInt();
    final cals = (score?['kilojoule'] as num?)?.toDouble();
    final calStr = cals != null ? '${(cals / 4.184).round()}' : '--';
    final actId = (workout['id'] ?? '').toString();

    Color strainColor;
    if (strain < 7) {
      strainColor = WhoopTheme.sleepBlue;
    } else if (strain < 14) {
      strainColor = WhoopTheme.warning;
    } else {
      strainColor = WhoopTheme.error;
    }

    return GlassCard(
      padding: const EdgeInsets.all(20),
      child: Column(
        children: [
          const Text('WHOOP ANALYSIS', style: TextStyle(
            color: WhoopTheme.textSecondary, fontSize: 11, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          const SizedBox(height: 16),

          // Strain score
          Text(strain.toStringAsFixed(1), style: TextStyle(
            color: strainColor, fontSize: 48, fontWeight: FontWeight.w700)),
          Text('STRAIN', style: TextStyle(
            color: strainColor.withOpacity(0.7), fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          const SizedBox(height: 20),

          // Metrics row
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              _metricColumn('AVG HR', avgHr != null ? '$avgHr' : '--', 'bpm'),
              Container(width: 1, height: 30, color: WhoopTheme.divider),
              _metricColumn('MAX HR', maxHr != null ? '$maxHr' : '--', 'bpm'),
              Container(width: 1, height: 30, color: WhoopTheme.divider),
              _metricColumn('CALORIES', calStr, 'cal'),
            ],
          ),

          // View full details button
          if (actId.isNotEmpty) ...[
            const SizedBox(height: 20),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: () {
                  final api = context.read<ApiService>();
                  api.cache.setPermanent('workout:$actId', workout);
                  context.push('/activity/$actId');
                },
                style: OutlinedButton.styleFrom(
                  foregroundColor: WhoopTheme.primary,
                  side: const BorderSide(color: WhoopTheme.primary),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                ),
                child: const Text('View Full Details'),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _metricColumn(String label, String value, String unit) {
    return Column(
      children: [
        Text(label, style: const TextStyle(
          color: WhoopTheme.textSecondary, fontSize: 10, fontWeight: FontWeight.w600, letterSpacing: 0.3)),
        const SizedBox(height: 4),
        Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(value, style: const TextStyle(
              color: WhoopTheme.textPrimary, fontSize: 20, fontWeight: FontWeight.w700)),
            Padding(
              padding: const EdgeInsets.only(bottom: 2, left: 2),
              child: Text(unit, style: const TextStyle(
                color: WhoopTheme.textSecondary, fontSize: 10)),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildNotFoundCard() {
    return GlassCard(
      padding: const EdgeInsets.all(24),
      child: Column(
        children: [
          const Icon(Icons.hourglass_empty, color: WhoopTheme.textSecondary, size: 32),
          const SizedBox(height: 12),
          const Text('Activity data processing',
              style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 15, fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          const Text(
            'Whoop cloud may need a few minutes to analyze your activity. Try again shortly.',
            style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 13),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 16),
          OutlinedButton.icon(
            onPressed: _fetchApiData,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Retry'),
            style: OutlinedButton.styleFrom(
              foregroundColor: WhoopTheme.primary,
              side: const BorderSide(color: WhoopTheme.primary),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
            ),
          ),
        ],
      ),
    );
  }
}
