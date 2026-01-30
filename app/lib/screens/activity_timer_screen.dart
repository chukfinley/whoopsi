import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/sport_types.dart';
import '../services/activity_tracker_service.dart';
import '../services/ble_service.dart';
import '../widgets/gradient_scaffold.dart';

class ActivityTimerScreen extends StatefulWidget {
  const ActivityTimerScreen({super.key});

  @override
  State<ActivityTimerScreen> createState() => _ActivityTimerScreenState();
}

class _ActivityTimerScreenState extends State<ActivityTimerScreen> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer2<ActivityTrackerService, BleService>(
      builder: (context, tracker, ble, _) {
        final activity = tracker.currentActivity;

        if (activity == null || activity.state != TrackedActivityState.active) {
          // No active activity — go back
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) context.go('/');
          });
          return const GradientScaffold(
            body: Center(child: CircularProgressIndicator(color: WhoopTheme.primary)),
          );
        }

        final elapsed = activity.elapsed;
        final sportId = activity.sportId ?? -1;
        final sportName = activity.sportName ?? 'Activity';
        final hr = ble.heartRate;
        final hrLive = ble.isHrLive;

        return GradientScaffold(
          appBar: AppBar(
            backgroundColor: Colors.transparent,
            elevation: 0,
            automaticallyImplyLeading: false,
            title: Row(
              children: [
                Icon(SportTypes.icon(sportId), color: WhoopTheme.primary, size: 22),
                const SizedBox(width: 8),
                Text(sportName, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
              ],
            ),
            actions: [
              IconButton(
                icon: const Icon(Icons.close, color: WhoopTheme.textSecondary),
                onPressed: () => context.go('/'),
              ),
            ],
          ),
          body: Column(
            children: [
              const Spacer(flex: 2),

              // Timer
              Text(
                _formatElapsed(elapsed),
                style: const TextStyle(
                  color: WhoopTheme.textPrimary,
                  fontSize: 64,
                  fontWeight: FontWeight.w200,
                  fontFeatures: [FontFeature.tabularFigures()],
                ),
              ),
              const SizedBox(height: 8),
              Text(
                'Started ${_formatStartTime(activity.startTime)}',
                style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13),
              ),

              const Spacer(),

              // Live HR
              Column(
                children: [
                  Icon(
                    Icons.favorite,
                    color: hrLive ? WhoopTheme.error : WhoopTheme.textSecondary,
                    size: 32,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    hrLive && hr > 0 ? '$hr' : '--',
                    style: TextStyle(
                      color: hrLive ? WhoopTheme.textPrimary : WhoopTheme.textSecondary,
                      fontSize: 48,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  Text(
                    'BPM',
                    style: TextStyle(
                      color: hrLive ? WhoopTheme.textSecondary : WhoopTheme.textSecondary.withOpacity(0.5),
                      fontSize: 14,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),

              const Spacer(flex: 2),

              // Stop button
              GestureDetector(
                onTap: () => _stopActivity(tracker),
                child: Container(
                  width: 80,
                  height: 80,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: WhoopTheme.error,
                    boxShadow: [
                      BoxShadow(
                        color: WhoopTheme.error.withOpacity(0.4),
                        blurRadius: 20,
                        spreadRadius: 2,
                      ),
                    ],
                  ),
                  child: const Icon(Icons.stop, color: Colors.white, size: 40),
                ),
              ),
              const SizedBox(height: 12),
              const Text('STOP', style: TextStyle(
                color: WhoopTheme.error,
                fontSize: 13,
                fontWeight: FontWeight.w700,
                letterSpacing: 1,
              )),

              const Spacer(),
            ],
          ),
        );
      },
    );
  }

  void _stopActivity(ActivityTrackerService tracker) {
    final activityId = tracker.currentActivity?.id;
    tracker.endActivity();
    // Trigger sync to upload latest data
    try {
      final ble = context.read<BleService>();
      if (ble.connected) ble.unifiedSync();
    } catch (_) {}
    if (activityId != null) {
      context.go('/activity-summary/$activityId');
    } else {
      context.go('/');
    }
  }

  String _formatElapsed(Duration d) {
    final h = d.inHours;
    final m = d.inMinutes % 60;
    final s = d.inSeconds % 60;
    if (h > 0) {
      return '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
    }
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }

  String _formatStartTime(DateTime dt) {
    final local = dt.toLocal();
    return '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
  }
}
