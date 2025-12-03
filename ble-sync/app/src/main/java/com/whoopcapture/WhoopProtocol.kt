package com.whoopcapture

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.CRC32

/**
 * Whoop Maverick BLE packet protocol (reverse-engineered from HCI snoop logs + APK).
 *
 * Packet layout:
 *   [0]     SOF         = 0xAA
 *   [1]     Revision    = 0x01 (Maverick)
 *   [2-3]   Length      = LE uint16, number of bytes from offset 8 to end of packet
 *   [4-5]   Routing     = 0x0001 (App->Strap) or 0x0100 (Strap->App)
 *   [6-7]   Header CRC  = CRC-16/MODBUS on bytes 0-5, stored LE
 *   [8]     Cmd Type    = 0x23 (COMMAND) or 0x24 (RESPONSE)
 *   [9]     Sequence    = incrementing counter
 *   [10]    Cmd Code    = identifies the command
 *   [11+]   Parameters  = command-specific
 *   [last4]  CRC32      = CRC32(init=0) on bytes 8..end-4, stored LE
 */
object WhoopProtocol {

    // ── Command codes (from decompiled APK: Io/e.java) ──
    // Connection & Identity
    const val CMD_LINK_VALID: Byte = 0x01
    const val CMD_GET_MAX_PROTOCOL_VERSION: Byte = 0x02
    const val CMD_TOGGLE_REALTIME_HR: Byte = 0x03
    const val CMD_REPORT_VERSION_INFO: Byte = 0x07
    const val CMD_SET_CLOCK: Byte = 0x0A
    const val CMD_GET_CLOCK: Byte = 0x0B
    const val CMD_TOGGLE_GENERIC_HR_PROFILE: Byte = 0x0E
    const val CMD_TOGGLE_R7_DATA: Byte = 0x10
    const val CMD_RUN_HAPTIC_PATTERN_MAVERICK: Byte = 0x13
    const val CMD_ABORT_HISTORICAL: Byte = 0x14
    const val CMD_SEND_HISTORICAL_DATA: Byte = 0x16
    const val CMD_HISTORICAL_DATA_RESULT: Byte = 0x17
    const val CMD_FORCE_TRIM: Byte = 0x19
    const val CMD_GET_BATTERY_LEVEL: Byte = 0x1A
    const val CMD_REBOOT_STRAP: Byte = 0x1D
    const val CMD_POWER_CYCLE_STRAP: Byte = 0x20
    const val CMD_SET_READ_POINTER: Byte = 0x21
    const val CMD_GET_DATA_RANGE: Byte = 0x22
    const val CMD_GET_HELLO_HARVARD: Byte = 0x23
    // Alarm
    const val CMD_SET_ALARM_TIME: Byte = 0x42
    const val CMD_GET_ALARM_TIME: Byte = 0x43
    const val CMD_RUN_ALARM: Byte = 0x44
    const val CMD_DISABLE_ALARM: Byte = 0x45
    // Haptics
    const val CMD_RUN_HAPTICS_PATTERN: Byte = 0x4F
    const val CMD_GET_ALL_HAPTICS_PATTERN: Byte = 0x50
    // Raw / Research
    const val CMD_START_RAW_DATA: Byte = 0x51
    const val CMD_STOP_RAW_DATA: Byte = 0x52
    const val CMD_VERIFY_FIRMWARE_IMAGE: Byte = 0x53
    const val CMD_GET_BODY_LOCATION: Byte = 0x54
    // High-freq sync
    const val CMD_ENTER_HIGH_FREQ_SYNC: Byte = 0x60
    const val CMD_EXIT_HIGH_FREQ_SYNC: Byte = 0x61
    const val CMD_GET_EXTENDED_BATTERY_INFO: Byte = 0x62
    // IMU / Optical
    const val CMD_TOGGLE_IMU_MODE_HISTORICAL: Byte = 0x69
    const val CMD_TOGGLE_IMU_MODE: Byte = 0x6A
    const val CMD_ENABLE_OPTICAL_DATA: Byte = 0x6B
    const val CMD_TOGGLE_OPTICAL_MODE: Byte = 0x6C
    // Feature flags / config
    const val CMD_STOP_HAPTICS: Byte = 0x7A
    const val CMD_SELECT_WRIST: Byte = 0x7B
    // Maverick-specific (high byte)
    const val CMD_GET_ADVERTISING_NAME: Byte = 0x8D.toByte()
    const val CMD_GET_HELLO_EXT: Byte = 0x91.toByte()

    // Packet constants
    private const val SOF: Byte = 0xAA.toByte()
    private const val REVISION: Byte = 0x01
    private const val CMD_TYPE_COMMAND: Byte = 0x23
    const val CMD_TYPE_RESPONSE: Byte = 0x24

