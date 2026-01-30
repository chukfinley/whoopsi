import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/ble_service.dart';
import '../services/sensor_db_service.dart';
import '../services/upload_service.dart';
import '../widgets/glass_card.dart';
import '../widgets/gradient_scaffold.dart';

class DeviceScreen extends StatefulWidget {
  const DeviceScreen({super.key});

  @override
  State<DeviceScreen> createState() => _DeviceScreenState();
}

class _DeviceScreenState extends State<DeviceScreen> {
  int _dbRecordCount = 0;
  String? _dbOldest;
  String? _dbNewest;
  bool _wasSyncing = false;

  @override
  void initState() {
    super.initState();
    _loadDbStats();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<BleService>().addListener(_onBleChange);
    });
  }

  @override
  void dispose() {
    try {
      context.read<BleService>().removeListener(_onBleChange);
    } catch (_) {}
    super.dispose();
  }

  void _onBleChange() {
    if (!mounted) return;
    final ble = context.read<BleService>();
    if (_wasSyncing && !ble.syncingHistory) {
      _loadDbStats();
    }
    _wasSyncing = ble.syncingHistory;
  }

  Future<void> _loadDbStats() async {
    final db = context.read<SensorDbService>();
    final count = await db.recordCount;
    final oldest = await db.oldestTimestamp;
    final newest = await db.newestTimestamp;
    if (mounted) {
      setState(() {
        _dbRecordCount = count;
        _dbOldest = oldest != null
            ? DateFormat('MMM d, HH:mm').format(
                DateTime.fromMillisecondsSinceEpoch(oldest).toLocal())
            : null;
        _dbNewest = newest != null
            ? DateFormat('MMM d, HH:mm').format(
                DateTime.fromMillisecondsSinceEpoch(newest).toLocal())
            : null;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return GradientScaffold(
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: const Text('Device', style: TextStyle(fontWeight: FontWeight.bold)),
        elevation: 0,
      ),
      body: Consumer2<BleService, UploadService>(
        builder: (context, ble, upload, _) {
          return RefreshIndicator(
            color: WhoopTheme.primary,
            onRefresh: () async {
              if (ble.connected) ble.requestBattery();
              await _loadDbStats();
            },
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _buildConnectionCard(ble),
                const SizedBox(height: 12),
                _buildLiveDataCard(ble),
                const SizedBox(height: 12),
                _buildUnifiedSyncCard(ble, upload),
                const SizedBox(height: 12),
                _buildDataCard(ble),
                const SizedBox(height: 12),
                _buildDeveloperCard(ble, upload),
                const SizedBox(height: 80),
              ],
            ),
          );
        },
      ),
    );
  }

  // === CONNECTION STATUS ===
  Widget _buildConnectionCard(BleService ble) {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 12,
                height: 12,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: ble.connected ? WhoopTheme.primary : WhoopTheme.error,
                ),
              ),
              const SizedBox(width: 10),
              Text(
                ble.connected ? 'Connected' : 'Disconnected',
                style: TextStyle(
                  color: ble.connected ? WhoopTheme.primary : WhoopTheme.error,
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const Spacer(),
              if (!ble.connected)
                _actionButton('Connect', WhoopTheme.primary, () => ble.connect())
              else
                _actionButton('Disconnect', WhoopTheme.error, () => ble.disconnect()),
            ],
          ),
          const SizedBox(height: 12),
          if (ble.status != (ble.connected ? 'Connected' : 'Disconnected'))
            _infoRow('Status', ble.status),
          if (ble.lastConnectedTime != null)
            _infoRow('Connected at', DateFormat('HH:mm:ss').format(ble.lastConnectedTime!)),
          if (ble.lastDisconnectedTime != null && !ble.connected)
            _infoRow('Disconnected at', DateFormat('HH:mm:ss').format(ble.lastDisconnectedTime!)),
          if (ble.deviceName.isNotEmpty)
            _infoRow('Device', ble.deviceName),
        ],
      ),
    );
  }

  // === LIVE DATA (HR + BATTERY) ===
  Widget _buildLiveDataCard(BleService ble) {
    final hr = ble.heartRate;
    final battery = ble.batteryLevel;
    final hrLive = ble.isHrLive;

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('LIVE DATA',
              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 11,
                  fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: _metricBox(
                  icon: Icons.favorite,
                  iconColor: hrLive ? WhoopTheme.error : WhoopTheme.textSecondary,
                  value: hrLive && hr > 0 ? '$hr' : '--',
                  unit: 'BPM',
                  sublabel: hrLive ? 'Live' : (ble.lastHrTime != null
                      ? 'Last: ${DateFormat('HH:mm').format(ble.lastHrTime!)}'
                      : 'No data'),
                ),
              ),
              Container(width: 1, height: 60, color: WhoopTheme.divider),
              Expanded(
                child: _metricBox(
                  icon: ble.isCharging
                      ? Icons.battery_charging_full
                      : (battery > 20 ? Icons.battery_full : Icons.battery_alert),
                  iconColor: battery >= 0
                      ? (battery > 20 ? WhoopTheme.primary : WhoopTheme.error)
                      : WhoopTheme.textSecondary,
                  value: battery >= 0 ? '$battery%' : '--',
                  unit: '',
                  sublabel: ble.isCharging ? 'Charging' : 'Battery',
                ),
              ),
            ],
          ),
          if (ble.hrHistory.length >= 3) ...[
            const SizedBox(height: 14),
            SizedBox(
              height: 40,
              child: CustomPaint(
                size: const Size(double.infinity, 40),
                painter: _MiniHrPainter(ble.hrHistory),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _metricBox({
    required IconData icon,
    required Color iconColor,
    required String value,
    required String unit,
    required String sublabel,
  }) {
    return Column(
      children: [
        Icon(icon, color: iconColor, size: 22),
        const SizedBox(height: 6),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(value, style: const TextStyle(
                color: WhoopTheme.textPrimary, fontSize: 28, fontWeight: FontWeight.w700)),
            if (unit.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 3, left: 3),
                child: Text(unit, style: const TextStyle(
                    color: WhoopTheme.textSecondary, fontSize: 12)),
              ),
          ],
        ),
        const SizedBox(height: 2),
        Text(sublabel, style: const TextStyle(
            color: WhoopTheme.textSecondary, fontSize: 11)),
      ],
    );
  }

  // === UNIFIED SYNC (single button, shows all phases) ===
  Widget _buildUnifiedSyncCard(BleService ble, UploadService upload) {
    final phase = ble.syncPhase;
    final busy = phase != SyncPhase.idle;
    final isError = phase == SyncPhase.error;
    final isDone = phase == SyncPhase.done;

    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Sync button
          SizedBox(
            width: double.infinity,
            child: ElevatedButton.icon(
              onPressed: busy || !ble.connected ? null : () => ble.unifiedSync(),
              icon: busy
                  ? const SizedBox(width: 18, height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : isDone
                      ? const Icon(Icons.check_circle, size: 18)
                      : const Icon(Icons.sync, size: 18),
              label: Text(
                busy ? ble.syncPhaseMessage
                    : isDone ? ble.syncPhaseMessage
                    : 'Sync',
                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
              ),
              style: ElevatedButton.styleFrom(
                backgroundColor: isError ? WhoopTheme.error : WhoopTheme.primary,
                foregroundColor: Colors.white,
                disabledBackgroundColor: (isError ? WhoopTheme.error : WhoopTheme.primary).withOpacity(0.5),
                disabledForegroundColor: Colors.white70,
                padding: const EdgeInsets.symmetric(vertical: 14),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
          ),
          if (!ble.connected && phase == SyncPhase.idle)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('Connect to strap to sync',
                  style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
            ),

          // Phase progress details
          if (busy || isDone || isError) ...[
            const SizedBox(height: 14),
            _buildPhaseIndicator(ble, upload),
          ],

          // BLE sync details while syncing
          if (phase == SyncPhase.bleSyncing && ble.syncingHistory) ...[
            const SizedBox(height: 10),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: const LinearProgressIndicator(
                backgroundColor: WhoopTheme.divider,
                color: WhoopTheme.primary,
                minHeight: 3,
              ),
            ),
            const SizedBox(height: 8),
            _infoRow('Round', '${ble.syncRound}'),
            _infoRow('New records', '${ble.syncNewRecords}'),
            if (ble.syncDateRange.isNotEmpty)
              _infoRow('Date range', ble.syncDateRange),
          ],

          // Upload progress while uploading
          if (phase == SyncPhase.uploading && upload.uploading) ...[
            const SizedBox(height: 10),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: upload.uploadProgress,
                backgroundColor: WhoopTheme.divider,
                color: WhoopTheme.sleepBlue,
                minHeight: 3,
              ),
            ),
            const SizedBox(height: 8),
            _infoRow('Uploaded', '${upload.uploadDone} / ${upload.uploadTotal}'),
          ],
        ],
      ),
    );
  }

  Widget _buildPhaseIndicator(BleService ble, UploadService upload) {
    final phase = ble.syncPhase;

    Widget phaseStep(SyncPhase step, String label, IconData icon) {
      final isActive = phase == step;
      final isPast = phase.index > step.index;
      final color = isActive ? WhoopTheme.primary
          : isPast ? WhoopTheme.primary.withOpacity(0.5)
          : WhoopTheme.textSecondary.withOpacity(0.3);

      return Row(
        children: [
          if (isActive)
            SizedBox(width: 14, height: 14,
              child: CircularProgressIndicator(strokeWidth: 2, color: color))
          else if (isPast)
            Icon(Icons.check_circle, size: 14, color: color)
          else
            Icon(icon, size: 14, color: color),
          const SizedBox(width: 6),
          Text(label, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w500)),
        ],
      );
    }

    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceAround,
      children: [
        phaseStep(SyncPhase.bleSyncing, 'Strap', Icons.bluetooth),
        phaseStep(SyncPhase.uploading, 'Cloud', Icons.cloud_upload),
        phaseStep(SyncPhase.refreshing, 'Refresh', Icons.refresh),
      ],
    );
  }

  // === YOUR DATA ===
  Widget _buildDataCard(BleService ble) {
    return GlassCard(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('YOUR DATA',
              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 11,
                  fontWeight: FontWeight.w600, letterSpacing: 0.5)),
          const SizedBox(height: 10),
          _infoRow('Records', _formatNumber(_dbRecordCount)),
          if (_dbOldest != null) _infoRow('From', _dbOldest!),
          if (_dbNewest != null) _infoRow('To', _dbNewest!),
        ],
      ),
    );
  }

  // === DEVELOPER (collapsed, advanced options) ===
  Widget _buildDeveloperCard(BleService ble, UploadService upload) {
    return GlassCard(
      padding: EdgeInsets.zero,
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          leading: const Icon(Icons.code, color: WhoopTheme.textSecondary, size: 16),
          title: const Text('Developer',
              style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12,
                  fontWeight: FontWeight.w600)),
          iconColor: WhoopTheme.textSecondary,
          collapsedIconColor: WhoopTheme.textSecondary,
          children: [
            // Advanced sync buttons
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: ble.syncPhase != SyncPhase.idle || !ble.connected
                        ? null
                        : () => ble.unifiedSync(full: true),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: WhoopTheme.warning,
                      side: BorderSide(
                          color: ble.syncPhase != SyncPhase.idle || !ble.connected
                              ? WhoopTheme.divider : WhoopTheme.warning),
                      padding: const EdgeInsets.symmetric(vertical: 10),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                    ),
                    child: const Text('Full Sync', style: TextStyle(fontSize: 12)),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton(
                    onPressed: upload.uploading || ble.syncPhase != SyncPhase.idle
                        ? null
                        : () => upload.syncToCloud(),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: WhoopTheme.sleepBlue,
                      side: BorderSide(
                          color: upload.uploading || ble.syncPhase != SyncPhase.idle
                              ? WhoopTheme.divider : WhoopTheme.sleepBlue),
                      padding: const EdgeInsets.symmetric(vertical: 10),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                    ),
                    child: const Text('Upload Only', style: TextStyle(fontSize: 12)),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // Device info
            if (ble.deviceSerial.isNotEmpty)
              _infoRow('Serial', ble.deviceSerial),
            if (ble.firmwareInfo.isNotEmpty)
              _infoRow('Firmware', ble.firmwareInfo),
            if (ble.activeProfileName.isNotEmpty)
              _infoRow('BLE Profile', ble.activeProfileName),

            // Strap trim position
            if (ble.dataRangeStart > 0) ...[
              const Divider(height: 20, color: WhoopTheme.divider),
              const Text('STRAP TRIM POSITION',
                  style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 10,
                      fontWeight: FontWeight.w600, letterSpacing: 0.3)),
              const SizedBox(height: 6),
              _infoRow('Trim', _formatUnixTs(ble.dataRangeStart)),
              if (ble.dataRangeEnd > 0)
                _infoRow('Write head', _formatUnixTs(ble.dataRangeEnd)),
            ],

            // Last sync session stats
            if (ble.syncedPackets > 0) ...[
              const Divider(height: 20, color: WhoopTheme.divider),
              const Text('LAST SYNC SESSION',
                  style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 10,
                      fontWeight: FontWeight.w600, letterSpacing: 0.3)),
              const SizedBox(height: 6),
              _infoRow('Rounds', '${ble.syncRound}'),
              _infoRow('Packets', '${ble.syncedPackets}'),
              _infoRow('New records', '${ble.syncNewRecords}'),
              if (ble.syncDateRange.isNotEmpty)
                _infoRow('Range', ble.syncDateRange),
            ],
          ],
        ),
      ),
    );
  }

  // === HELPERS ===

  Widget _infoRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
          const SizedBox(width: 16),
          Flexible(
            child: Text(value, style: const TextStyle(
                color: WhoopTheme.textPrimary, fontSize: 13, fontWeight: FontWeight.w500),
                textAlign: TextAlign.end),
          ),
        ],
      ),
    );
  }

  Widget _actionButton(String label, Color color, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        decoration: BoxDecoration(
          color: color.withOpacity(0.12),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(label, style: TextStyle(
            color: color, fontSize: 13, fontWeight: FontWeight.w600)),
      ),
    );
  }

  String _formatUnixTs(int unixSec) {
    final dt = DateTime.fromMillisecondsSinceEpoch(unixSec * 1000).toLocal();
    return DateFormat('MMM d, HH:mm').format(dt);
  }

  String _formatNumber(int n) {
    if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}K';
    return '$n';
  }
}

// === Mini HR chart painter ===
class _MiniHrPainter extends CustomPainter {
  final List<int> values;
  _MiniHrPainter(this.values);

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final minHr = values.reduce((a, b) => a < b ? a : b).toDouble() - 5;
    final maxHr = values.reduce((a, b) => a > b ? a : b).toDouble() + 5;
    final range = maxHr - minHr;
    if (range <= 0) return;

    final paint = Paint()
      ..color = WhoopTheme.error.withOpacity(0.8)
      ..strokeWidth = 1.5
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    final path = Path();
    for (var i = 0; i < values.length; i++) {
      final x = i / (values.length - 1) * size.width;
      final y = (1 - (values[i] - minHr) / range) * size.height;
      if (i == 0) path.moveTo(x, y); else path.lineTo(x, y);
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _MiniHrPainter old) => true;
}
