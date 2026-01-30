import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

enum TrackedActivityState { active, ended }

class TrackedActivity {
  final String id;
  final DateTime startTime;
  DateTime? endTime;
  int? sportId;
  String? sportName;
  TrackedActivityState state;

  TrackedActivity({
    required this.id,
    required this.startTime,
    this.endTime,
    this.sportId,
    this.sportName,
    this.state = TrackedActivityState.active,
  });

  Duration get elapsed => (endTime ?? DateTime.now()).difference(startTime);

  Map<String, dynamic> toJson() => {
        'id': id,
        'startTime': startTime.toIso8601String(),
        'endTime': endTime?.toIso8601String(),
        'sportId': sportId,
        'sportName': sportName,
        'state': state.name,
      };

  factory TrackedActivity.fromJson(Map<String, dynamic> json) =>
      TrackedActivity(
        id: json['id'] as String,
        startTime: DateTime.parse(json['startTime'] as String),
        endTime: json['endTime'] != null
            ? DateTime.parse(json['endTime'] as String)
            : null,
        sportId: json['sportId'] as int?,
        sportName: json['sportName'] as String?,
        state: TrackedActivityState.values.firstWhere(
          (e) => e.name == json['state'],
          orElse: () => TrackedActivityState.ended,
        ),
      );
}

class ActivityTrackerService extends ChangeNotifier {
  static const _storageKey = 'tracked_activities';

  TrackedActivity? _currentActivity;
  TrackedActivity? get currentActivity => _currentActivity;
  bool get hasActiveActivity =>
      _currentActivity != null &&
      _currentActivity!.state == TrackedActivityState.active;

  final List<TrackedActivity> _history = [];
  List<TrackedActivity> get history => List.unmodifiable(_history);

  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getString(_storageKey);
    if (stored != null) {
      try {
        final list = (jsonDecode(stored) as List)
            .map((e) => TrackedActivity.fromJson(e as Map<String, dynamic>))
            .toList();
        _history.addAll(list);
        // Restore active activity if app was killed mid-activity
        final active = _history
            .where((a) => a.state == TrackedActivityState.active)
            .toList();
        if (active.isNotEmpty) {
          _currentActivity = active.last;
        }
      } catch (e) {
        debugPrint('ActivityTracker: Failed to restore: $e');
      }
    }
  }

  void startActivity(int sportId, String sportName) {
    if (hasActiveActivity) return; // Only one active at a time
    _currentActivity = TrackedActivity(
      id: DateTime.now().millisecondsSinceEpoch.toString(),
      startTime: DateTime.now(),
      sportId: sportId,
      sportName: sportName,
    );
    _history.add(_currentActivity!);
    _persist();
    notifyListeners();
  }

  void endActivity() {
    if (_currentActivity == null) return;
    _currentActivity!.endTime = DateTime.now();
    _currentActivity!.state = TrackedActivityState.ended;
    final ended = _currentActivity!;
    _currentActivity = null;
    _persist();
    notifyListeners();
    debugPrint(
        'ActivityTracker: Ended ${ended.sportName} (${ended.elapsed.inMinutes}m)');
  }

  void startSleep() {
    startActivity(-2, 'Sleep');
  }

  void endSleep() {
    endActivity();
  }

  bool get isSleeping =>
      hasActiveActivity && _currentActivity?.sportId == -2;

  Future<void> _persist() async {
    final prefs = await SharedPreferences.getInstance();
    // Keep last 50 activities
    final toStore = _history.length > 50
        ? _history.sublist(_history.length - 50)
        : _history;
    await prefs.setString(
      _storageKey,
      jsonEncode(toStore.map((a) => a.toJson()).toList()),
    );
  }
}
