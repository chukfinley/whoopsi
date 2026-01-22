# Maverick (Whoop 5.0) — Custom Firmware Update Protocol Guide

Complete byte-level documentation for pushing firmware updates to the Whoop 5.0 (Maverick)
via BLE from a custom Kotlin/Android app.

---

## 1. Overview

The Maverick uses **Ambiq-native OTA** over the Whoop AA01 BLE protocol (not Nordic DFU).
Firmware images are `.zbin` files: a 512-byte header + gzip-compressed ARM binary.

**Validation:** The firmware performs **CRC32-only** integrity checking. No cryptographic
signatures (RSA, ECDSA, AES) were found in the firmware binary. The SBL (Secure Boot Loader)
is in ROM and *may* enforce additional checks at boot — this is the main unknown risk factor.

### 1.1 Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| CRC mismatch during transfer | Low | Each chunk is write-verified on NOR flash |
| SBL rejects image at boot | Medium | SBL may fall back to recovery; untested |
| Brick (unrecoverable) | Low-Medium | External NOR flash staging means MCU flash isn't touched until reboot |
| Power loss during transfer | Low | Image staged on external flash, not internal MRAM |

**Critical safety feature:** The firmware is staged on **external NOR flash** (ISSI/Winbond 64Mb)
before being applied. The MCU's internal MRAM is NOT modified during the transfer phase. The
actual flash-to-MRAM copy happens during reboot, managed by the SBL.

---

## 2. BLE Connection Setup

### 2.1 UUIDs

```kotlin
val MAVERICK_SERVICE  = UUID.fromString("fd4b0001-cce1-4033-93ce-002d5875f58a")
val MAVERICK_CMD_TO   = UUID.fromString("fd4b0002-cce1-4033-93ce-002d5875f58a")
val MAVERICK_CMD_FROM = UUID.fromString("fd4b0003-cce1-4033-93ce-002d5875f58a")
val CCCD              = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
```

### 2.2 Connection Steps

1. Connect to GATT server
2. Discover services, find `MAVERICK_SERVICE`
3. Enable notifications on `CMD_FROM` (write `0x01 0x00` to CCCD)
4. Request MTU 512 (strap supports up to 517)

---

## 3. AA01 Packet Format

Every command uses this framing:

```
Offset  Size  Field
0       1     SOF = 0xAA
1       1     Revision = 0x01
2-3     2     Payload length (LE) — bytes from offset 8 to end, including CRC32
4-5     2     Routing: 0x00 0x01 (App→Strap)
6-7     2     Header CRC16 (MODBUS, LE) on bytes 0-5
--- payload ---
8       1     Type: 0x23 (COMMAND)
9       1     Sequence (0x00-0xFF, incrementing)
10      1     Command code
11+     N     Parameters (MUST be 4-byte aligned, pad with 0x00)
last 4  4     CRC32 (LE) on bytes 8..end-4
```

### 3.1 CRC Functions

```kotlin
// CRC-16/MODBUS for header (bytes 0-5)
fun crc16Modbus(data: ByteArray): Int {
    var crc = 0xFFFF
    for (b in data) {
        crc = crc xor (b.toInt() and 0xFF)
        repeat(8) {
            crc = if (crc and 1 != 0) (crc ushr 1) xor 0xA001 else crc ushr 1
        }
    }
    return crc
}

// CRC32 for payload (standard, same as java.util.zip.CRC32)
fun crc32(data: ByteArray): Long {
    val c = java.util.zip.CRC32()
    c.update(data)
    return c.value
}
```

### 3.2 Build AA01 Packet

```kotlin
fun buildAA01(seq: Int, cmdCode: Int, params: ByteArray): ByteArray {
    // 4-byte align params
    val padded = if (params.size % 4 != 0)
        params + ByteArray(4 - params.size % 4)
    else params

    val payloadLen = 3 + padded.size + 4  // type+seq+cmd + params + crc32
    val packet = ByteBuffer.allocate(8 + payloadLen).order(ByteOrder.LITTLE_ENDIAN)

    // Header
    packet.put(0xAA.toByte())
    packet.put(0x01)
    packet.putShort(payloadLen.toShort())
    packet.put(0x00); packet.put(0x01)
    val hdrCrc = crc16Modbus(packet.array().copyOfRange(0, 6))
    packet.putShort(hdrCrc.toShort())

    // Payload
    val payloadStart = packet.position()
    packet.put(0x23)
    packet.put(seq.toByte())
    packet.put(cmdCode.toByte())
    packet.put(padded)

    val payloadBytes = packet.array().copyOfRange(payloadStart, packet.position())
    val crc = crc32(payloadBytes)
    packet.putInt(crc.toInt())

    return packet.array()
}
```

