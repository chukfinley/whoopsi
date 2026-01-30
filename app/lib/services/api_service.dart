import 'dart:convert';

import 'package:http/http.dart' as http;

import '../core/constants.dart';
import 'auth_service.dart';
import 'storage_service.dart';

class ApiService {
  final AuthService _auth;
  final StorageService cache;
  DateTime? _lastRequest;

  ApiService(this._auth, {StorageService? cache}) : cache = cache ?? StorageService();

  Future<Map<String, String>> get _headers async {
    final token = await _auth.accessToken;
    if (token == null) throw Exception('Not authenticated');
    return WhoopConstants.headers(token);
  }

  Future<dynamic> _get(String path, {Map<String, String>? queryParams}) async {
    await _rateLimit();

    final uri = Uri.parse('${WhoopConstants.apiBase}/$path')
        .replace(queryParameters: queryParams);
    var headers = await _headers;
    var res = await http.get(uri, headers: headers);

    if (res.statusCode == 401) {
      await _auth.refresh();
      headers = await _headers;
      res = await http.get(uri, headers: headers);
    }

    if (res.statusCode != 200) {
      throw ApiException(res.statusCode, res.body);
    }

    return jsonDecode(res.body);
  }

  Future<void> _rateLimit() async {
    if (_lastRequest != null) {
      final elapsed = DateTime.now().difference(_lastRequest!).inMilliseconds;
      if (elapsed < WhoopConstants.rateLimitMs) {
        await Future.delayed(
            Duration(milliseconds: WhoopConstants.rateLimitMs - elapsed));
      }
    }
    _lastRequest = DateTime.now();
  }

  Future<Map<String, dynamic>> getProfile({bool forceRefresh = false}) async {
    const key = 'profile';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('developer/v1/user/profile/basic') as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  Future<Map<String, dynamic>> getCycles({
    int limit = 25,
    String? nextToken,
  }) async {
    // Cache each page permanently by token
    final key = 'cycles:$limit:${nextToken ?? 'first'}';
    final cached = cache.get<Map<String, dynamic>>(key);
    if (cached != null) return cached;
    final params = {'limit': '$limit'};
    if (nextToken != null) params['nextToken'] = nextToken;
    final data = await _get('developer/v1/cycle', queryParams: params)
        as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  /// Fetch deep-dive data with permanent caching.
  Future<Map<String, dynamic>> getDeepDive(String type, String date,
      {bool forceRefresh = false}) async {
    final key = 'deep_dive:$type:$date';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('home-service/v1/deep-dive/$type',
        queryParams: {'date': date}) as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  Future<Map<String, dynamic>> getHealthTab({bool forceRefresh = false}) async {
    const key = 'health_tab';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('health-tab-bff/v1/health-tab') as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  Future<Map<String, dynamic>> getHealthMonitor({bool forceRefresh = false}) async {
    const key = 'health_monitor';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('coaching-service/v1/health/bff/monitor') as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  Future<Map<String, dynamic>> getStressMonitor(String date, {bool forceRefresh = false}) async {
    final key = 'stress:$date';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final now = DateTime.now().toUtc().toIso8601String().split('.')[0] + 'Z';
    final data = await _get('health-service/v2/stress-bff/$date',
        queryParams: {'timestamp': now}) as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  Future<Map<String, dynamic>> getActivityDetails(String activityId,
      {bool forceRefresh = false}) async {
    final key = 'activity:$activityId';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('core-details-bff/v1/cardio-details',
        queryParams: {'activityId': activityId}) as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  /// Detailed sleep data with stages breakdown.
  Future<Map<String, dynamic>> getSleepLastNight(String date,
      {bool forceRefresh = false}) async {
    final key = 'sleep_last_night:$date';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('home-service/v1/deep-dive/sleep/last-night',
        queryParams: {'date': date}) as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  /// Trends data for recovery/sleep/strain.
  Future<Map<String, dynamic>> getDeepDiveTrends(String type, String date,
      {bool forceRefresh = false}) async {
    final key = 'trends:$type:$date';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('home-service/v1/deep-dive/$type/trends',
        queryParams: {'date': date}) as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  /// Sleep activities for a date range (includes naps).
  Future<Map<String, dynamic>> getSleepActivities(String date,
      {bool forceRefresh = false}) async {
    final key = 'sleep_activities:$date';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('developer/v2/activity/sleep',
        queryParams: {'limit': '10', 'start': '${date}T00:00:00.000Z', 'end': '${date}T23:59:59.999Z'})
        as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  /// Workout activities for a date range.
  Future<Map<String, dynamic>> getWorkoutActivities(String date,
      {bool forceRefresh = false}) async {
    final key = 'workout_activities:$date';
    if (!forceRefresh) {
      final cached = cache.get<Map<String, dynamic>>(key);
      if (cached != null) return cached;
    }
    final data = await _get('developer/v2/activity/workout',
        queryParams: {'limit': '25', 'start': '${date}T00:00:00.000Z', 'end': '${date}T23:59:59.999Z'})
        as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  /// Recovery collection with pagination.
  Future<Map<String, dynamic>> getRecoveryCollection({
    int limit = 25,
    String? nextToken,
  }) async {
    final key = 'recovery_collection:$limit:${nextToken ?? 'first'}';
    final cached = cache.get<Map<String, dynamic>>(key);
    if (cached != null) return cached;
    final params = <String, String>{'limit': '$limit'};
    if (nextToken != null) params['nextToken'] = nextToken;
    final data = await _get('developer/v1/recovery', queryParams: params) as Map<String, dynamic>;
    cache.setPermanent(key, data);
    return data;
  }

  Future<Map<String, dynamic>> getRollups(String userId, int days) async =>
      await _get('rollups-service/v1/rollups/$userId',
          queryParams: {'days': '$days'}) as Map<String, dynamic>;
}

class ApiException implements Exception {
  final int statusCode;
  final String body;
  ApiException(this.statusCode, this.body);

  @override
  String toString() => 'ApiException($statusCode): $body';
}
