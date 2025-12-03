package com.whoopcapture

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.util.Log
import com.whoopcapture.db.AppDatabase
import com.whoopcapture.db.SensorRecord
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

object WhoopDbSiphon {

    private const val TAG = "WhoopSiphon"
    private const val WHOOP_PACKAGE = "com.whoop.android"
    private const val BATCH_SIZE = 500
    private const val AA01_FORMAT_A_SIZE = 124  // Standard historical data packet

    // v5.438+ uses persistent_packet_db with table_metrics_data
    // v5.430 and older used cached_packet_db with cached_table_metrics_data
    private val DB_CANDIDATES = listOf(
        DbCandidate("persistent_packet_db", "table_metrics_data"),
        DbCandidate("cached_packet_db", "cached_table_metrics_data"),
    )

    private data class DbCandidate(val dbName: String, val tableName: String)

    suspend fun siphon(context: Context): SiphonResult = withContext(Dispatchers.IO) {
        val dao = AppDatabase.get(context).sensorRecordDao()

        // Try each DB candidate
        for (candidate in DB_CANDIDATES) {
            val localFile = File(context.filesDir, "whoop_siphon_copy.db")
            try {
                val copied = copyDbViaRunAs(candidate.dbName, localFile)
                        || copyDbViaSu(candidate.dbName, localFile)
                if (!copied || !localFile.exists() || localFile.length() < 1024) {
                    localFile.delete()
                    continue
                }

                val db = SQLiteDatabase.openDatabase(
                    localFile.absolutePath, null, SQLiteDatabase.OPEN_READONLY
                )

                // Verify the table exists and has data
                val count = try {
                    val c = db.rawQuery("SELECT COUNT(*) FROM ${candidate.tableName}", null)
                    c.moveToFirst()
                    val n = c.getInt(0)
                    c.close()
                    n
                } catch (e: Exception) {
                    db.close()
                    localFile.delete()
                    Log.w(TAG, "${candidate.dbName}/${candidate.tableName}: ${e.message}")
                    continue
                }

                if (count == 0) {
                    Log.i(TAG, "${candidate.dbName}/${candidate.tableName}: 0 records, skipping")
                    db.close()
                    localFile.delete()
                    continue
                }

                Log.i(TAG, "Using ${candidate.dbName}/${candidate.tableName} ($count records)")
                val result = importRecords(db, candidate.tableName, dao)
                db.close()
                localFile.delete()
                return@withContext result

            } catch (e: Exception) {
                Log.w(TAG, "Failed with ${candidate.dbName}: ${e.message}")
                localFile.delete()
            }
        }

        SiphonResult(0, 0, "No data found — is Whoop app synced with strap?")
    }

