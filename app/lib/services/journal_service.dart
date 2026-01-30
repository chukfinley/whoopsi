import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/journal_entry.dart';
import '../models/recovery.dart';
import 'api_service.dart';

class JournalService extends ChangeNotifier {
  static const _key = 'journal_entries';

  SharedPreferences? _prefs;
  final Map<String, JournalEntry> _entries = {}; // date -> entry

  List<JournalEntry> get allEntries {
    final list = _entries.values.toList();
    list.sort((a, b) => b.date.compareTo(a.date));
    return list;
  }

  int get entryCount => _entries.length;

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    _load();
  }

  void _load() {
    final raw = _prefs?.getString(_key);
    if (raw == null) return;
    try {
      final list = jsonDecode(raw) as List;
      for (final item in list) {
        final entry = JournalEntry.fromJson(item as Map<String, dynamic>);
        _entries[entry.date] = entry;
      }
    } catch (_) {}
  }

  void _save() {
    final list = _entries.values.map((e) => e.toJson()).toList();
    _prefs?.setString(_key, jsonEncode(list));
  }

  JournalEntry? getEntry(String date) => _entries[date];

  void saveEntry(JournalEntry entry) {
    _entries[entry.date] = entry;
    _save();
    notifyListeners();
  }

  void deleteEntry(String date) {
    _entries.remove(date);
    _save();
    notifyListeners();
  }

  /// Build a map of date -> recovery score from the API cache.
  Map<String, double> buildRecoveryMap(ApiService api) {
    final map = <String, double>{};
    for (final date in _entries.keys) {
      try {
        final cached = api.cache.get<Map<String, dynamic>>('deep_dive:recovery:$date');
        if (cached == null) continue;
        final rec = Recovery.fromDeepDive(cached);
        if (rec.score > 0) map[date] = rec.score;
      } catch (_) {}
    }
    return map;
  }

  /// Compute correlations for all behaviors that have been used.
  /// Returns a sorted list (by absolute impact, descending).
  List<BehaviorCorrelation> computeAllCorrelations(Map<String, double> recoveryByDate) {
    final usedBehaviors = <String>{};
    for (final entry in _entries.values) {
      usedBehaviors.addAll(entry.behaviors);
    }

    final results = <BehaviorCorrelation>[];
    for (final behavior in usedBehaviors) {
      final result = computeCorrelation(behavior, recoveryByDate);
      if (result != null) {
        results.add(BehaviorCorrelation(
          behavior: behavior,
          avgWith: result['with']!,
          avgWithout: result['without']!,
          diff: result['diff']!,
        ));
      }
    }

    results.sort((a, b) => b.diff.abs().compareTo(a.diff.abs()));
    return results;
  }

  /// Compute average recovery when a behavior is present vs absent.
  /// Returns null if insufficient data.
  Map<String, double>? computeCorrelation(
      String behavior, Map<String, double> recoveryByDate) {
    double sumWith = 0, countWith = 0;
    double sumWithout = 0, countWithout = 0;

    for (final entry in _entries.values) {
      final rec = recoveryByDate[entry.date];
      if (rec == null) continue;
      if (entry.behaviors.contains(behavior)) {
        sumWith += rec;
        countWith++;
      } else {
        sumWithout += rec;
        countWithout++;
      }
    }

    if (countWith < 3 || countWithout < 3) return null;
    return {
      'with': sumWith / countWith,
      'without': sumWithout / countWithout,
      'diff': (sumWith / countWith) - (sumWithout / countWithout),
    };
  }
}

class BehaviorCorrelation {
  final String behavior;
  final double avgWith;
  final double avgWithout;
  final double diff;

  const BehaviorCorrelation({
    required this.behavior,
    required this.avgWith,
    required this.avgWithout,
    required this.diff,
  });
}