---

## 4. Firmware Update Commands

### 4.1 Command Codes

| AA01 Code | Internal | Name | Direction |
|-----------|----------|------|-----------|
| 0x50 (80) | 0x8E | START_FIRMWARE_LOAD | App -> Strap |
| 0x51 (81) | 0x8F | LOAD_FW_DATA | App -> Strap |
| 0x52 (82) | 0x90 | PROCESS_FIRMWARE_IMAGE | App -> Strap |
| 0x53 (83) | 0x91 | VERIFY_FW_IMAGE | App -> Strap |

### 4.2 Step 1: START_FIRMWARE_LOAD (0x50)

Initializes the external NOR flash for receiving firmware.

```kotlin
val fileSize = zbinFile.size.toLong()
val params = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN)
    .putInt(fileSize.toInt()).array()
val pkt = buildAA01(seq++, 0x50, params)
writeToStrap(pkt)
// Wait for response on CMD_FROM with status byte
```

**Response:** AA01 with type=0x24, cmd=0x50. Status 0x00 = success (NOR erased, ready).

### 4.3 Step 2: LOAD_FW_DATA (0x51)

Send the `.zbin` file in chunks. Each chunk is write-verified on NOR flash.

```kotlin
val CHUNK_SIZE = 512  // Conservative. Increase to ~4000 with MTU 517.
var offset = 0
while (offset < zbinData.size) {
    val end = minOf(offset + CHUNK_SIZE, zbinData.size)
    val chunk = zbinData.copyOfRange(offset, end)

    // params: [offset (4 LE), chunk_data...]
    val params = ByteBuffer.allocate(4 + chunk.size)
        .order(ByteOrder.LITTLE_ENDIAN)
        .putInt(offset)
        .put(chunk)
        .array()

    val pkt = buildAA01(seq++, 0x51, params)
    writeToStrap(pkt)
    waitForResponse()  // Status 0 = OK

    offset = end
}
```

**Safety:** Each chunk is written to NOR, read back, and compared by the firmware.
Failure logged as: `"Update Flash: Memory compare failed at addr 0x%x"`

### 4.4 Step 3: PROCESS_FIRMWARE_IMAGE (0x52)

Triggers CRC32 validation of the complete image on NOR flash.

```kotlin
sendCommand(0x52, byteArrayOf(0x00))
// Wait up to 30s — strap reads entire image and computes CRC32
```

**Response:**
- Status 0x00 = `"CRC of update image passed"`
- Non-zero = `"CRC of update image failed"` — re-transfer needed

The strap computes CRC32 over the .zbin payload (bytes 0x200 onward) using hardware
MSPI DMA and compares against the CRC32 in the header (offsets 0x000 and 0x1FC).

### 4.5 Step 4: VERIFY_FW_IMAGE (0x53)

Chunk-by-chunk readback verification. String evidence: `"Verify FW image chunk offset"`.

```kotlin
sendCommand(0x53, byteArrayOf(0x00))
// Wait for verification complete
```

### 4.6 Step 5: Reboot

After verification, the strap reboots. The SBL copies from external NOR to internal MRAM.
Monitor for BLE disconnection as the reboot indicator.

---

## 5. .zbin File Format

### 5.1 Header (512 bytes)

