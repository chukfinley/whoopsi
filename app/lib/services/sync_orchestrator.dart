import 'package:flutter/foundation.dart';

/// Lightweight event bus that notifies when cloud data should be refreshed.
/// Screens listen to this to auto-refresh after BLE sync + upload completes.
class SyncOrchestrator extends ChangeNotifier {
  DateTime? _lastCloudRefresh;
  DateTime? get lastCloudRefresh => _lastCloudRefresh;

  void notifyCloudDataStale() {
    _lastCloudRefresh = DateTime.now();
    notifyListeners();
  }
}
