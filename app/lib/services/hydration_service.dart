import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:intl/intl.dart';

class HydrationService extends ChangeNotifier {
  static const _keyIntake = 'hydration_intake_ml';
  static const _keyDate = 'hydration_date';
  static const _keyGoal = 'hydration_goal_ml';
  static const _keyGlassSize = 'hydration_glass_ml';

  SharedPreferences? _prefs;
  int _intakeMl = 0;
  int _goalMl = 2500;
  int _glassSizeMl = 250;
  String _currentDate = '';

  int get intakeMl => _intakeMl;
  int get goalMl => _goalMl;
  int get glassSizeMl => _glassSizeMl;
  double get progress => _goalMl > 0 ? (_intakeMl / _goalMl).clamp(0.0, 1.5) : 0;
  bool get goalReached => _intakeMl >= _goalMl;
  int get glassesConsumed => _glassSizeMl > 0 ? (_intakeMl / _glassSizeMl).floor() : 0;

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    _goalMl = _prefs?.getInt(_keyGoal) ?? 2500;
    _glassSizeMl = _prefs?.getInt(_keyGlassSize) ?? 250;
    _checkDateReset();
  }

  void _checkDateReset() {
    final today = DateFormat('yyyy-MM-dd').format(DateTime.now());
    final savedDate = _prefs?.getString(_keyDate) ?? '';
    if (savedDate != today) {
      _intakeMl = 0;
      _prefs?.setString(_keyDate, today);
      _prefs?.setInt(_keyIntake, 0);
      _currentDate = today;
    } else {
      _intakeMl = _prefs?.getInt(_keyIntake) ?? 0;
      _currentDate = savedDate;
    }
  }

  void addGlass() {
    _checkDateReset();
    _intakeMl += _glassSizeMl;
    _prefs?.setInt(_keyIntake, _intakeMl);
    notifyListeners();
  }

  void removeGlass() {
    _checkDateReset();
    _intakeMl = (_intakeMl - _glassSizeMl).clamp(0, 99999);
    _prefs?.setInt(_keyIntake, _intakeMl);
    notifyListeners();
  }

  void setGoal(int ml) {
    _goalMl = ml;
    _prefs?.setInt(_keyGoal, ml);
    notifyListeners();
  }

  void setGlassSize(int ml) {
    _glassSizeMl = ml;
    _prefs?.setInt(_keyGlassSize, ml);
    notifyListeners();
  }
}