```
Offset  Size  Field                              Value (v50.35.2.0)
0x000   4     Payload CRC32                      0xA4B443FC
0x004   4     Compressed payload size             0x00102C80 (1,059,968)
0x008   4     Compression algorithm               0x00000005
0x00C   4     Encryption algorithm                0x00000005 (none)
0x010   4     Image type                          0x0000000D (main app)
0x014   4     Reserved                            0x00000000
0x018   52    Build info (#J\ni + git hash + ts)
0x04C   16    Version string "50.35.x.x"
0x064   24    Builder machine name
0x07C   4     Version major                       50
0x080   4     Version minor                       35
0x084   4     Version patch                       2
0x088   136   Zero padding
0x110   4     Algorithm flags (repeat)            0x00000005
0x114   4     Total image size                    header + payload
0x118   4     Total size - 2
0x11C   4     Header size                         0x00000200 (512)
0x120   64    Zero padding
0x160   152   Erased flash fill (0xFF)
0x1F8   4     Header CRC32 = CRC32(0x008:0x1F8)
0x1FC   4     Payload CRC32 copy
```

### 5.2 Three Integrity Checks (All Required)

1. **Payload CRC32** — `CRC32(zbin[0x200:]) == zbin[0x000] == zbin[0x1FC]`
2. **Header CRC32** — `CRC32(zbin[0x008:0x1F8]) == zbin[0x1F8]`
3. **Size match** — `zbin[0x004] + zbin[0x11C] == zbin[0x114] == len(zbin)`

### 5.3 Build a .zbin (Python)

```python
import struct, gzip, zlib

def build_zbin(arm_binary, ver_major=50, ver_minor=35, ver_patch=2):
    compressed = gzip.compress(arm_binary)
    header = bytearray(512)

    payload_crc = zlib.crc32(compressed) & 0xFFFFFFFF
    struct.pack_into('<I', header, 0x000, payload_crc)
    struct.pack_into('<I', header, 0x004, len(compressed))
    struct.pack_into('<I', header, 0x008, 5)       # compression
    struct.pack_into('<I', header, 0x00C, 5)       # encryption
    struct.pack_into('<I', header, 0x010, 0x0D)    # image type

    ver_str = f"{ver_major}.{ver_minor}.x.x".encode()[:15]
    header[0x04C:0x04C+len(ver_str)] = ver_str
    struct.pack_into('<I', header, 0x07C, ver_major)
    struct.pack_into('<I', header, 0x080, ver_minor)
    struct.pack_into('<I', header, 0x084, ver_patch)

    struct.pack_into('<I', header, 0x110, 5)
    total = len(compressed) + 512
    struct.pack_into('<I', header, 0x114, total)
    struct.pack_into('<I', header, 0x118, total - 2)
    struct.pack_into('<I', header, 0x11C, 512)

    header[0x160:0x1F8] = b'\xFF' * (0x1F8 - 0x160)

    hdr_crc = zlib.crc32(bytes(header[0x008:0x1F8])) & 0xFFFFFFFF
    struct.pack_into('<I', header, 0x1F8, hdr_crc)
    struct.pack_into('<I', header, 0x1FC, payload_crc)

    return bytes(header) + compressed
```

---

## 6. Complete Kotlin OTA Implementation

```kotlin
class MaverickOtaUpdater(
    private val gatt: BluetoothGatt,
    private val cmdTo: BluetoothGattCharacteristic,
    private val cmdFrom: BluetoothGattCharacteristic
) {
    private var seq = 0
    private val responseChannel = Channel<ByteArray>(1)

    fun onResponse(data: ByteArray) {
        responseChannel.trySend(data)
    }

    suspend fun updateFirmware(zbinFile: ByteArray, onProgress: (Float) -> Unit) {
        // Step 1: START_FIRMWARE_LOAD
        val sizeParams = ByteBuffer.allocate(4)
            .order(ByteOrder.LITTLE_ENDIAN)
            .putInt(zbinFile.size).array()
        sendCommand(0x50, sizeParams)
        val startResp = awaitResponse(10_000)
        check(getStatus(startResp) == 0) { "START failed: ${getStatus(startResp)}" }

        // Step 2: LOAD_FW_DATA in chunks
        val chunkSize = 512
        var offset = 0
        while (offset < zbinFile.size) {
            val end = minOf(offset + chunkSize, zbinFile.size)
            val chunk = zbinFile.copyOfRange(offset, end)
            val params = ByteBuffer.allocate(4 + chunk.size)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(offset).put(chunk).array()
            sendCommand(0x51, params)
            val resp = awaitResponse(5_000)
            check(getStatus(resp) == 0) { "LOAD failed at $offset" }
            offset = end
            onProgress(offset.toFloat() / zbinFile.size)
        }

        // Step 3: PROCESS_FIRMWARE_IMAGE (CRC validation)
        sendCommand(0x52, byteArrayOf(0x00))
        val processResp = awaitResponse(30_000)
        check(getStatus(processResp) == 0) { "CRC FAILED — do NOT reboot" }

        // Step 4: VERIFY_FW_IMAGE
        sendCommand(0x53, byteArrayOf(0x00))
        val verifyResp = awaitResponse(30_000)
        check(getStatus(verifyResp) == 0) { "Verify FAILED — do NOT reboot" }

        // Strap reboots automatically
    }

    private fun sendCommand(cmdCode: Int, params: ByteArray) {
        val packet = buildAA01(seq++, cmdCode, params)
        cmdTo.value = packet
        gatt.writeCharacteristic(cmdTo)
    }

    private suspend fun awaitResponse(timeout: Long): ByteArray =
        withTimeout(timeout) { responseChannel.receive() }
}
```

