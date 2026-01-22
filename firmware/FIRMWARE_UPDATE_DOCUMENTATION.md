# Whoop Firmware Update — Reverse Engineering Documentation

Complete documentation of how the Whoop Android app downloads and flashes firmware updates, reverse-engineered from the decompiled APK (`apk/decompiled/`).

---

## 1. Architecture Overview

```
┌─────────────┐     HTTPS/JSON      ┌──────────────────────────┐
│  Whoop App   │ ──────────────────► │  api.prod.whoop.com      │
│  (Android)   │                     │  firmware-service/v4     │
│              │ ◄────────────────── │  (returns firmware ZIP)  │
└──────┬──────┘                     └──────────────────────────┘
       │
       │  Nordic DFU (BLE)
       │
       ▼
┌──────────────┐
│  Whoop Strap  │
│  (BLE GATT)   │
└──────────────┘
```

**Key insight:** Firmware is NOT downloaded from a static CDN/S3 URL. The API returns the firmware binary **inline as Base64-encoded ZIP** in the JSON response field `firmware_zip_file`.

---

## 2. API Endpoints

**Base URL:** `https://api.prod.whoop.com`

### 2.1 Check for Available Update

```
POST /firmware-service/v4/firmware/check?deviceName={DEVICE}
Authorization: Bearer {access_token}
Content-Type: application/json
x-whoop-app-version: 5.430.0
x-whoop-device-platform: ANDROID

Body: [
  {"chip_name": "AMBIQ", "version": "50.35.2.0"},
  ...
]
```

**Response (200):**
```json
{
  "hardware_device": "MAVERICK",
  "chip_firmwares": [
    {"chip_name": "AMBIQ", "version": "50.35.2.0"}
  ],
  "force_update": false,
  "force_update_reprompt_cadence": null
}
```

### 2.2 Download Firmware

```
POST /firmware-service/v4/firmware/version?deviceName={DEVICE}
Authorization: Bearer {access_token}
Content-Type: application/json

Body: {
  "current_chip_firmwares": [
    {"chip_name": "AMBIQ", "version": "1.0.0"}
  ],
  "chip_firmwares_of_upgrade": [
    {"chip_name": "AMBIQ", "version": "50.35.2.0"}
  ]
}
```

**Response (200):**
```json
{
  "desired_device_firmware_config": {
    "hardware_device": "MAVERICK",
    "chip_firmwares": [{"chip_name": "AMBIQ", "version": "50.35.2.0"}],
    "force_update": false,
    "force_update_reprompt_cadence": null
  },
  "firmware_zip_file": "UEsDBBQAAAAIAE9W..."
}
```

The `firmware_zip_file` field contains a **Base64-encoded ZIP file**. Decode it with `base64.b64decode()`.

### 2.3 Report Update Result

```
POST /firmware-service/v4/firmware/result?deviceName={DEVICE}
Authorization: Bearer {access_token}
Content-Type: application/json
X-WHOOP-Auxiliary-Device-Id: {optional}

Body: {
  "chip_firmware": [
    {"chip_name": "AMBIQ", "version": "50.35.2.0"}
  ],
  "successful": true
}
```

### 2.4 Submit Firmware Events

```
POST /firmware-service/v1/events
Authorization: Bearer {access_token}
```

---

## 3. Device Types & Chip Names

| Device Name | Hardware | Chip(s) Required | Notes |
|-------------|----------|-------------------|-------|
| `GOOSE` | MAVERICK (Whoop 5.0) | `AMBIQ` | Primary target |
| `HARVARD` | Gen 4 | `MAXIM`, `NORDIC` | Needs both chips |
| `PUFFIN` | Puffin accessory | `NORDIC` | Standalone sensor |
| `MAVERICK` | (not in v4 API) | — | 404 on v4, use GOOSE instead |

**Important:** `GOOSE` is the API device name for the Whoop 5.0 (Maverick hardware). The `MAVERICK` device name returns 404 on the v4 firmware API. `HARVARD` returns 404 unless the user account has a Gen 4 device registered.

---

## 4. Downloaded Firmware Files

### 4.1 MAVERICK / Whoop 5.0 (AMBIQ v50.35.2.0)

| Property | Value |
|----------|-------|
| **File** | `firmware/maverick_ambiq_50.35.2.0.zip` |
| **Size (ZIP)** | 1,060,332 bytes (1.0 MB) |
| **Inner file** | `maverick-50.35.2.0.zbin` (1,060,480 bytes) |
| **Format** | `.zbin` — Ambiq Micro Secure OTA format |
| **Build date** | 2025-11-04T18:46:59 |
| **Build info** | (builder redacted), commit `54fc551ae08a204f9d30ab17` |
| **Version string** | `50.35.x.x` |
| **Chip** | Ambiq Apollo4 Blue Plus (BGA) |