    private var sequenceNumber: Int = 0

    private fun nextSeq(): Byte {
        val s = sequenceNumber
        sequenceNumber = (sequenceNumber + 1) and 0xFF
        return s.toByte()
    }

    /**
     * Build a fully framed Maverick command packet.
     */
    fun buildCommand(cmdCode: Byte, params: ByteArray = byteArrayOf()): ByteArray {
        // Payload: cmdType(1) + seq(1) + cmdCode(1) + params(N) + padding to 4-byte align
        val rawPayloadLen = 3 + params.size
        val paddingNeeded = if (rawPayloadLen % 4 != 0) 4 - (rawPayloadLen % 4) else 0
        val payloadLen = rawPayloadLen + paddingNeeded
        // Total length field = payload + 4 (CRC32)
        val lengthField = payloadLen + 4
        // Total packet = header(8) + payload + CRC32(4)
        val totalLen = 8 + payloadLen + 4

        val buf = ByteBuffer.allocate(totalLen).order(ByteOrder.LITTLE_ENDIAN)

        // Header bytes 0-5 (for CRC16 calculation)
        buf.put(SOF)                          // [0] SOF
        buf.put(REVISION)                     // [1] Revision
        buf.putShort(lengthField.toShort())   // [2-3] Length (LE)
        buf.put(0x00)                         // [4] Routing byte 1
        buf.put(0x01)                         // [5] Routing byte 2

        // Calculate header CRC16 on bytes 0-5
        val headerBytes = ByteArray(6)
        buf.position(0)
        buf.get(headerBytes)
        val headerCrc = crc16Modbus(headerBytes)
        buf.position(6)
        buf.putShort(headerCrc.toShort())     // [6-7] Header CRC16 (LE)

        // Payload bytes 8+
        val payloadStart = buf.position()
        buf.put(CMD_TYPE_COMMAND)             // [8] Cmd Type
        buf.put(nextSeq())                    // [9] Sequence
        buf.put(cmdCode)                      // [10] Cmd Code
        if (params.isNotEmpty()) {
            buf.put(params)                   // [11+] Parameters
        }
        // 4-byte alignment padding (required by strap firmware)
        for (i in 0 until paddingNeeded) buf.put(0x00)

        // Calculate CRC32 on payload (bytes 8..current position)
        val payloadBytes = ByteArray(buf.position() - payloadStart)
        buf.position(payloadStart)
        buf.get(payloadBytes)
        val payloadCrc = crc32Standard(payloadBytes)
        buf.putInt(payloadCrc.toInt())        // Last 4 bytes: CRC32 (LE)

        return buf.array()
    }

    // --- CRC-16/MODBUS: reflected poly 0xA001, init 0xFFFF ---
    private fun crc16Modbus(data: ByteArray): Int {
        var crc = 0xFFFF
        for (b in data) {
            crc = crc xor (b.toInt() and 0xFF)
            for (i in 0 until 8) {
                crc = if (crc and 1 != 0) (crc ushr 1) xor 0xA001 else crc ushr 1
            }
        }
        return crc and 0xFFFF
    }

    // --- Standard CRC32 (init=0xFFFFFFFF, final XOR 0xFFFFFFFF) ---
    private fun crc32Standard(data: ByteArray): Long {
        val c = CRC32()
        c.update(data)
        return c.value
    }

    // ── Convenience command builders ──

    // --- Identity ---
    fun getHelloExt(): ByteArray = buildCommand(CMD_GET_HELLO_EXT, byteArrayOf(0x01))
    fun getAdvertisingName(): ByteArray = buildCommand(CMD_GET_ADVERTISING_NAME, byteArrayOf(0x01))

    // --- Historical data ---
    fun getDataRange(): ByteArray = buildCommand(CMD_GET_DATA_RANGE, byteArrayOf(0x00))
    fun sendHistoricalData(): ByteArray = buildCommand(CMD_SEND_HISTORICAL_DATA, byteArrayOf(0x00))
    fun abortHistoricalTransmits(): ByteArray = buildCommand(CMD_ABORT_HISTORICAL)

    /**
     * Acknowledge a historical data burst (CMD 0x17).
     * The strap requires this ACK after each burst before it sends the next one.
     * Payload: [SUCCESS=0x01] [sector:4] [offset:4] = 9 bytes total
     * where sector:offset comes from the 0x31 end-event packet.
     * Official app: AbstractC9475q.java allocates exactly 9 bytes.
     */
    fun historicalDataResult(sector: Int, offset: Int): ByteArray {
        val p = ByteBuffer.allocate(9).order(ByteOrder.LITTLE_ENDIAN)
            .put(0x01) // SUCCESS status (EnumC9323b.SUCCESS)
            .putInt(sector)
            .putInt(offset)
            .array()
        return buildCommand(CMD_HISTORICAL_DATA_RESULT, p)
    }

