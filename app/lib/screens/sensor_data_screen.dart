import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/ble_service.dart';
import '../services/sensor_db_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class SensorDataScreen extends StatefulWidget {
  const SensorDataScreen({super.key});

  @override
  State<SensorDataScreen> createState() => _SensorDataScreenState();
}

class _SensorDataScreenState extends State<SensorDataScreen> {
  int _recordCount = 0;
  String? _oldestDate;
  String? _newestDate;
  String? _exportPath;
  List<Map<String, dynamic>> _recentRecords = [];

  @override
  void initState() {
    super.initState();
    _loadStats();
  }

  Future<void> _loadStats() async {
    final db = context.read<SensorDbService>();
    final count = await db.recordCount;
    final oldest = await db.oldestTimestamp;
    final newest = await db.newestTimestamp;
    final recent = await db.getRecentRecords(200);

    if (mounted) {
      setState(() {
        _recordCount = count;
        _oldestDate = oldest != null ? DateTime.fromMillisecondsSinceEpoch(oldest).toLocal().toString().substring(0, 16) : null;
        _newestDate = newest != null ? DateTime.fromMillisecondsSinceEpoch(newest).toLocal().toString().substring(0, 16) : null;
        _recentRecords = recent.reversed.toList();
      });
    }
  }

  Future<void> _export() async {
    final selectedDir = await FilePicker.platform.getDirectoryPath(
      dialogTitle: 'Choose CSV export folder',
    );
    if (selectedDir == null) return;
    final db = context.read<SensorDbService>();
    final path = await db.exportCsv(directory: selectedDir);
    if (mounted) {
      setState(() => _exportPath = path);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Exported to $path'), backgroundColor: WhoopTheme.surface),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        title: const Text('Sensor Data', style: TextStyle(fontWeight: FontWeight.bold)),
      ),
      body: SafeArea(
        child: Consumer<BleService>(
          builder: (context, ble, _) {
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                // Connection status
                GlassCard(
                  padding: const EdgeInsets.all(16),
                  child: Row(
                    children: [
                      Icon(
                        ble.connected ? Icons.bluetooth_connected : Icons.bluetooth_disabled,
                        color: ble.connected ? WhoopTheme.primary : WhoopTheme.textSecondary,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(ble.connected ? 'Connected' : 'Disconnected',
                                style: TextStyle(color: ble.connected ? WhoopTheme.primary : WhoopTheme.textSecondary, fontSize: 16, fontWeight: FontWeight.w600)),
                            Text(ble.status, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
                          ],
                        ),
                      ),
                      if (!ble.connected)
                        GestureDetector(
                          onTap: () => ble.connect(),
                          child: Container(
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                            decoration: BoxDecoration(color: WhoopTheme.primary.withValues(alpha:0.15), borderRadius: BorderRadius.circular(12)),
                            child: const Text('Connect', style: TextStyle(color: WhoopTheme.primary, fontSize: 13, fontWeight: FontWeight.w600)),
                          ),
                        ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),

                // Database stats
                GlassCard(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('DATABASE', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
                      const SizedBox(height: 12),
                      _statRow('Records', '$_recordCount'),
                      if (_oldestDate != null) _statRow('Oldest', _oldestDate!),
                      if (_newestDate != null) _statRow('Newest', _newestDate!),
                    ],
                  ),
                ),
                const SizedBox(height: 16),

                // Sync button
                GlassCard(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('SYNC FROM STRAP', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: GestureDetector(
                              onTap: ble.connected ? () async {
                                ble.requestHistoricalData();
                                // Refresh stats periodically
                                for (var i = 0; i < 30; i++) {
                                  await Future.delayed(const Duration(seconds: 2));
                                  if (mounted) _loadStats();
                                  if (!ble.syncingHistory) break;
                                }
                              } : null,
                              child: Container(
                                padding: const EdgeInsets.symmetric(vertical: 14),
                                decoration: BoxDecoration(
                                  color: ble.connected ? WhoopTheme.primary : WhoopTheme.divider,
                                  borderRadius: BorderRadius.circular(12),
                                ),
                                child: Center(
                                  child: Text(
                                    ble.syncingHistory ? 'Syncing... (${ble.syncedPackets} packets)' : 'Sync Historical Data',
                                    style: TextStyle(color: ble.connected ? Colors.black : WhoopTheme.textSecondary, fontSize: 14, fontWeight: FontWeight.w600),
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                      if (ble.syncingHistory)
                        Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: LinearProgressIndicator(
                            backgroundColor: WhoopTheme.divider,
                            color: WhoopTheme.primary,
                            minHeight: 3,
                          ),
                        ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),

                // Export
                GestureDetector(
                  onTap: _export,
                  child: GlassCard(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    child: Row(
                      children: [
                        const Icon(Icons.file_download_outlined, color: WhoopTheme.textPrimary, size: 20),
                        const SizedBox(width: 12),
                        const Expanded(child: Text('Export as CSV', style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 15, fontWeight: FontWeight.w500))),
                        Icon(Icons.chevron_right, color: WhoopTheme.textSecondary, size: 20),
                      ],
                    ),
                  ),
                ),
                if (_exportPath != null) ...[
                  const SizedBox(height: 8),
                  Text('Last export: $_exportPath', style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
                ],
                const SizedBox(height: 24),

                // Mini HR chart from recent records
                if (_recentRecords.isNotEmpty) ...[
                  const Text('RECENT HEART RATE', style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 0.5)),
                  const SizedBox(height: 8),
                  GlassCard(
                    padding: const EdgeInsets.all(12),
                    child: SizedBox(
                      height: 80,
                      child: CustomPaint(
                        size: const Size(double.infinity, 80),
                        painter: _SensorHrPainter(_recentRecords),
                      ),
                    ),
                  ),
                ],
                const SizedBox(height: 80),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _statRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 14)),
          Text(value, style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

class _SensorHrPainter extends CustomPainter {
  final List<Map<String, dynamic>> records;
  _SensorHrPainter(this.records);

  @override
  void paint(Canvas canvas, Size size) {
    final hrValues = records.where((r) => (r['heart_rate'] as int?) != null && (r['heart_rate'] as int) > 0).map((r) => (r['heart_rate'] as int).toDouble()).toList();
    if (hrValues.length < 2) return;

    final minHr = hrValues.reduce((a, b) => a < b ? a : b) - 5;
    final maxHr = hrValues.reduce((a, b) => a > b ? a : b) + 5;
    final range = maxHr - minHr;
    if (range <= 0) return;

    final paint = Paint()
      ..color = WhoopTheme.error.withValues(alpha:0.8)
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final path = Path();
    for (var i = 0; i < hrValues.length; i++) {
      final x = i / (hrValues.length - 1) * size.width;
      final y = (1 - (hrValues[i] - minHr) / range) * size.height;
      if (i == 0) path.moveTo(x, y); else path.lineTo(x, y);
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _SensorHrPainter old) => true;
}