### 4.2 PUFFIN (NORDIC v3.30.5.0)

| Property | Value |
|----------|-------|
| **File** | `firmware/puffin_3.30.5.0.zip` |
| **Size (ZIP)** | 268,664 bytes (262.4 KB) |
| **Inner file** | `usb_update_puffin_3.30.5.0_5ce45820.bin` (410,019 bytes) |
| **Format** | `.bin` — Nordic nRF DFU binary |
| **Hash** | `5ce45820` (in filename) |
| **Chip** | Nordic Semiconductor nRF52/nRF53 |

### 4.3 Older Versions

The API **always returns the latest firmware version** regardless of what `current_chip_firmwares` version you report. There is no way to request older firmware versions through this API. The server determines the target version server-side per device type.

Tested with PUFFIN (all map to 3.30.5.0):
- 1.0.0 → 3.30.5.0
- 2.0.0 → 3.30.5.0
- 3.29.0.0 → 3.30.5.0
- 3.30.4.0 → 3.30.5.0

Tested with GOOSE/MAVERICK (all map to 50.35.2.0):
- 1.0.0 → 50.35.2.0
- 40.0.0.0 → 50.35.2.0
- 50.34.0.0 → 50.35.2.0

---

## 5. Firmware Update Flow (from Decompiled APK)

### 5.1 Step-by-Step Flow

```
1. PeriodicFwUpdateCheckWorker (runs periodically)
   │
   ├── POST /firmware-service/v4/firmware/check
   │   Body: current chip firmware versions from device
   │   Response: DesiredDeviceFirmwareConfig
   │
   ├── If update available (chip_firmwares differ):
   │   └── Schedule FirmwareUpdateWorker
   │
2. FirmwareUpdateWorker
   │
   ├── POST /firmware-service/v4/firmware/version
   │   Body: {current_chip_firmwares, chip_firmwares_of_upgrade}
   │   Response: {firmware_zip_file (base64 ZIP), config}
   │
   ├── base64_decode(firmware_zip_file)
   │   └── Save to: filesDir/whoop_firmware/{serial}/firmwares.zip
   │
   ├── Unzip to: filesDir/whoop_firmware/{serial}/
   │   └── Extract: *.bin or *.zbin files
   │
   ├── Start Nordic DFU via DfuServiceInitiator
   │   ├── setZip(null, firmwareFilePath)
   │   ├── setKeepBond(true/false)
   │   ├── setForceDfu(true/false)
   │   ├── setPacketsReceiptNotificationsEnabled(true)
   │   ├── setPacketsReceiptNotificationsValue(12)
   │   ├── setMtu(517) [negotiated]
   │   └── start() → NewDfuService.class
   │
   ├── Nordic DFU library handles:
   │   ├── BLE connection to strap
   │   ├── DFU mode entry (buttonless or legacy)
   │   ├── Firmware image transfer
   │   ├── Validation and activation
   │   └── Progress callbacks
   │
   └── POST /firmware-service/v4/firmware/result
       Body: {chip_firmware: [...], successful: true/false}
```

### 5.2 Key Source Files (Decompiled APK)

| File | Class | Purpose |
|------|-------|---------|
| `com/whoop/firmwareUpdateService/data/api/OtaFirmwareUpdateApi.java` | OtaFirmwareUpdateApi | Retrofit API interface |
| `com/whoop/firmwareUpdateService/data/models/DesiredDeviceFirmwareUpdate.java` | DesiredDeviceFirmwareUpdate | Response with `firmware_zip_file` |
| `com/whoop/firmwareUpdateService/data/models/DesiredDeviceFirmwareConfig.java` | DesiredDeviceFirmwareConfig | Target firmware config |
| `com/whoop/firmwareUpdateService/data/models/ChipFirmwareVersion.java` | ChipFirmwareVersion | `chip_name` + `version` |
| `com/whoop/firmwareUpdateService/data/models/DownloadFwFilePayload.java` | DownloadFwFilePayload | Download request body |
| `com/whoop/firmwareUpdateService/data/models/ReportFwUpdateResultPayload.java` | ReportFwUpdateResultPayload | Report result body |
| `com/whoop/firmwareUpdateService/strategy/NewDfuService.java` | NewDfuService | Nordic DFU service |
| `com/whoop/firmwareUpdateService/workmanager/FirmwareUpdateWorker.java` | FirmwareUpdateWorker | Main update worker |
| `com/whoop/firmwareUpdateService/workmanager/PeriodicFwUpdateCheckWorker.java` | PeriodicFwUpdateCheckWorker | Periodic check |
| `ow/C17069c.java` | (obfuscated) | Firmware file storage/unzip |
| `qw/C17588a.java` | (obfuscated) | DfuServiceInitiator wrapper |
| `mw/C16166a.java` | (obfuscated) | FirmwareUpdateManager |
| `nw/C16674a.java` | (obfuscated) | OtaFirmwareUpdateRepository |
| `sw/C18301c.java` | (obfuscated) | State machine for FW updates |
| `no/nordicsemi/android/dfu/DfuServiceInitiator.java` | DfuServiceInitiator | Nordic DFU setup |
| `no/nordicsemi/android/dfu/DfuBaseService.java` | DfuBaseService | Nordic DFU base |

