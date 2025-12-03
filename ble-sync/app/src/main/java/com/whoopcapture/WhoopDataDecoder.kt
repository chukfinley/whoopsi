package com.whoopcapture

import java.nio.ByteBuffer
import java.nio.ByteOrder

data class DecodedRecord(
    val timestamp: Long,
    val heartRate: Int,
    val rrCount: Int,
    val rr1Ms: Int,
    val rr2Ms: Int,
    val rr3Ms: Int,
    val accelX: Float,
    val accelY: Float,
    val accelZ: Float,
    val gyro: Float,
    val spo2Raw: Int,
    val spo2Percent: Int,
    val byte16: Int,
    val byte17: Int,
    val byte66: Int,
    val byte68: Int,
    val byte105: Int,
    val byte106: Int,
)

object WhoopDataDecoder {

    private const val RECORD_SIZE = 124
    private const val PAYLOAD_OFFSET = 3
    private const val PAYLOAD_SIZE = 116

    const val PKT_TYPE_HISTORICAL_DATA = 0x2F
    const val PKT_TYPE_EVENTS = 0x31
    const val PKT_TYPE_CONSOLE_LOG = 0x32

    fun decode(raw: ByteArray): List<DecodedRecord> {
        // 1. Try AA01 frame extraction first
        val inner = extractAA01Payload(raw)
        if (inner != null) {
            val pktType = if (inner.isNotEmpty()) inner[0].toInt() and 0xFF else -1
            if (pktType == PKT_TYPE_HISTORICAL_DATA && inner.size >= 15) {
                val record = decodeAA01SensorPacket(inner)
                if (record != null) return listOf(record)
            }
        }

        // 2. Fallback: try as raw 124-byte aligned records (cached_packet_db format)
        val records = mutableListOf<DecodedRecord>()
        var offset = 0
        while (offset + RECORD_SIZE <= raw.size) {
            val record = decodeCachedPacket(raw, offset)
            if (record != null) records.add(record)
            offset += RECORD_SIZE
        }

        // 3. Last resort: try as single payload
        if (records.isEmpty() && raw.size >= 20) {
            val record = decodePayloadLegacy(raw, 0)
            if (record != null) records.add(record)
        }
        return records
    }

    fun extractAA01Payload(packet: ByteArray): ByteArray? {
        if (packet.size < 12) return null
        if (packet[0] != 0xAA.toByte() || packet[1] != 0x01.toByte()) return null
        val payloadEnd = packet.size - 4
        if (payloadEnd <= 8) return null
        return packet.copyOfRange(8, payloadEnd)
    }

    fun getPacketType(raw: ByteArray): Int {
        val inner = extractAA01Payload(raw) ?: return -1
        return if (inner.isNotEmpty()) inner[0].toInt() and 0xFF else -1
    }

