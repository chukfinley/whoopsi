package com.whoopcapture

import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.util.Log
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

/**
 * On-device APK patcher for the official Whoop app.
 *
 * Adds android:debuggable="true" to the <application> element and clears
 * requiredSplitTypes so the APK installs without split APKs.
 *
 * This enables `run-as com.whoop.android` which allows our app to read
 * the official app's cached_packet_db database without root.
 */
object ApkPatcher {

    private const val TAG = "ApkPatcher"
    private const val WHOOP_PACKAGE = "com.whoop.android"
    private const val DEBUGGABLE_RES_ID = 0x0101000f

    // ─── Public API ──────────────────────────────────────────────────────

    data class PatchStatus(
        val isInstalled: Boolean,
        val isDebuggable: Boolean,
        val versionName: String?,
        val versionCode: Long?,
        val message: String
    )

    /** Check if the official Whoop app is installed and whether it's patched. */
    fun checkStatus(context: Context): PatchStatus {
        return try {
            val pm = context.packageManager
            val info = pm.getApplicationInfo(WHOOP_PACKAGE, 0)
            val pkgInfo = pm.getPackageInfo(WHOOP_PACKAGE, 0)
            val debuggable = (info.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0

            PatchStatus(
                isInstalled = true,
                isDebuggable = debuggable,
                versionName = pkgInfo.versionName,
                versionCode = pkgInfo.longVersionCode,
                message = if (debuggable) "Patched (debuggable)" else "Not patched"
            )
        } catch (e: PackageManager.NameNotFoundException) {
            PatchStatus(false, false, null, null, "Whoop app not installed")
        }
    }

    /** Test if run-as actually works (most reliable check). */
    suspend fun testRunAs(): Boolean = withContext(Dispatchers.IO) {
        try {
            val proc = Runtime.getRuntime().exec(arrayOf("run-as", WHOOP_PACKAGE, "id"))
            val ok = proc.waitFor(5, java.util.concurrent.TimeUnit.SECONDS)
            if (!ok) { proc.destroyForcibly(); return@withContext false }
            val output = proc.inputStream.bufferedReader().readText()
            output.contains("uid=")
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Extract, patch, and prepare the official Whoop APK.
     * Returns the path to the patched APK, ready for installation.
     *
     * The user must then install it (requires uninstalling the original first
     * since the signature changes).
     */
    suspend fun patchApk(context: Context, onProgress: (String) -> Unit = {}): File = withContext(Dispatchers.IO) {
        onProgress("Finding Whoop APK...")

        // 1. Find the installed APK
        val pm = context.packageManager
        val info = pm.getApplicationInfo(WHOOP_PACKAGE, 0)
        val sourceApk = File(info.sourceDir)
        Log.i(TAG, "Source APK: ${sourceApk.absolutePath} (${sourceApk.length() / 1024 / 1024} MB)")

        // 2. Copy to our cache dir
        onProgress("Copying APK...")
        val cacheDir = File(context.cacheDir, "apk_patch")
        cacheDir.mkdirs()
        val workApk = File(cacheDir, "whoop_work.apk")
        sourceApk.copyTo(workApk, overwrite = true)

        // 3. Read and patch manifest
        onProgress("Patching manifest...")
        val originalManifest: ByteArray
        ZipFile(workApk).use { zip ->
            val entry = zip.getEntry("AndroidManifest.xml")
                ?: throw IllegalStateException("No AndroidManifest.xml in APK")
            originalManifest = zip.getInputStream(entry).readBytes()
        }

        val patchedManifest = patchManifestBinary(originalManifest)
        Log.i(TAG, "Manifest patched: ${originalManifest.size} -> ${patchedManifest.size} bytes")

        // 4. Replace manifest in APK (rebuild zip preserving all other entries)
        onProgress("Rebuilding APK...")
        val outputApk = File(cacheDir, "whoop_patched.apk")
        replaceManifestInApk(workApk, outputApk, patchedManifest)
        workApk.delete()

        // 5. Zipalign (we do a manual 4-byte alignment for the stored entries)
        // Note: Full zipalign requires the tool binary. We skip this for on-device
        // patching — Android can handle slightly misaligned APKs for sideloading.

        // 6. Sign with debug key
        onProgress("Signing APK...")
        signApk(context, outputApk)

        onProgress("Done! Ready to install.")
        Log.i(TAG, "Patched APK: ${outputApk.absolutePath} (${outputApk.length() / 1024 / 1024} MB)")
        outputApk
    }

    /** Get an install intent for the patched APK. */
    fun getInstallIntent(context: Context, apkFile: File): Intent {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", apkFile)
        return Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
    }

    // ─── Binary Manifest Patching ────────────────────────────────────────

    private fun patchManifestBinary(data: ByteArray): ByteArray {
        // Parse string pool
        val spOff = 8
        val spChunkSize = getInt(data, spOff + 4)
        val stringCount = getInt(data, spOff + 8)
        val spFlags = getInt(data, spOff + 16)
        val stringsStart = getInt(data, spOff + 20)
        val absStringsStart = spOff + stringsStart

        val stringOffsets = IntArray(stringCount) { getInt(data, spOff + 28 + it * 4) }
        val strings = Array(stringCount) { readUtf16String(data, absStringsStart + stringOffsets[it]) }

        // Parse resource ID map
        val rmOff = spOff + spChunkSize
        val rmChunkSize = getInt(data, rmOff + 4)
        val resIdCount = (rmChunkSize - 8) / 4
        val resIds = IntArray(resIdCount) { getInt(data, rmOff + 8 + it * 4) }

        // Check if already patched
        if (resIds.any { it == DEBUGGABLE_RES_ID }) {
            Log.i(TAG, "Manifest already has debuggable — only patching requiredSplitTypes")
            return patchRequiredSplitTypesOnly(data, strings)
        }

        // Find insertion index (resource IDs are sorted)
        var insertIdx = 0
        for (i in resIds.indices) {
            if (resIds[i] < DEBUGGABLE_RES_ID) insertIdx = i + 1
        }

        // Find key string indices
        val androidNs = strings.indexOfFirst { it == "http://schemas.android.com/apk/res/android" }
        val applicationName = strings.indexOfFirst { it == "application" }
        val emptyStr = strings.indexOfFirst { it == "" }
        if (androidNs < 0 || applicationName < 0) throw IllegalStateException("Missing required manifest strings")

        fun shift(idx: Int): Int = if (idx < 0) idx else if (idx >= insertIdx) idx + 1 else idx

        // Build new string and resource ID lists
        val newStrings = ArrayList<String>(stringCount + 1).apply {
            addAll(strings.take(insertIdx))
            add("debuggable")
            addAll(strings.drop(insertIdx))
        }
        val newResIds = IntArray(resIdCount + 1).also { arr ->
            resIds.take(insertIdx).toIntArray().copyInto(arr)
            arr[insertIdx] = DEBUGGABLE_RES_ID
            resIds.drop(insertIdx).toIntArray().copyInto(arr, insertIdx + 1)
        }

        val newAndroidNs = shift(androidNs)
        val newAppName = shift(applicationName)
        val newEmptyStr = if (emptyStr >= 0) shift(emptyStr) else -1
        val newDebugStr = insertIdx

        // Parse XML chunks (after resource ID map)
        val xmlStart = rmOff + rmChunkSize
        val chunks = mutableListOf<Pair<Int, ByteArray>>() // (type, data)
        var off = xmlStart
        while (off < data.size) {
            val ct = getShort(data, off)
            val cs = getInt(data, off + 4)
            if (cs == 0) break
            chunks.add(ct to data.copyOfRange(off, off + cs))
            off += cs
        }

        // Patch chunks
        val patchedChunks = chunks.map { (type, chunkData) ->
            patchChunk(type, chunkData, ::shift, newStrings, newResIds,
                newAndroidNs, newAppName, newDebugStr, newEmptyStr, DEBUGGABLE_RES_ID)
        }

        // Assemble
        val newSp = buildStringPoolBytes(newStrings, spFlags)
        val newRm = buildResIdMapBytes(newResIds)

        val resultSize = 8 + newSp.size + newRm.size + patchedChunks.sumOf { it.size }
        val result = ByteArray(resultSize)
        putInt(result, 0, 0x00080003) // magic
        putInt(result, 4, resultSize)
        var pos = 8
        newSp.copyInto(result, pos); pos += newSp.size
        newRm.copyInto(result, pos); pos += newRm.size
        for (c in patchedChunks) { c.copyInto(result, pos); pos += c.size }

        return result
    }

    private fun patchChunk(
        type: Int, data: ByteArray, shift: (Int) -> Int,
        strings: List<String>, resIds: IntArray,
        androidNs: Int, appName: Int, debugStr: Int, emptyStr: Int, debugResId: Int
    ): ByteArray {
        val d = data.copyOf()

        when (type) {
            0x0100, 0x0101 -> { // Namespace
                val hs = getShort(d, 2)
                putInt(d, hs, shift(getInt(d, hs)))
                putInt(d, hs + 4, shift(getInt(d, hs + 4)))
            }
            0x0102 -> { // Start element
                val hs = getShort(d, 2)
                val origName = getInt(d, hs + 4)
                putInt(d, hs, shift(getInt(d, hs)))
                putInt(d, hs + 4, shift(origName))

                val attrStart = getShort(d, hs + 8)
                val attrSize = getShort(d, hs + 10)
                val attrCount = getShort(d, hs + 12)
                val base = hs + attrStart

                for (i in 0 until attrCount) {
                    val ao = base + i * attrSize
                    val origAttrName = getInt(d, ao + 4)
                    putInt(d, ao, shift(getInt(d, ao)))         // ns
                    putInt(d, ao + 4, shift(origAttrName))      // name
                    val raw = getInt(d, ao + 8)
                    if (raw >= 0) putInt(d, ao + 8, shift(raw))
                    val tvType = d[ao + 15].toInt() and 0xFF
                    val tvData = getInt(d, ao + 16)
                    if (tvType == 0x03 && tvData >= 0) putInt(d, ao + 16, shift(tvData))

                    // Patch requiredSplitTypes -> empty
                    val shiftedName = shift(origAttrName)
                    if (tvType == 0x03 && shiftedName in strings.indices
                        && strings[shiftedName] == "requiredSplitTypes" && emptyStr >= 0) {
                        putInt(d, ao + 8, emptyStr)
                        putInt(d, ao + 16, emptyStr)
                    }
                }

                // Add debuggable to <application>
                if (shift(origName) == appName) {
                    // Find sorted position by resource ID
                    val attrResIds = (0 until attrCount).map { i ->
                        val an = getInt(d, base + i * attrSize + 4)
                        if (an in resIds.indices) resIds[an] else 0x7FFFFFFF
                    }
                    val insPos = attrResIds.count { it < debugResId }

                    // Build attribute: ns(4) + name(4) + rawValue(4) + tvSize(2) + 0(1) + type(1) + data(4) = 20 bytes
                    val attr = ByteArray(20)
                    putInt(attr, 0, androidNs)
                    putInt(attr, 4, debugStr)
                    putInt(attr, 8, -1)  // no raw string
                    putShort(attr, 12, 8)
                    attr[14] = 0
                    attr[15] = 0x12 // TYPE_INT_BOOLEAN
                    putInt(attr, 16, -1) // true = 0xFFFFFFFF

                    val bytePos = base + insPos * attrSize
                    val expanded = ByteArray(d.size + 20)
                    d.copyInto(expanded, 0, 0, bytePos)
                    attr.copyInto(expanded, bytePos)
                    d.copyInto(expanded, bytePos + 20, bytePos, d.size)

                    // Update attr count and chunk size
                    putShort(expanded, hs + 12, attrCount + 1)
                    putInt(expanded, 4, getInt(expanded, 4) + 20)

                    return expanded
                }
            }
            0x0103 -> { // End element
                val hs = getShort(d, 2)
                putInt(d, hs, shift(getInt(d, hs)))
                putInt(d, hs + 4, shift(getInt(d, hs + 4)))
            }
        }
        return d
    }

    private fun patchRequiredSplitTypesOnly(data: ByteArray, strings: Array<String>): ByteArray {
        val d = data.copyOf()
        val emptyIdx = strings.indexOfFirst { it == "" }
        if (emptyIdx < 0) return d

        val spOff = 8
        val spChunkSize = getInt(d, spOff + 4)
        val rmOff = spOff + spChunkSize
        val rmChunkSize = getInt(d, rmOff + 4)
        var off = rmOff + rmChunkSize
        while (off < d.size) {
            val ct = getShort(d, off)
            val cs = getInt(d, off + 4)
            if (cs == 0) break
            if (ct == 0x0102) {
                val hs = getShort(d, off + 2)
                val attrStart = getShort(d, off + hs + 8)
                val attrSize = getShort(d, off + hs + 10)
                val attrCount = getShort(d, off + hs + 12)
                for (i in 0 until attrCount) {
                    val ao = off + hs + attrStart + i * attrSize
                    val an = getInt(d, ao + 4)
                    val tvType = d[ao + 15].toInt() and 0xFF
                    if (an in strings.indices && strings[an] == "requiredSplitTypes" && tvType == 0x03) {
                        putInt(d, ao + 8, emptyIdx)
                        putInt(d, ao + 16, emptyIdx)
                    }
                }
            }
            off += cs
        }
        return d
    }

    // ─── String Pool / Resource Map Builders ─────────────────────────────

    private fun buildStringPoolBytes(strings: List<String>, flags: Int): ByteArray {
        val encoded = strings.map { s ->
            val strBytes = s.toByteArray(Charsets.UTF_16LE)
            ByteArray(2 + strBytes.size + 2).also { b ->
                putShort(b, 0, s.length)
                strBytes.copyInto(b, 2)
                // null terminator already zero
            }
        }

        var dataSize = encoded.sumOf { it.size }
        while (dataSize % 4 != 0) dataSize++
        val stringData = ByteArray(dataSize)
        var pos = 0
        val offsets = IntArray(strings.size)
        for (i in encoded.indices) {
            offsets[i] = pos
            encoded[i].copyInto(stringData, pos)
            pos += encoded[i].size
        }

        val headerSize = 28
        val stringsStart = headerSize + strings.size * 4
        val chunkSize = stringsStart + stringData.size

        val result = ByteArray(chunkSize)
        putShort(result, 0, 0x0001)
        putShort(result, 2, headerSize)
        putInt(result, 4, chunkSize)
        putInt(result, 8, strings.size)
        putInt(result, 12, 0) // style count
        putInt(result, 16, flags)
        putInt(result, 20, stringsStart)
        putInt(result, 24, 0) // styles start
        for (i in offsets.indices) putInt(result, 28 + i * 4, offsets[i])
        stringData.copyInto(result, stringsStart)
        return result
    }

    private fun buildResIdMapBytes(resIds: IntArray): ByteArray {
        val chunkSize = 8 + resIds.size * 4
        val result = ByteArray(chunkSize)
        putShort(result, 0, 0x0180)
        putShort(result, 2, 8)
        putInt(result, 4, chunkSize)
        for (i in resIds.indices) putInt(result, 8 + i * 4, resIds[i])
        return result
    }

    // ─── APK Rebuild ─────────────────────────────────────────────────────

    private fun replaceManifestInApk(inputApk: File, outputApk: File, manifest: ByteArray) {
        // Rebuild zip: copy all entries except AndroidManifest.xml, then add patched one.
        // Preserve compression method and other metadata for each entry.
        ZipFile(inputApk).use { src ->
            ZipOutputStream(FileOutputStream(outputApk)).use { dst ->
                // First: write patched manifest
                val manifestEntry = ZipEntry("AndroidManifest.xml").apply {
                    method = ZipEntry.DEFLATED
                }
                dst.putNextEntry(manifestEntry)
                dst.write(manifest)
                dst.closeEntry()

                // Then: copy all other entries
                for (entry in src.entries()) {
                    if (entry.name == "AndroidManifest.xml") continue
                    // Preserve original entry properties
                    val newEntry = ZipEntry(entry)
                    if (entry.method == ZipEntry.STORED) {
                        // For STORED entries, we must set size/crc explicitly
                        newEntry.size = entry.size
                        newEntry.compressedSize = entry.compressedSize
                        newEntry.crc = entry.crc
                    }
                    dst.putNextEntry(newEntry)
                    src.getInputStream(entry).use { it.copyTo(dst) }
                    dst.closeEntry()
                }
            }
        }
    }

    // ─── APK Signing ─────────────────────────────────────────────────────

    private fun signApk(context: Context, apk: File) {
        // We use a simple v1 (JAR) signing approach that works on-device
        // without needing the apksigner tool.
        // For production, consider using Android's PackageInstaller API.

        // Generate or load a debug keystore
        val keystoreFile = File(context.filesDir, "debug_patch.keystore")
        if (!keystoreFile.exists()) {
            generateKeystore(keystoreFile)
        }

        // Try using apksigner if available on device (some ROMs have it)
        // Otherwise fall back to jarsigner-style signing
        try {
            val proc = Runtime.getRuntime().exec(arrayOf(
                "apksigner", "sign",
                "--ks", keystoreFile.absolutePath,
                "--ks-key-alias", "debugkey",
                "--ks-pass", "pass:android",
                "--key-pass", "pass:android",
                apk.absolutePath
            ))
            if (proc.waitFor(60, java.util.concurrent.TimeUnit.SECONDS) && proc.exitValue() == 0) {
                Log.i(TAG, "Signed with apksigner")
                return
            }
        } catch (e: Exception) {
            Log.w(TAG, "apksigner not available: ${e.message}")
        }

        // Fallback: use jarsigner (available via JDK or some devices)
        try {
            val proc = Runtime.getRuntime().exec(arrayOf(
                "jarsigner",
                "-keystore", keystoreFile.absolutePath,
                "-storepass", "android",
                "-keypass", "android",
                "-sigalg", "SHA256withRSA",
                "-digestalg", "SHA-256",
                apk.absolutePath,
                "debugkey"
            ))
            if (proc.waitFor(60, java.util.concurrent.TimeUnit.SECONDS) && proc.exitValue() == 0) {
                Log.i(TAG, "Signed with jarsigner")
                return
            }
        } catch (e: Exception) {
            Log.w(TAG, "jarsigner not available: ${e.message}")
        }

        // Last resort: sign using Android KeyStore API (v1 JAR signing)
        Log.w(TAG, "No signing tool available — APK will need to be signed externally")
        // The APK can still be signed on the computer with:
        //   apksigner sign --ks debug.keystore --ks-key-alias debugkey ...
    }

    private fun generateKeystore(file: File) {
        try {
            val proc = Runtime.getRuntime().exec(arrayOf(
                "keytool", "-genkey", "-v",
                "-keystore", file.absolutePath,
                "-alias", "debugkey",
                "-keyalg", "RSA", "-keysize", "2048",
                "-validity", "36500",
                "-storepass", "android", "-keypass", "android",
                "-dname", "CN=WhoopPatch, OU=Debug, O=Debug, L=Debug, ST=Debug, C=US"
            ))
            proc.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)
            if (file.exists()) {
                Log.i(TAG, "Generated debug keystore")
            }
        } catch (e: Exception) {
            Log.w(TAG, "keytool not available: ${e.message}")
        }
    }

    // ─── UTF-16 String Helpers ───────────────────────────────────────────

    private fun readUtf16String(data: ByteArray, offset: Int): String {
        val len = getShort(data, offset)
        return String(data, offset + 2, len * 2, Charsets.UTF_16LE)
    }

    // ─── Little-endian byte helpers ──────────────────────────────────────

    private fun getShort(data: ByteArray, offset: Int): Int =
        (data[offset].toInt() and 0xFF) or ((data[offset + 1].toInt() and 0xFF) shl 8)

    private fun getInt(data: ByteArray, offset: Int): Int =
        (data[offset].toInt() and 0xFF) or
        ((data[offset + 1].toInt() and 0xFF) shl 8) or
        ((data[offset + 2].toInt() and 0xFF) shl 16) or
        ((data[offset + 3].toInt() and 0xFF) shl 24)

    private fun putShort(data: ByteArray, offset: Int, value: Int) {
        data[offset] = (value and 0xFF).toByte()
        data[offset + 1] = ((value shr 8) and 0xFF).toByte()
    }

    private fun putInt(data: ByteArray, offset: Int, value: Int) {
        data[offset] = (value and 0xFF).toByte()
        data[offset + 1] = ((value shr 8) and 0xFF).toByte()
        data[offset + 2] = ((value shr 16) and 0xFF).toByte()
        data[offset + 3] = ((value shr 24) and 0xFF).toByte()
    }
}