    /**
     * Acknowledge using raw sector/offset bytes from the 0x31 event packet.
     * The official app copies F() and H() raw bytes directly — this avoids
     * any byte-order interpretation issues.
     */
    fun historicalDataResultRaw(sectorBytes: ByteArray, offsetBytes: ByteArray): ByteArray {
        require(sectorBytes.size == 4 && offsetBytes.size == 4)
        val p = ByteArray(9)
        p[0] = 0x01 // SUCCESS
        System.arraycopy(sectorBytes, 0, p, 1, 4)
        System.arraycopy(offsetBytes, 0, p, 5, 4)
        return buildCommand(CMD_HISTORICAL_DATA_RESULT, p)
    }

    /** Send a failure ACK (no position data) */
    fun historicalDataResultFailure(): ByteArray {
        return buildCommand(CMD_HISTORICAL_DATA_RESULT, byteArrayOf(0x00))
    }

    fun forceTrimToStart(): ByteArray {
        val p = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putInt(0).putInt(0).array()
        return buildCommand(CMD_FORCE_TRIM, p)
    }

    fun forceTrimAll(): ByteArray {
        val p = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(0xFEFEFEFE.toInt()).putInt(0xFEFEFEFE.toInt()).array()
        return buildCommand(CMD_FORCE_TRIM, p)
    }

    fun setReadPointer(sector: Int, offset: Int): ByteArray {
        val p = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putInt(sector).putInt(offset).array()
        return buildCommand(CMD_SET_READ_POINTER, p)
    }

    // --- Battery ---
    fun getBatteryLevel(): ByteArray = buildCommand(CMD_GET_BATTERY_LEVEL)
    fun getExtendedBatteryInfo(): ByteArray = buildCommand(CMD_GET_EXTENDED_BATTERY_INFO)

    // --- Clock ---
    /** Set strap clock to given time in millis since epoch */
    fun setClock(millis: Long): ByteArray {
        val seconds = (millis / 1000).toInt()
        val subseconds = (((millis % 1000) * 32768) / 1000).toInt()
        val p = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(seconds).putInt(subseconds).array()
        return buildCommand(CMD_SET_CLOCK, p)
    }

    fun getClock(): ByteArray = buildCommand(CMD_GET_CLOCK)

    // --- HR / Sensor ---
    fun toggleRealtimeHr(enable: Boolean): ByteArray =
        buildCommand(CMD_TOGGLE_REALTIME_HR, byteArrayOf(if (enable) 0x01 else 0x00))

    fun toggleGenericHrProfile(enable: Boolean): ByteArray =
        buildCommand(CMD_TOGGLE_GENERIC_HR_PROFILE, byteArrayOf(if (enable) 0x01 else 0x00))

    fun toggleImuMode(enable: Boolean): ByteArray =
        buildCommand(CMD_TOGGLE_IMU_MODE, byteArrayOf(0x01, if (enable) 0x01 else 0x00))

    fun toggleImuModeHistorical(enable: Boolean): ByteArray =
        buildCommand(CMD_TOGGLE_IMU_MODE_HISTORICAL, byteArrayOf(0x01, if (enable) 0x01 else 0x00))

    fun toggleOpticalMode(enable: Boolean): ByteArray =
        buildCommand(CMD_TOGGLE_OPTICAL_MODE, byteArrayOf(0x01, if (enable) 0x01 else 0x00))

    fun enableOpticalData(enable: Boolean): ByteArray =
        buildCommand(CMD_ENABLE_OPTICAL_DATA, byteArrayOf(0x01, if (enable) 0x01 else 0x00))

    // --- Alarm ---
    /**
     * Set alarm on strap. Time is absolute millis since epoch.
     * Uses Revision 4 format with default haptic pattern (gentle vibration, 30s duration).
     */
    fun setAlarmTime(alarmTimeMillis: Long): ByteArray {
        val seconds = (alarmTimeMillis / 1000).toInt()
        val subseconds = (((alarmTimeMillis % 1000) * 32768) / 1000).toShort()
        val p = ByteBuffer.allocate(20).order(ByteOrder.LITTLE_ENDIAN)
            .put(0x04)           // Revision 4
            .put(0x01)           // Alarm ID = 1
            .putInt(seconds)     // Epoch seconds
            .putShort(subseconds) // Sub-seconds (32768ths)
            // Haptic pattern (12 bytes) — default Whoop alarm pattern
            .put(47)             // WaveFormEffect1
            .put(0x98.toByte())  // WaveFormEffect2 = 152
            .put(0).put(0).put(0).put(0).put(0).put(0) // Effects 3-8
            .putShort(0)         // LoopControlForEffects
            .put(7)              // OverallWaveformLoopControl
            .put(30)             // AlarmDurationInSeconds
            .array()
        return buildCommand(CMD_SET_ALARM_TIME, p)
    }

