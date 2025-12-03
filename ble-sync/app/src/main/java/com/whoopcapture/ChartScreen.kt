package com.whoopcapture

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.whoopcapture.db.SensorRecord
import com.whoopcapture.db.SensorRecordDao
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.*

@Composable
fun ChartScreen(dao: SensorRecordDao, onBack: () -> Unit) {
    var selectedTab by remember { mutableIntStateOf(0) }
    val tabs = listOf("Heart Rate", "SpO2", "Accel", "Stats")

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF111111))
    ) {
        // Top bar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            TextButton(onClick = onBack) {
                Text("< Back", color = Color(0xFF44D62C), fontSize = 14.sp)
            }
            Spacer(Modifier.weight(1f))
            Text("DATA CHARTS", color = Color(0xFF44D62C), fontSize = 18.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.weight(1f))
            Spacer(Modifier.width(60.dp))
        }

        // Tabs
        ScrollableTabRow(
            selectedTabIndex = selectedTab,
            containerColor = Color(0xFF1A1A1A),
            contentColor = Color(0xFF44D62C),
            edgePadding = 8.dp
        ) {
            tabs.forEachIndexed { index, title ->
                Tab(
                    selected = selectedTab == index,
                    onClick = { selectedTab = index },
                    text = {
                        Text(
                            title,
                            color = if (selectedTab == index) Color(0xFF44D62C) else Color.Gray,
                            fontSize = 13.sp
                        )
                    }
                )
            }
        }

        // Content
        when (selectedTab) {
            0 -> HeartRateTab(dao)
            1 -> SpO2Tab(dao)
            2 -> AccelTab(dao)
            3 -> StatsTab(dao)
        }
    }
}

@Composable
private fun HeartRateTab(dao: SensorRecordDao) {
    var records by remember { mutableStateOf<List<SensorRecord>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        records = withContext(Dispatchers.IO) { dao.getHrRecords(5000) }
        loading = false
    }

    if (loading) {
        LoadingView()
        return
    }
    if (records.isEmpty()) {
        EmptyView("No heart rate data")
        return
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
    ) {
        val sampled = downsample(records, 500)
        val hrValues = sampled.map { it.heartRate.toFloat() }
        val timestamps = sampled.map { it.timestamp }
        val avg = hrValues.average()
        val min = hrValues.min().toInt()
        val max = hrValues.max().toInt()

        Text("Heart Rate", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(4.dp))
        Text(
            "Avg: ${avg.toInt()} BPM  |  Min: $min  |  Max: $max  |  ${records.size} records",
            color = Color.Gray, fontSize = 12.sp
        )
        TimeRangeLabel(timestamps)
        Spacer(Modifier.height(12.dp))

        LineChart(
            values = hrValues,
            color = Color(0xFFFF4444),
            label = "BPM",
            minY = (min - 10).coerceAtLeast(30).toFloat(),
            maxY = (max + 10).coerceAtMost(220).toFloat()
        )

        Spacer(Modifier.height(24.dp))

        // Show last 20 readings
        Text("Recent Readings", color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        records.takeLast(20).reversed().forEach { r ->
            Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                Text(formatTimestamp(r.timestamp), color = Color.Gray, fontSize = 11.sp, modifier = Modifier.weight(1f))
                Text("${r.heartRate} BPM", color = Color(0xFFFF4444), fontSize = 11.sp)
            }
        }
    }
}

@Composable
private fun SpO2Tab(dao: SensorRecordDao) {
    var records by remember { mutableStateOf<List<SensorRecord>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        records = withContext(Dispatchers.IO) { dao.getSpo2Records(5000) }
        loading = false
    }

    if (loading) { LoadingView(); return }
    if (records.isEmpty()) { EmptyView("No SpO2 data"); return }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
    ) {
        val sampled = downsample(records, 500)
        val values = sampled.map { it.spo2Percent.toFloat() }
        val timestamps = sampled.map { it.timestamp }
        val avg = values.average()

        Text("Blood Oxygen (SpO2)", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(4.dp))
        Text(
            "Avg: ${"%.1f".format(avg)}%  |  ${records.size} points",
            color = Color.Gray, fontSize = 12.sp
        )
        TimeRangeLabel(timestamps)
        Spacer(Modifier.height(12.dp))

        LineChart(
            values = values,
            color = Color(0xFF44AAFF),
            label = "%",
            minY = 85f,
            maxY = 100f
        )
    }
}