---

## 6. How to Build a Custom Firmware Update App (Kotlin)

### 6.1 Dependencies

```kotlin
// build.gradle.kts
dependencies {
    // Nordic DFU library (same as used by official app)
    implementation("no.nordicsemi.android:dfu:2.5.0")

    // Networking
    implementation("com.squareup.retrofit2:retrofit:2.9.0")
    implementation("com.squareup.retrofit2:converter-gson:2.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.0")
}
```

### 6.2 API Interface

```kotlin
interface WhoopFirmwareApi {

    @POST("firmware-service/v4/firmware/check")
    suspend fun checkFirmware(
        @Query("deviceName") deviceName: String,
        @Body chipFirmwares: List<ChipFirmwareVersion>
    ): DesiredDeviceFirmwareConfig

    @POST("firmware-service/v4/firmware/version")
    suspend fun downloadFirmware(
        @Query("deviceName") deviceName: String,
        @Body payload: DownloadFwFilePayload
    ): DesiredDeviceFirmwareUpdate

    @POST("firmware-service/v4/firmware/result")
    suspend fun reportResult(
        @Query("deviceName") deviceName: String,
        @Body payload: ReportFwUpdateResultPayload,
        @Header("X-WHOOP-Auxiliary-Device-Id") auxDeviceId: String? = null
    ): Response<Unit>
}

data class ChipFirmwareVersion(
    @SerializedName("chip_name") val chipName: String,
    @SerializedName("version") val version: String
)

data class DesiredDeviceFirmwareConfig(
    @SerializedName("hardware_device") val hardwareDevice: String,
    @SerializedName("chip_firmwares") val chipFirmwares: List<ChipFirmwareVersion>,
    @SerializedName("force_update") val isForceUpdate: Boolean,
    @SerializedName("force_update_reprompt_cadence") val forceUpdateRepromptTime: Long?
)

data class DesiredDeviceFirmwareUpdate(
    @SerializedName("desired_device_firmware_config") val config: DesiredDeviceFirmwareConfig,
    @SerializedName("firmware_zip_file") val firmwareZipFile: String  // Base64-encoded ZIP
)

data class DownloadFwFilePayload(
    @SerializedName("current_chip_firmwares") val currentChipFirmwares: List<ChipFirmwareVersion>,
    @SerializedName("chip_firmwares_of_upgrade") val chipFirmwareUpgrade: List<ChipFirmwareVersion>
)

data class ReportFwUpdateResultPayload(
    @SerializedName("chip_firmware") val chipFirmwares: List<ChipFirmwareVersion>,
    @SerializedName("successful") val isSuccessful: Boolean
)
```

### 6.3 Firmware Update Manager

