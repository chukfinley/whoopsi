package com.whoopcapture

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.*
import android.content.Intent
import android.os.Binder
import android.os.IBinder
import android.util.Log
import com.whoopcapture.db.AppDatabase
import com.whoopcapture.db.SensorRecord
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.UUID

class WhoopBleService : Service() {

    companion object {
        private const val TAG = "WhoopBLE"
        const val ACTION_CONNECT = "com.whoopcapture.CONNECT"
        const val ACTION_DISCONNECT = "com.whoopcapture.DISCONNECT"
    }

    inner class LocalBinder : Binder() {
        fun getService(): WhoopBleService = this@WhoopBleService
    }

    private val binder = LocalBinder()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var gatt: BluetoothGatt? = null
    private var activeProfile: WhoopUuids.StrapProfile? = null
    private var cmdToStrapChar: BluetoothGattCharacteristic? = null

    private val _status = MutableStateFlow("Idle")
    val status: StateFlow<String> = _status

    private val _totalRecords = MutableStateFlow(0)
    val totalRecords: StateFlow<Int> = _totalRecords

    private val _syncRunning = MutableStateFlow(false)
    val syncRunning: StateFlow<Boolean> = _syncRunning

    private val _syncRound = MutableStateFlow(0)
    val syncRound: StateFlow<Int> = _syncRound

    private val _syncTotalPackets = MutableStateFlow(0)
    val syncTotalPackets: StateFlow<Int> = _syncTotalPackets

    private val _syncNewRecords = MutableStateFlow(0)
    val syncNewRecords: StateFlow<Int> = _syncNewRecords

    private val _syncDateRange = MutableStateFlow("") // "Feb 06 05:13 → 08:42"
    val syncDateRange: StateFlow<String> = _syncDateRange

    @Volatile private var syncMinTimestamp = Long.MAX_VALUE
    @Volatile private var syncMaxTimestamp = 0L
    @Volatile private var lastPacketTimestamp = 0L  // Most recent packet timestamp (can go backwards on buffer wrap)

    private val _batteryLevel = MutableStateFlow(-1)
    val batteryLevel: StateFlow<Int> = _batteryLevel

    private val _isCharging = MutableStateFlow(false)
    val isCharging: StateFlow<Boolean> = _isCharging

    @Volatile private var lastNotificationUpdate = 0L

    private var recordCount = 0
    private var shouldReconnect = true
    private var reconnectAttempts = 0