---

## 7. Safe Update Procedure

### 7.1 Pre-Flight

1. Charge strap to **50%+**
2. Note current firmware version via `GET_HELLO_EXT`
3. Keep stock `maverick-50.35.2.0.zbin` for recovery
4. **Test with stock .zbin first** before attempting custom images

### 7.2 Abort Points

| Phase | Safe to Abort? | MCU Affected? |
|-------|---------------|---------------|
| LOAD_FW_DATA (chunks) | Yes | No — only NOR flash written |
| PROCESS_FIRMWARE_IMAGE | Yes (wait for response) | No |
| After VERIFY passes | Risky — image queued for install | No (yet) |
| During reboot/SBL install | **NO** | Yes — MRAM being written |

### 7.3 Recovery

- **Failed CRC / transfer:** Re-start from step 1. NOR is overwritten, MCU untouched.
- **SBL rejects image:** Unknown — may boot old image or enter recovery mode.
  String `"Fast Recovery Mode set to: %d"` suggests recovery exists.
- **Last resort:** JTAG/SWD on Ambiq Apollo4 (requires physical access).

### 7.4 Unknown: SBL Checks

The SBL in ROM may enforce checks not visible in the application firmware:
- Header `image_type` must be `0x0D`
- Header `auth_algo` / `auth_key_idx` fields may need specific values
- Version monotonic counter (no evidence found, but possible)

---

## 8. Downloading Stock Firmware

```kotlin
// Login via AWS Cognito (no client secret needed)
POST https://api.prod.whoop.com/auth-service/v3/whoop/
Header: x-amz-target: AWSCognitoIdentityProviderService.InitiateAuth
Body: {"AuthFlow":"USER_PASSWORD_AUTH",
       "AuthParameters":{"USERNAME":"email","PASSWORD":"pass"},
       "ClientId":"<COGNITO_CLIENT_ID>"}

// Download firmware (returns Base64 ZIP inline in JSON)
POST https://api.prod.whoop.com/firmware-service/v4/firmware/version
Header: Authorization: Bearer {access_token}
Body: {"current_chip_firmwares":[{"chip_name":"AMBIQ","version":"0.0.0.0"}],
       "chip_firmwares_of_upgrade":[{"chip_name":"AMBIQ","version":"50.35.2.0"}]}

// Response: {"firmware_zip_file": "<base64 ZIP containing .zbin>"}
```

---

## 9. Quick Reference

| Item | Value |
|------|-------|
| Protocol | AA01 over BLE GATT |
| File format | .zbin = 512-byte header + gzip ARM binary |
| Validation | CRC32 only (hardware MSPI DMA) |
| Staging | External NOR flash (64Mb ISSI/Winbond) |
| Commands | 0x50 -> 0x51 (chunks) -> 0x52 (CRC check) -> 0x53 (verify) -> reboot |
| Main risk | SBL may reject unsigned images at boot |
| Safe abort | Any time before reboot — MCU untouched |
| Language | C (QP RTOS framework, ARM Cortex-M4F Thumb-2) |
