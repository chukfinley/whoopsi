import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:permission_handler/permission_handler.dart';

import 'sensor_db_service.dart';
import 'upload_service.dart';

enum SyncPhase { idle, bleSyncing, uploading, refreshing, done, error }

/// BLE service for connecting to Whoop strap and streaming live heart rate + battery.
class BleService extends ChangeNotifier {
  // Standard BLE UUIDs
  static final _hrServiceUuid = Guid('0000180d-0000-1000-8000-00805f9b34fb');
  static final _hrCharUuid = Guid('00002a37-0000-1000-8000-00805f9b34fb');
  // Whoop proprietary service UUIDs (Maverick / Whoop 5.0)
  static final _maverickService = Guid('fd4b0001-cce1-4033-93ce-002d5875f58a');
  static final _maverickCmdTo = Guid('fd4b0002-cce1-4033-93ce-002d5875f58a');
  static final _maverickCmdFrom = Guid('fd4b0003-cce1-4033-93ce-002d5875f58a');
  static final _maverickEvents = Guid('fd4b0004-cce1-4033-93ce-002d5875f58a');

  // Gen4
  static final _gen4Service = Guid('61080001-8d6d-82b8-614a-1c8cb0f8dcc6');
  static final _gen4CmdTo = Guid('61080002-8d6d-82b8-614a-1c8cb0f8dcc6');
  static final _gen4CmdFrom = Guid('61080003-8d6d-82b8-614a-1c8cb0f8dcc6');
  static final _gen4Events = Guid('61080004-8d6d-82b8-614a-1c8cb0f8dcc6');

  // Puffin
  static final _puffinService = Guid('11500001-6215-11ee-8c99-0242ac120002');
  static final _puffinCmdTo = Guid('11500002-6215-11ee-8c99-0242ac120002');
  static final _puffinCmdFrom = Guid('11500003-6215-11ee-8c99-0242ac120002');
  static final _puffinEvents = Guid('11500004-6215-11ee-8c99-0242ac120002');

  static final _profiles = [
    _StrapProfile('Maverick', _maverickService, _maverickCmdTo, _maverickCmdFrom, _maverickEvents),
    _StrapProfile('Gen4', _gen4Service, _gen4CmdTo, _gen4CmdFrom, _gen4Events),
    _StrapProfile('Puffin', _puffinService, _puffinCmdTo, _puffinCmdFrom, _puffinEvents),
  ];

  BluetoothDevice? _device;
  BluetoothCharacteristic? _cmdToChar;
  _StrapProfile? _activeProfile;
  final List<StreamSubscription> _subscriptions = [];

  String _status = 'Disconnected';
  String get status => _status;

  // Separate sync progress status (so connection status stays stable)
  String _syncStatus = '';
  String get syncStatus => _syncStatus;

  // Unified sync phase tracking
  SyncPhase _syncPhase = SyncPhase.idle;
  SyncPhase get syncPhase => _syncPhase;
  String _syncPhaseMessage = '';
  String get syncPhaseMessage => _syncPhaseMessage;

  /// Callback fired after sync + upload completes. Used to trigger cloud data refresh.
  VoidCallback? onSyncComplete;

  int _heartRate = 0;
  int get heartRate => _heartRate;

  int _batteryLevel = -1;
  int get batteryLevel => _batteryLevel;

  bool _isCharging = false;
  bool get isCharging => _isCharging;

  bool _connected = false;
  bool get connected => _connected;

  // Device info from BLE commands
  String _deviceSerial = '';
  String get deviceSerial => _deviceSerial;
  String _deviceName = '';
  String get deviceName => _deviceName;
  String _firmwareInfo = '';
  String get firmwareInfo => _firmwareInfo;

  // Timestamps for connection tracking
  DateTime? _lastConnectedTime;
  DateTime? get lastConnectedTime => _lastConnectedTime;
  DateTime? _lastDisconnectedTime;
  DateTime? get lastDisconnectedTime => _lastDisconnectedTime;
  DateTime? _lastHrTime;
  DateTime? get lastHrTime => _lastHrTime;

  // BLE profile name
  String get activeProfileName => _activeProfile?.name ?? '';

  // Expose data range for device screen
  int get dataRangeStart => _dataRangeStart;
  int get dataRangeEnd => _dataRangeEnd;

  /// Whether HR data is live (received within last 30 seconds)
  bool get isHrLive =>
      _lastHrTime != null &&
      DateTime.now().difference(_lastHrTime!).inSeconds < 30;

  // Recent HR values for mini graph
  final List<int> _hrHistory = [];
  List<int> get hrHistory => List.unmodifiable(_hrHistory);

  int _sequenceNumber = 0;

  // Sensor DB for permanent storage
  SensorDbService? _sensorDb;
  set sensorDb(SensorDbService? db) => _sensorDb = db;

  // Upload service for auto-upload after sync
  UploadService? _uploadService;
  set uploadService(UploadService? svc) => _uploadService = svc;

