package com.whoopcapture.db

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(tableName = "sensor_records", indices = [Index(value = ["timestamp"], unique = true)])
data class SensorRecord(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val capturedAt: Long = System.currentTimeMillis(),
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
    val spo2Percent: Int,
    val byte16: Int,
    val byte17: Int,
    val byte66: Int,
    val byte68: Int,
    val byte105: Int,
    val byte106: Int,
    val rawHex: String,
)
