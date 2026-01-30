import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:intl/intl.dart';

import 'api_service.dart';

class PrefetchService extends ChangeNotifier {
  static const _notifId = 42;
  static const _channelId = 'prefetch_progress';
  static const _channelName = 'Data Sync';

  final FlutterLocalNotificationsPlugin _notif;
  ApiService? _api;

  bool _active = false;
  String? _status;
  double _progress = 0;

  bool get active => _active;
  String? get status => _status;
  double get progress => _progress;

  PrefetchService(this._notif);

  void setApi(ApiService api) => _api = api;

  Future<void> prefetchRange(String label, int days) async {
    if (_active || _api == null) return;
    await _notif.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>()?.requestNotificationsPermission();
    _active = true;
    _status = 'Fetching $label...';
    _progress = 0;
    notifyListeners();
    await _showNotification(0, 'Fetching $label...');

    final api = _api!;
    final now = DateTime.now();

    // 10 operations per day:
    //   3 deep-dives + sleepLastNight + sleepActivities + workoutActivities
    //   + stressMonitor + 3 deep-dive trends
    const opsPerDay = 10;
    final totalOps = days * opsPerDay;
    var completed = 0;
    var _lastNotifPct = 0;

    void _tick() {
      completed++;
      final pct = (completed * 100 / totalOps).round().clamp(0, 100);
      _progress = completed / totalOps;
      _status = 'Fetching $label... $pct%';
      notifyListeners();
      // Update notification every 1% change to stay in sync
      if (pct != _lastNotifPct) {
        _lastNotifPct = pct;
        _showNotification(pct, 'Fetching $label... $pct%');
      }
    }

    try {
      for (var d = 0; d < days; d++) {
        if (!_active) break;
        final date = now.subtract(Duration(days: d));
        final dateStr = DateFormat('yyyy-MM-dd').format(date);

        // 3 deep-dives
        for (final type in ['recovery', 'sleep', 'strain']) {
          try { await api.getDeepDive(type, dateStr); } catch (_) {}
          _tick();
        }

        // Extra data
        try { await api.getSleepLastNight(dateStr); } catch (_) {}
        _tick();
        try { await api.getSleepActivities(dateStr); } catch (_) {}
        _tick();
        try { await api.getWorkoutActivities(dateStr); } catch (_) {}
        _tick();
        try { await api.getStressMonitor(dateStr); } catch (_) {}
        _tick();

        // 3 trends
        for (final type in ['recovery', 'sleep', 'strain']) {
          try { await api.getDeepDiveTrends(type, dateStr); } catch (_) {}
          _tick();
        }
      }

      _status = '$label synced (${api.cache.cachedFileCount} files cached)';
      _progress = 1;
    } catch (e) {
      _status = 'Fetch failed: $e';
    }

    _active = false;
    notifyListeners();
    await _dismissNotification();
    await _showCompletionNotification(_status ?? 'Done');
  }

  void cancel() {
    _active = false;
    _status = 'Cancelled';
    _progress = 0;
    notifyListeners();
    _dismissNotification();
  }

  Future<void> _showNotification(int pct, String body) async {
    final details = AndroidNotificationDetails(
      _channelId, _channelName,
      channelDescription: 'Shows progress during data sync',
      importance: Importance.low,
      priority: Priority.low,
      ongoing: true,
      autoCancel: false,
      showProgress: true,
      maxProgress: 100,
      progress: pct,
      onlyAlertOnce: true,
    );
    await _notif.show(
      id: _notifId,
      title: 'Open Whoop',
      body: body,
      notificationDetails: NotificationDetails(android: details),
    );
  }

  Future<void> _showCompletionNotification(String body) async {
    const details = AndroidNotificationDetails(
      _channelId, _channelName,
      channelDescription: 'Shows progress during data sync',
      importance: Importance.defaultImportance,
      priority: Priority.defaultPriority,
    );
    await _notif.show(
      id: _notifId + 1,
      title: 'Open Whoop',
      body: body,
      notificationDetails: const NotificationDetails(android: details),
    );
  }

  Future<void> _dismissNotification() async {
    await _notif.cancel(id: _notifId);
  }
}
