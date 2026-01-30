import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import '../core/constants.dart';

/// Hybrid cache: in-memory for speed, JSON files on disk for persistence.
class StorageService {
  final Map<String, _CacheEntry> _memory = {};
  Directory? _cacheDir;
  bool _initialized = false;

  /// Must be called once at app startup.
  Future<void> init() async {
    if (_initialized) return;
    final appDir = await getApplicationSupportDirectory();
    _cacheDir = Directory('${appDir.path}/api_cache');
    if (!_cacheDir!.existsSync()) {
      _cacheDir!.createSync(recursive: true);
    }
    _initialized = true;
  }

  String _keyToFilename(String key) {
    // Sanitize key for filesystem
    return key.replaceAll(RegExp(r'[^a-zA-Z0-9_\-.]'), '_');
  }

  File _fileFor(String key) => File('${_cacheDir!.path}/${_keyToFilename(key)}.json');

  T? get<T>(String key) {
    // Check memory first
    final entry = _memory[key];
    if (entry != null) {
      if (entry.isExpired) {
        _memory.remove(key);
        return null;
      }
      return entry.value as T?;
    }

    // Check disk
    if (!_initialized) return null;
    final file = _fileFor(key);
    if (!file.existsSync()) return null;
    try {
      final json = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
      final expiryMs = json['expiry'] as int?;
      if (expiryMs != null && DateTime.now().millisecondsSinceEpoch > expiryMs) {
        file.deleteSync();
        return null;
      }
      final value = json['value'];
      // Store in memory for fast subsequent access
      _memory[key] = _CacheEntry(
        value: value,
        expiry: expiryMs != null ? DateTime.fromMillisecondsSinceEpoch(expiryMs) : null,
      );
      return value as T?;
    } catch (e) {
      debugPrint('Cache read error for $key: $e');
      return null;
    }
  }

  void set(String key, dynamic value, {Duration? ttl}) {
    final expiry = DateTime.now().add(ttl ?? WhoopConstants.defaultCacheTtl);
    _memory[key] = _CacheEntry(value: value, expiry: expiry);
    _writeToDisk(key, value, expiry.millisecondsSinceEpoch);
  }

  /// Cache permanently (never expires) — persisted to disk.
  void setPermanent(String key, dynamic value) {
    _memory[key] = _CacheEntry(value: value, expiry: null);
    _writeToDisk(key, value, null);
  }

  void _writeToDisk(String key, dynamic value, int? expiryMs) {
    if (!_initialized) return;
    try {
      final file = _fileFor(key);
      final json = jsonEncode({'value': value, 'expiry': expiryMs});
      file.writeAsStringSync(json);
    } catch (e) {
      debugPrint('Cache write error for $key: $e');
    }
  }

  bool has(String key) => get<dynamic>(key) != null;

  void remove(String key) {
    _memory.remove(key);
    if (_initialized) {
      final file = _fileFor(key);
      if (file.existsSync()) file.deleteSync();
    }
  }

  void clear() {
    _memory.clear();
    if (_initialized && _cacheDir != null && _cacheDir!.existsSync()) {
      for (final f in _cacheDir!.listSync()) {
        if (f is File) f.deleteSync();
      }
    }
  }

  /// Returns the cache directory path (for export).
  String? get cacheDirPath => _cacheDir?.path;

  /// Returns count of cached files on disk.
  int get cachedFileCount {
    if (!_initialized || _cacheDir == null || !_cacheDir!.existsSync()) return 0;
    return _cacheDir!.listSync().whereType<File>().length;
  }

  /// Returns total size of cached files in bytes.
  int get cachedSizeBytes {
    if (!_initialized || _cacheDir == null || !_cacheDir!.existsSync()) return 0;
    var total = 0;
    for (final f in _cacheDir!.listSync().whereType<File>()) {
      total += f.lengthSync();
    }
    return total;
  }

  /// Export all cache files as a Map<filename, jsonContent> for ZIP creation.
  Map<String, String> exportAll() {
    final result = <String, String>{};
    if (!_initialized || _cacheDir == null || !_cacheDir!.existsSync()) return result;
    for (final f in _cacheDir!.listSync().whereType<File>()) {
      try {
        result[f.uri.pathSegments.last] = f.readAsStringSync();
      } catch (_) {}
    }
    return result;
  }
}

class _CacheEntry {
  final dynamic value;
  final DateTime? expiry;

  _CacheEntry({required this.value, required this.expiry});

  bool get isExpired => expiry != null && DateTime.now().isAfter(expiry!);
}
