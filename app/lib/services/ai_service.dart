import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:intl/intl.dart';

class AiModel {
  final String id;
  final String name;
  final String provider;
  final double? pricePer1k; // input price per 1k tokens

  const AiModel({
    required this.id,
    required this.name,
    required this.provider,
    this.pricePer1k,
  });
}

class AiService extends ChangeNotifier {
  static const _keyApiKey = 'openrouter_api_key';
  static const _keyModel = 'openrouter_model';
  static const _keyEnabled = 'ai_insights_enabled';
  static const _keyCachedInsight = 'ai_cached_insight';
  static const _keyCachedDate = 'ai_cached_date';
  static const _keyCachedModels = 'ai_cached_models';

  static const defaultModel = 'anthropic/claude-3.5-haiku';

  final _secureStorage = const FlutterSecureStorage();
  SharedPreferences? _prefs;

  String? _apiKey;
  String _model = defaultModel;
  bool _enabled = false;
  String? _cachedInsight;
  String? _cachedDate;
  bool _loading = false;
  String? _error;
  List<AiModel> _availableModels = [];
  bool _loadingModels = false;

  bool get enabled => _enabled && _apiKey != null && _apiKey!.isNotEmpty;
  bool get hasApiKey => _apiKey != null && _apiKey!.isNotEmpty;
  String get model => _model;
  String? get cachedInsight => _cachedInsight;
  bool get loading => _loading;
  String? get error => _error;
  List<AiModel> get availableModels => _availableModels;
  bool get loadingModels => _loadingModels;

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    _apiKey = await _secureStorage.read(key: _keyApiKey);
    _model = _prefs?.getString(_keyModel) ?? defaultModel;
    _enabled = _prefs?.getBool(_keyEnabled) ?? false;
    _cachedInsight = _prefs?.getString(_keyCachedInsight);
    _cachedDate = _prefs?.getString(_keyCachedDate);
    _loadCachedModels();
  }

  void _loadCachedModels() {
    final raw = _prefs?.getString(_keyCachedModels);
    if (raw == null) return;
    try {
      final list = jsonDecode(raw) as List;
      _availableModels = list.map((m) => AiModel(
        id: m['id'] as String,
        name: m['name'] as String,
        provider: m['provider'] as String,
        pricePer1k: (m['price'] as num?)?.toDouble(),
      )).toList();
    } catch (_) {}
  }

  Future<void> setApiKey(String key) async {
    _apiKey = key;
    await _secureStorage.write(key: _keyApiKey, value: key);
    notifyListeners();
  }

  void setModel(String model) {
    _model = model;
    _prefs?.setString(_keyModel, model);
    notifyListeners();
  }

  void setEnabled(bool enabled) {
    _enabled = enabled;
    _prefs?.setBool(_keyEnabled, enabled);
    notifyListeners();
  }

  /// Fetch available models from OpenRouter API
  Future<void> fetchModels() async {
    _loadingModels = true;
    notifyListeners();

    try {
      final res = await http.get(
        Uri.parse('https://openrouter.ai/api/v1/models'),
        headers: {
          if (_apiKey != null) 'Authorization': 'Bearer $_apiKey',
        },
      );

      if (res.statusCode != 200) {
        _loadingModels = false;
        notifyListeners();
        return;
      }

      final json = jsonDecode(res.body) as Map<String, dynamic>;
      final data = json['data'] as List? ?? [];

      final models = <AiModel>[];
      for (final m in data) {
        final map = m as Map<String, dynamic>;
        final id = map['id'] as String? ?? '';
        final name = map['name'] as String? ?? id;
        if (id.isEmpty) continue;

        // Extract pricing
        final pricing = map['pricing'] as Map<String, dynamic>?;
        double? price;
        if (pricing != null) {
          final prompt = pricing['prompt'] as String?;
          if (prompt != null) {
            price = double.tryParse(prompt);
            if (price != null) price = price * 1000; // per-token -> per-1k
          }
        }

        final parts = id.split('/');
        final provider = parts.length > 1 ? parts.first : '';

        models.add(AiModel(
          id: id,
          name: name,
          provider: provider,
          pricePer1k: price,
        ));
      }

      // Sort: cheapest first, then by name
      models.sort((a, b) {
        if (a.pricePer1k != null && b.pricePer1k != null) {
          return a.pricePer1k!.compareTo(b.pricePer1k!);
        }
        if (a.pricePer1k != null) return -1;
        if (b.pricePer1k != null) return 1;
        return a.name.compareTo(b.name);
      });

      _availableModels = models;

      // Cache
      final cacheList = models.map((m) => {
        'id': m.id,
        'name': m.name,
        'provider': m.provider,
        'price': m.pricePer1k,
      }).toList();
      _prefs?.setString(_keyCachedModels, jsonEncode(cacheList));
    } catch (e) {
      debugPrint('Failed to fetch models: $e');
    }

    _loadingModels = false;
    notifyListeners();
  }

  /// Check if we already have today's insight cached
  bool get hasTodayInsight {
    final today = DateFormat('yyyy-MM-dd').format(DateTime.now());
    return _cachedDate == today && _cachedInsight != null;
  }

  /// Get AI insight for today's metrics. Returns cached if available.
  Future<String?> getInsight({
    required double recovery,
    required double hrvMs,
    required int rhr,
    required double sleepHours,
    required double sleepNeeded,
    required double sleepEfficiency,
    required double yesterdayStrain,
    required List<double> recoveryTrend, // last 7 days
  }) async {
    if (!enabled) return null;

    final today = DateFormat('yyyy-MM-dd').format(DateTime.now());
    if (_cachedDate == today && _cachedInsight != null) return _cachedInsight;

    _loading = true;
    _error = null;
    notifyListeners();

    try {
      final prompt = _buildPrompt(
        recovery: recovery,
        hrvMs: hrvMs,
        rhr: rhr,
        sleepHours: sleepHours,
        sleepNeeded: sleepNeeded,
        sleepEfficiency: sleepEfficiency,
        yesterdayStrain: yesterdayStrain,
        recoveryTrend: recoveryTrend,
      );

      final response = await http.post(
        Uri.parse('https://openrouter.ai/api/v1/chat/completions'),
        headers: {
          'Authorization': 'Bearer $_apiKey',
          'Content-Type': 'application/json',
          'HTTP-Referer': 'https://open-whoop.app',
          'X-Title': 'Open Whoop',
        },
        body: jsonEncode({
          'model': _model,
          'messages': [
            {'role': 'user', 'content': prompt},
          ],
          'max_tokens': 300,
          'temperature': 0.7,
        }),
      );

      if (response.statusCode != 200) {
        throw Exception('OpenRouter error: ${response.statusCode}');
      }

      final json = jsonDecode(response.body) as Map<String, dynamic>;
      final choices = json['choices'] as List? ?? [];
      if (choices.isEmpty) throw Exception('No response from AI');

      final message = choices.first['message'] as Map<String, dynamic>?;
      final content = message?['content'] as String? ?? '';

      _cachedInsight = content;
      _cachedDate = today;
      _prefs?.setString(_keyCachedInsight, content);
      _prefs?.setString(_keyCachedDate, today);

      _loading = false;
      notifyListeners();
      return content;
    } catch (e) {
      _error = e.toString();
      _loading = false;
      notifyListeners();
      return null;
    }
  }

  String _buildPrompt({
    required double recovery,
    required double hrvMs,
    required int rhr,
    required double sleepHours,
    required double sleepNeeded,
    required double sleepEfficiency,
    required double yesterdayStrain,
    required List<double> recoveryTrend,
  }) {
    final trendStr = recoveryTrend.map((v) => v.round()).join(', ');
    return '''You are a fitness coach analyzing Whoop data. Today's metrics:
Recovery: ${recovery.round()}%, HRV: ${hrvMs.round()}ms, RHR: ${rhr}bpm
Sleep: ${sleepHours.toStringAsFixed(1)}h (needed: ${sleepNeeded.toStringAsFixed(1)}h), efficiency: ${sleepEfficiency.round()}%
Yesterday's strain: ${yesterdayStrain.toStringAsFixed(1)}
7-day recovery trend: [$trendStr]

Give 2-3 brief, actionable insights for today. Be specific and direct. No headers or bullet points, just short paragraphs.''';
  }

  void clearCache() {
    _cachedInsight = null;
    _cachedDate = null;
    _prefs?.remove(_keyCachedInsight);
    _prefs?.remove(_keyCachedDate);
    notifyListeners();
  }
}
