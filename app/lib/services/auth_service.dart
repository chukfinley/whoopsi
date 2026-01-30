import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;

import '../core/constants.dart';

class AuthService extends ChangeNotifier {
  static const _accessKey = 'whoop_access_token';
  static const _refreshKey = 'whoop_refresh_token';

  final _storage = const FlutterSecureStorage();

  String? _accessToken;
  String? _refreshToken;
  Map<String, dynamic>? _jwt;

  bool get isAuthenticated => _accessToken != null;
  String? get userId => _jwt?['custom:user_id'] as String?;

  /// Returns a valid access token, refreshing if within 5 min of expiry.
  /// If refresh fails (offline, server error), returns the existing token
  /// so the API call can fail and trigger a retry later.
  Future<String?> get accessToken async {
    if (_accessToken == null) return null;
    if (_isExpiringSoon) await refresh();
    return _accessToken; // Return even if expired — let API call fail gracefully
  }

  bool get _isExpiringSoon {
    final exp = _jwt?['exp'] as int?;
    if (exp == null) return true;
    final expiry = DateTime.fromMillisecondsSinceEpoch(exp * 1000);
    return expiry.difference(DateTime.now()).inMinutes <
        WhoopConstants.tokenRefreshBufferMinutes;
  }

  // --- File-based fallback for Linux (FlutterSecureStorage needs libsecret) ---

  static File get _tokenFile {
    final home = Platform.environment['HOME'] ?? '/tmp';
    final dir = Directory(p.join(home, '.config', 'open_whoop'));
    if (!dir.existsSync()) dir.createSync(recursive: true);
    return File(p.join(dir.path, 'tokens.json'));
  }

  Future<String?> _readToken(String key) async {
    // Try secure storage first
    try {
      final val = await _storage.read(key: key);
      if (val != null) return val;
    } catch (e) {
      debugPrint('SecureStorage read failed ($key): $e');
    }
    // Fallback to file
    try {
      final file = _tokenFile;
      if (file.existsSync()) {
        final data = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
        return data[key] as String?;
      }
    } catch (e) {
      debugPrint('File token read failed ($key): $e');
    }
    return null;
  }

  Future<void> _writeToken(String key, String value) async {
    // Write to secure storage
    try {
      await _storage.write(key: key, value: value);
    } catch (e) {
      debugPrint('SecureStorage write failed ($key): $e');
    }
    // Also write to file fallback
    try {
      final file = _tokenFile;
      Map<String, dynamic> data = {};
      if (file.existsSync()) {
        try {
          data = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
        } catch (_) {}
      }
      data[key] = value;
      file.writeAsStringSync(jsonEncode(data));
    } catch (e) {
      debugPrint('File token write failed ($key): $e');
    }
  }

  Future<void> _deleteToken(String key) async {
    try {
      await _storage.delete(key: key);
    } catch (_) {}
    try {
      final file = _tokenFile;
      if (file.existsSync()) {
        final data = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
        data.remove(key);
        file.writeAsStringSync(jsonEncode(data));
      }
    } catch (_) {}
  }

  /// Load stored tokens on app start.
  Future<void> init() async {
    _accessToken = await _readToken(_accessKey);
    _refreshToken = await _readToken(_refreshKey);
    if (_accessToken != null) {
      _jwt = _decodeJwt(_accessToken!);
      debugPrint('Auth init: token loaded, exp in ${_minutesToExpiry()} min');
      if (_isExpiringSoon && _refreshToken != null) {
        debugPrint('Auth init: token expiring soon, refreshing...');
        await refresh();
      }
    } else {
      debugPrint('Auth init: no stored tokens');
    }
    notifyListeners();
  }

  int _minutesToExpiry() {
    final exp = _jwt?['exp'] as int?;
    if (exp == null) return 0;
    final expiry = DateTime.fromMillisecondsSinceEpoch(exp * 1000);
    return expiry.difference(DateTime.now()).inMinutes;
  }

  Future<void> login(String email, String password) async {
    final body = jsonEncode({
      'AuthFlow': 'USER_PASSWORD_AUTH',
      'ClientId': WhoopConstants.cognitoClientId,
      'AuthParameters': {
        'USERNAME': email,
        'PASSWORD': password,
      },
    });

    final res = await http.post(
      Uri.parse(WhoopConstants.authEndpoint),
      headers: WhoopConstants.authHeaders,
      body: body,
    );

    if (res.statusCode != 200) {
      final msg = jsonDecode(res.body)['message'] ?? 'Login failed';
      throw Exception(msg);
    }

    final result = jsonDecode(res.body)['AuthenticationResult'];
    await _setTokens(
      result['AccessToken'] as String,
      result['RefreshToken'] as String,
    );
  }

  Future<void> refresh() async {
    if (_refreshToken == null) {
      await logout(); // No refresh token at all → must logout
      return;
    }

    final body = jsonEncode({
      'AuthFlow': 'REFRESH_TOKEN_AUTH',
      'ClientId': WhoopConstants.cognitoClientId,
      'AuthParameters': {
        'REFRESH_TOKEN': _refreshToken,
      },
    });

    try {
      final res = await http.post(
        Uri.parse(WhoopConstants.authEndpoint),
        headers: WhoopConstants.authHeaders,
        body: body,
      ).timeout(const Duration(seconds: 10));

      if (res.statusCode == 200) {
        final result = jsonDecode(res.body)['AuthenticationResult'];
        final newRefresh = result['RefreshToken'] as String?;
        await _setTokens(
          result['AccessToken'] as String,
          newRefresh ?? _refreshToken!,
        );
        debugPrint('Token refreshed, new exp in ${_minutesToExpiry()} min');
      } else if (res.statusCode == 401 || res.statusCode == 403) {
        // Refresh token truly invalid → must logout
        debugPrint('Token refresh rejected (${res.statusCode}), logging out');
        await logout();
      } else {
        // Server error (500, 503, etc.) — keep tokens, try again later
        debugPrint('Token refresh server error: ${res.statusCode}');
      }
    } on SocketException {
      debugPrint('Token refresh failed: offline');
      // Keep tokens — will retry on next API call
    } on TimeoutException {
      debugPrint('Token refresh timed out');
      // Keep tokens — will retry on next API call
    } catch (e) {
      debugPrint('Token refresh error: $e');
      // DON'T logout on unknown errors — keep tokens
    }
  }

  Future<void> logout() async {
    _accessToken = null;
    _refreshToken = null;
    _jwt = null;
    await _deleteToken(_accessKey);
    await _deleteToken(_refreshKey);
    notifyListeners();
  }

  Future<void> _setTokens(String access, String refresh) async {
    _accessToken = access;
    _refreshToken = refresh;
    _jwt = _decodeJwt(access);
    await _writeToken(_accessKey, access);
    await _writeToken(_refreshKey, refresh);
    notifyListeners();
  }

  static Map<String, dynamic> _decodeJwt(String token) {
    final parts = token.split('.');
    if (parts.length != 3) return {};
    var payload = parts[1];
    switch (payload.length % 4) {
      case 2: payload += '=='; break;
      case 3: payload += '='; break;
    }
    final decoded = utf8.decode(base64Url.decode(payload));
    return jsonDecode(decoded) as Map<String, dynamic>;
  }
}