@Composable
private fun AccelTab(dao: SensorRecordDao) {
    var records by remember { mutableStateOf<List<SensorRecord>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        records = withContext(Dispatchers.IO) { dao.getRecordsForChart(5000) }
        loading = false
    }

    if (loading) { LoadingView(); return }
    if (records.isEmpty()) { EmptyView("No accelerometer data"); return }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
    ) {
        val sampled = downsample(records, 500)
        val timestamps = sampled.map { it.timestamp }

        Text("Accelerometer", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
        TimeRangeLabel(timestamps)
        Spacer(Modifier.height(12.dp))

        Text("X-Axis", color = Color(0xFFFF6666), fontSize = 12.sp)
        LineChart(
            values = sampled.map { it.accelX },
            color = Color(0xFFFF6666),
            label = "g",
            minY = -3f, maxY = 3f
        )

        Spacer(Modifier.height(16.dp))
        Text("Y-Axis", color = Color(0xFF66FF66), fontSize = 12.sp)
        LineChart(
            values = sampled.map { it.accelY },
            color = Color(0xFF66FF66),
            label = "g",
            minY = -3f, maxY = 3f
        )

        Spacer(Modifier.height(16.dp))
        Text("Z-Axis", color = Color(0xFF6666FF), fontSize = 12.sp)
        LineChart(
            values = sampled.map { it.accelZ },
            color = Color(0xFF6666FF),
            label = "g",
            minY = -3f, maxY = 3f
        )

        Spacer(Modifier.height(16.dp))
        Text("Gyroscope", color = Color(0xFFFFAA44), fontSize = 12.sp)
        LineChart(
            values = sampled.map { it.gyro },
            color = Color(0xFFFFAA44),
            label = "°/s",
            minY = null, maxY = null
        )
    }
}

