import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:intl/intl.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/recovery.dart';
import '../models/sleep.dart';
import '../models/strain.dart';
import 'ai_service.dart';
import 'api_service.dart';
import 'weather_service.dart';

class ChatMessage {
  final String role; // 'user', 'assistant', 'system'
  final String content;
  final int timestamp;

  ChatMessage({required this.role, required this.content, int? timestamp})
      : timestamp = timestamp ?? DateTime.now().millisecondsSinceEpoch;

  Map<String, dynamic> toJson() => {
        'role': role,
        'content': content,
        'timestamp': timestamp,
      };

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
        role: json['role'] as String,
        content: json['content'] as String,
        timestamp: json['timestamp'] as int? ?? 0,
      );
}

class Conversation {
  final String id;
  final String title;
  final List<ChatMessage> messages;
  final int createdAt;

  Conversation({
    required this.id,
    required this.title,
    required this.messages,
    int? createdAt,
  }) : createdAt = createdAt ?? DateTime.now().millisecondsSinceEpoch;

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'messages': messages.map((m) => m.toJson()).toList(),
        'createdAt': createdAt,
      };

  factory Conversation.fromJson(Map<String, dynamic> json) => Conversation(
        id: json['id'] as String,
        title: json['title'] as String? ?? 'Chat',
        messages: (json['messages'] as List? ?? [])
            .map((m) => ChatMessage.fromJson(m as Map<String, dynamic>))
            .toList(),
        createdAt: json['createdAt'] as int? ?? 0,
      );
}

class ChatService extends ChangeNotifier {
  static const _keyConversations = 'chat_conversations';
  static const _keyCurrentConvo = 'chat_current_conversation';
  static const _keyDataDays = 'chat_data_days';
  static const _keyIncludeWorkouts = 'chat_include_workouts';
  static const _keyIncludeSleepStages = 'chat_include_sleep_stages';
  static const _keyIncludeStress = 'chat_include_stress';
  static const _keySystemPrompt = 'chat_system_prompt';

  static const defaultSystemPrompt =
      'You are a personal health coach with access to the user\'s Whoop wearable data.\n'
      'Rules:\n'
      '- Be concise. Give short answers unless the user asks for detail.\n'
      '- Talk like a knowledgeable friend, not a report generator.\n'
      '- Only use markdown (headers, bullets, bold) when it genuinely helps readability — plain text is fine for simple answers.\n'
      '- NEVER use markdown tables. Always use bullet points or bold labels instead. Tables break on mobile screens.\n'
      '- When referencing data, mention specific numbers naturally (e.g., "your HRV was 48ms today, that\'s solid").\n'
      '- Scores ending in _pct are percentages (0-100). Strain is on a 0-21 scale.\n'
      '- "hours_vs_needed" shows actual vs recommended sleep (e.g., "7h 12m / 7h 45m").\n'
      '- Don\'t repeat data the user can already see. Focus on insights and recommendations.\n'
      '- If the user just says hi, respond casually.';

  static const defaultSystemPromptPreview =
      'Personal health coach — concise, friendly, data-driven.';

  SharedPreferences? _prefs;
  AiService? _ai;
  ApiService? _api;
  WeatherService? _weather;

  List<Conversation> _conversations = [];
  Conversation? _current;
  bool _loading = false;
  String? _error;

  // Custom system prompt
  String? _customSystemPrompt;

  // Data scope settings
  int _dataDays = 7;
  bool _includeWorkouts = true;
  bool _includeSleepStages = true;
  bool _includeStress = false;

  List<Conversation> get conversations => _conversations;
  Conversation? get current => _current;
  List<ChatMessage> get messages => _current?.messages ?? [];
  bool get loading => _loading;
  String? get error => _error;
  int get dataDays => _dataDays;
  bool get includeWorkouts => _includeWorkouts;
  bool get includeSleepStages => _includeSleepStages;
  bool get includeStress => _includeStress;
  String? get customSystemPrompt => _customSystemPrompt;