```kotlin
class FirmwareUpdateManager(
    private val api: WhoopFirmwareApi,
    private val context: Context
) {
    private val firmwareDir = File(context.filesDir, "whoop_firmware")

    /**
     * Step 1: Check if firmware update is available
     */
    suspend fun checkForUpdate(
        deviceName: String,   // "GOOSE" for Whoop 5.0, "PUFFIN" for Puffin
        currentChips: List<ChipFirmwareVersion>
    ): DesiredDeviceFirmwareConfig {
        return api.checkFirmware(deviceName, currentChips)
    }

    /**
     * Step 2: Download firmware ZIP
     */
    suspend fun downloadFirmware(
        deviceName: String,
        currentChips: List<ChipFirmwareVersion>,
        targetChips: List<ChipFirmwareVersion>,
        serial: String
    ): File {
        val response = api.downloadFirmware(
            deviceName,
            DownloadFwFilePayload(currentChips, targetChips)
        )

        // Decode base64 ZIP
        val zipBytes = Base64.decode(response.firmwareZipFile, Base64.DEFAULT)

        // Save and extract
        val deviceDir = File(firmwareDir, serial).apply { mkdirs() }
        val zipFile = File(deviceDir, "firmwares.zip")
        zipFile.writeBytes(zipBytes)

        // Unzip
        unzip(zipFile, deviceDir)

        // Find the firmware file (.bin or .zbin)
        return deviceDir.listFiles()?.firstOrNull {
            it.extension in listOf("bin", "zbin", "zip") && it.name != "firmwares.zip"
        } ?: throw Exception("No firmware file found in ZIP")
    }

    /**
     * Step 3: Flash firmware via Nordic DFU
     */
    fun startDfu(
        deviceAddress: String,  // BLE MAC address
        deviceName: String,     // BLE device name
        firmwareFile: File
    ): DfuServiceController {
        val starter = DfuServiceInitiator(deviceAddress)
            .setDeviceName(deviceName)
            .setKeepBond(false)
            .setForceDfu(false)
            .setPacketsReceiptNotificationsEnabled(true)
            .setPacketsReceiptNotificationsValue(12)
            .setUnsafeExperimentalButtonlessServiceInSecureDfuEnabled(true)

        // Set firmware file based on extension
        if (firmwareFile.extension == "zip") {
            starter.setZip(null, firmwareFile.absolutePath)
        } else {
            // For .bin/.zbin files, wrap in DFU init packet
            starter.setBinOrHex(DfuBaseService.TYPE_APPLICATION, firmwareFile.absolutePath)
        }

        return starter.start(context, DfuService::class.java)
    }

    /**
     * Step 4: Report result
     */
    suspend fun reportResult(
        deviceName: String,
        chipFirmwares: List<ChipFirmwareVersion>,
        success: Boolean
    ) {
        api.reportResult(
            deviceName,
            ReportFwUpdateResultPayload(chipFirmwares, success)
        )
    }

    private fun unzip(zipFile: File, outputDir: File) {
        ZipInputStream(FileInputStream(zipFile)).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                val outFile = File(outputDir, entry.name)
                if (entry.isDirectory) {
                    outFile.mkdirs()
                } else {
                    BufferedOutputStream(FileOutputStream(outFile)).use { bos ->
                        val buffer = ByteArray(1024)
                        var len: Int
                        while (zis.read(buffer).also { len = it } > 0) {
                            bos.write(buffer, 0, len)
                        }
                    }
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }
    }
}

// Nordic DFU Service implementation
class DfuService : DfuBaseService() {
    override fun getNotificationTarget(): Class<out Activity> = MainActivity::class.java
    override fun isDebug(): Boolean = false
}
```

### 6.4 Authentication

```kotlin
// Get access token via Cognito
suspend fun authenticate(email: String, password: String): String {
    val client = OkHttpClient()
    val body = """
    {
        "AuthFlow": "USER_PASSWORD_AUTH",
        "AuthParameters": {"USERNAME": "$email", "PASSWORD": "$password"},
        "ClientId": "<COGNITO_CLIENT_ID>"
    }
    """.trimIndent()

    val request = Request.Builder()
        .url("https://api.prod.whoop.com/auth-service/v3/whoop/")
        .addHeader("Content-Type", "application/x-amz-json-1.1")
        .addHeader("x-amz-target", "AWSCognitoIdentityProviderService.InitiateAuth")
        .post(body.toRequestBody("application/x-amz-json-1.1".toMediaType()))
        .build()

    val response = client.newCall(request).execute()
    val json = JSONObject(response.body?.string() ?: "")
    return json.getJSONObject("AuthenticationResult").getString("AccessToken")
}

// Required headers for all firmware API calls
fun authHeaders(token: String) = mapOf(
    "Authorization" to "Bearer $token",
    "x-whoop-app-version" to "5.430.0",
    "x-whoop-device-platform" to "ANDROID",
    "Content-Type" to "application/json"
)
```

### 6.5 Complete Usage Example

