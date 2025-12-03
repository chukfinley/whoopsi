package com.whoopcapture

import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothProfile
import android.content.Context
import android.util.Log
import androidx.work.*
import com.whoopcapture.db.AppDatabase
import com.whoopcapture.db.SensorRecord
import kotlinx.coroutines.*
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Background worker that periodically connects to the Whoop strap via BLE,
 * dumps all available historical data, and disconnects.
 *
 * Runs every 6 hours via WorkManager. No UI needed.
 */
class AutoSyncWorker(
    context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    companion object {
        private const val TAG = "AutoSync"
        private const val WORK_NAME = "whoop_auto_ble_sync"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<AutoSyncWorker>(
                6, TimeUnit.HOURS
            )
                .setConstraints(
                    Constraints.Builder()
                        .setRequiresBatteryNotLow(true)
                        .build()
                )
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.MINUTES)
                .build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request
            )
            Log.i(TAG, "Scheduled auto BLE sync every 6 hours")
        }

        /** Trigger an immediate one-shot sync */
        fun triggerNow(context: Context) {
            val request = OneTimeWorkRequestBuilder<AutoSyncWorker>()
                .build()
            WorkManager.getInstance(context).enqueue(request)
            Log.i(TAG, "Triggered immediate BLE sync")
        }
    }

    private val dao by lazy { AppDatabase.get(applicationContext).sensorRecordDao() }

    @Volatile private var gatt: BluetoothGatt? = null
    @Volatile private var cmdToStrapChar: BluetoothGattCharacteristic? = null
    @Volatile private var connected = false
    @Volatile private var subscribed = false
    @Volatile private var sensorPacketCount = 0

    private val subscriptionQueue = mutableListOf<BluetoothGattCharacteristic>()

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        Log.i(TAG, "Starting background BLE sync...")

        val adapter = BluetoothAdapter.getDefaultAdapter()
        if (adapter == null || !adapter.isEnabled) {
            Log.w(TAG, "Bluetooth not available")
            return@withContext Result.retry()
        }

        val device = adapter.bondedDevices?.firstOrNull { d ->
            d.name?.contains("Whoop", ignoreCase = true) == true
        }
        if (device == null) {
            Log.w(TAG, "No bonded Whoop device found")
            return@withContext Result.retry()
        }

        try {
            // Connect
            connectAndSync(device)
            Log.i(TAG, "Background sync complete: $sensorPacketCount packets")
            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "Background sync failed: ${e.message}", e)
            Result.retry()
        } finally {
            try {
                gatt?.disconnect()
                gatt?.close()
            } catch (_: Exception) {}
            gatt = null
        }
    }

    private suspend fun connectAndSync(device: BluetoothDevice) {
        connected = false
        subscribed = false
        sensorPacketCount = 0

        val completionDeferred = CompletableDeferred<Unit>()

        gatt = device.connectGatt(applicationContext, false, object : BluetoothGattCallback() {

            override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
                when (newState) {
                    BluetoothProfile.STATE_CONNECTED -> {
                        Log.i(TAG, "Connected, discovering services...")
                        connected = true
                        g.discoverServices()
                    }
                    BluetoothProfile.STATE_DISCONNECTED -> {
                        Log.i(TAG, "Disconnected")
                        connected = false
                        if (!completionDeferred.isCompleted) {
                            completionDeferred.complete(Unit)
                        }
                    }
                }
            }

            override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
                if (status != BluetoothGatt.GATT_SUCCESS) return

                for (profile in WhoopUuids.ALL_PROFILES) {
                    val svc = g.getService(profile.service) ?: continue

                    Log.i(TAG, "Found ${profile.name} service")

                    val cmdUuid = when (profile.name) {
                        "Maverick" -> WhoopUuids.MAVERICK_CMD_TO
                        "Gen4" -> WhoopUuids.GEN4_CMD_TO
                        "Puffin" -> WhoopUuids.PUFFIN_CMD_TO
                        else -> null
                    }
                    cmdToStrapChar = cmdUuid?.let { svc.getCharacteristic(it) }

                    val cmdFromUuid = when (profile.name) {
                        "Maverick" -> WhoopUuids.MAVERICK_CMD_FROM
                        "Gen4" -> WhoopUuids.GEN4_CMD_FROM
                        "Puffin" -> WhoopUuids.PUFFIN_CMD_FROM
                        else -> null
                    }

                    // Build subscription list
                    synchronized(subscriptionQueue) {
                        subscriptionQueue.clear()
                        cmdFromUuid?.let { svc.getCharacteristic(it)?.let { c -> subscriptionQueue.add(c) } }
                        svc.getCharacteristic(profile.events)?.let { subscriptionQueue.add(it) }
                        svc.getCharacteristic(profile.data)?.let { subscriptionQueue.add(it) }
                    }
                    processSubscriptions(g)
                    return
                }
            }

            override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
                processSubscriptions(g)
            }

            override fun onCharacteristicChanged(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic, value: ByteArray) {
                handleBackgroundData(characteristic.uuid, value)
            }

            @Suppress("DEPRECATION")
            override fun onCharacteristicChanged(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
                characteristic.value?.let { handleBackgroundData(characteristic.uuid, it) }
            }

            override fun onCharacteristicWrite(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
                // write complete
            }
        }, BluetoothDevice.TRANSPORT_LE)

        // Wait for connection + subscription (max 30s)
        withTimeout(30_000) {
            while (!subscribed) delay(500)
        }

        Log.i(TAG, "Subscribed, starting sync...")

        // Run init + historical sync (coexistence mode: always rewind, never trim after)
        sendCmd(WhoopProtocol.getHelloExt())
        delay(300)
        // Always rewind trim to beginning — undoes any FORCE_TRIM by official Whoop app
        sendCmd(WhoopProtocol.forceTrimToStart())
        delay(1500)
        sendCmd(WhoopProtocol.setReadPointer(0, 0))
        delay(1000)
        sendCmd(WhoopProtocol.getDataRange())
        delay(2000)

        // Sync loop with duplicate detection for fast completion
        var emptyRounds = 0
        var allDuplicateRounds = 0
        var round = 0
        val dbCountBefore = dao.count()
        while (emptyRounds < 5 && connected && round < 400) {
            round++
            val before = sensorPacketCount
            val dbBefore = dao.count()
            Log.i(TAG, "Sync round $round (total: $sensorPacketCount)")
            sendCmd(WhoopProtocol.sendHistoricalData())

            var waitMs = 0L
            var lastCount = before
            while (waitMs < 60000) {
                delay(2000)
                waitMs += 2000
                val current = sensorPacketCount
                if (current == lastCount && current > before) break
                if (current == before && waitMs > 10000) break
                lastCount = current
            }

            val newPackets = sensorPacketCount - before
            val newDbRecords = dao.count() - dbBefore
            if (newPackets == 0) {
                emptyRounds++
            } else {
                emptyRounds = 0
            }

            // Duplicate detection: if we got packets but no new DB records,
            // we're re-reading data we already have. Stop after 10 such rounds.
            if (newPackets > 0 && newDbRecords == 0) {
                allDuplicateRounds++
                if (allDuplicateRounds >= 10) {
                    Log.i(TAG, "10 consecutive all-duplicate rounds — stopping")
                    break
                }
            } else {
                allDuplicateRounds = 0
            }

            delay(1000)
        }

        val totalNewDb = dao.count() - dbCountBefore
        Log.i(TAG, "Sync finished: $sensorPacketCount pkts, $totalNewDb new DB records in $round rounds")
        // NOTE: No FORCE_TRIM_ALL — leave trim at 0 for official app coexistence

        // Disconnect cleanly
        gatt?.disconnect()
        // Wait for disconnect callback
        withTimeoutOrNull(5000) {
            completionDeferred.await()
        }
    }

    private fun processSubscriptions(g: BluetoothGatt) {
        val char: BluetoothGattCharacteristic
        synchronized(subscriptionQueue) {
            if (subscriptionQueue.isEmpty()) {
                subscribed = true
                return
            }
            char = subscriptionQueue.removeAt(0)
        }
        g.setCharacteristicNotification(char, true)
        val cccd = char.getDescriptor(WhoopUuids.CCCD)
        if (cccd != null) {
            cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            g.writeDescriptor(cccd)
        } else {
            processSubscriptions(g)
        }
    }

    private fun sendCmd(data: ByteArray) {
        val char = cmdToStrapChar ?: return
        char.value = data
        char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        gatt?.writeCharacteristic(char)
    }

    private fun handleBackgroundData(uuid: UUID, data: ByteArray) {
        val isCmdFrom = uuid == WhoopUuids.MAVERICK_CMD_FROM ||
                uuid == WhoopUuids.GEN4_CMD_FROM ||
                uuid == WhoopUuids.PUFFIN_CMD_FROM
        if (isCmdFrom) return // Skip command responses in background

        val records = WhoopDataDecoder.decode(data)
        if (records.isEmpty()) return

        sensorPacketCount += records.size

        CoroutineScope(Dispatchers.IO).launch {
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
                    dao.insertAll(chunk)
                }
            } catch (e: Exception) {
                Log.e(TAG, "DB insert failed: ${e.message}")
            }
        }
    }
}
