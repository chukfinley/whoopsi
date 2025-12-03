package com.whoopcapture

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.whoopcapture.db.AppDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.io.File

class MainActivity : ComponentActivity() {

    private var bleService: WhoopBleService? = null
    private var bound = mutableStateOf(false)

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            bleService = (binder as WhoopBleService.LocalBinder).getService()
            bound.value = true
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            bleService = null
            bound.value = false
        }
    }

    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        if (results.values.all { it }) {
            startAndConnect()
        } else {
            Toast.makeText(this, "Permissions required for BLE", Toast.LENGTH_LONG).show()
        }
    }

    private var pendingRecovery = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingRecovery = intent?.getBooleanExtra("recover", false) == true
        setContent {
            WhoopCaptureTheme {
                val isBound by bound
                if (isBound) {
                    // Trigger recovery once when bound
                    LaunchedEffect(pendingRecovery) {
                        if (pendingRecovery) {
                            pendingRecovery = false
                            bleService?.deepRecovery()
                        }
                    }
                    SyncDashboard(bleService!!)
                } else {
                    Box(Modifier.fillMaxSize().background(Color(0xFF111111)), contentAlignment = Alignment.Center) {
                        Text("Starting...", color = Color.Gray)
                    }
                }
            }
        }
        requestPermissionsAndConnect()
    }

    override fun onStart() {
        super.onStart()
        bindService(Intent(this, WhoopBleService::class.java), connection, Context.BIND_AUTO_CREATE)
    }

    override fun onStop() {
        super.onStop()
        if (bound.value) {
            unbindService(connection)
            bound.value = false
        }
    }

    fun requestPermissionsAndConnect() {
        val needed = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 31) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_SCAN) != PackageManager.PERMISSION_GRANTED)
                needed.add(Manifest.permission.BLUETOOTH_SCAN)
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED)
                needed.add(Manifest.permission.BLUETOOTH_CONNECT)
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED)
            needed.add(Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (needed.isNotEmpty()) {
            permLauncher.launch(needed.toTypedArray())
        } else {
            startAndConnect()
        }
    }

    private fun startAndConnect() {
        val intent = Intent(this, WhoopBleService::class.java).apply {
            action = WhoopBleService.ACTION_CONNECT
        }
        ContextCompat.startForegroundService(this, intent)
    }

    @Composable
    fun SyncDashboard(service: WhoopBleService) {
        val dao = remember { AppDatabase.get(this@MainActivity).sensorRecordDao() }

        val status by service.status.collectAsStateWithLifecycle()
        val syncing by service.syncRunning.collectAsStateWithLifecycle()
        val syncRound by service.syncRound.collectAsStateWithLifecycle()
        val syncPackets by service.syncTotalPackets.collectAsStateWithLifecycle()
        val syncNew by service.syncNewRecords.collectAsStateWithLifecycle()
        val syncDateRange by service.syncDateRange.collectAsStateWithLifecycle()
        val battery by service.batteryLevel.collectAsStateWithLifecycle()
        val charging by service.isCharging.collectAsStateWithLifecycle()

        val dbCount by dao.countFlow().collectAsStateWithLifecycle(initialValue = 0)

        // Today's records
        val todayStart = remember {
            val cal = Calendar.getInstance(TimeZone.getDefault())
            cal.set(Calendar.HOUR_OF_DAY, 0)
            cal.set(Calendar.MINUTE, 0)
            cal.set(Calendar.SECOND, 0)
            cal.set(Calendar.MILLISECOND, 0)
            cal.timeInMillis / 1000
        }
        val todayCount by dao.countTodayFlow(todayStart).collectAsStateWithLifecycle(initialValue = 0)

        val scope = rememberCoroutineScope()

        // Determine connection state for indicator
        val isConnected = status.contains("HR:") || status.contains("Subscribed") ||
            status.contains("Sync") || status.contains("Receiving") ||
            status.contains("Data:") || status.contains("connected", ignoreCase = true) ||
            status.contains("Done:") || status.contains("Smart sync") || status.contains("Full sync")
        val indicatorColor = when {
            syncing -> Color(0xFF44D62C) // Green when syncing
            isConnected -> Color(0xFF2288FF) // Blue when connected
            status.contains("Disconnect") -> Color(0xFF666666) // Gray when disconnected
            else -> Color(0xFFFFAA00) // Orange otherwise (connecting, etc.)
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Color(0xFF111111))
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(Modifier.height(16.dp))

            // Title + connection indicator
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(12.dp)
                        .clip(CircleShape)
                        .background(indicatorColor)
                )
                Spacer(Modifier.width(8.dp))
                Text("WHOOP SYNC", color = Color(0xFF44D62C), fontSize = 22.sp, fontWeight = FontWeight.Bold)
            }

            Spacer(Modifier.height(8.dp))
            Text(status, color = Color.Gray, fontSize = 12.sp, maxLines = 2)

            // Sync progress
            if (syncing || syncDateRange.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = Color(0xFF1A2A1A)),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(
                            if (syncing) "Syncing: round $syncRound — ${"%,d".format(syncNew)} new / ${"%,d".format(syncPackets)} pkts"
                            else "Last sync: ${"%,d".format(syncNew)} new records",
                            color = Color(0xFF44D62C),
                            fontSize = 13.sp
                        )
                        if (syncDateRange.isNotEmpty()) {
                            Text(
                                syncDateRange,
                                color = Color(0xFF88AA88),
                                fontSize = 11.sp
                            )
                        }
                    }
                }
            }

            Spacer(Modifier.height(24.dp))

            // Main stats
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                StatCard("Total", formatCompact(dbCount), Modifier.weight(1f))
                StatCard("Today", "%,d".format(todayCount), Modifier.weight(1f))
                StatCard(
                    "Battery",
                    if (battery >= 0) "$battery%${if (charging) " +" else ""}" else "--",
                    Modifier.weight(1f)
                )
            }

            Spacer(Modifier.height(12.dp))

            // Whoop app patch status
            WhoopPatchCard()

            Spacer(Modifier.height(12.dp))

            // Today's sync status
            val todayOk = todayCount >= 3600 // ~1 hour of data = reasonable
            val todayPartial = todayCount in 1..3599
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = Color(0xFF1A1A1A)),
                shape = RoundedCornerShape(12.dp)
            ) {
                Row(
                    Modifier.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        Modifier
                            .size(10.dp)
                            .clip(CircleShape)
                            .background(
                                when {
                                    todayOk -> Color(0xFF44D62C)
                                    todayPartial -> Color(0xFFFFAA00)
                                    else -> Color(0xFF661111)
                                }
                            )
                    )
                    Spacer(Modifier.width(12.dp))
                    Text(
                        when {
                            todayOk -> "Today: synced (%,d records)".format(todayCount)
                            todayPartial -> "Today: partial (%,d records)".format(todayCount)
                            else -> "Today: no data synced yet"
                        },
                        color = Color.White,
                        fontSize = 14.sp
                    )
                }
            }

            Spacer(Modifier.weight(1f))

            // Action buttons
            Button(
                onClick = { service.requestSync() },
                enabled = !syncing,
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color(0xFF44D62C),
                    disabledContainerColor = Color(0xFF2A2A2A)
                ),
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape = RoundedCornerShape(12.dp)
            ) {
                Text(
                    if (syncing) "Syncing..." else "Sync Now",
                    color = if (syncing) Color.Gray else Color.Black,
                    fontWeight = FontWeight.Bold,
                    fontSize = 16.sp
                )
            }

            Spacer(Modifier.height(8.dp))

            // Two rows of 2 buttons each — consistent sizing
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { service.requestFullSyncPublic() },
                    enabled = !syncing,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color(0xFFCC7700),
                        disabledContainerColor = Color(0xFF2A2A2A)
                    ),
                    modifier = Modifier.weight(1f).height(44.dp),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text("Full Sync", color = Color.White, fontSize = 13.sp, maxLines = 1)
                }
                Button(
                    onClick = {
                        scope.launch(Dispatchers.IO) {
                            val file = CsvExporter.export(this@MainActivity)
                            launch(Dispatchers.Main) {
                                Toast.makeText(this@MainActivity, "Exported: ${file.absolutePath}", Toast.LENGTH_LONG).show()
                            }
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF333333)),
                    modifier = Modifier.weight(1f).height(44.dp),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text("Export CSV", color = Color.White, fontSize = 13.sp, maxLines = 1)
                }
            }

            Spacer(Modifier.height(8.dp))

            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { requestPermissionsAndConnect() },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF333333)),
                    modifier = Modifier.weight(1f).height(44.dp),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text("Reconnect", color = Color.White, fontSize = 13.sp, maxLines = 1)
                }
                Button(
                    onClick = { service.disconnect() },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF441111)),
                    modifier = Modifier.weight(1f).height(44.dp),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text("Disconnect", color = Color(0xFFFF6666), fontSize = 13.sp, maxLines = 1)
                }
            }

            Spacer(Modifier.height(16.dp))
        }
    }

    @Composable
    fun WhoopPatchCard() {
        val scope = rememberCoroutineScope()
        var patchStatus by remember { mutableStateOf(ApkPatcher.checkStatus(this@MainActivity)) }
        var patchProgress by remember { mutableStateOf("") }
        var patching by remember { mutableStateOf(false) }
        var patchedApk by remember { mutableStateOf<File?>(null) }

        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(
                containerColor = if (patchStatus.isDebuggable) Color(0xFF1A2A1A) else Color(0xFF2A1A1A)
            ),
            shape = RoundedCornerShape(8.dp)
        ) {
            Column(Modifier.padding(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(
                                if (patchStatus.isDebuggable) Color(0xFF44D62C)
                                else if (patchStatus.isInstalled) Color(0xFFFF4444)
                                else Color(0xFF666666)
                            )
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        buildString {
                            append("Whoop App: ")
                            if (!patchStatus.isInstalled) append("Not installed")
                            else {
                                append(patchStatus.versionName ?: "?")
                                append(if (patchStatus.isDebuggable) " (patched)" else " (needs patch)")
                            }
                        },
                        color = Color.White, fontSize = 12.sp
                    )
                }

                if (patchStatus.isInstalled && !patchStatus.isDebuggable) {
                    Spacer(Modifier.height(8.dp))
                    if (patchProgress.isNotEmpty()) {
                        Text(patchProgress, color = Color(0xFFFFAA00), fontSize = 11.sp)
                        Spacer(Modifier.height(4.dp))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = {
                                patching = true
                                patchProgress = "Starting..."
                                scope.launch(Dispatchers.IO) {
                                    try {
                                        val apk = ApkPatcher.patchApk(this@MainActivity) { msg ->
                                            patchProgress = msg
                                        }
                                        patchedApk = apk
                                        patchProgress = "Ready! Tap 'Install' to apply."
                                    } catch (e: Exception) {
                                        patchProgress = "Error: ${e.message}"
                                    } finally {
                                        patching = false
                                    }
                                }
                            },
                            enabled = !patching,
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFCC4400)),
                            modifier = Modifier.weight(1f).height(36.dp),
                            shape = RoundedCornerShape(8.dp),
                            contentPadding = PaddingValues(4.dp)
                        ) {
                            Text(
                                if (patching) "Patching..." else "Patch APK",
                                color = Color.White, fontSize = 12.sp, maxLines = 1
                            )
                        }
                        if (patchedApk != null) {
                            Button(
                                onClick = {
                                    try {
                                        val intent = ApkPatcher.getInstallIntent(this@MainActivity, patchedApk!!)
                                        startActivity(intent)
                                    } catch (e: Exception) {
                                        patchProgress = "Install failed: ${e.message}"
                                    }
                                },
                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF44D62C)),
                                modifier = Modifier.weight(1f).height(36.dp),
                                shape = RoundedCornerShape(8.dp),
                                contentPadding = PaddingValues(4.dp)
                            ) {
                                Text("Install", color = Color.Black, fontSize = 12.sp, maxLines = 1, fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                    Text(
                        "Note: You'll need to uninstall the original app first, then re-login.",
                        color = Color(0xFF888888), fontSize = 10.sp,
                        modifier = Modifier.padding(top = 4.dp)
                    )
                }

                if (patchStatus.isDebuggable) {
                    Spacer(Modifier.height(8.dp))

                    var siphonStatus by remember { mutableStateOf("") }
                    var siphoning by remember { mutableStateOf(false) }

                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            onClick = {
                                siphoning = true
                                siphonStatus = "Siphoning..."
                                scope.launch(Dispatchers.IO) {
                                    try {
                                        val result = WhoopDbSiphon.siphon(this@MainActivity)
                                        siphonStatus = "${result.newRecords} new / ${result.totalRead} total — ${result.status}"
                                    } catch (e: Exception) {
                                        siphonStatus = "Error: ${e.message}"
                                    } finally {
                                        siphoning = false
                                    }
                                }
                            },
                            enabled = !siphoning,
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF2255AA)),
                            modifier = Modifier.height(32.dp),
                            shape = RoundedCornerShape(8.dp),
                            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp)
                        ) {
                            Text(
                                if (siphoning) "Siphoning..." else "Siphon Now",
                                color = Color.White, fontSize = 11.sp, maxLines = 1
                            )
                        }
                        Text(
                            if (siphonStatus.isNotEmpty()) siphonStatus
                            else "DB siphon active — reads sensor data from Whoop app",
                            color = Color(0xFF88AA88), fontSize = 10.sp,
                            maxLines = 2
                        )
                    }
                }
            }
        }
    }

    @Composable
    fun StatCard(label: String, value: String, modifier: Modifier = Modifier) {
        Card(
            modifier = modifier,
            colors = CardDefaults.cardColors(containerColor = Color(0xFF1A1A1A)),
            shape = RoundedCornerShape(12.dp)
        ) {
            Column(
                Modifier.padding(horizontal = 8.dp, vertical = 12.dp).fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(label, color = Color.Gray, fontSize = 11.sp, maxLines = 1)
                Spacer(Modifier.height(4.dp))
                Text(value, color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.Bold, maxLines = 1)
            }
        }
    }

    /** Format large numbers compactly: 1,234,567 → "1.23M", 85,432 → "85.4K" */
    private fun formatCompact(n: Int): String = when {
        n >= 1_000_000 -> "%.2fM".format(n / 1_000_000.0)
        n >= 10_000 -> "%.1fK".format(n / 1_000.0)
        else -> "%,d".format(n)
    }
}

@Composable
fun WhoopCaptureTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Color(0xFF44D62C),
            background = Color(0xFF111111),
            surface = Color(0xFF1A1A1A),
        ),
        content = content
    )
}