    /**
     * Import sensor records from Whoop's SQLite DB into our Room DB.
     *
     * Blob format: Full AA01 packet (124 bytes for Format A).
     * Layout (Format A, 124-byte AA01):
     *   blob[0..7]   = AA01 header (SOF, rev, len, routing, CRC16)
     *   blob[8]      = 0x2F (packet type)
     *   blob[9]      = inner revision (0x12 for Format A)
     *   blob[15..18] = timestamp (uint32 LE, Unix seconds)
     *   blob[22]     = SpO2 raw (add 10 for %, valid when 1-99)
     *   blob[23]     = RR interval count (0, 1, or 2)
     *   blob[24..25] = RR1 (uint16 LE, ms)
     *   blob[26..27] = RR2 (uint16 LE, ms)
     *   blob[44..47] = Gyroscope (float32 BE)
     *   blob[48..51] = Accel X (float32 BE, g)
     *   blob[52..55] = Accel Y (float32 BE, g)
     *   blob[56..59] = Accel Z (float32 BE, g)
     *
     * DB timestamps are in milliseconds; blob timestamps are in seconds.
     * HR is computed from RR1: HR = 60000 / RR1_ms (no direct HR byte in Format A).
     */
    private suspend fun importRecords(
        db: SQLiteDatabase,
        tableName: String,
        dao: com.whoopcapture.db.SensorRecordDao
    ): SiphonResult {
        val cursor = db.rawQuery(
            "SELECT timestamp, packet_revision, version_id, packet_data FROM $tableName ORDER BY timestamp",
            null
        )

        var newRecords = 0
        var totalRead = 0
        var skipped = 0
        val batch = mutableListOf<SensorRecord>()

        cursor.use { c ->
            while (c.moveToNext()) {
                totalRead++
                val data: ByteArray
                try {
                    data = c.getBlob(3)
                } catch (e: Exception) {
                    skipped++
                    continue
                }

                if (data.size < AA01_FORMAT_A_SIZE) {
                    skipped++
                    continue
                }

                try {
                    // Verify AA01 framing
                    if (data[0] != 0xAA.toByte() || data[1] != 0x01.toByte()) {
                        skipped++
                        continue
                    }

                    // Packet type at blob[8]
                    val packetType = data[8].toInt() and 0xFF
                    if (packetType != 0x2F) {
                        skipped++
                        continue
                    }

                    // Timestamp at blob[15..18] (inner[7..10]), uint32 LE, Unix seconds
                    val unixTs = ByteBuffer.wrap(data, 15, 4)
                        .order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xFFFFFFFFL

                    // SpO2 raw at blob[22] (inner[14])
                    val spo2Raw = data[22].toInt() and 0xFF

                    // RR intervals at blob[23..27] (inner[15..19])
                    val rrCount = data[23].toInt() and 0xFF
                    val rr1 = ByteBuffer.wrap(data, 24, 2)
                        .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                    val rr2 = ByteBuffer.wrap(data, 26, 2)
                        .order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF

                    // HR computed from RR1 (no direct HR byte in Format A)
                    val hr = if (rrCount > 0 && rr1 in 200..2000) (60000 / rr1) else 0

                    // Motion sensors (big-endian floats)
                    val gyro = ByteBuffer.wrap(data, 44, 4).order(ByteOrder.BIG_ENDIAN).float
                    val accelX = ByteBuffer.wrap(data, 48, 4).order(ByteOrder.BIG_ENDIAN).float
                    val accelY = ByteBuffer.wrap(data, 52, 4).order(ByteOrder.BIG_ENDIAN).float
                    val accelZ = ByteBuffer.wrap(data, 56, 4).order(ByteOrder.BIG_ENDIAN).float

                    batch.add(
                        SensorRecord(
                            capturedAt = System.currentTimeMillis(),
                            timestamp = unixTs,
                            heartRate = hr,
                            rrCount = rrCount,
                            rr1Ms = rr1, rr2Ms = rr2, rr3Ms = 0,
                            accelX = accelX, accelY = accelY, accelZ = accelZ,
                            gyro = gyro,
                            spo2Percent = if (spo2Raw in 1..99) spo2Raw + 10 else 0,
                            byte16 = data[24].toInt() and 0xFF,
                            byte17 = data[25].toInt() and 0xFF,
                            byte66 = if (data.size > 74) data[74].toInt() and 0xFF else 0,
                            byte68 = if (data.size > 76) data[76].toInt() and 0xFF else 0,
                            byte105 = if (data.size > 113) data[113].toInt() and 0xFF else 0,
                            byte106 = if (data.size > 114) data[114].toInt() and 0xFF else 0,
                            rawHex = ""
                        )
                    )
                } catch (e: Exception) {
                    Log.w(TAG, "Parse error at record $totalRead: ${e.message}")
                    skipped++
                    continue
                }

                if (batch.size >= BATCH_SIZE) {
                    try {
                        dao.insertAll(batch)
                        newRecords += batch.size
                    } catch (e: Exception) {
                        // IGNORE strategy means duplicates return -1, not exception
                        // But count based on what was attempted
                        Log.e(TAG, "Batch insert failed: ${e.message}")
                    }
                    batch.clear()
                }
            }
        }

        if (batch.isNotEmpty()) {
            try {
                dao.insertAll(batch)
                newRecords += batch.size
            } catch (e: Exception) {
                Log.e(TAG, "Final batch insert failed: ${e.message}")
            }
        }

        Log.i(TAG, "Siphoned $newRecords new from $totalRead total (skipped $skipped)")
        return SiphonResult(totalRead, newRecords, "OK")
    }

    // ─── DB Copy Methods ─────────────────────────────────────────────────

    private fun copyDbViaRunAs(dbName: String, localFile: File): Boolean {
        try {
            val cmd = "run-as $WHOOP_PACKAGE cat databases/$dbName"
            val process = Runtime.getRuntime().exec(arrayOf("sh", "-c", cmd))
            localFile.outputStream().use { out ->
                process.inputStream.copyTo(out)
            }
            val finished = process.waitFor(60, java.util.concurrent.TimeUnit.SECONDS)
            if (!finished) { process.destroyForcibly(); return false }

            // Also copy WAL for consistency
            try {
                val walCmd = "run-as $WHOOP_PACKAGE cat databases/$dbName-wal"
                val walProc = Runtime.getRuntime().exec(arrayOf("sh", "-c", walCmd))
                File(localFile.absolutePath + "-wal").outputStream().use { out ->
                    walProc.inputStream.copyTo(out)
                }
                walProc.waitFor(10, java.util.concurrent.TimeUnit.SECONDS)
            } catch (_: Exception) {}

            if (localFile.exists() && localFile.length() > 1024) {
                Log.i(TAG, "Copied $dbName via run-as: ${localFile.length()} bytes")
                return true
            }
            localFile.delete()
            return false
        } catch (e: Exception) {
            Log.w(TAG, "run-as copy of $dbName failed: ${e.message}")
            localFile.delete()
            return false
        }
    }

    private fun copyDbViaSu(dbName: String, localFile: File): Boolean {
        try {
            val whoopDb = "/data/data/$WHOOP_PACKAGE/databases/$dbName"
            val cmd = "su -c 'cp $whoopDb ${localFile.absolutePath} && " +
                    "cp ${whoopDb}-shm ${localFile.absolutePath}-shm 2>/dev/null; " +
                    "cp ${whoopDb}-wal ${localFile.absolutePath}-wal 2>/dev/null; " +
                    "chmod 666 ${localFile.absolutePath}*'"
            val process = Runtime.getRuntime().exec(arrayOf("sh", "-c", cmd))
            val finished = process.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
            if (!finished) { process.destroyForcibly(); return false }

            if (localFile.exists() && localFile.length() > 1024) {
                Log.i(TAG, "Copied $dbName via su: ${localFile.length()} bytes")
                return true
            }
            return false
        } catch (e: Exception) {
            Log.w(TAG, "su copy of $dbName failed: ${e.message}")
            return false
        }
    }

    data class SiphonResult(val totalRead: Int, val newRecords: Int, val status: String)
}