    private val dao by lazy { AppDatabase.get(this).sensorRecordDao() }
    private val dateFmt = java.text.SimpleDateFormat("MMM dd HH:mm", java.util.Locale.US)
    private val timeFmt = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.US)

    private fun formatTs(ts: Long): String = dateFmt.format(java.util.Date(ts * 1000))
    private fun formatTime(ts: Long): String = timeFmt.format(java.util.Date(ts * 1000))

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(1, buildNotification("Starting..."))
        when (intent?.action) {
            ACTION_CONNECT -> connect()
            ACTION_DISCONNECT -> disconnect()
        }
        return START_STICKY
    }

    private fun buildNotification(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, "whoop_capture")
            .setContentTitle("Whoop Capture")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(text: String) {
        val now = System.currentTimeMillis()
        if (now - lastNotificationUpdate < 2000) return // Throttle to max once per 2s
        lastNotificationUpdate = now
        val nm = getSystemService(NOTIFICATION_SERVICE) as android.app.NotificationManager
        nm.notify(1, buildNotification(text))
    }

    fun connect() {
        shouldReconnect = true
        reconnectAttempts = 0
        val adapter = BluetoothAdapter.getDefaultAdapter()
        if (adapter == null) {
            _status.value = "No Bluetooth adapter"
            return
        }

        _status.value = "Looking for Whoop..."

        val whoopDevice = adapter.bondedDevices?.firstOrNull { device ->
            device.name?.contains("Whoop", ignoreCase = true) == true
        }

        if (whoopDevice == null) {
            val leDevice = adapter.bondedDevices?.firstOrNull { device ->
                device.type == BluetoothDevice.DEVICE_TYPE_LE || device.type == BluetoothDevice.DEVICE_TYPE_DUAL
            }
            if (leDevice != null) {
                _status.value = "Trying ${leDevice.name ?: leDevice.address}..."
                connectToDevice(leDevice)
            } else {
                _status.value = "No Whoop found in bonded devices"
            }
            return
        }

        _status.value = "Connecting to ${whoopDevice.name}..."
        connectToDevice(whoopDevice)
    }

    private fun connectToDevice(device: BluetoothDevice) {
        gatt?.close()
        // First try direct connect (false), fall back to autoConnect (true) on retry.
        // Direct connect is faster but can timeout if official Whoop app holds the link.
        val auto = reconnectAttempts > 0  // First attempt: direct, retries: auto
        gatt = device.connectGatt(this, auto, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    fun disconnect() {
        shouldReconnect = false
        historySyncRunning = false
        gatt?.disconnect()
        gatt?.close()
        gatt = null
        cmdToStrapChar = null
        synchronized(writeQueue) {
            writeQueue.clear()
            writeInProgress = false
        }
        subscriptionQueue.clear()
        subscriptionTimeoutJob?.cancel()
        _status.value = "Disconnected"
    }

    // Queue for write operations (BLE only allows one write at a time)
    private val writeQueue = mutableListOf<Pair<BluetoothGattCharacteristic, ByteArray>>()
    private var writeInProgress = false

    private fun enqueueWrite(char: BluetoothGattCharacteristic, data: ByteArray) {
        synchronized(writeQueue) {
            writeQueue.add(Pair(char, data))
            if (!writeInProgress) {
                processWriteQueue()
            }
        }
    }

    private fun processWriteQueue() {
        synchronized(writeQueue) {
            if (writeQueue.isEmpty()) {
                writeInProgress = false
                return
            }
            writeInProgress = true
            val (char, data) = writeQueue.removeAt(0)
            Log.i(TAG, "CMD >>> ${data.joinToString("") { "%02x".format(it) }}")
            char.value = data
            char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
            gatt?.writeCharacteristic(char)
        }
    }

    private fun sendCommand(data: ByteArray) {
        val char = cmdToStrapChar
        if (char == null) {
            Log.w(TAG, "CMD_TO_STRAP not available")
            return
        }
        enqueueWrite(char, data)
    }

    private val gattCallback = object : BluetoothGattCallback() {

        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    reconnectAttempts = 0
                    servicesDiscoveryStarted = false
                    Log.i(TAG, "Connected, discovering services...")
                    _status.value = "Connected, discovering services..."
                    g.discoverServices()
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    reconnectAttempts++
                    cmdToStrapChar = null
                    servicesDiscoveryStarted = false
                    Log.i(TAG, "Disconnected (attempt $reconnectAttempts)")
                    if (shouldReconnect) {
                        val backoff = minOf(reconnectAttempts * 5000L, 60000L)
                        _status.value = "Disconnected — retry in ${backoff/1000}s (#$reconnectAttempts)"
                        scope.launch {
                            delay(backoff)
                            if (shouldReconnect) {
                                g.device?.let { connectToDevice(it) }
                            }
                        }
                    } else {
                        _status.value = "Disconnected"
                    }
                }
            }
        }

        private var servicesDiscoveryStarted = false

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            Log.i(TAG, "MTU changed to $mtu (status=$status)")
            // Don't trigger discovery here — already started from onConnectionStateChange
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                _status.value = "Service discovery failed ($status)"
                return
            }

            Log.i(TAG, "Services discovered: ${g.services.map { it.uuid }}")

            for (profile in WhoopUuids.ALL_PROFILES) {
                val svc = g.getService(profile.service)
                if (svc != null) {
                    Log.i(TAG, "Found ${profile.name} service")
                    activeProfile = profile

                    // Get CMD_TO_STRAP for sending commands
                    val cmdUuid = when (profile.name) {
                        "Maverick" -> WhoopUuids.MAVERICK_CMD_TO
                        "Gen4" -> WhoopUuids.GEN4_CMD_TO
                        "Puffin" -> WhoopUuids.PUFFIN_CMD_TO
                        else -> null
                    }
                    if (cmdUuid != null) {
                        cmdToStrapChar = svc.getCharacteristic(cmdUuid)
                        Log.i(TAG, "CMD_TO_STRAP: ${cmdToStrapChar?.uuid}")
                    }

                    // Also get CMD_FROM_STRAP for responses
                    val cmdFromUuid = when (profile.name) {
                        "Maverick" -> WhoopUuids.MAVERICK_CMD_FROM
                        "Gen4" -> WhoopUuids.GEN4_CMD_FROM
                        "Puffin" -> WhoopUuids.PUFFIN_CMD_FROM
                        else -> null
                    }

                    _status.value = "Found ${profile.name} — subscribing..."
                    subscribeToCharacteristics(g, svc, profile, cmdFromUuid)
                    return
                }
            }

            val allUuids = g.services.joinToString("\n") { svc ->
                "SVC ${svc.uuid}: ${svc.characteristics.map { it.uuid }}"
            }
            Log.w(TAG, "No Whoop service found. Available:\n$allUuids")
            _status.value = "No Whoop service found (${g.services.size} services)"
        }

        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray
        ) {
            handleData(characteristic.uuid, value)
        }

        @Deprecated("Deprecated in API 33")
        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic
        ) {
            characteristic.value?.let { handleData(characteristic.uuid, it) }
        }

        override fun onCharacteristicWrite(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            Log.i(TAG, "Write ${characteristic.uuid}: status=$status")
            processWriteQueue()
        }

        override fun onDescriptorWrite(
            g: BluetoothGatt,
            descriptor: BluetoothGattDescriptor,
            status: Int
        ) {
            Log.i(TAG, "Descriptor write ${descriptor.characteristic.uuid}: status=$status")
            subscriptionTimeoutJob?.cancel()
            processSubscriptionQueue(g)
        }
    }

    private val subscriptionQueue = java.util.concurrent.CopyOnWriteArrayList<BluetoothGattCharacteristic>()
    private var subscriptionTimeoutJob: Job? = null

    private fun subscribeToCharacteristics(
        g: BluetoothGatt,
        svc: BluetoothGattService,
        profile: WhoopUuids.StrapProfile,
        cmdFromUuid: UUID?
    ) {
        synchronized(subscriptionQueue) {
            subscriptionQueue.clear()

            // CMD_FROM_STRAP (responses to our commands)
            cmdFromUuid?.let { svc.getCharacteristic(it)?.let { c -> subscriptionQueue.add(c) } }
            // EVENTS_FROM_STRAP
            svc.getCharacteristic(profile.events)?.let { subscriptionQueue.add(it) }
            // DATA_FROM_STRAP (sensor data)
            svc.getCharacteristic(profile.data)?.let { subscriptionQueue.add(it) }

            Log.i(TAG, "Subscription queue: ${subscriptionQueue.map { it.uuid }}")
        }
        processSubscriptionQueue(g)
    }

    private fun processSubscriptionQueue(g: BluetoothGatt) {
        val char: BluetoothGattCharacteristic
        synchronized(subscriptionQueue) {
            if (subscriptionQueue.isEmpty()) {
                _status.value = "Subscribed — sending init commands..."
                updateNotification("Connected — initializing")
                scope.launch {
                    delay(500)
                    runInitSequence()
                }
                return
            }
            char = subscriptionQueue.removeAt(0)
        }
        Log.i(TAG, "Subscribing to ${char.uuid}...")

        g.setCharacteristicNotification(char, true)
        val cccd = char.getDescriptor(WhoopUuids.CCCD)
        if (cccd != null) {
            cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            g.writeDescriptor(cccd)
            // Timeout: if descriptor write callback never fires, continue after 5s
            subscriptionTimeoutJob?.cancel()
            subscriptionTimeoutJob = scope.launch {
                delay(5000)
                Log.w(TAG, "Subscription timeout for ${char.uuid}, continuing...")
                processSubscriptionQueue(g)
            }
        } else {
            Log.w(TAG, "No CCCD for ${char.uuid}")
            processSubscriptionQueue(g)
        }
    }

    private fun runInitSequence() {
        Log.i(TAG, "=== Starting init sequence ===")

        // Abort any leftover dump from previous connection, then identify device
        sendCommand(WhoopProtocol.abortHistoricalTransmits())
        sendCommand(WhoopProtocol.getHelloExt())
        sendCommand(WhoopProtocol.getBatteryLevel())
        sendCommand(WhoopProtocol.getExtendedBatteryInfo())

        scope.launch {
            delay(2000)
            // Don't auto-sync — let user decide between Smart and Full Sync
            Log.i(TAG, "BLE connected — ready for sync. Tap Sync Now or Full Sync.")
            _status.value = "Connected — ready"
        }
    }

    private var lastHistoryStart: Int = 0
    private var lastHistoryEnd: Int = 0
    @Volatile private var sensorPacketCount = 0
    @Volatile private var historySyncRunning = false
    private var isSmartSync = false
    @Volatile private var trimAllCompleted = false
    // Trim position tracking — updated from 0x31 events or strap console logs
    @Volatile private var lastBurstTrimSector: Int = 10   // sector 0x0a
    @Volatile private var lastBurstTrimOffset: Int = 0
    @Volatile private var lastBurstSectorRaw: ByteArray? = null  // raw bytes for ACK
    @Volatile private var lastBurstOffsetRaw: ByteArray? = null  // raw bytes for ACK
    @Volatile private var burstComplete = false  // set true when 0x31 HISTORY_END or "History burst success" seen
    @Volatile private var dumpComplete = false    // set true when 0x31 HISTORY_COMPLETE or "Historical Dump Complete" seen

    private fun resetSyncCounters() {
        _syncNewRecords.value = 0
        _syncDateRange.value = ""
        syncMinTimestamp = Long.MAX_VALUE
        syncMaxTimestamp = 0L
        lastPacketTimestamp = 0L
    }

    /**
     * Smart sync (default): "Borrow & Restore" trim pointer strategy.
     *
     * Coexists with the official Whoop app by temporarily rewinding the trim
     * pointer, syncing our data, then leaving trim at 0 so the official app
     * can do its own sync unaffected.
     *
     * Flow:
     * 1. GET_DATA_RANGE — read current trim position (may have been advanced by official app)
     * 2. FORCE_TRIM(0, 0) — rewind trim to beginning of circular buffer
     * 3. SEND_HISTORICAL_DATA loop — dump all data (DB deduplicates via UNIQUE)
     * 4. After sync: leave trim at 0 (does NOT persist across BLE sessions anyway)
     *
     * The duplicate detection (10 consecutive all-duplicate rounds) ensures we
     * stop quickly once we've re-traversed already-captured data and reach
     * only new records. Typical sync with mostly-captured data: 2-5 minutes.
     */
    private fun requestSmartSync() {
        if (historySyncRunning) {
            Log.w(TAG, "Sync already running, ignoring")
            return
        }
        isSmartSync = true
        Log.i(TAG, "=== Smart sync (borrow & restore) ===")
        _status.value = "Smart sync..."
        updateNotification("Smart sync...")
        resetSyncCounters()

        scope.launch {
            // Step 1: Check current data range (see where official app left the trim)
            sendCommand(WhoopProtocol.getDataRange())
            delay(2000)

            val nowSec = System.currentTimeMillis() / 1000
            val dbMaxTs = dao.getMaxValidTimestamp(nowSec + 86400) ?: 0L
            val trimTs = lastHistoryStart.toLong() and 0xFFFFFFFFL

            if (dbMaxTs > 0) {
                Log.i(TAG, "Smart sync: DB up to ${formatTs(dbMaxTs)}, trim at ${formatTs(trimTs)}")
            } else {
                Log.i(TAG, "Smart sync: no DB data yet, trim at ${formatTs(trimTs)}")
            }

            // Step 2: Always rewind trim to beginning so we get ALL available data
            // This undoes any FORCE_TRIM the official Whoop app may have done
            Log.i(TAG, "Smart sync: rewinding trim to start (coexistence mode)")
            _status.value = "Rewinding trim..."
            sendCommand(WhoopProtocol.forceTrimToStart())
            delay(1500)

            // Verify rewind worked
            sendCommand(WhoopProtocol.getDataRange())
            delay(1500)

            val newTrimTs = lastHistoryStart.toLong() and 0xFFFFFFFFL
            Log.i(TAG, "Smart sync: trim after rewind: ${formatTs(newTrimTs)}")
            _status.value = "Syncing from buffer start..."

            // Step 3: Dump all data — duplicate detection will stop us quickly
            // once we pass through already-captured records
            startHistorySyncLoop()

            // Step 4: Leave trim at 0 — it doesn't persist across BLE sessions,
            // so the official app won't be affected on its next connect.
            // No FORCE_TRIM_ALL needed.
        }
    }

    /**
     * Full sync: disconnect, reconnect (resets trim), then download ALL data.
     *
     * Key insight: The trim pointer does NOT persist across BLE sessions.
     * On a fresh connection, ALL buffered data is available for SEND_HISTORICAL_DATA.
     * No FORCE_TRIM needed — just reconnect and start dumping.
     *
     * If FORCE_TRIM(0,0) doesn't work properly (only gets wrap-around segment),
     * a clean reconnect gives us a fresh start with the strap's default trim.
     */
    private fun requestFullSync() {
        if (historySyncRunning) {
            Log.i(TAG, "Stopping current sync for Full Sync...")
            historySyncRunning = false
        }
        isSmartSync = false
        Log.i(TAG, "=== Full sync — reconnect and download ALL ===")
        _status.value = "Full sync — reconnecting..."
        updateNotification("Full sync...")
        resetSyncCounters()

        scope.launch {
            // Wait for any running sync to stop
            var waitStop = 0
            while (_syncRunning.value && waitStop < 10000) {
                delay(500)
                waitStop += 500
            }

            // Strategy: Disconnect and reconnect to reset the trim pointer.
            // The strap resets trim on disconnect, so a fresh connection starts clean.
            Log.i(TAG, "Full sync: disconnecting to reset trim pointer...")
            val device = gatt?.device
            shouldReconnect = false
            gatt?.disconnect()
            gatt?.close()
            gatt = null
            cmdToStrapChar = null
            synchronized(writeQueue) {
                writeQueue.clear()
                writeInProgress = false
            }
            subscriptionQueue.clear()

            delay(3000) // Wait for BLE stack to clean up

            if (device == null) {
                _status.value = "Full sync failed — no device"
                return@launch
            }

            // Reconnect
            Log.i(TAG, "Full sync: reconnecting to ${device.name ?: device.address}...")
            _status.value = "Reconnecting..."
            shouldReconnect = true
            reconnectAttempts = 0
            connectToDevice(device)

            // Wait for connection + service discovery + init sequence
            var waitConn = 0
            while (cmdToStrapChar == null && waitConn < 20000) {
                delay(500)
                waitConn += 500
            }
            if (cmdToStrapChar == null) {
                Log.e(TAG, "Full sync: reconnection failed after ${waitConn}ms")
                _status.value = "Full sync failed — reconnection timeout"
                return@launch
            }
            // Wait for init sequence to complete
            delay(3000)

            // Now we have a fresh connection with reset trim pointer.
            // Check data range to see what's available
            Log.i(TAG, "Full sync: checking data range after fresh connect...")
            sendCommand(WhoopProtocol.getDataRange())
            delay(2000)

            // Start the dump — all data should be available
            Log.i(TAG, "Full sync: starting data download...")
            startHistorySyncLoop()
        }
    }

    /**
     * Event-driven sync loop matching the official Whoop app's flow.
     *
     * The official app's sync is:
     * 1. Send SEND_HISTORICAL_DATA once to start the dump
     * 2. Receive burst of data packets + HISTORY_END event with sector:offset
     * 3. Send HISTORICAL_DATA_RESULT ACK with the sector:offset
     * 4. Strap automatically sends next burst (no need to re-send SEND_HISTORICAL_DATA)
     * 5. Repeat 2-4 until HISTORY_COMPLETE event
     *
     * Key insight: After the ACK, the strap automatically continues dumping.
     * We do NOT need to send SEND_HISTORICAL_DATA again for each burst.
     */
    private suspend fun startHistorySyncLoop() {
        if (historySyncRunning) return
        historySyncRunning = true
        _syncRunning.value = true
        var totalPackets = 0
        var round = 0
        val maxRounds = if (isSmartSync) 400 else 50000  // Full buffer: ~34000 bursts needed
        val nowSec = System.currentTimeMillis() / 1000
        val dbMaxTs = dao.getMaxValidTimestamp(nowSec + 86400) ?: 0L
        var emptyBursts = 0
        // Circular buffer wrap detection
        var prevRoundMaxTs = 0L
        var wrapCount = 0
        // Duplicate detection
        var allDuplicateRounds = 0

        if (dbMaxTs > 0) {
            Log.i(TAG, "Sync: DB has data up to ${formatTs(dbMaxTs)}, will stop when caught up or buffer wraps")
        }

        try {
            // Step 1: Send SEND_HISTORICAL_DATA ONCE to start the dump
            burstComplete = false
            dumpComplete = false
            Log.i(TAG, "=== Starting history dump ===")
            sendCommand(WhoopProtocol.sendHistoricalData())

            // Step 2-5: Receive bursts, ACK, repeat
            while (historySyncRunning && round < maxRounds && !dumpComplete) {
                round++
                _syncRound.value = round
                val countBefore = sensorPacketCount
                val newRecordsBefore = _syncNewRecords.value
                burstComplete = false
                // Don't reset dumpComplete here — it persists across rounds
                val latestTs = if (syncMaxTimestamp > 0) " @ ${formatTime(syncMaxTimestamp)}" else ""
                Log.i(TAG, "=== Burst $round (${_syncNewRecords.value} new, $totalPackets total pkts)$latestTs ===")
                _status.value = "Burst $round$latestTs — ${_syncNewRecords.value} new / $totalPackets pkts"
                updateNotification("Sync burst $round: ${_syncNewRecords.value} new")

                // Wait for burst data to arrive.
                // The strap sends data packets followed by a HISTORY_END event (0x31 type 2).
                // We wait until both conditions are met:
                // 1. Data packets have stopped arriving (stall)
                // 2. HISTORY_END event has been received (burstComplete=true)
                // OR: HISTORY_COMPLETE event (dumpComplete=true) = all done
                val maxWaitMs = if (isSmartSync) 15000L else 60000L
                val stallTimeout = 2000L
                var waitMs = 0L
                var lastCount = countBefore
                var lastChangeMs = 0L
                var dataReceived = false
                while (waitMs < maxWaitMs) {
                    delay(200)
                    waitMs += 200
                    val current = sensorPacketCount
                    if (current != lastCount) {
                        lastCount = current
                        lastChangeMs = waitMs
                        dataReceived = true
                    }

                    // All data sent — we're done
                    if (dumpComplete) break

                    // Burst end signaled AND data has stalled
                    if (burstComplete && dataReceived && (waitMs - lastChangeMs) > stallTimeout) break

                    // Burst end signaled but no data — wait up to 3s for late-arriving packets
                    if (burstComplete && !dataReceived && waitMs > 3000) break

                    // Data stalled without burst end signal (fallback)
                    if (dataReceived && !burstComplete && (waitMs - lastChangeMs) > stallTimeout * 2) break

                    // No activity at all
                    if (!dataReceived && !burstComplete && !dumpComplete && waitMs > (if (round == 1) 10000L else 5000L)) break
                }

                val newPackets = sensorPacketCount - countBefore
                totalPackets += newPackets
                _syncTotalPackets.value = totalPackets
                val newDbThisRound = _syncNewRecords.value - newRecordsBefore
                val dateInfo = if (syncMaxTimestamp > 0) " [${formatTs(syncMinTimestamp)}→${formatTs(syncMaxTimestamp)}]" else ""
                Log.i(TAG, "Burst $round: $newPackets pkts, $newDbThisRound new DB$dateInfo (burstComplete=$burstComplete, dumpComplete=$dumpComplete)")

                // Send ACK after receiving data or burst-end event
                if (newPackets > 0 || burstComplete) {
                    val sRaw = lastBurstSectorRaw
                    val oRaw = lastBurstOffsetRaw
                    if (sRaw != null && oRaw != null) {
                        Log.i(TAG, "Sending ACK (0x17) raw — sector=$lastBurstTrimSector offset=$lastBurstTrimOffset")
                        sendCommand(WhoopProtocol.historicalDataResultRaw(sRaw, oRaw))
                    } else {
                        Log.i(TAG, "Sending ACK (0x17) int — sector=$lastBurstTrimSector offset=$lastBurstTrimOffset")
                        sendCommand(WhoopProtocol.historicalDataResult(lastBurstTrimSector, lastBurstTrimOffset))
                    }
                    lastBurstSectorRaw = null
                    lastBurstOffsetRaw = null
                    emptyBursts = 0
                } else {
                    emptyBursts++
                    Log.i(TAG, "Empty burst ($emptyBursts/5)")
                    if (emptyBursts >= 5) {
                        Log.i(TAG, "5 consecutive empty bursts — retrying SEND_HISTORICAL_DATA")
                        emptyBursts = 0
                        sendCommand(WhoopProtocol.sendHistoricalData())
                    }
                }

                // All done?
                if (dumpComplete) {
                    Log.i(TAG, "Dump complete — all data synced")
                    break
                }

                // --- Circular buffer wrap detection ---
                if (lastPacketTimestamp > 0 && prevRoundMaxTs > 0) {
                    if (lastPacketTimestamp < prevRoundMaxTs - 86400) {
                        wrapCount++
                        Log.i(TAG, "Buffer wrap #$wrapCount: ${formatTs(prevRoundMaxTs)} → ${formatTs(lastPacketTimestamp)}")
                        if (wrapCount >= 1) {
                            Log.i(TAG, "Buffer fully traversed, stopping")
                            break
                        }
                    }
                }
                if (lastPacketTimestamp > prevRoundMaxTs) prevRoundMaxTs = lastPacketTimestamp

                // --- Duplicate detection ---
                if (newPackets > 0 && newDbThisRound == 0) {
                    allDuplicateRounds++
                    // Don't stop on duplicates during Full Sync — we need to traverse
                    // the entire circular buffer to reach new data at the end.
                    // Only stop on duplicates during Smart Sync.
                    if (isSmartSync && allDuplicateRounds >= 15) {
                        Log.i(TAG, "$allDuplicateRounds consecutive all-duplicate bursts — stopping (smart sync)")
                        break
                    }
                } else if (newDbThisRound > 0) {
                    allDuplicateRounds = 0
                }

                // --- Caught-up detection ---
                if (syncMaxTimestamp > 0 && syncMaxTimestamp >= (nowSec - 300)) {
                    Log.i(TAG, "Sync caught up to present! Latest: ${formatTs(syncMaxTimestamp)}")
                    break
                }

                // Brief pause for strap to prepare next burst
                delay(300)
            }
        } finally {
            historySyncRunning = false
            _syncRunning.value = false
        }
        val newDbRecords = _syncNewRecords.value
        val dateRange = if (syncMaxTimestamp > 0) "${formatTs(syncMinTimestamp)} → ${formatTs(syncMaxTimestamp)}" else "no data"
        Log.i(TAG, "=== Sync complete: $totalPackets pkts, $newDbRecords new DB records, $round bursts ===")
        Log.i(TAG, "=== Date range: $dateRange ===")

        _status.value = if (newDbRecords > 0) {
            "Done: $newDbRecords new ($dateRange)"
        } else if (totalPackets == 0) {
            "Done: up to date"
        } else {
            "Done: no new data ($totalPackets pkts, all duplicates)"
        }
        updateNotification("Sync: $newDbRecords new records")
    }

    fun requestSync() {
        scope.launch {
            requestSmartSync()
        }
    }

    fun requestFullSyncPublic() {
        scope.launch {
            requestFullSync()
        }
    }

    /** Reboot strap to reset internal state, then reconnect and sync */
    fun rebootAndResync() {
        scope.launch {
            Log.i(TAG, "=== Rebooting strap to reset internal state ===")
            _status.value = "Rebooting strap..."
            updateNotification("Rebooting strap...")
            sendCommand(WhoopProtocol.rebootStrap())
            // Strap will disconnect, shouldReconnect will auto-reconnect
            // The onServicesDiscovered → runInitSequence will trigger sync
        }
    }

    /** Deep recovery: power cycle, then try multiple flash access strategies */
    fun deepRecovery() {
        scope.launch {
            Log.i(TAG, "=== DEEP RECOVERY: Attempting flash data recovery ===")
            _status.value = "Deep recovery..."
            updateNotification("Deep recovery attempt...")

            // Strategy 1: Try FORCE_TRIM with sector 10 specifically
            Log.i(TAG, "Strategy 1: FORCE_TRIM(sector=10, offset=0)")
            sendCommand(WhoopProtocol.forceTrim(10, 0))
            delay(2000)
            sendCommand(WhoopProtocol.getDataRange())
            delay(2000)

            // Strategy 2: Try SET_READ_POINTER to various positions + SEND_HISTORICAL
            val offsets = listOf(0, 1000, 10000, 50000, 65000, 100000, 120000, 130000)
            for (off in offsets) {
                Log.i(TAG, "Strategy 2: SET_READ_POINTER(10, $off) + SEND_HISTORICAL")
                sendCommand(WhoopProtocol.setReadPointer(10, off))
                delay(500)
                sendCommand(WhoopProtocol.sendHistoricalData())
                delay(3000)
                // Check if we got any new data
                Log.i(TAG, "After offset $off: sensorPacketCount=$sensorPacketCount")
            }

            // Strategy 3: Try CMD_START_RAW_DATA
            Log.i(TAG, "Strategy 3: CMD_START_RAW_DATA")
            sendCommand(WhoopProtocol.startRawData())
            delay(5000)
            sendCommand(WhoopProtocol.stopRawData())
            delay(1000)

            // Strategy 4: Power cycle (more aggressive than reboot)
            Log.i(TAG, "Strategy 4: POWER_CYCLE_STRAP")
            _status.value = "Power cycling strap..."
            sendCommand(WhoopProtocol.powerCycleStrap())
            // Strap will disconnect and reconnect, auto-sync will kick in
        }
    }

    private fun handleData(uuid: UUID, data: ByteArray) {
        Log.d(TAG, "<<< ${uuid.toString().take(8)}: ${data.size}B")

        if (activeProfile == null) return

        // Check which characteristic this is from
        val isCmdFrom = uuid == WhoopUuids.MAVERICK_CMD_FROM ||
                uuid == WhoopUuids.GEN4_CMD_FROM ||
                uuid == WhoopUuids.PUFFIN_CMD_FROM

        if (isCmdFrom) {
            handleCommandResponse(data)
            return
        }

        // Decode AA01 frame
        val inner = WhoopDataDecoder.extractAA01Payload(data)
        val pktType = if (inner != null && inner.isNotEmpty()) inner[0].toInt() and 0xFF else -1

        // Decode console logs (0x32) — firmware debug messages
        if (pktType == 0x32 && inner != null && inner.size > 3) {
            val ascii = inner.copyOfRange(3, inner.size)
                .filter { it in 0x20..0x7E || it == 0x0A.toByte() }
                .toByteArray()
            if (ascii.isNotEmpty()) {
                val msg = String(ascii).trim()
                Log.i(TAG, "CONSOLE: $msg")
                // Detect TrimAll completion from strap firmware
                // Message may be fragmented: "TrimAllCount=67, leaving TrimAll mod" or full
                if (msg.contains("leaving TrimAll") || msg.contains("TrimAllCount")) {
                    trimAllCompleted = true
                    Log.i(TAG, "TrimAll completed by strap: $msg")
                }
                // Detect burst success with trim position
                // Format: "BLE: History burst success. Trim: 0x0000000a:0001fff8 (10:131064)"
                if (msg.contains("History burst success")) {
                    burstComplete = true
                    // Parse trim position from hex: "Trim: 0xSSSSSSSS:OOOOOOOO"
                    val trimMatch = Regex("""Trim:\s*0x([0-9a-fA-F]+):([0-9a-fA-F]+)""").find(msg)
                    if (trimMatch != null) {
                        lastBurstTrimSector = trimMatch.groupValues[1].toLong(16).toInt()
                        lastBurstTrimOffset = trimMatch.groupValues[2].toLong(16).toInt()
                        Log.i(TAG, "Burst trim position: sector=$lastBurstTrimSector offset=$lastBurstTrimOffset")
                    }
                }
                // Log dump complete from console (informational only — don't set dumpComplete flag!
                // The console message can arrive early or be a stale fragment from a previous dump.
                // Only the 0x31 HISTORY_COMPLETE event (metadata type 3) should set dumpComplete.)
                if (msg.contains("Historical Dump Complete")) {
                    Log.i(TAG, "Console: Historical Dump Complete (informational, not setting flag)")
                }
            }
        }

        // Pass full raw data to decoder — it handles AA01 frame extraction
        val records = WhoopDataDecoder.decode(data)
        if (records.isNotEmpty()) {
            recordCount += records.size
            sensorPacketCount += records.size
            _totalRecords.value = recordCount

            // Track timestamps for date range display
            for (r in records) {
                if (r.timestamp > 0) {
                    lastPacketTimestamp = r.timestamp  // Always update (can go backwards on wrap)
                    if (r.timestamp < syncMinTimestamp) syncMinTimestamp = r.timestamp
                    if (r.timestamp > syncMaxTimestamp) {
                        syncMaxTimestamp = r.timestamp
                        _syncDateRange.value = "${formatTs(syncMinTimestamp)} → ${formatTs(syncMaxTimestamp)}"
                    }
                }
            }

            // Log every 50th packet with timestamp for debugging
            if (recordCount % 50 == 0) {
                val latest = records.maxByOrNull { it.timestamp }
                if (latest != null && latest.timestamp > 0) {
                    Log.i(TAG, "SYNC #$recordCount: ${formatTime(latest.timestamp)} HR=${latest.heartRate} " +
                            "SpO2=${latest.spo2Percent} [new=${_syncNewRecords.value}]")
                }
            }

            scope.launch {
                try {
                    val entities = records.map { r ->
                        SensorRecord(
                            timestamp = r.timestamp,
                            heartRate = r.heartRate,
                            rrCount = r.rrCount,
                            rr1Ms = r.rr1Ms, rr2Ms = r.rr2Ms, rr3Ms = r.rr3Ms,
                            accelX = r.accelX, accelY = r.accelY, accelZ = r.accelZ,
                            gyro = r.gyro,
                            spo2Percent = r.spo2Percent,
                            byte16 = r.byte16, byte17 = r.byte17,
                            byte66 = r.byte66, byte68 = r.byte68,
                            byte105 = r.byte105, byte106 = r.byte106,
                            rawHex = ""
                        )
                    }
                    for (chunk in entities.chunked(100)) {
                        val results = dao.insertAll(chunk)
                        val newCount = results.count { it != -1L }
                        if (newCount > 0) {
                            _syncNewRecords.value += newCount
                        }
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "DB insert failed: ${e.message}", e)
                }
            }

            if (recordCount % 100 == 0) {
                val latestTs = if (syncMaxTimestamp > 0) " @ ${formatTime(syncMaxTimestamp)}" else ""
                _status.value = "Receiving$latestTs — ${_syncNewRecords.value} new / $recordCount pkts"
                updateNotification("Syncing: ${_syncNewRecords.value} new$latestTs")
            }
        } else if (pktType == 0x31 && inner != null && inner.size >= 5) {
            // Event packet (0x31) — burst marker from strap
            //
            // Layout (from official app's C10257b class + live data analysis):
            //   inner[0]     = 0x31 (packet type)
            //   inner[1]     = sequence/counter
            //   inner[2]     = metadata type: 1=HISTORY_START, 2=HISTORY_END, 3=HISTORY_COMPLETE
            //   inner[3..6]  = timestamp (uint32 LE)
            //
            // Official app's D() method creates sub-payload from parent position 9.
            // In our inner (after AA01 extraction), this corresponds to inner[9:].
            // Within that sub-payload:
            //   sub[4..7] = F() = sector bytes (raw, put directly in ACK)
            //   sub[8..11] = H() = offset bytes (raw, put directly in ACK)
            // i.e. inner[13..16] = sector_raw, inner[17..20] = offset_raw
            //
            // IMPORTANT: The ACK uses raw bytes (F() and H()), NOT interpreted LE values.
            // The official app does: buf.put(F()); buf.put(H()); — raw byte copy.
            val hexDump = inner.joinToString("") { "%02x".format(it) }
            val metadataType = inner[2].toInt() and 0xFF
            val metaName = when (metadataType) { 1 -> "START"; 2 -> "END"; 3 -> "COMPLETE"; else -> "?($metadataType)" }
            Log.i(TAG, "EVENT 0x31 [$metaName] ${inner.size}B: $hexDump")

            // Only process HISTORY_END (type 2) — this is the burst-complete marker
            if (metadataType == 2 && inner.size >= 21) {
                // Extract sector:offset raw bytes at inner[13..16] and inner[17..20]
                // These are the bytes the official app sends in the ACK (F() and H())
                val sectorRaw = inner.copyOfRange(13, 17)
                val offsetRaw = inner.copyOfRange(17, 21)
                val evtBuf = java.nio.ByteBuffer.wrap(inner).order(java.nio.ByteOrder.LITTLE_ENDIAN)
                val sectorVal = evtBuf.getInt(13)
                val offsetVal = evtBuf.getInt(17)
                Log.i(TAG, "EVENT END: sector=$sectorVal offset=$offsetVal " +
                        "(raw: ${sectorRaw.joinToString(""){"%02x".format(it)}} ${offsetRaw.joinToString(""){"%02x".format(it)}})")

                lastBurstTrimSector = sectorVal
                lastBurstTrimOffset = offsetVal
                lastBurstSectorRaw = sectorRaw
                lastBurstOffsetRaw = offsetRaw
                burstComplete = true
            } else if (metadataType == 3) {
                // HISTORY_COMPLETE — all historical data has been sent
                Log.i(TAG, "EVENT COMPLETE: all historical data sent")
                dumpComplete = true
                burstComplete = true
            } else if (metadataType == 1) {
                // HISTORY_START — burst beginning, no action needed
                Log.i(TAG, "EVENT START: new burst beginning")
            } else if (metadataType == 2 && inner.size < 21) {
                Log.w(TAG, "EVENT END too short (${inner.size}B < 21), using console log fallback")
                burstComplete = true
            } else {
                Log.d(TAG, "EVENT 0x31 type=$metadataType, ignoring")
            }
        } else if (data.size > 3) {
            Log.d(TAG, "Undecoded ${data.size}B on ${uuid.toString().take(8)}: ${data.take(16).joinToString("") { "%02x".format(it) }}")
        }
    }

    private fun handleCommandResponse(data: ByteArray) {
        val hex = data.joinToString("") { "%02x".format(it) }
        Log.i(TAG, "CMD_FROM raw ${data.size}B: $hex")

        // Parse AA01 framed response
        val pkt = WhoopProtocol.parseResponse(data)
        if (pkt == null) {
            Log.w(TAG, "CMD_FROM: failed to parse AA01 frame")
            return
        }

        val cmdCode = pkt.cmdCode.toInt() and 0xFF
        val params = pkt.params
        val paramsHex = params.joinToString("") { "%02x".format(it) }
        Log.i(TAG, "CMD RSP: type=0x${(pkt.cmdType.toInt() and 0xFF).toString(16)} seq=${pkt.sequence} " +
                "cmd=0x${cmdCode.toString(16)} hCRC=${pkt.headerCrcOk} pCRC=${pkt.payloadCrcOk} params=$paramsHex")

        when (cmdCode) {
            0x91 -> { // GET_HELLO_EXT response (device serial + info)
                val ascii = params.filter { it in 0x20..0x7E }.toByteArray()
                if (ascii.isNotEmpty()) {
                    Log.i(TAG, "Device serial: ${String(ascii)}")
                }
            }
            0x8D -> { // GET_ADVERTISING_NAME response
                if (params.size > 3) {
                    val name = params.copyOfRange(3, params.size)
                        .takeWhile { it != 0.toByte() }.toByteArray()
                    Log.i(TAG, "Device name: ${String(name)}")
                }
            }
            0x22 -> { // GET_DATA_RANGE response
                Log.i(TAG, "History query response (${params.size}B): $paramsHex")
                val buf = java.nio.ByteBuffer.wrap(params).order(java.nio.ByteOrder.LITTLE_ENDIAN)
                val nowSec = System.currentTimeMillis() / 1000

                // Extract known timestamp positions:
                // @43 = trim/data start position, @59 = current write head
                // Also log all found timestamps for debugging
                val knownOffsets = listOf(35, 43, 51, 59)
                var trimTs = 0L
                var headTs = 0L
                for (off in knownOffsets) {
                    if (off + 4 <= params.size) {
                        try {
                            val ts = buf.getInt(off).toLong() and 0xFFFFFFFFL
                            if (ts in (nowSec - 86400 * 400)..(nowSec + 86400)) {
                                val date = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
                                    .format(java.util.Date(ts * 1000))
                                Log.i(TAG, "  DATA_RANGE @$off: $ts ($date)")
                            }
                        } catch (_: Exception) {}
                    }
                }

                // @43 = trim position (data start after FORCE_TRIM)
                if (params.size >= 47) {
                    try {
                        val ts = buf.getInt(43).toLong() and 0xFFFFFFFFL
                        if (ts in (nowSec - 86400 * 400)..(nowSec + 86400)) trimTs = ts
                    } catch (_: Exception) {}
                }
                // @59 = write head (current time / data end)
                if (params.size >= 63) {
                    try {
                        val ts = buf.getInt(59).toLong() and 0xFFFFFFFFL
                        if (ts in (nowSec - 86400 * 400)..(nowSec + 86400)) headTs = ts
                    } catch (_: Exception) {}
                }

                if (trimTs > 0 && headTs > 0) {
                    lastHistoryStart = trimTs.toInt()
                    lastHistoryEnd = headTs.toInt()
                    val days = (headTs - trimTs) / 86400.0
                    Log.i(TAG, "Data range: ${formatTs(trimTs)} → ${formatTs(headTs)} (%.1f days)".format(days))
                    _status.value = "Data: ${formatTs(trimTs)} → ${formatTs(headTs)} (%.0fd)".format(days)
                } else {
                    Log.w(TAG, "Could not parse data range (trimTs=$trimTs headTs=$headTs)")
                }
            }
            0x16 -> { // SEND_HISTORICAL_DATA response
                Log.i(TAG, "Send historical data response: $paramsHex")
                if (params.isNotEmpty()) {
                    val respStatus = params[0].toInt() and 0xFF
                    Log.i(TAG, "Historical data response status: 0x${respStatus.toString(16)}")
                }
            }
            0x13 -> { // RUN_HAPTIC_PATTERN_MAVERICK response
                Log.i(TAG, "Haptic pattern response: $paramsHex")
            }
            0x17 -> { // HISTORICAL_DATA_RESULT / READ_FLASH_DATA response
                Log.i(TAG, "Data result/flash response: $paramsHex")
            }
            0x19 -> { // FORCE_TRIM response
                Log.i(TAG, "Force trim response: $paramsHex")
                // Don't overwrite "Done:" status after sync completes
                if (_syncRunning.value) {
                    _status.value = "Trim pointer updated..."
                }
            }
            0x1A -> { // GET_BATTERY_LEVEL response
                // Response format: [version, charging_flag, battery%, ...]
                // params[0] is NOT the battery level (it's a version/type byte)
                // Use heuristic: find first byte in 5..100 range (proven by open_whoop project)
                if (params.size >= 3) {
                    var level = -1
                    for (i in 0 until minOf(params.size, 8)) {
                        val v = params[i].toInt() and 0xFF
                        if (v in 5..100) { level = v; break }
                    }
                    if (level > 0) {
                        _batteryLevel.value = level
                    }
                    // Charging flag at params[1]: 1=charging, 0=not charging
                    val chargingFlag = params[1].toInt() and 0xFF
                    _isCharging.value = chargingFlag == 1
                    Log.i(TAG, "Battery: $level% (charging=$chargingFlag, raw=$paramsHex)")
                } else if (params.isNotEmpty()) {
                    val level = params[0].toInt() and 0xFF
                    _batteryLevel.value = level
                    Log.i(TAG, "Battery: $level% (short response, raw=$paramsHex)")
                }
            }
            0x0A -> Log.d(TAG, "Set clock response: $paramsHex")
            0x0B -> Log.d(TAG, "Get clock response: $paramsHex")
            0x62 -> { // GET_EXTENDED_BATTERY_INFO response
                // Extended info supplements 0x1A — don't overwrite charging flag from 0x1A
                Log.i(TAG, "Extended battery info: $paramsHex")
            }
            else -> {
                Log.d(TAG, "Cmd response 0x${cmdCode.toString(16)}: $paramsHex")
            }
        }
    }

    fun queryBattery() {
        sendCommand(WhoopProtocol.getBatteryLevel())
        sendCommand(WhoopProtocol.getExtendedBatteryInfo())
    }

    override fun onDestroy() {
        disconnect()
        scope.cancel()
        super.onDestroy()
    }
}