  // Historical sync state
  bool _syncingHistory = false;
  bool get syncingHistory => _syncingHistory;
  int _syncedPackets = 0;
  int get syncedPackets => _syncedPackets;
  Timer? _syncTimeoutTimer;

  // Smart sync state
  int _syncNewRecords = 0;
  int get syncNewRecords => _syncNewRecords;
  int _syncRound = 0;
  int get syncRound => _syncRound;
  String _syncDateRange = '';
  String get syncDateRange => _syncDateRange;
  int _syncMinTimestamp = 0;
  int _syncMaxTimestamp = 0;

  // Data range from GET_DATA_RANGE response
  int _dataRangeStart = 0;
  int _dataRangeEnd = 0;

  // TrimAll completion detection (from console logs)
  bool _trimAllCompleted = false;

  static const _maxRounds = 400;

  /// Request BLE runtime permissions (Android 12+ needs BLUETOOTH_SCAN + BLUETOOTH_CONNECT).
  Future<bool> _requestPermissions() async {
    if (!Platform.isAndroid) return true;

    final statuses = await [
      Permission.bluetoothScan,
      Permission.bluetoothConnect,
      Permission.location,
    ].request();

    final scanOk = statuses[Permission.bluetoothScan]?.isGranted ?? false;
    final connectOk = statuses[Permission.bluetoothConnect]?.isGranted ?? false;
    final locationOk = statuses[Permission.location]?.isGranted ?? false;

    if (scanOk && connectOk) return true;
    if (locationOk) return true;

    debugPrint('BLE permissions denied: scan=$scanOk connect=$connectOk location=$locationOk');
    return false;
  }

  /// Connect to a bonded Whoop device.
  Future<void> connect() async {
    try {
      if (!await _requestPermissions()) {
        _setStatus('Bluetooth permissions denied');
        return;
      }

      if (await FlutterBluePlus.isSupported == false) {
        _setStatus('BLE not supported');
        return;
      }

      final adapterState = await FlutterBluePlus.adapterState.first;
      if (adapterState != BluetoothAdapterState.on) {
        _setStatus('Bluetooth is off');
        try { await FlutterBluePlus.turnOn(); } catch (_) {}
        return;
      }

      _setStatus('Scanning for Whoop...');

      final bonded = await FlutterBluePlus.bondedDevices;
      BluetoothDevice? whoopDevice;
      for (final d in bonded) {
        if (d.platformName.toLowerCase().contains('whoop')) {
          whoopDevice = d;
          break;
        }
      }

      if (whoopDevice == null) {
        _setStatus('Scanning...');
        final completer = Completer<BluetoothDevice?>();
        final sub = FlutterBluePlus.scanResults.listen((results) {
          for (final r in results) {
            if (r.device.platformName.toLowerCase().contains('whoop')) {
              if (!completer.isCompleted) completer.complete(r.device);
            }
          }
        });
        await FlutterBluePlus.startScan(timeout: const Duration(seconds: 10));
        whoopDevice = await completer.future.timeout(
          const Duration(seconds: 12),
          onTimeout: () => null,
        );
        await sub.cancel();
        await FlutterBluePlus.stopScan();
      }

      if (whoopDevice == null) {
        _setStatus('No Whoop found');
        return;
      }

      _device = whoopDevice;
      _setStatus('Connecting to ${whoopDevice.platformName}...');

      final disconnectSub = whoopDevice.connectionState.listen((state) {
        if (state == BluetoothConnectionState.disconnected && _connected) {
          _connected = false;
          _syncingHistory = false;
          _lastDisconnectedTime = DateTime.now();
          _setStatus('Disconnected');
          _cleanup();
          Future.delayed(const Duration(seconds: 5), () {
            if (!_connected && _device != null) connect();
          });
        }
      });
      _subscriptions.add(disconnectSub);

      await whoopDevice.connect(autoConnect: false, timeout: const Duration(seconds: 15));

      await whoopDevice.connectionState
          .where((s) => s == BluetoothConnectionState.connected)
          .first
          .timeout(const Duration(seconds: 15));
      _connected = true;
      _lastConnectedTime = DateTime.now();

      _setStatus('Discovering services...');
      final services = await whoopDevice.discoverServices();

      for (final svc in services) {
        if (svc.serviceUuid == _hrServiceUuid) {
          for (final c in svc.characteristics) {
            if (c.characteristicUuid == _hrCharUuid) {
              await c.setNotifyValue(true);
              _subscriptions.add(c.onValueReceived.listen(_onHrData));
              debugPrint('BLE: Subscribed to standard HR service');
            }
          }
        }
      }

      for (final profile in _profiles) {
        for (final svc in services) {
          if (svc.serviceUuid == profile.service) {
            _activeProfile = profile;
            debugPrint('BLE: Found ${profile.name} service');

            for (final c in svc.characteristics) {
              if (c.characteristicUuid == profile.cmdTo) {
                _cmdToChar = c;
              }
              if (c.characteristicUuid == profile.cmdFrom) {
                await c.setNotifyValue(true);
                _subscriptions.add(c.onValueReceived.listen(_onCmdResponse));
              }
              if (c.characteristicUuid == profile.events) {
                await c.setNotifyValue(true);
                _subscriptions.add(c.onValueReceived.listen(_onEventData));
              }
            }
            break;
          }
        }
        if (_activeProfile != null) break;
      }

      _setStatus('Connected');

      if (_cmdToChar != null) {
        await Future.delayed(const Duration(milliseconds: 500));
        // Init sequence: abort leftover dump, identify device, get battery
        // Delays between commands prevent GATT_WRITE_REQUEST_BUSY errors
        await _sendCommand(_buildCommand(0x14, [])); // ABORT_HISTORICAL
        await Future.delayed(const Duration(milliseconds: 300));
        await _sendCommand(_buildCommand(0x91, [0x01])); // GET_HELLO_EXT
        await Future.delayed(const Duration(milliseconds: 300));
        await _sendCommand(_buildCommand(0x8D, [0x01])); // GET_ADVERTISING_NAME
        await Future.delayed(const Duration(milliseconds: 300));
        await _sendCommand(_buildCommand(0x1A, [])); // GET_BATTERY_LEVEL
        await Future.delayed(const Duration(milliseconds: 500));
        // Auto-sync on connect (fire-and-forget — unifiedSync has its own error handling)
        unawaited(unifiedSync());
      }
    } catch (e) {
      debugPrint('BLE connect error: $e');
      _setStatus('Error: ${e.toString().split('\n').first}');
    }
  }