  void setDependencies(AiService ai, ApiService api, WeatherService weather) {
    _ai = ai;
    _api = api;
    _weather = weather;
  }

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    _dataDays = _prefs?.getInt(_keyDataDays) ?? 7;
    _includeWorkouts = _prefs?.getBool(_keyIncludeWorkouts) ?? true;
    _includeSleepStages = _prefs?.getBool(_keyIncludeSleepStages) ?? true;
    _includeStress = _prefs?.getBool(_keyIncludeStress) ?? false;
    _customSystemPrompt = _prefs?.getString(_keySystemPrompt);
    _loadConversations();
    final currentId = _prefs?.getString(_keyCurrentConvo);
    if (currentId != null) {
      _current = _conversations.where((c) => c.id == currentId).firstOrNull;
    }
  }

  void _loadConversations() {
    final raw = _prefs?.getString(_keyConversations);
    if (raw == null) return;
    try {
      final list = jsonDecode(raw) as List;
      _conversations = list
          .map((c) => Conversation.fromJson(c as Map<String, dynamic>))
          .toList();
      _conversations.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    } catch (e) {
      debugPrint('Failed to load conversations: $e');
    }
  }

  void _saveConversations() {
    final json = _conversations.map((c) => c.toJson()).toList();
    _prefs?.setString(_keyConversations, jsonEncode(json));
  }

  // ─── Settings ──────────────────────────────────────────────

  void setDataDays(int days) {
    _dataDays = days;
    _prefs?.setInt(_keyDataDays, days);
    notifyListeners();
  }

  void setIncludeWorkouts(bool v) {
    _includeWorkouts = v;
    _prefs?.setBool(_keyIncludeWorkouts, v);
    notifyListeners();
  }

  void setIncludeSleepStages(bool v) {
    _includeSleepStages = v;
    _prefs?.setBool(_keyIncludeSleepStages, v);
    notifyListeners();
  }

  void setIncludeStress(bool v) {
    _includeStress = v;
    _prefs?.setBool(_keyIncludeStress, v);
    notifyListeners();
  }

  void setCustomSystemPrompt(String? prompt) {
    _customSystemPrompt = prompt;
    if (prompt == null) {
      _prefs?.remove(_keySystemPrompt);
    } else {
      _prefs?.setString(_keySystemPrompt, prompt);
    }
    notifyListeners();
  }

  // ─── Conversation management ───────────────────────────────

  void newConversation() {
    _current = null;
    _error = null;
    _prefs?.remove(_keyCurrentConvo);
    notifyListeners();
  }

  void selectConversation(String id) {
    _current = _conversations.where((c) => c.id == id).firstOrNull;
    _error = null;
    _prefs?.setString(_keyCurrentConvo, id);
    notifyListeners();
  }

  void deleteConversation(String id) {
    _conversations.removeWhere((c) => c.id == id);
    if (_current?.id == id) {
      _current = null;
      _prefs?.remove(_keyCurrentConvo);
    }
    _saveConversations();
    notifyListeners();
  }

  // ─── Send message ──────────────────────────────────────────

  Future<void> sendMessage(String text) async {
    if (_ai == null || _api == null || !_ai!.hasApiKey) {
      _error = 'Set your OpenRouter API key in Settings first';
      notifyListeners();
      return;
    }

    _error = null;

    // Create conversation if needed
    if (_current == null) {
      final id = DateTime.now().millisecondsSinceEpoch.toString();
      final title = text.length > 40 ? '${text.substring(0, 40)}...' : text;
      _current = Conversation(id: id, title: title, messages: []);
      _conversations.insert(0, _current!);
      _prefs?.setString(_keyCurrentConvo, id);
    }

    // Add user message
    _current!.messages.add(ChatMessage(role: 'user', content: text));
    _loading = true;
    notifyListeners();

    try {
      // Build data context
      final context = await _buildDataContext();

      // Build messages for API
      final apiMessages = <Map<String, String>>[];

      // System message with data context
      apiMessages.add({
        'role': 'system',
        'content': _buildSystemPrompt(context),
      });

      // Conversation history (last 20 messages to avoid token overflow)
      final historyMessages = _current!.messages;
      final start = historyMessages.length > 20 ? historyMessages.length - 20 : 0;
      for (var i = start; i < historyMessages.length; i++) {
        final msg = historyMessages[i];
        if (msg.role == 'user' || msg.role == 'assistant') {
          apiMessages.add({'role': msg.role, 'content': msg.content});
        }
      }

      // Call OpenRouter
      final response = await http.post(
        Uri.parse('https://openrouter.ai/api/v1/chat/completions'),
        headers: {
          'Authorization': 'Bearer ${await _getApiKey()}',
          'Content-Type': 'application/json',
          'HTTP-Referer': 'https://open-whoop.app',
          'X-Title': 'Open Whoop',
        },
        body: jsonEncode({
          'model': _ai!.model,
          'messages': apiMessages,
          'max_tokens': 1000,
          'temperature': 0.7,
        }),
      );

      if (response.statusCode != 200) {
        throw Exception('API error ${response.statusCode}: ${response.body}');
      }

      final json = jsonDecode(response.body) as Map<String, dynamic>;
      final choices = json['choices'] as List? ?? [];
      if (choices.isEmpty) throw Exception('No response from AI');

      final message = choices.first['message'] as Map<String, dynamic>?;
      final content = message?['content'] as String? ?? '';

      _current!.messages.add(ChatMessage(role: 'assistant', content: content));
      _saveConversations();
    } catch (e) {
      _error = e.toString();
      // Remove the user message if we failed
      if (_current!.messages.isNotEmpty && _current!.messages.last.role == 'user') {
        _current!.messages.removeLast();
      }
    }

    _loading = false;
    notifyListeners();
  }

  Future<String> _getApiKey() async {
    // Read from secure storage via AiService (it stores the key)
    const storage = FlutterSecureStorage();
    return await storage.read(key: 'openrouter_api_key') ?? '';
  }

  // ─── Data context builder ──────────────────────────────────

  Future<Map<String, dynamic>> _buildDataContext() async {
    final api = _api!;
    final now = DateTime.now();
    final todayStr = DateFormat('yyyy-MM-dd').format(now);
    final context = <String, dynamic>{};

    // Today's data
    try {
      final results = await Future.wait([
        api.getDeepDive('recovery', todayStr).catchError((_) => <String, dynamic>{}),
        api.getDeepDive('sleep', todayStr).catchError((_) => <String, dynamic>{}),
        api.getDeepDive('strain', todayStr).catchError((_) => <String, dynamic>{}),
        api.getSleepLastNight(todayStr).catchError((_) => <String, dynamic>{}),
      ]);

      final recRaw = results[0] as Map<String, dynamic>;
      final slpRaw = results[1] as Map<String, dynamic>;
      final strRaw = results[2] as Map<String, dynamic>;
      final sleepDetail = results[3] as Map<String, dynamic>;

      final rec = recRaw.isNotEmpty ? Recovery.fromDeepDive(recRaw) : null;
      final slp = slpRaw.isNotEmpty ? Sleep.fromDeepDive(slpRaw) : null;
      final str = strRaw.isNotEmpty ? Strain.fromDeepDive(strRaw) : null;

      final today = <String, dynamic>{'date': todayStr};
      if (rec != null) {
        today['recovery'] = {
          'score_pct': rec.score.round(),
          'hrv_ms': rec.hrvMs.round(),
          'rhr_bpm': rec.rhr,
          'respiratory_rate': rec.respiratoryRate,
        };
      }
      if (slp != null) {
        today['sleep'] = {
          'score_pct': slp.score.round(),
          'hours_vs_needed': slp.hoursVsNeeded,
          'consistency_pct': slp.consistency,
          'efficiency_pct': slp.efficiency,
        };
      }
      if (str != null) {
        today['strain'] = {
          'score': str.score,
          'steps': str.steps,
          'hr_zones_1_3': str.hrZones13,
          'hr_zones_4_5': str.hrZones45,
        };
      }

      // Sleep stages from detailed data
      if (_includeSleepStages && sleepDetail.isNotEmpty) {
        final stages = _extractSleepStages(sleepDetail);
        if (stages.isNotEmpty) today['sleep_stages'] = stages;
      }

      context['today'] = today;
    } catch (e) {
      debugPrint('Failed to build today context: $e');
    }

    // Historical data
    final history = <Map<String, dynamic>>[];
    for (var d = 1; d <= _dataDays; d++) {
      final date = now.subtract(Duration(days: d));
      final dateStr = DateFormat('yyyy-MM-dd').format(date);
      try {
        final cached = api.cache.get<Map<String, dynamic>>('deep_dive:recovery:$dateStr');
        final cachedSlp = api.cache.get<Map<String, dynamic>>('deep_dive:sleep:$dateStr');
        final cachedStr = api.cache.get<Map<String, dynamic>>('deep_dive:strain:$dateStr');

        if (cached == null && cachedSlp == null && cachedStr == null) continue;

        final entry = <String, dynamic>{'date': dateStr};
        if (cached != null) {
          final rec = Recovery.fromDeepDive(cached);
          entry['recovery_pct'] = rec.score.round();
          entry['hrv_ms'] = rec.hrvMs.round();
          entry['rhr_bpm'] = rec.rhr;
        }
        if (cachedSlp != null) {
          final slp = Sleep.fromDeepDive(cachedSlp);
          entry['sleep_score_pct'] = slp.score.round();
          entry['sleep_hours_vs_needed'] = slp.hoursVsNeeded;
        }
        if (cachedStr != null) {
          final str = Strain.fromDeepDive(cachedStr);
          entry['strain'] = str.score;
        }
        history.add(entry);
      } catch (_) {}
    }
    if (history.isNotEmpty) context['history'] = history;

    // Workouts
    if (_includeWorkouts) {
      final workouts = <Map<String, dynamic>>[];
      for (var d = 0; d < _dataDays && d < 7; d++) {
        final date = now.subtract(Duration(days: d));
        final dateStr = DateFormat('yyyy-MM-dd').format(date);
        final cached = api.cache.get<Map<String, dynamic>>('workout_activities:$dateStr');
        if (cached == null) continue;
        final records = cached['records'] as List? ?? [];
        for (final r in records) {
          final w = r as Map<String, dynamic>;
          final score = w['score'] as Map<String, dynamic>?;
          workouts.add({
            'date': dateStr,
            'sport': w['sport_id'] ?? 'Unknown',
            'strain': score?['strain'],
            'avg_hr': score?['average_heart_rate'],
            'max_hr': score?['max_heart_rate'],
            'calories_kj': score?['kilojoule'],
          });
        }
      }
      if (workouts.isNotEmpty) context['workouts'] = workouts;
    }

    // Stress
    if (_includeStress) {
      try {
        final stress = api.cache.get<Map<String, dynamic>>('stress:$todayStr');
        if (stress != null) context['stress'] = _extractStressData(stress);
      } catch (_) {}
    }

    // Weather
    final w = _weather?.current;
    if (w != null) {
      context['weather'] = {
        'location': _weather!.city,
        'temperature_c': w.temperature,
        'condition': w.weatherDescription,
        'humidity_pct': w.humidity,
        'wind_kmh': w.windSpeed,
        'high_c': w.tempMax,
        'low_c': w.tempMin,
        if (w.trainingAdvice.isNotEmpty) 'training_advice': w.trainingAdvice,
      };
    }

    return context;
  }

  Map<String, dynamic> _extractSleepStages(Map<String, dynamic> data) {
    final stages = <String, dynamic>{};
    // Try to find sleep stage breakdown in nested sections
    for (final section in data['sections'] as List? ?? []) {
      for (final item in (section as Map)['items'] as List? ?? []) {
        final map = item as Map<String, dynamic>;
        final content = map['content'] as Map<String, dynamic>?;
        if (content == null) continue;
        for (final m in content['metrics'] as List? ?? []) {
          final metric = m as Map<String, dynamic>;
          final title = (metric['title'] as String? ?? '').toUpperCase();
          final status = metric['status'] as String? ?? '';
          if (title.contains('REM')) stages['rem'] = status;
          if (title.contains('DEEP') || title.contains('SWS')) stages['deep'] = status;
          if (title.contains('LIGHT')) stages['light'] = status;
          if (title.contains('AWAKE')) stages['awake'] = status;
          if (title.contains('DISTURBANCES')) stages['disturbances'] = status;
          if (title.contains('TIME IN BED')) stages['time_in_bed'] = status;
        }
      }
    }
    return stages;
  }

  Map<String, dynamic> _extractStressData(Map<String, dynamic> data) {
    // Extract top-level stress state
    String? state;
    for (final section in data['sections'] as List? ?? []) {
      for (final item in (section as Map)['items'] as List? ?? []) {
        final map = item as Map<String, dynamic>;
        final content = map['content'] as Map<String, dynamic>?;
        if (content != null && content['stress_state'] != null) {
          state = content['stress_state'] as String?;
        }
      }
    }
    if (state == null) state = data['stressState'] as String?;
    if (state == null) state = data['stress_state'] as String?;
    return {'state': state ?? 'unknown'};
  }

  String _buildSystemPrompt(Map<String, dynamic> context) {
    final now = DateTime.now();
    final today = DateFormat('yyyy-MM-dd').format(now);
    final time = DateFormat('HH:mm').format(now);
    final weekday = DateFormat('EEEE').format(now);
    final contextJson = const JsonEncoder.withIndent('  ').convert(context);
    final promptBase = _customSystemPrompt ?? defaultSystemPrompt;
    return '$promptBase\n\nCurrent time: $weekday, $today $time\n\nUser\'s data:\n$contextJson';
  }
}