    /**
     * Decode sensor data from AA01-framed 0x2F packet.
     *
     * Inner payload layout (112 bytes after AA01 extraction):
     *   [0]     0x2F (packet type)
     *   [1-2]   Counter/routing
     *   [3]     Sequence (incrementing)
     *   [4-6]   Sub-info
     *   [7-10]  Timestamp (LE uint32, Unix seconds)
     *   [11]    Unknown
     *   [12-13] Flags/sample info
     *   [14]    SpO2 raw (add 10 for percentage)
     *   [15-24] HR/RR data (zeros when not worn)
     *   [25+]   Accel/gyro sensor data
     */
    /**
     * Decode sensor data from AA01-framed 0x2F packet.
     *
     * Inner payload layout (112 bytes after AA01 extraction):
     *   [0]     0x2F (packet type)
     *   [1-2]   Sequence/routing
     *   [3-6]   Sub-header (revision, counter)
     *   [7-10]  Timestamp (LE uint32, Unix seconds)
     *   [11-13] Flags/sample info
     *   [14]    SpO2 raw (add 10 for percentage)
     *   [15-24] Zeros when not worn / HR-related when worn
     *   [25-35] Signal processing data
     *   [36-39] Gyroscope (BE float32)
     *   [40-43] Accelerometer X (BE float32)
     *   [44-47] Accelerometer Y (BE float32)
     *   [48-51] Accelerometer Z (BE float32)
     *   [52+]   Additional sensor data, config bytes
     */
    private fun decodeAA01SensorPacket(inner: ByteArray): DecodedRecord? {
        if (inner.size < 52) return null
        return try {
            val ts = ByteBuffer.wrap(inner, 7, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xFFFFFFFFL
            if (ts < 1600000000L || ts > 2100000000L) return null

            val spo2Raw = inner[14].toInt() and 0xFF
            val spo2 = if (spo2Raw in 1..99) spo2Raw + 10 else 0

            // HR extraction depends on inner payload size:
            // - 76-byte inner (88-byte AA01): inner[19] = direct HR BPM
            // - 112-byte inner (124-byte AA01): inner[15] = RR count,
            //   inner[16:18] = RR1 (uint16 LE ms), inner[18:20] = RR2
            //   HR = 60000 / RR1_ms (computed from RR intervals)
            val hr: Int
            val rrCount: Int
            val rr1: Int
            val rr2: Int
            val rr3: Int
            if (inner.size <= 80) {
                // 76-byte format: direct HR at inner[19], RR at inner[20-26]
                hr = if (inner.size > 19) inner[19].toInt() and 0xFF else 0
                rrCount = if (inner.size > 20) inner[20].toInt() and 0xFF else 0
                rr1 = if (inner.size > 22) ByteBuffer.wrap(inner, 21, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF else 0
                rr2 = if (inner.size > 24) ByteBuffer.wrap(inner, 23, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF else 0
                rr3 = if (inner.size > 26) ByteBuffer.wrap(inner, 25, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF else 0
            } else {
                // 112-byte format: RR intervals at inner[15:21], compute HR
                rrCount = if (inner.size > 15) inner[15].toInt() and 0xFF else 0
                rr1 = if (rrCount >= 1 && inner.size > 17) ByteBuffer.wrap(inner, 16, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF else 0
                rr2 = if (rrCount >= 2 && inner.size > 19) ByteBuffer.wrap(inner, 18, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF else 0
                rr3 = 0
                hr = if (rr1 in 201..1999) (60000 / rr1) else 0
            }

            // Verified offsets from raw packet analysis:
            val gyro = ByteBuffer.wrap(inner, 36, 4).order(ByteOrder.BIG_ENDIAN).float
            val accelX = ByteBuffer.wrap(inner, 40, 4).order(ByteOrder.BIG_ENDIAN).float
            val accelY = ByteBuffer.wrap(inner, 44, 4).order(ByteOrder.BIG_ENDIAN).float
            val accelZ = ByteBuffer.wrap(inner, 48, 4).order(ByteOrder.BIG_ENDIAN).float

            DecodedRecord(
                timestamp = ts,
                heartRate = hr,
                rrCount = rrCount,
                rr1Ms = rr1, rr2Ms = rr2, rr3Ms = rr3,
                accelX = accelX, accelY = accelY, accelZ = accelZ,
                gyro = gyro,
                spo2Raw = spo2Raw, spo2Percent = spo2,
                byte16 = if (inner.size > 16) inner[16].toInt() and 0xFF else 0,
                byte17 = if (inner.size > 17) inner[17].toInt() and 0xFF else 0,
                byte66 = if (inner.size > 66) inner[66].toInt() and 0xFF else 0,
                byte68 = if (inner.size > 68) inner[68].toInt() and 0xFF else 0,
                byte105 = if (inner.size > 105) inner[105].toInt() and 0xFF else 0,
                byte106 = if (inner.size > 106) inner[106].toInt() and 0xFF else 0,
            )
        } catch (_: Exception) {
            null
        }
    }

    // Decode from cached_packet_db format (raw 124-byte records)
    private fun decodeCachedPacket(raw: ByteArray, offset: Int): DecodedRecord? {
        if (offset + RECORD_SIZE > raw.size) return null
        val p = offset + PAYLOAD_OFFSET
        return decodePayloadLegacy(raw, p)
    }

    private fun decodePayloadLegacy(raw: ByteArray, p: Int): DecodedRecord? {
        if (p + PAYLOAD_SIZE > raw.size) return null
        return try {
            val ts = ByteBuffer.wrap(raw, p + 12, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xFFFFFFFFL
            val hr = raw[p + 19].toInt() and 0xFF
            val rrCount = raw[p + 20].toInt() and 0xFF
            val rr1 = ByteBuffer.wrap(raw, p + 21, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
            val rr2 = ByteBuffer.wrap(raw, p + 23, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
            val rr3 = ByteBuffer.wrap(raw, p + 25, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
            val gyro = ByteBuffer.wrap(raw, p + 29, 4).order(ByteOrder.BIG_ENDIAN).float
            val accelX = ByteBuffer.wrap(raw, p + 45, 4).order(ByteOrder.BIG_ENDIAN).float
            val accelY = ByteBuffer.wrap(raw, p + 49, 4).order(ByteOrder.BIG_ENDIAN).float
            val accelZ = ByteBuffer.wrap(raw, p + 53, 4).order(ByteOrder.BIG_ENDIAN).float
            val spo2Raw = raw[p + 55].toInt() and 0xFF
            val spo2 = spo2Raw + 10

            DecodedRecord(
                timestamp = ts, heartRate = hr,
                rrCount = rrCount, rr1Ms = rr1, rr2Ms = rr2, rr3Ms = rr3,
                accelX = accelX, accelY = accelY, accelZ = accelZ, gyro = gyro,
                spo2Raw = spo2Raw, spo2Percent = spo2,
                byte16 = raw[p + 16].toInt() and 0xFF,
                byte17 = raw[p + 17].toInt() and 0xFF,
                byte66 = raw[p + 66].toInt() and 0xFF,
                byte68 = raw[p + 68].toInt() and 0xFF,
                byte105 = raw[p + 105].toInt() and 0xFF,
                byte106 = raw[p + 106].toInt() and 0xFF,
            )
        } catch (_: Exception) {
            null
        }
    }
}
