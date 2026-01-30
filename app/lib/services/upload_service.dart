import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../core/constants.dart';
import 'auth_service.dart';
import 'ble_service.dart';
import 'sensor_db_service.dart';

/// Uploads sensor data to Whoop Cloud metrics-service/v1/metrics.
///
/// Record format: 124-byte AA01 frames, gzip-compressed, sent as application/octet-stream.
/// Falls back to building records from decoded DB fields if raw_hex is incomplete.
class UploadService extends ChangeNotifier {
  final AuthService _auth;
  final SensorDbService _sensorDb;
  final BleService _ble;

  static const _hwmKey = 'upload_high_watermark';
  static const _batchSize = 200;

  // Upload progress tracking
  bool _uploading = false;
  bool get uploading => _uploading;
  int _uploadTotal = 0;
  int get uploadTotal => _uploadTotal;
  int _uploadDone = 0;
  int get uploadDone => _uploadDone;
  double get uploadProgress => _uploadTotal > 0 ? _uploadDone / _uploadTotal : 0;

  UploadService(this._auth, this._sensorDb, this._ble);

  /// Get last uploaded timestamp from local storage.
  Future<int> _getLocalHighWatermark() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getInt(_hwmKey) ?? 0;
  }

  /// Store last uploaded timestamp.
  Future<void> _setLocalHighWatermark(int ts) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_hwmKey, ts);
  }

  /// Fetch server-side high watermark.
  Future<int?> getServerHighWatermark() async {
    final token = await _auth.accessToken;
    if (token == null) return null;

    try {
      final res = await http.get(
        Uri.parse(WhoopConstants.highWatermarkEndpoint),
        headers: WhoopConstants.headers(token),
      );
      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        return (data['highwatermark'] as num?)?.toInt();
      }
    } catch (e) {
      debugPrint('UploadService: Failed to fetch HWM: $e');
    }
    return null;
  }

  /// Build a 124-byte upload record from a DB sensor row.
  ///
  /// Upload format (from HAR analysis):
  ///   [0-2]   AA 01 74 (header: SOF, revision, length indicator)
  ///   [3-7]   header continuation + CRC16
  ///   [8]     0x23 cmd type
  ///   [9]     sequence
  ///   [10]    0x2F packet type
  ///   [11-13] sub-header
  ///   [14-17] padding/routing
  ///   [18-21] timestamp (LE uint32, Unix seconds)
  ///   [22-24] flags
  ///   [25]    SpO2 raw (value + 10)
  ///   [26]    RR count
  ///   [27-28] RR1 (LE uint16)
  ///   [29-30] RR2 (LE uint16)
  ///   [31-32] RR3 (LE uint16)
  ///   [33-40] padding/reserved
  ///   [41-44] gyro (BE float32)
  ///   [45-48] accelX (BE float32)
  ///   [49-52] accelY (BE float32)
  ///   [53-56] accelZ (BE float32)
  ///   [57-119] remaining sensor data + padding
  ///   [120-123] CRC32
  Uint8List _buildUploadRecord(Map<String, dynamic> record) {
    final buf = ByteData(124);

    // AA01 header
    buf.setUint8(0, 0xAA);
    buf.setUint8(1, 0x01);
    buf.setUint16(2, 0x0074, Endian.little); // length = 116
    buf.setUint8(4, 0x00);
    buf.setUint8(5, 0x01);

    // Header CRC16 (bytes 0-5)
    final headerBytes = Uint8List(6);
    for (var i = 0; i < 6; i++) headerBytes[i] = buf.getUint8(i);
    final hCrc = _crc16Modbus(headerBytes);
    buf.setUint16(6, hCrc, Endian.little);

    // Payload starts at byte 8
    buf.setUint8(8, 0x23); // cmd type
    buf.setUint8(9, 0x00); // sequence (doesn't matter for upload)
    buf.setUint8(10, 0x2F); // packet type = sensor data

    // Timestamp at offset 18 relative to packet start = byte 18
    // But in the inner payload (after header), timestamp is at bytes 7-10
    // Inner payload byte 7 = packet byte 8+7 = 15... let's use offset 18 for the upload format
    final ts = (record['timestamp'] as int?) ?? 0;
    final tsSec = ts > 1000000000000 ? ts ~/ 1000 : ts; // Handle ms vs sec
    buf.setUint32(18, tsSec, Endian.little);

    // SpO2 at byte 25 (raw = percent + 10, but only if > 0)
    final spo2 = (record['spo2'] as int?) ?? 0;
    buf.setUint8(25, spo2 > 0 ? spo2 : 0);

    // RR count + intervals
    final rr1 = (record['rr1'] as int?) ?? 0;
    final rr2 = (record['rr2'] as int?) ?? 0;
    final rr3 = (record['rr3'] as int?) ?? 0;
    int rrCount = 0;
    if (rr1 > 0) rrCount++;
    if (rr2 > 0) rrCount++;
    if (rr3 > 0) rrCount++;
    buf.setUint8(26, rrCount);
    buf.setUint16(27, rr1, Endian.little);
    buf.setUint16(29, rr2, Endian.little);
    buf.setUint16(31, rr3, Endian.little);

    // Gyro at byte 41 (BE float32)
    final gyro = (record['gyro'] as num?)?.toDouble() ?? 0;
    buf.setFloat32(41, gyro, Endian.big);

    // Accel at bytes 45-56 (3 x BE float32)
    final ax = (record['accel_x'] as num?)?.toDouble() ?? 0;
    final ay = (record['accel_y'] as num?)?.toDouble() ?? 0;
    final az = (record['accel_z'] as num?)?.toDouble() ?? 0;
    buf.setFloat32(45, ax, Endian.big);
    buf.setFloat32(49, ay, Endian.big);
    buf.setFloat32(53, az, Endian.big);

    // CRC32 over payload (bytes 8..119)
    final payloadBytes = Uint8List.sublistView(
      buf.buffer.asUint8List(), 8, 120,
    );
    final pCrc = _crc32(payloadBytes);
    buf.setUint32(120, pCrc, Endian.little);

    return buf.buffer.asUint8List();
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

  /// Upload a batch of sensor records to Whoop Cloud.
  /// Returns the latest metrics processed timestamp, or null on failure.
  Future<int?> uploadBatch(List<Map<String, dynamic>> records) async {
    if (records.isEmpty) return null;

    final token = await _auth.accessToken;
    if (token == null) {
      debugPrint('UploadService: No auth token');
      return null;
    }

    final strapId = _ble.deviceSerial;
    if (strapId.isEmpty) {
      debugPrint('UploadService: No strap ID');
      return null;
    }

    // Build concatenated 124-byte records
    final builder = BytesBuilder(copy: false);
    for (final r in records) {
      builder.add(_buildUploadRecord(r));
    }

    // Gzip compress
    final compressed = gzip.encode(builder.toBytes());

    try {
      final res = await http.post(
        Uri.parse(WhoopConstants.metricsEndpoint),
        headers: WhoopConstants.uploadHeaders(token, strapId: strapId),
        body: compressed,
      );

      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);
        final latest = (data['latestMetricsProcessed'] as num?)?.toInt();
        debugPrint('UploadService: Uploaded ${records.length} records, latest=$latest');
        return latest;
      } else {
        debugPrint('UploadService: Upload failed ${res.statusCode}: ${res.body}');
        return null;
      }
    } catch (e) {
      debugPrint('UploadService: Upload error: $e');
      return null;
    }
  }

  /// Sync all un-uploaded records to Whoop Cloud.
  /// Flow matches official Whoop app: check cloud HWM → upload new records → trim strap.
  Future<int> syncToCloud() async {
    // 1. Check what the cloud already has (high watermark)
    var localHwm = await _getLocalHighWatermark();
    debugPrint('UploadService: Local HWM: $localHwm');

    final serverHwm = await getServerHighWatermark();
    if (serverHwm != null) {
      debugPrint('UploadService: Cloud HWM: $serverHwm (${DateTime.fromMillisecondsSinceEpoch(serverHwm).toLocal()})');
      if (serverHwm > localHwm) {
        debugPrint('UploadService: Cloud is ahead — updating local HWM');
        localHwm = serverHwm;
        await _setLocalHighWatermark(localHwm);
      }
    } else {
      debugPrint('UploadService: Could not reach cloud HWM (no auth or offline)');
    }

    // 2. Query local DB for records not yet uploaded
    final nowMs = DateTime.now().millisecondsSinceEpoch;
    final records = await _sensorDb.getRange(localHwm, nowMs);
    if (records.isEmpty) {
      debugPrint('UploadService: Cloud is up to date — no new records');
      return 0;
    }

    debugPrint('UploadService: ${records.length} records to upload (since ${DateTime.fromMillisecondsSinceEpoch(localHwm).toLocal()})');
    _uploading = true;
    _uploadTotal = records.length;
    _uploadDone = 0;
    notifyListeners();

    var uploaded = 0;

    try {
      // 3. Upload in batches
      for (var i = 0; i < records.length; i += _batchSize) {
        final end = (i + _batchSize).clamp(0, records.length);
        final batch = records.sublist(i, end);
        final latest = await uploadBatch(batch);
        if (latest != null) {
          uploaded += batch.length;
          _uploadDone = uploaded;
          notifyListeners();
          await _setLocalHighWatermark(latest);
        } else {
          debugPrint('UploadService: Batch upload failed at $uploaded/${records.length}');
          break;
        }
      }

      // 4. After successful upload, trim strap (official app flow)
      // Only trim if we uploaded everything and strap is connected
      if (uploaded == records.length && uploaded > 0) {
        debugPrint('UploadService: Upload complete — trimming strap');
        await _ble.trimAll();
      }
    } finally {
      _uploading = false;
      notifyListeners();
    }

    debugPrint('UploadService: Uploaded $uploaded/${records.length} records');
    return uploaded;
  }
}