    fun getAlarmTime(): ByteArray =
        buildCommand(CMD_GET_ALARM_TIME, byteArrayOf(0x04, 0x01)) // Rev4, alarmId=1

    fun runAlarm(): ByteArray =
        buildCommand(CMD_RUN_ALARM, byteArrayOf(0x02, 0x01)) // Rev2, alarmId=1

    fun disableAlarm(): ByteArray =
        buildCommand(CMD_DISABLE_ALARM, byteArrayOf(0x02, 0xFF.toByte())) // Rev2, alarmId=0xFF

    // --- Haptics ---
    /** Run the Maverick haptic motor with a pattern. Default: gentle double-tap. */
    fun runHapticPattern(
        effect1: Int = 47, effect2: Int = 14, effect3: Int = 0, effect4: Int = 0,
        effect5: Int = 0, effect6: Int = 0, effect7: Int = 0, effect8: Int = 0,
        loopControl: Short = 0, overallLoop: Int = 3
    ): ByteArray {
        val p = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN)
            .put(0x01)                          // Revision 1
            .put(effect1.toByte()).put(effect2.toByte())
            .put(effect3.toByte()).put(effect4.toByte())
            .put(effect5.toByte()).put(effect6.toByte())
            .put(effect7.toByte()).put(effect8.toByte())
            .putShort(loopControl)
            .put(overallLoop.toByte())
            .array()
        return buildCommand(CMD_RUN_HAPTIC_PATTERN_MAVERICK, p)
    }

    fun stopHaptics(): ByteArray = buildCommand(CMD_STOP_HAPTICS)

    // --- Device control ---
    fun rebootStrap(): ByteArray = buildCommand(CMD_REBOOT_STRAP)
    fun powerCycleStrap(): ByteArray = buildCommand(CMD_POWER_CYCLE_STRAP)

    /** FORCE_TRIM with specific sector:offset */
    fun forceTrim(sector: Int, offset: Int): ByteArray {
        val p = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putInt(sector).putInt(offset).array()
        return buildCommand(CMD_FORCE_TRIM, p)
    }

    fun startRawData(): ByteArray = buildCommand(CMD_START_RAW_DATA, byteArrayOf(0x01))
    fun stopRawData(): ByteArray = buildCommand(CMD_STOP_RAW_DATA)

    fun selectWrist(right: Boolean): ByteArray =
        buildCommand(CMD_SELECT_WRIST, byteArrayOf(0x01, if (right) 0x01 else 0x02))

    fun getBodyLocationAndStatus(): ByteArray = buildCommand(CMD_GET_BODY_LOCATION)

    fun enterHighFreqSync(intervalSec: Short = 2, durationSec: Short = 300): ByteArray {
        val p = ByteBuffer.allocate(5).order(ByteOrder.LITTLE_ENDIAN)
            .put(0x02) // Revision 2
            .putShort(intervalSec).putShort(durationSec).array()
        return buildCommand(CMD_ENTER_HIGH_FREQ_SYNC, p)
    }

    fun exitHighFreqSync(): ByteArray = buildCommand(CMD_EXIT_HIGH_FREQ_SYNC)

    // --- Response parser ---

    data class ParsedPacket(
        val cmdType: Byte,
        val sequence: Int,
        val cmdCode: Byte,
        val params: ByteArray,
        val headerCrcOk: Boolean,
        val payloadCrcOk: Boolean
    )

    fun parseResponse(data: ByteArray): ParsedPacket? {
        if (data.size < 12) return null
        if (data[0] != SOF) return null

        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        val length = buf.getShort(2).toInt() and 0xFFFF

        // Verify header CRC
        val headerBytes = data.copyOfRange(0, 6)
        val storedHeaderCrc = buf.getShort(6).toInt() and 0xFFFF
        val calcHeaderCrc = crc16Modbus(headerBytes)
        val headerOk = storedHeaderCrc == calcHeaderCrc

        // Payload: bytes 8 to end-4
        val payloadEnd = data.size - 4
        if (payloadEnd <= 8) return null
        val payload = data.copyOfRange(8, payloadEnd)
        val storedPayloadCrc = buf.getInt(data.size - 4).toLong() and 0xFFFFFFFFL
        val calcPayloadCrc = crc32Standard(payload)
        val payloadOk = storedPayloadCrc == calcPayloadCrc

        return ParsedPacket(
            cmdType = payload[0],
            sequence = payload[1].toInt() and 0xFF,
            cmdCode = payload[2],
            params = if (payload.size > 3) payload.copyOfRange(3, payload.size) else byteArrayOf(),
            headerCrcOk = headerOk,
            payloadCrcOk = payloadOk
        )
    }
}
