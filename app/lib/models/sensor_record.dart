import 'dart:math';

class SensorRecord {
  final int timestamp;
  final int heartRate;
  final int rr1Ms;
  final int rr2Ms;
  final int rr3Ms;
  final double accelX;
  final double accelY;
  final double accelZ;
  final double gyro;
  final int spo2;

  SensorRecord({
    required this.timestamp,
    required this.heartRate,
    required this.rr1Ms,
    required this.rr2Ms,
    required this.rr3Ms,
    required this.accelX,
    required this.accelY,
    required this.accelZ,
    required this.gyro,
    required this.spo2,
  });

  factory SensorRecord.fromDb(Map<String, dynamic> row) {
    return SensorRecord(
      timestamp: (row['timestamp'] as num?)?.toInt() ?? 0,
      heartRate: (row['heart_rate'] as num?)?.toInt() ?? 0,
      rr1Ms: (row['rr1'] as num?)?.toInt() ?? 0,
      rr2Ms: (row['rr2'] as num?)?.toInt() ?? 0,
      rr3Ms: (row['rr3'] as num?)?.toInt() ?? 0,
      accelX: (row['accel_x'] as num?)?.toDouble() ?? 0,
      accelY: (row['accel_y'] as num?)?.toDouble() ?? 0,
      accelZ: (row['accel_z'] as num?)?.toDouble() ?? 0,
      gyro: (row['gyro'] as num?)?.toDouble() ?? 0,
      spo2: (row['spo2'] as num?)?.toInt() ?? 0,
    );
  }

  DateTime get dateTime =>
      DateTime.fromMillisecondsSinceEpoch(timestamp, isUtc: true).toLocal();

  double get movement {
    final mag = accelX * accelX + accelY * accelY + accelZ * accelZ;
    return mag > 0 ? sqrt(mag) - 1.0 : 0.0;
  }
}
