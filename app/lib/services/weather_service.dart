import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class WeatherData {
  final double temperature;
  final int weatherCode;
  final double humidity;
  final double windSpeed;
  final double tempMax;
  final double tempMin;
  final List<DailyForecast> forecast;
  final DateTime fetchedAt;

  const WeatherData({
    required this.temperature,
    required this.weatherCode,
    required this.humidity,
    required this.windSpeed,
    required this.tempMax,
    required this.tempMin,
    this.forecast = const [],
    required this.fetchedAt,
  });

  String get weatherDescription => _weatherCodeToDescription(weatherCode);
  String get weatherIcon => _weatherCodeToIcon(weatherCode);

  String get trainingAdvice {
    if (temperature < 5) return 'Cold today -- extend warm-up';
    if (temperature > 30) return 'Hot today -- stay hydrated, reduce intensity';
    if (weatherCode >= 61 && weatherCode <= 67) return 'Rain expected -- consider indoor workout';
    if (weatherCode >= 71 && weatherCode <= 77) return 'Snow expected -- be cautious outdoors';
    if (windSpeed > 40) return 'Windy conditions -- adjust outdoor plans';
    return '';
  }

  static String _weatherCodeToDescription(int code) {
    if (code == 0) return 'Clear sky';
    if (code <= 3) return 'Partly cloudy';
    if (code <= 48) return 'Foggy';
    if (code <= 57) return 'Drizzle';
    if (code <= 67) return 'Rain';
    if (code <= 77) return 'Snow';
    if (code <= 82) return 'Rain showers';
    if (code <= 86) return 'Snow showers';
    if (code >= 95) return 'Thunderstorm';
    return 'Unknown';
  }

  static String _weatherCodeToIcon(int code) {
    if (code == 0) return 'sunny';
    if (code <= 3) return 'partly_cloudy';
    if (code <= 48) return 'foggy';
    if (code <= 67) return 'rainy';
    if (code <= 77) return 'snowy';
    if (code <= 86) return 'snowy';
    if (code >= 95) return 'thunderstorm';
    return 'cloudy';
  }
}

class DailyForecast {
  final DateTime date;
  final double tempMax;
  final double tempMin;
  final int weatherCode;

  const DailyForecast({
    required this.date,
    required this.tempMax,
    required this.tempMin,
    required this.weatherCode,
  });

  String get description => WeatherData._weatherCodeToDescription(weatherCode);
  String get icon => WeatherData._weatherCodeToIcon(weatherCode);
}

class WeatherService extends ChangeNotifier {
  static const _keyLat = 'weather_lat';
  static const _keyLon = 'weather_lon';
  static const _keyCity = 'weather_city';
  static const _keyCache = 'weather_cache';

  SharedPreferences? _prefs;
  WeatherData? _current;
  double? _lat;
  double? _lon;
  String _city = '';
  bool _loading = false;

  WeatherData? get current => _current;
  String get city => _city;
  bool get loading => _loading;
  bool get hasLocation => _lat != null && _lon != null;

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    _lat = _prefs?.getDouble(_keyLat);
    _lon = _prefs?.getDouble(_keyLon);
    _city = _prefs?.getString(_keyCity) ?? '';
    _loadCache();
  }

  void _loadCache() {
    final raw = _prefs?.getString(_keyCache);
    if (raw == null) return;
    try {
      final json = jsonDecode(raw) as Map<String, dynamic>;
      final fetchedAt = DateTime.tryParse(json['fetchedAt'] as String? ?? '');
      if (fetchedAt == null) return;
      // Cache valid for 30 min
      if (DateTime.now().difference(fetchedAt).inMinutes > 30) return;
      _current = _parseWeatherData(json, fetchedAt);
    } catch (_) {}
  }

  void setLocation(double lat, double lon, String city) {
    _lat = lat;
    _lon = lon;
    _city = city;
    _prefs?.setDouble(_keyLat, lat);
    _prefs?.setDouble(_keyLon, lon);
    _prefs?.setString(_keyCity, city);
    notifyListeners();
    refresh();
  }

  Future<void> refresh() async {
    if (_lat == null || _lon == null) return;
    _loading = true;
    notifyListeners();

    try {
      final url = Uri.parse(
        'https://api.open-meteo.com/v1/forecast'
        '?latitude=$_lat&longitude=$_lon'
        '&current=temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m'
        '&daily=temperature_2m_max,temperature_2m_min,weather_code'
        '&timezone=auto'
        '&forecast_days=7',
      );

      final res = await http.get(url);
      if (res.statusCode != 200) throw Exception('Weather API error: ${res.statusCode}');

      final json = jsonDecode(res.body) as Map<String, dynamic>;
      final now = DateTime.now();
      _current = _parseWeatherData(json, now);

      // Cache
      json['fetchedAt'] = now.toIso8601String();
      _prefs?.setString(_keyCache, jsonEncode(json));
    } catch (e) {
      debugPrint('Weather fetch failed: $e');
    }

    _loading = false;
    notifyListeners();
  }

  WeatherData _parseWeatherData(Map<String, dynamic> json, DateTime fetchedAt) {
    final current = json['current'] as Map<String, dynamic>? ?? {};
    final daily = json['daily'] as Map<String, dynamic>? ?? {};
    final dates = daily['time'] as List? ?? [];
    final maxTemps = daily['temperature_2m_max'] as List? ?? [];
    final minTemps = daily['temperature_2m_min'] as List? ?? [];
    final codes = daily['weather_code'] as List? ?? [];

    final forecast = <DailyForecast>[];
    for (var i = 0; i < dates.length && i < 7; i++) {
      final date = DateTime.tryParse(dates[i] as String? ?? '');
      if (date == null) continue;
      forecast.add(DailyForecast(
        date: date,
        tempMax: (maxTemps.length > i ? maxTemps[i] as num : 0).toDouble(),
        tempMin: (minTemps.length > i ? minTemps[i] as num : 0).toDouble(),
        weatherCode: codes.length > i ? (codes[i] as num).toInt() : 0,
      ));
    }

    return WeatherData(
      temperature: (current['temperature_2m'] as num? ?? 0).toDouble(),
      weatherCode: (current['weather_code'] as num? ?? 0).toInt(),
      humidity: (current['relative_humidity_2m'] as num? ?? 0).toDouble(),
      windSpeed: (current['wind_speed_10m'] as num? ?? 0).toDouble(),
      tempMax: forecast.isNotEmpty ? forecast.first.tempMax : 0,
      tempMin: forecast.isNotEmpty ? forecast.first.tempMin : 0,
      forecast: forecast,
      fetchedAt: fetchedAt,
    );
  }

  /// Search cities via Open-Meteo geocoding API
  Future<List<Map<String, dynamic>>> searchCities(String query) async {
    if (query.length < 2) return [];
    try {
      final url = Uri.parse(
        'https://geocoding-api.open-meteo.com/v1/search?name=${Uri.encodeComponent(query)}&count=5&language=en',
      );
      final res = await http.get(url);
      if (res.statusCode != 200) return [];
      final json = jsonDecode(res.body) as Map<String, dynamic>;
      return (json['results'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    } catch (_) {
      return [];
    }
  }
}