  void disconnect() {
    _cleanup();
    _device?.disconnect();
    _device = null;
    _connected = false;
    _heartRate = 0;
    _batteryLevel = -1;
    _deviceSerial = '';
    _deviceName = '';
    _firmwareInfo = '';
    _hrHistory.clear();
    _setStatus('Disconnected');
  }

  void _cleanup() {
    for (final s in _subscriptions) {
      s.cancel();
    }
    _subscriptions.clear();
    _cmdToChar = null;
    _activeProfile = null;
    _syncTimeoutTimer?.cancel();
  }

  void _setStatus(String s) {
    _status = s;
    notifyListeners();
  }

  void _setSyncStatus(String s) {
    _syncStatus = s;
    notifyListeners();
  }

  // === Smart Sync (ported from Kotlin WhoopBleService) ===

  void _resetSyncCounters() {
    _syncNewRecords = 0;
    _syncRound = 0;
    _syncDateRange = '';
    _syncMinTimestamp = 0;
    _syncMaxTimestamp = 0;
    _syncedPackets = 0;
  }

  /// Smart sync: check data range, skip already-synced data, multi-round loop.
  Future<void> smartSync() async {
    if (_cmdToChar == null || _syncingHistory) return;
    _syncingHistory = true;
    _resetSyncCounters();
    notifyListeners();

    debugPrint('BLE: === Smart sync ===');
    _setSyncStatus('Smart sync...');

    try {
      // 1. Abort any leftover dump
      await _sendCommand(_buildCommand(0x14, [])); // ABORT_HISTORICAL
      await Future.delayed(const Duration(milliseconds: 500));

      // 2. Query data range
      await _sendCommand(_buildCommand(0x22, [])); // GET_DATA_RANGE
      await Future.delayed(const Duration(seconds: 2));

      // 3. Check DB for newest timestamp
      final dbMaxTs = await _sensorDb?.newestTimestamp;
      final nowMs = DateTime.now().millisecondsSinceEpoch;
      final nowSec = nowMs ~/ 1000;

      if (dbMaxTs != null && dbMaxTs > 0) {
        final gapMs = nowMs - dbMaxTs;
        final gapMinutes = gapMs / 60000;
        debugPrint('BLE: Smart sync: DB up to ${_formatTs(dbMaxTs ~/ 1000)}, gap ${gapMinutes.toStringAsFixed(1)} min');

        // If DB is very recent (< 3 min), skip sync entirely — data is current
        if (gapMs < 180000) {
          debugPrint('BLE: Smart sync: DB is current (${gapMinutes.toStringAsFixed(1)} min ago), skipping');
          _setSyncStatus('Up to date');
          return;
        }

        // If DB is recent (< 10 min), advance trim past old data
        if (gapMs < 600000) {
          debugPrint('BLE: Smart sync: DB recent, advancing trim...');
          _setSyncStatus('Advancing trim...');

          _trimAllCompleted = false;
          await _sendCommand(_buildForceTrimAll());
          var waited = 0;
          while (!_trimAllCompleted && waited < 15000) {
            await Future.delayed(const Duration(milliseconds: 500));
            waited += 500;
          }
          if (_trimAllCompleted) {
            debugPrint('BLE: TrimAll completed in ${waited}ms');
          } else {
            debugPrint('BLE: TrimAll timeout after ${waited}ms, continuing');
          }
          _resetSyncCounters();
          _setSyncStatus('Fetching new data...');
        }
      } else {
        // No DB data — first sync
        debugPrint('BLE: Smart sync: no DB data, syncing from current trim');
      }

      // 4. Run multi-round sync loop
      await _syncLoop(nowSec, dbMaxTs);

      // NOTE: Do NOT trim here. Trim happens after successful cloud upload.
      // This matches the official Whoop app flow:
      //   sync BLE → upload to cloud → trim strap
    } catch (e) {
      debugPrint('BLE: Smart sync error: $e');
    } finally {
      _syncingHistory = false;
      if (_syncNewRecords > 0) {
        _syncStatus = 'Synced $_syncNewRecords new';
      } else if (_syncedPackets == 0) {
        _syncStatus = 'Up to date';
      } else {
        _syncStatus = 'Done: no new ($_syncedPackets pkts)';
      }
      _status = _connected ? 'Connected' : 'Disconnected';
      notifyListeners();
    }
  }

