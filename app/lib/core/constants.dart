class WhoopConstants {
  WhoopConstants._();

  static const apiBase = 'https://api.prod.whoop.com';
  static const authEndpoint = '$apiBase/auth-service/v3/whoop/';
  // Extract from official Whoop APK or set WHOOP_COGNITO_CLIENT_ID env var
  static const cognitoClientId = String.fromEnvironment(
    'WHOOP_COGNITO_CLIENT_ID',
  );
  static const appVersion = '5.430.0';
  static const appVersionCode = '375528';
  static const platform = 'ANDROID';
  static const userAgent = 'Whoop-Android\\$appVersion';
  static const packageName = 'com.whoop.android';

  static const defaultLimit = 25;
  static const rateLimitMs = 200;
  static const tokenRefreshBufferMinutes = 5;
  static const defaultCacheTtl = Duration(minutes: 5);

  static Map<String, String> headers(String token, {String? timeZone}) => {
    'Authorization': 'Bearer $token',
    'User-Agent': userAgent,
    'x-whoop-app-version': appVersion,
    'x-whoop-app-version-code': appVersionCode,
    'x-whoop-device-platform': platform,
    'x-whoop-package-name': packageName,
    'x-whoop-time-zone': timeZone ?? DateTime.now().timeZoneName,
    'Content-Type': 'application/json',
  };

  static Map<String, String> get authHeaders => {
    'Content-Type': 'application/x-amz-json-1.1',
    'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
    'User-Agent': userAgent,
  };

  static const metricsEndpoint = '$apiBase/metrics-service/v1/metrics';
  static const highWatermarkEndpoint =
      '$apiBase/metrics-service/v1/consumerstats/mobile/highwatermark/min';

  static Map<String, String> uploadHeaders(
    String token, {
    required String strapId,
    String hwVersion = '13.82.0',
    String fwVersion = '50.35.2.0',
  }) => {
    'Authorization': 'Bearer $token',
    'User-Agent': userAgent,
    'x-whoop-app-version': appVersion,
    'x-whoop-app-version-code': appVersionCode,
    'x-whoop-device-platform': platform,
    'x-whoop-package-name': packageName,
    'x-whoop-strap-id': strapId,
    'x-whoop-hw-version': hwVersion,
    'x-whoop-fw-version': fwVersion,
    'x-whoop-dsp-version': '10.3.32',
    'x-whoop-binary-version': '5',
    'x-whoop-binary-flags': '0',
    'content-type': 'application/octet-stream',
    'content-encoding': 'gzip',
  };
}