```kotlin
// 1. Authenticate
val token = authenticate("user@example.com", "password")

// 2. Check for update (Whoop 5.0)
val config = api.checkFirmware(
    deviceName = "GOOSE",
    chipFirmwares = listOf(ChipFirmwareVersion("AMBIQ", "50.34.0.0"))
)
// config.chipFirmwares = [ChipFirmwareVersion("AMBIQ", "50.35.2.0")]

// 3. Download firmware
val fwFile = manager.downloadFirmware(
    deviceName = "GOOSE",
    currentChips = listOf(ChipFirmwareVersion("AMBIQ", "50.34.0.0")),
    targetChips = config.chipFirmwares,
    serial = "<STRAP_SERIAL>"
)
// fwFile = .../whoop_firmware/<STRAP_SERIAL>/maverick-50.35.2.0.zbin

// 4. Flash via Nordic DFU (BLE)
val controller = manager.startDfu(
    deviceAddress = "<STRAP_MAC>",
    deviceName = "Whoop",
    firmwareFile = fwFile
)

// 5. Monitor progress
val listener = object : DfuProgressListenerAdapter() {
    override fun onProgressChanged(
        deviceAddress: String, percent: Int, speed: Float,
        avgSpeed: Float, currentPart: Int, partsTotal: Int
    ) {
        Log.d("DFU", "Progress: $percent%")
    }
    override fun onDfuCompleted(deviceAddress: String) {
        // 6. Report success
        runBlocking {
            manager.reportResult("GOOSE", config.chipFirmwares, true)
        }
    }
}
DfuServiceListenerHelper.registerProgressListener(context, listener)
```

---

## 7. Firmware File Formats

### 7.1 Maverick (.zbin) — Ambiq Secure OTA

The `.zbin` format is Ambiq Micro's secure OTA update format for Apollo4 Blue Plus SoCs.

**Header structure (first 24 bytes):**
```
Offset  Size  Field
0       4     Magic (0xfc43b4a4 = Ambiq OTA magic)
4       4     Image size
8       4     ???
12      4     ???
16      4     ???
20      4     ???
24+     N     Build info string (null-terminated)
```

Contains embedded build metadata:
- Git commit hash: `54fc551ae08a204f9d30ab17`
- Build date: `2025-11-04T18:46:59`
- Version: `50.35.x.x`
- Builder: (redacted)

### 7.2 Puffin (.bin) — Nordic nRF DFU

Standard Nordic Semiconductor DFU binary format for nRF52/nRF53 series.

**Header structure:**
```
Offset  Size  Field
0       4     CRC32 or magic (0x3db8f396)
4       4     Reserved (0x00000000)
8       4     Image type (0x00020000)
12      4     Image size (0x00063f0c = 409,356 bytes)
```

The filename includes a hash: `5ce45820`.

---

## 8. Nordic DFU Protocol (BLE)

The Whoop app uses the Nordic Semiconductor DFU library (`no.nordicsemi.android:dfu`) for BLE firmware flashing. The DFU process:

1. **Buttonless DFU Entry** — App sends DFU trigger command via BLE
2. **Device reboots into DFU bootloader** — New BLE advertisement with DFU service UUID
3. **Init packet** — Firmware metadata sent first (type, size, hash)
4. **Firmware transfer** — Binary data sent in MTU-sized chunks (up to 517 bytes)
5. **Validation** — Bootloader verifies CRC/signature
6. **Activation** — Bootloader swaps to new firmware and reboots

**DFU Configuration (from decompiled APK):**
- MTU: 517 bytes (negotiated)
- Packet receipt notifications: enabled, every 12 packets
- Keep bond: configurable per device
- Force DFU: configurable
- Unsafe buttonless: enabled for secure DFU

**Supported DFU implementations (from `DfuServiceProvider.java`):**
- SecureDfuImpl (primary)
- ButtonlessDfuWithBondSharingImpl
- ButtonlessDfuWithoutBondSharingImpl
- LegacyDfuImpl
- LegacyButtonlessDfuImpl
- ExperimentalButtonlessDfuImpl

---

## 9. Feature Flags

| Flag | Purpose |
|------|---------|
| `droid-fw-update-worker-fix-onboarding` | Enables v2 FirmwareUpdateWorker with improved scheduling |
| `droid-fw-update-worker-fix` | Runtime worker version selection |
| `silent-forced-fw-update` | Forces firmware update without user prompt |

---

## 10. Caveats & Limitations

1. **No older firmware versions available** — The API always returns the latest version for each device type. There is no version parameter to request specific firmware.

2. **Device-account binding** — HARVARD firmware requires the user account to have a Gen 4 device registered. GOOSE/PUFFIN work without device registration.

3. **Token expiration** — Access tokens expire after 24 hours. Use Cognito refresh flow with the same ClientId.

4. **Ambiq .zbin format** — The Maverick firmware uses Ambiq's proprietary secure OTA format. Standard Nordic DFU may not work directly — the official app likely uses a custom DFU flow for Ambiq chips.

5. **DFU service UUID** — During DFU, the Whoop strap advertises with the Nordic DFU service UUID (`0xFE59`), not the normal Whoop service UUIDs.