  /// Full sync: rewind trim to beginning, re-download ALL data.
  Future<void> fullSync() async {
    if (_cmdToChar == null || _syncingHistory) return;
    _syncingHistory = true;
    _resetSyncCounters();
    notifyListeners();

    debugPrint('BLE: === Full sync ===');
    _setSyncStatus('Full sync — rewinding...');

    try {
      // 1. Abort + rewind trim to start
      await _sendCommand(_buildCommand(0x14, [])); // ABORT_HISTORICAL
      await Future.delayed(const Duration(milliseconds: 500));
      await _sendCommand(_buildForceTrim(0, 0)); // FORCE_TRIM(0,0)
      await Future.delayed(const Duration(seconds: 1));
      await _sendCommand(_buildSetReadPointer(0, 0)); // SET_READ_POINTER(0,0)
      await Future.delayed(const Duration(milliseconds: 500));
      await _sendCommand(_buildCommand(0x22, [])); // GET_DATA_RANGE
      await Future.delayed(const Duration(seconds: 2));

      // 2. Run multi-round sync loop
      final nowSec = DateTime.now().millisecondsSinceEpoch ~/ 1000;
      await _syncLoop(nowSec, null);
    } catch (e) {
      debugPrint('BLE: Full sync error: $e');
    } finally {
      _syncingHistory = false;
      if (_syncNewRecords > 0) {
        _syncStatus = 'Synced $_syncNewRecords new';
      } else {
        _syncStatus = 'Done: $_syncedPackets pkts';
      }
      _status = _connected ? 'Connected' : 'Disconnected';
      notifyListeners();
    }
  }

  /// Unified sync: BLE sync → upload to cloud → refresh → trim.
  /// This is the single entry point called by the UI "Sync" button.
  Future<void> unifiedSync({bool full = false}) async {
    if (_syncPhase != SyncPhase.idle) return;

    try {
      // Phase 1: BLE sync
      _syncPhase = SyncPhase.bleSyncing;
      _syncPhaseMessage = full ? 'Full sync from strap...' : 'Syncing strap...';
      notifyListeners();

      if (full) {
        await fullSync();
      } else {
        await smartSync();
      }

      // Phase 2: Upload to cloud
      if (_syncNewRecords > 0 && _uploadService != null) {
        _syncPhase = SyncPhase.uploading;
        _syncPhaseMessage = 'Uploading to cloud...';
        notifyListeners();

        final uploaded = await _uploadService!.syncToCloud();

        if (uploaded > 0) {
          _syncPhaseMessage = 'Uploaded $uploaded records';
        } else {
          _syncPhaseMessage = 'Upload skipped (no auth or up to date)';
        }
        notifyListeners();
      }

      // Phase 3: Trigger cloud data refresh
      _syncPhase = SyncPhase.refreshing;
      _syncPhaseMessage = 'Refreshing data...';
      notifyListeners();
      onSyncComplete?.call();

      // Phase 4: Done
      _syncPhase = SyncPhase.done;
      _syncPhaseMessage = _syncNewRecords > 0
          ? 'Synced $_syncNewRecords records'
          : 'Up to date';
      notifyListeners();
    } catch (e) {
      _syncPhase = SyncPhase.error;
      _syncPhaseMessage = 'Error: ${e.toString().split('\n').first}';
      notifyListeners();
      debugPrint('BLE: Unified sync error: $e');
    }

    // Auto-reset after delay
    Future.delayed(const Duration(seconds: 5), () {
      if (_syncPhase == SyncPhase.done || _syncPhase == SyncPhase.error) {
        _syncPhase = SyncPhase.idle;
        _syncPhaseMessage = '';
        notifyListeners();
      }
    });
  }

