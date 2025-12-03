package com.whoopcapture

import android.content.Context
import android.os.Environment
import com.whoopcapture.db.AppDatabase
import java.io.File
import java.text.SimpleDateFormat
import java.util.*

object CsvExporter {

    private const val BATCH_SIZE = 5000

    suspend fun export(context: Context): File {
        val dao = AppDatabase.get(context).sensorRecordDao()
        val total = dao.count()
        val dir = context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS)
            ?: context.filesDir
        val sdf = SimpleDateFormat("yyyy-MM-dd_HH-mm", Locale.US)
        val file = File(dir, "whoop_${sdf.format(Date())}.csv")

        file.bufferedWriter().use { w ->
            w.write("id,captured_at,timestamp,heart_rate,rr_count,rr1_ms,rr2_ms,rr3_ms,")
            w.write("accel_x,accel_y,accel_z,gyro,spo2_percent,")
            w.write("byte16,byte17,byte66,byte68,byte105,byte106")
            w.newLine()

            var offset = 0
            while (offset < total) {
                val batch = dao.getPage(BATCH_SIZE, offset)
                for (r in batch) {
                    w.write("${r.id},${r.capturedAt},${r.timestamp},${r.heartRate},${r.rrCount},")
                    w.write("${r.rr1Ms},${r.rr2Ms},${r.rr3Ms},")
                    w.write("${r.accelX},${r.accelY},${r.accelZ},${r.gyro},${r.spo2Percent},")
                    w.write("${r.byte16},${r.byte17},${r.byte66},${r.byte68},${r.byte105},${r.byte106}")
                    w.newLine()
                }
                offset += batch.size
                if (batch.size < BATCH_SIZE) break
            }
        }

        return file
    }
}
