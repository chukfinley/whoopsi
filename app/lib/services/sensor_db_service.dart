import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

class SensorDbService extends ChangeNotifier {
  Database? _db;

  Future<void> init() async {
    final dir = await getApplicationSupportDirectory();
    final dbPath = p.join(dir.path, 'sensor_data.db');
    _db = await openDatabase(
      dbPath,
      version: 1,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER UNIQUE NOT NULL,
            heart_rate INTEGER,
            rr1 INTEGER, rr2 INTEGER, rr3 INTEGER,
            spo2 INTEGER,
            accel_x REAL, accel_y REAL, accel_z REAL,
            gyro REAL,
            raw_hex TEXT
          )
        ''');
        await db.execute('CREATE INDEX idx_ts ON sensor_data(timestamp)');
      },
    );
  }

  Future<void> insertBatch(List<Map<String, dynamic>> records) async {
    if (_db == null) return;
    final batch = _db!.batch();
    for (final rec in records) {
      batch.insert('sensor_data', rec, conflictAlgorithm: ConflictAlgorithm.ignore);
    }
    await batch.commit(noResult: true);
    notifyListeners();
  }

  Future<int> get recordCount async {
    if (_db == null) return 0;
    final result = await _db!.rawQuery('SELECT COUNT(*) as cnt FROM sensor_data');
    return Sqflite.firstIntValue(result) ?? 0;
  }

  Future<int?> get oldestTimestamp async {
    if (_db == null) return null;
    final result = await _db!.rawQuery('SELECT MIN(timestamp) as ts FROM sensor_data');
    return result.first['ts'] as int?;
  }

  Future<int?> get newestTimestamp async {
    if (_db == null) return null;
    final result = await _db!.rawQuery('SELECT MAX(timestamp) as ts FROM sensor_data');
    return result.first['ts'] as int?;
  }

  Future<List<Map<String, dynamic>>> getRange(int startTs, int endTs) async {
    if (_db == null) return [];
    return _db!.query(
      'sensor_data',
      where: 'timestamp >= ? AND timestamp <= ?',
      whereArgs: [startTs, endTs],
      orderBy: 'timestamp ASC',
    );
  }

  Future<List<Map<String, dynamic>>> getHrvRecords(int startTs, int endTs) async {
    if (_db == null) return [];
    return _db!.query(
      'sensor_data',
      columns: ['timestamp', 'rr1', 'rr2', 'rr3', 'heart_rate'],
      where: 'timestamp >= ? AND timestamp <= ? AND (rr1 > 0 OR rr2 > 0 OR rr3 > 0)',
      whereArgs: [startTs, endTs],
      orderBy: 'timestamp ASC',
    );
  }

  Future<List<Map<String, dynamic>>> getRecordsForDay(DateTime date) async {
    final startTs = DateTime(date.year, date.month, date.day).millisecondsSinceEpoch;
    final endTs = startTs + 86400000;
    return getRange(startTs, endTs);
  }

  Future<List<Map<String, dynamic>>> getRecentRecords(int limit) async {
    if (_db == null) return [];
    return _db!.query(
      'sensor_data',
      orderBy: 'timestamp DESC',
      limit: limit,
    );
  }

  Future<String> exportCsv({String? directory}) async {
    if (_db == null) return '';
    final records = await _db!.query('sensor_data', orderBy: 'timestamp ASC');
    final buf = StringBuffer();
    buf.writeln('timestamp,heart_rate,rr1,rr2,rr3,spo2,accel_x,accel_y,accel_z,gyro');
    for (final r in records) {
      buf.writeln('${r['timestamp']},${r['heart_rate'] ?? ''},${r['rr1'] ?? ''},${r['rr2'] ?? ''},${r['rr3'] ?? ''},${r['spo2'] ?? ''},${r['accel_x'] ?? ''},${r['accel_y'] ?? ''},${r['accel_z'] ?? ''},${r['gyro'] ?? ''}');
    }
    final dir = directory ?? (await getApplicationDocumentsDirectory()).path;
    final file = File('$dir/sensor_export.csv');
    await file.writeAsString(buf.toString());
    return file.path;
  }
}