  /// Multi-round sync loop: send HISTORICAL_DATA, wait for burst, repeat.
  Future<void> _syncLoop(int nowSec, int? dbMaxTs) async {
    var emptyRounds = 0;
    var round = 0;
    // Convert dbMaxTs from ms to sec for comparison
    final dbMaxSec = dbMaxTs != null ? dbMaxTs ~/ 1000 : 0;

    while (_syncingHistory && emptyRounds < 3 && round < _maxRounds) {
      round++;
      _syncRound = round;

      final latestTs = _syncMaxTimestamp > 0 ? ' @ ${_formatTime(_syncMaxTimestamp)}' : '';
      debugPrint('BLE: === Sync round $round ($_syncNewRecords new, $_syncedPackets pkts)$latestTs ===');
      _setSyncStatus('Round $round$latestTs — $_syncNewRecords new');

      final countBefore = _syncedPackets;
      final newBefore = _syncNewRecords;

      // Send HISTORICAL_DATA command
      await _sendCommand(_buildCommand(0x16, []));

      // Wait for burst to complete (detect by silence)
      var waitMs = 0;
      var lastCount = countBefore;
      const maxWaitMs = 15000;
      const idleTimeout = 5000;

      while (waitMs < maxWaitMs) {
        await Future.delayed(const Duration(seconds: 2));
        waitMs += 2000;
        final current = _syncedPackets;
        if (current == lastCount && current > countBefore) break; // Burst done
        if (current == countBefore && waitMs > idleTimeout) break; // No data
        lastCount = current;
      }

      final newPackets = _syncedPackets - countBefore;
      final newDbThisRound = _syncNewRecords - newBefore;
      debugPrint('BLE: Round $round: $newPackets pkts, $newDbThisRound new DB');

      if (newPackets == 0) {
        emptyRounds++;
        debugPrint('BLE: Empty round ($emptyRounds/3)');
      } else {
        emptyRounds = 0;
      }

      // Early exit: stop when caught up to recent data
      if (_syncMaxTimestamp > 0) {
        final reachedRecent = _syncMaxTimestamp >= (nowSec - 7200);
        final pastDb = dbMaxSec > 0 && _syncMaxTimestamp >= dbMaxSec;
        if (reachedRecent || pastDb) {
          debugPrint('BLE: Smart sync caught up (latest: ${_formatTs(_syncMaxTimestamp)})');
          break;
        }
      }

      await Future.delayed(const Duration(milliseconds: 500));
    }
  }

  // === Command builders for sync ===

  /// FORCE_TRIM(0xFEFEFEFE, 0xFEFEFEFE) — mark all data as consumed
  Uint8List _buildForceTrimAll() {
    return _buildCommand(0x19, [
      0xFE, 0xFE, 0xFE, 0xFE, // sector = sentinel
      0xFE, 0xFE, 0xFE, 0xFE, // offset = sentinel
    ]);
  }

  /// FORCE_TRIM(sector, offset) — set trim pointer to specific position
  Uint8List _buildForceTrim(int sector, int offset) {
    return _buildCommand(0x19, [
      sector & 0xFF, (sector >> 8) & 0xFF, (sector >> 16) & 0xFF, (sector >> 24) & 0xFF,
      offset & 0xFF, (offset >> 8) & 0xFF, (offset >> 16) & 0xFF, (offset >> 24) & 0xFF,
    ]);
  }

  /// SET_READ_POINTER(sector, offset)
  Uint8List _buildSetReadPointer(int sector, int offset) {
    return _buildCommand(0x21, [
      sector & 0xFF, (sector >> 8) & 0xFF, (sector >> 16) & 0xFF, (sector >> 24) & 0xFF,
      offset & 0xFF, (offset >> 8) & 0xFF, (offset >> 16) & 0xFF, (offset >> 24) & 0xFF,
    ]);
  }