@Composable
private fun StatsTab(dao: SensorRecordDao) {
    var totalRecords by remember { mutableIntStateOf(0) }
    var minTs by remember { mutableStateOf<Long?>(null) }
    var maxTs by remember { mutableStateOf<Long?>(null) }
    var avgHr by remember { mutableStateOf<Double?>(null) }
    var minHr by remember { mutableStateOf<Int?>(null) }
    var maxHr by remember { mutableStateOf<Int?>(null) }
    var avgSpo2 by remember { mutableStateOf<Double?>(null) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) {
            totalRecords = dao.count()
            minTs = dao.getMinTimestamp()
            maxTs = dao.getMaxTimestamp()
            avgHr = dao.getAvgHeartRate()
            minHr = dao.getMinHeartRate()
            maxHr = dao.getMaxHeartRate()
            avgSpo2 = dao.getAvgSpo2()
        }
        loading = false
    }

    if (loading) { LoadingView(); return }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
    ) {
        Text("DATABASE STATISTICS", color = Color(0xFF44D62C), fontSize = 18.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(16.dp))

        StatRow("Total Records", "$totalRecords")
        StatRow("Data Range", if (minTs != null && maxTs != null) {
            "${formatTimestamp(minTs!!)} → ${formatTimestamp(maxTs!!)}"
        } else "No data")

        val durationHours = if (minTs != null && maxTs != null) {
            ((maxTs!! - minTs!!) / 3600.0)
        } else 0.0
        StatRow("Duration", if (durationHours > 0) "${"%.1f".format(durationHours)} hours" else "--")

        Spacer(Modifier.height(16.dp))
        Text("HEART RATE", color = Color(0xFFFF4444), fontSize = 14.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        StatRow("Average HR", avgHr?.let { "${it.toInt()} BPM" } ?: "--")
        StatRow("Min HR", minHr?.let { "$it BPM" } ?: "--")
        StatRow("Max HR", maxHr?.let { "$it BPM" } ?: "--")

        Spacer(Modifier.height(16.dp))
        Text("BLOOD OXYGEN", color = Color(0xFF44AAFF), fontSize = 14.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        StatRow("Average SpO2", avgSpo2?.let { "${"%.1f".format(it)}%" } ?: "--")

        Spacer(Modifier.height(24.dp))
        Text(
            "Data is collected from Whoop 5.0 (Maverick) via BLE.\n" +
                "Records contain: HR, RR intervals, SpO2, accelerometer, gyroscope.",
            color = Color.Gray, fontSize = 11.sp
        )
    }
}

// --- Chart Component ---

@Composable
private fun LineChart(
    values: List<Float>,
    color: Color,
    label: String,
    minY: Float?,
    maxY: Float?,
    modifier: Modifier = Modifier
) {
    if (values.isEmpty()) return

    val actualMin = minY ?: values.min()
    val actualMax = maxY ?: values.max()
    val range = (actualMax - actualMin).coerceAtLeast(1f)

    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF1A1A1A)),
        shape = RoundedCornerShape(12.dp)
    ) {
        Column(Modifier.padding(12.dp)) {
            // Y-axis labels
            Row(Modifier.fillMaxWidth()) {
                Text("${"%.0f".format(actualMax)} $label", color = Color.Gray, fontSize = 9.sp)
                Spacer(Modifier.weight(1f))
                Text("${"%.0f".format(actualMin)} $label", color = Color.Gray, fontSize = 9.sp)
            }

            Spacer(Modifier.height(4.dp))

            Canvas(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(150.dp)
            ) {
                val w = size.width
                val h = size.height
                val n = values.size
                if (n < 2) return@Canvas

                // Grid lines
                for (i in 0..4) {
                    val y = h * i / 4f
                    drawLine(
                        color = Color(0xFF333333),
                        start = Offset(0f, y),
                        end = Offset(w, y),
                        strokeWidth = 1f
                    )
                }

                // Line path
                val path = Path()
                val step = w / (n - 1).toFloat()

                for (i in values.indices) {
                    val x = i * step
                    val normalized = ((values[i] - actualMin) / range).coerceIn(0f, 1f)
                    val y = h - (normalized * h)

                    if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
                }

                drawPath(path, color, style = Stroke(width = 2f))
            }
        }
    }
}

// --- Helper Composables ---

@Composable
private fun StatRow(label: String, value: String) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
    ) {
        Text(label, color = Color.Gray, fontSize = 13.sp, modifier = Modifier.weight(1f))
        Text(value, color = Color.White, fontSize = 13.sp)
    }
}

@Composable
private fun TimeRangeLabel(timestamps: List<Long>) {
    if (timestamps.isEmpty()) return
    val first = timestamps.first()
    val last = timestamps.last()
    Text(
        "${formatTimestamp(first)} → ${formatTimestamp(last)}",
        color = Color.Gray, fontSize = 11.sp
    )
}

@Composable
private fun LoadingView() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = Color(0xFF44D62C))
    }
}

@Composable
private fun EmptyView(msg: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(msg, color = Color.Gray, fontSize = 14.sp)
    }
}

private fun formatTimestamp(ts: Long): String {
    return try {
        val sdf = SimpleDateFormat("MM/dd HH:mm", Locale.US)
        sdf.format(Date(ts * 1000))
    } catch (_: Exception) {
        ts.toString()
    }
}

/** Downsample a list to at most [maxPoints] entries using LTTB-like min/max selection */
private fun <T> downsample(data: List<T>, maxPoints: Int): List<T> {
    if (data.size <= maxPoints) return data
    val step = data.size.toFloat() / maxPoints
    return (0 until maxPoints).map { i ->
        data[(i * step).toInt().coerceAtMost(data.size - 1)]
    }
}