  String _formatTs(int unixSec) {
    final dt = DateTime.fromMillisecondsSinceEpoch(unixSec * 1000);
    return '${dt.month}/${dt.day} ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }

  String _formatTime(int unixSec) {
    final dt = DateTime.fromMillisecondsSinceEpoch(unixSec * 1000);
    return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }

  // === Legacy API (kept for backward compatibility) ===

  Future<void> requestHistoricalData() async {
    await smartSync();
  }

  // === Data handlers ===

  void _onHrData(List<int> data) {
    if (data.isEmpty) return;
    final flags = data[0];
    final is16bit = (flags & 0x01) != 0;
    int hr;
    if (is16bit && data.length >= 3) {
      hr = data[1] | (data[2] << 8);
    } else if (data.length >= 2) {
      hr = data[1];
    } else {
      return;
    }
    if (hr > 0 && hr < 250) {
      _heartRate = hr;
      _lastHrTime = DateTime.now();
      _hrHistory.add(hr);
      if (_hrHistory.length > 60) _hrHistory.removeAt(0);
      // Don't overwrite _status with HR — it causes flicker and hides connection state
      notifyListeners();

      // Store live HR to sensor DB
      _sensorDb?.insertBatch([{
        'timestamp': DateTime.now().millisecondsSinceEpoch,
        'heart_rate': hr,
      }]);
    }
  }

  void _onCmdResponse(List<int> data) {
    if (data.length < 12) return;
    final parsed = _parseResponse(Uint8List.fromList(data));
    if (parsed == null) return;

    final cmdCode = parsed.cmdCode & 0xFF;
    final paramsHex = parsed.params.map((b) => (b & 0xFF).toRadixString(16).padLeft(2, '0')).join(' ');
    debugPrint('BLE CMD RSP: 0x${cmdCode.toRadixString(16)} params=${parsed.params.length}B [$paramsHex]');

    switch (cmdCode) {
      case 0x1A: // GET_BATTERY_LEVEL
        // Response format: [version, charging_flag, battery%]
        if (parsed.params.length >= 3) {
          _isCharging = (parsed.params[1] & 0xFF) == 1;
          _batteryLevel = parsed.params[2] & 0xFF;
        } else if (parsed.params.isNotEmpty) {
          _batteryLevel = parsed.params[0] & 0xFF;
        }
        debugPrint('BLE: Battery $_batteryLevel% charging=$_isCharging (params: $paramsHex)');
        notifyListeners();
        break;
      case 0x91: // GET_HELLO_EXT
        final buf = StringBuffer();
        for (final b in parsed.params) {
          final c = b & 0xFF;
          if ((c >= 0x30 && c <= 0x39) || (c >= 0x41 && c <= 0x5A) || (c >= 0x61 && c <= 0x7A)) {
            buf.writeCharCode(c);
          }
        }
        final serial = buf.toString();
        if (serial.isNotEmpty) {
          _deviceSerial = serial;
          debugPrint('BLE: Serial: $_deviceSerial');
          notifyListeners();
        }
        break;
      case 0x8D: // GET_ADVERTISING_NAME
        if (parsed.params.length > 3) {
          final hwRev = parsed.params[0] & 0xFF;
          final fw1 = parsed.params[1] & 0xFF;
          final fw2 = parsed.params[2] & 0xFF;
          _firmwareInfo = 'HW:$hwRev FW:$fw1.$fw2';
          final nameBuf = StringBuffer();
          for (var i = 3; i < parsed.params.length; i++) {
            final c = parsed.params[i] & 0xFF;
            if (c == 0) break;
            if (c >= 0x20 && c <= 0x7E) nameBuf.writeCharCode(c);
          }
          final name = nameBuf.toString().trim();
          if (name.isNotEmpty) _deviceName = name;
          debugPrint('BLE: Name: $_deviceName, FW: $_firmwareInfo');
          notifyListeners();
        }
        break;
      case 0x22: // GET_DATA_RANGE
        _parseDataRange(parsed.params);
        break;
      case 0x16: // SEND_HISTORICAL_DATA response
        debugPrint('BLE: Historical data response: $paramsHex');
        break;
      case 0x19: // FORCE_TRIM response
        debugPrint('BLE: Force trim response: $paramsHex');
        break;
    }
  }

  void _parseDataRange(Uint8List params) {
    if (params.length < 63) {
      debugPrint('BLE: DATA_RANGE too short: ${params.length}B');
      return;
    }

    final nowSec = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    final bd = ByteData.sublistView(params);

    // Extract timestamps at known offsets (LE uint32)
    for (final off in [35, 43, 51, 59]) {
      if (off + 4 <= params.length) {
        final ts = bd.getUint32(off, Endian.little);
        if (ts > (nowSec - 86400 * 400) && ts < (nowSec + 86400)) {
          final dt = DateTime.fromMillisecondsSinceEpoch(ts * 1000);
          debugPrint('BLE: DATA_RANGE @$off: $ts (${dt.toLocal()})');
        }
      }
    }

    // @43 = trim position (data start)
    if (params.length >= 47) {
      final ts = bd.getUint32(43, Endian.little);
      if (ts > (nowSec - 86400 * 400) && ts < (nowSec + 86400)) {
        _dataRangeStart = ts;
      }
    }
    // @59 = write head (data end)
    if (params.length >= 63) {
      final ts = bd.getUint32(59, Endian.little);
      if (ts > (nowSec - 86400 * 400) && ts < (nowSec + 86400)) {
        _dataRangeEnd = ts;
      }
    }

    if (_dataRangeStart > 0 && _dataRangeEnd > 0) {
      final days = (_dataRangeEnd - _dataRangeStart) / 86400.0;
      debugPrint('BLE: Data range: ${_formatTs(_dataRangeStart)} -> ${_formatTs(_dataRangeEnd)} (${days.toStringAsFixed(1)} days)');
    }
  }

  void _onEventData(List<int> data) {
    // Check for AA01 framed sensor packets
    if (data.length >= 12 && data[0] == 0xAA && data[1] == 0x01) {
      final parsed = _parseResponse(Uint8List.fromList(data));
      if (parsed != null) {
        final packetType = parsed.params.isNotEmpty ? parsed.params[0] & 0xFF : 0;

        // 0x2F = sensor data
        if (packetType == 0x2F && parsed.params.length >= 52) {
          _parseSensorPacket(parsed.params);
          _syncedPackets++;
          if (_syncingHistory) {
            _resetSyncTimeout();
            notifyListeners();
          }
        }

        // 0x32 = console logs (detect TrimAll completion)
        if (packetType == 0x32 && parsed.params.length > 3) {
          final ascii = parsed.params.sublist(3)
              .where((b) => (b & 0xFF) >= 0x20 && (b & 0xFF) <= 0x7E)
              .map((b) => String.fromCharCode(b & 0xFF))
              .join();
          if (ascii.isNotEmpty) {
            debugPrint('BLE CONSOLE: $ascii');
            if (ascii.contains('leaving TrimAll') || ascii.contains('TrimAllCount')) {
              _trimAllCompleted = true;
              debugPrint('BLE: TrimAll completed');
            }
          }
        }
      }
    }
  }

  void _parseSensorPacket(Uint8List inner) {
    if (inner.length < 52) return;

    // Extract timestamp from bytes 7-10 (big-endian seconds since epoch)
    int timestampSec = 0;
    if (inner.length > 10) {
      timestampSec = (inner[7] << 24) | (inner[8] << 16) | (inner[9] << 8) | inner[10];
    }
    final timestampMs = timestampSec > 0
        ? timestampSec * 1000
        : DateTime.now().millisecondsSinceEpoch;

    // Track sync date range
    if (timestampSec > 0) {
      if (_syncMinTimestamp == 0 || timestampSec < _syncMinTimestamp) {
        _syncMinTimestamp = timestampSec;
      }
      if (timestampSec > _syncMaxTimestamp) {
        _syncMaxTimestamp = timestampSec;
        _syncDateRange = '${_formatTs(_syncMinTimestamp)} -> ${_formatTs(_syncMaxTimestamp)}';
      }
    }

    // SpO2 at byte 14
    final spo2 = inner.length > 14 ? inner[14] & 0xFF : 0;

    // RR interval count at byte 15, RR intervals at bytes 16-21 (LE uint16)
    int rr1 = 0, rr2 = 0, rr3 = 0;
    if (inner.length > 20) {
      rr1 = (inner[15] << 8) | inner[16];
      rr2 = (inner[17] << 8) | inner[18];
      rr3 = (inner[19] << 8) | inner[20];
    }

    // Compute HR from RR
    int hr = 0;
    final validRr = [rr1, rr2, rr3].where((v) => v > 200 && v < 2000).toList();
    if (validRr.isNotEmpty) {
      final avgRr = validRr.reduce((a, b) => a + b) / validRr.length;
      hr = (60000 / avgRr).round();
    }

    // Gyro at bytes 36-39 (float32)
    double gyro = 0;
    if (inner.length > 39) {
      final bd = ByteData.sublistView(inner, 36, 40);
      gyro = bd.getFloat32(0, Endian.big);
    }

    // Accel at bytes 40-51 (3 x float32)
    double accelX = 0, accelY = 0, accelZ = 0;
    if (inner.length > 51) {
      final bd = ByteData.sublistView(inner, 40, 52);
      accelX = bd.getFloat32(0, Endian.big);
      accelY = bd.getFloat32(4, Endian.big);
      accelZ = bd.getFloat32(8, Endian.big);
    }

    final rawHex = inner.map((b) => (b & 0xFF).toRadixString(16).padLeft(2, '0')).join(' ');

    // Insert into DB — track new records count
    _sensorDb?.insertBatch([{
      'timestamp': timestampMs,
      'heart_rate': hr > 0 ? hr : null,
      'rr1': rr1 > 0 ? rr1 : null,
      'rr2': rr2 > 0 ? rr2 : null,
      'rr3': rr3 > 0 ? rr3 : null,
      'spo2': spo2 > 0 ? spo2 : null,
      'accel_x': accelX != 0 ? accelX : null,
      'accel_y': accelY != 0 ? accelY : null,
      'accel_z': accelZ != 0 ? accelZ : null,
      'gyro': gyro != 0 ? gyro : null,
      'raw_hex': rawHex,
    }]).then((_) {
      // UNIQUE constraint means insertBatch with ConflictAlgorithm.ignore
      // won't throw on duplicates — DB notifyListeners tells us about new records
      _syncNewRecords++;
    });

    // Log every 50th packet for debugging
    if (_syncedPackets % 50 == 0 && timestampSec > 0) {
      debugPrint('BLE: SYNC #$_syncedPackets: ${_formatTime(timestampSec)} HR=$hr SpO2=$spo2 [new=$_syncNewRecords]');
    }
  }

  void _resetSyncTimeout() {
    _syncTimeoutTimer?.cancel();
    _syncTimeoutTimer = Timer(const Duration(seconds: 8), () {
      if (_syncingHistory) {
        _syncingHistory = false;
        notifyListeners();
        debugPrint('BLE: Historical sync complete (timeout). Packets: $_syncedPackets');
      }
    });
  }

  // === Command protocol (AA01 framing) ===

  Future<void> _sendCommand(Uint8List data) async {
    final char = _cmdToChar;
    if (char == null) return;
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        await char.write(data.toList(), withoutResponse: false);
        return;
      } catch (e) {
        if (attempt < 2 && e.toString().contains('BUSY')) {
          await Future.delayed(Duration(milliseconds: 200 * (attempt + 1)));
        } else {
          debugPrint('BLE write error: $e');
        }
      }
    }
  }

  Uint8List _buildCommand(int cmdCode, List<int> params) {
    final rawPayloadLen = 3 + params.length;
    final paddingNeeded = rawPayloadLen % 4 != 0 ? 4 - (rawPayloadLen % 4) : 0;
    final payloadLen = rawPayloadLen + paddingNeeded;
    final lengthField = payloadLen + 4;
    final totalLen = 8 + payloadLen + 4;

    final buf = ByteData(totalLen);
    var offset = 0;

    buf.setUint8(offset++, 0xAA);
    buf.setUint8(offset++, 0x01);
    buf.setUint16(offset, lengthField, Endian.little); offset += 2;
    buf.setUint8(offset++, 0x00);
    buf.setUint8(offset++, 0x01);

    final headerBytes = Uint8List(6);
    for (var i = 0; i < 6; i++) headerBytes[i] = buf.getUint8(i);
    final headerCrc = _crc16Modbus(headerBytes);
    buf.setUint16(offset, headerCrc, Endian.little); offset += 2;

    final payloadStart = offset;
    buf.setUint8(offset++, 0x23);
    buf.setUint8(offset++, _nextSeq());
    buf.setUint8(offset++, cmdCode & 0xFF);
    for (final b in params) buf.setUint8(offset++, b & 0xFF);
    for (var i = 0; i < paddingNeeded; i++) buf.setUint8(offset++, 0x00);

    final payloadBytes = Uint8List(offset - payloadStart);
    for (var i = 0; i < payloadBytes.length; i++) {
      payloadBytes[i] = buf.getUint8(payloadStart + i);
    }
    final payloadCrc = _crc32(payloadBytes);
    buf.setUint32(offset, payloadCrc, Endian.little);

    return Uint8List.view(buf.buffer);
  }

  int _nextSeq() {
    final s = _sequenceNumber;
    _sequenceNumber = (_sequenceNumber + 1) & 0xFF;
    return s;
  }

  _ParsedPacket? _parseResponse(Uint8List data) {
    if (data.length < 12 || data[0] != 0xAA) return null;
    final payloadEnd = data.length - 4;
    if (payloadEnd <= 8) return null;
    final payload = data.sublist(8, payloadEnd);
    if (payload.length < 3) return null;
    return _ParsedPacket(
      cmdCode: payload[2],
      params: payload.length > 3 ? payload.sublist(3) : Uint8List(0),
    );
  }

  static int _crc16Modbus(Uint8List data) {
    var crc = 0xFFFF;
    for (final b in data) {
      crc ^= b & 0xFF;
      for (var i = 0; i < 8; i++) {
        crc = (crc & 1) != 0 ? (crc >> 1) ^ 0xA001 : crc >> 1;
      }
    }
    return crc & 0xFFFF;
  }

  static int _crc32(Uint8List data) {
    var crc = 0xFFFFFFFF;
    for (final b in data) {
      crc ^= b & 0xFF;
      for (var i = 0; i < 8; i++) {
        crc = (crc & 1) != 0 ? (crc >> 1) ^ 0xEDB88320 : crc >> 1;
      }
    }
    return crc ^ 0xFFFFFFFF;
  }

  Future<void> requestBattery() async {
    if (_cmdToChar != null) {
      await _sendCommand(_buildCommand(0x1A, []));
    }
  }

  /// Public trim: advance strap trim pointer past all downloaded data.
  /// Called by UploadService after successful cloud upload (official app flow).
  Future<void> trimAll() async {
    if (_cmdToChar == null || !_connected) return;
    debugPrint('BLE: Trim after upload — advancing strap trim pointer');
    _trimAllCompleted = false;
    await _sendCommand(_buildForceTrimAll());
    // Wait for trim confirmation (up to 15s)
    var waited = 0;
    while (!_trimAllCompleted && waited < 15000) {
      await Future.delayed(const Duration(milliseconds: 500));
      waited += 500;
    }
    debugPrint('BLE: TrimAll ${_trimAllCompleted ? 'completed' : 'timeout'} in ${waited}ms');
  }
}

class _StrapProfile {
  final String name;
  final Guid service;
  final Guid cmdTo;
  final Guid cmdFrom;
  final Guid events;
  const _StrapProfile(this.name, this.service, this.cmdTo, this.cmdFrom, this.events);
}

class _ParsedPacket {
  final int cmdCode;
  final Uint8List params;
  _ParsedPacket({required this.cmdCode, required this.params});
}
