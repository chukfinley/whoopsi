package com.whoopcapture.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface SensorRecordDao {
    @Insert
    suspend fun insert(record: SensorRecord)

    @Insert(onConflict = androidx.room.OnConflictStrategy.IGNORE)
    suspend fun insertAll(records: List<SensorRecord>): List<Long>

    @Query("SELECT MAX(timestamp) FROM sensor_records")
    suspend fun getMaxTimestamp(): Long?

    @Query("SELECT MAX(timestamp) FROM sensor_records WHERE timestamp > 0 AND timestamp < :maxValid")
    suspend fun getMaxValidTimestamp(maxValid: Long): Long?

    @Query("SELECT MIN(timestamp) FROM sensor_records WHERE timestamp > 0")
    suspend fun getMinTimestamp(): Long?

    @Query("SELECT COUNT(*) FROM sensor_records")
    fun countFlow(): Flow<Int>

    @Query("SELECT COUNT(*) FROM sensor_records")
    suspend fun count(): Int

    @Query("SELECT * FROM sensor_records ORDER BY id DESC LIMIT 1")
    fun latestFlow(): Flow<SensorRecord?>

    @Query("SELECT * FROM sensor_records ORDER BY id ASC")
    suspend fun getAll(): List<SensorRecord>

    @Query("SELECT * FROM sensor_records ORDER BY id ASC LIMIT :limit OFFSET :offset")
    suspend fun getPage(limit: Int, offset: Int): List<SensorRecord>

    @Query("SELECT * FROM sensor_records WHERE heartRate > 0 ORDER BY timestamp ASC LIMIT :limit")
    suspend fun getHrRecords(limit: Int = 2000): List<SensorRecord>

    @Query("SELECT * FROM sensor_records WHERE spo2Percent > 10 AND spo2Percent <= 100 ORDER BY timestamp ASC LIMIT :limit")
    suspend fun getSpo2Records(limit: Int = 2000): List<SensorRecord>

    @Query("SELECT * FROM sensor_records ORDER BY timestamp ASC LIMIT :limit")
    suspend fun getRecordsForChart(limit: Int = 2000): List<SensorRecord>

    @Query("SELECT AVG(CAST(heartRate AS REAL)) FROM sensor_records WHERE heartRate > 0")
    suspend fun getAvgHeartRate(): Double?

    @Query("SELECT MIN(heartRate) FROM sensor_records WHERE heartRate > 0")
    suspend fun getMinHeartRate(): Int?

    @Query("SELECT MAX(heartRate) FROM sensor_records WHERE heartRate > 0")
    suspend fun getMaxHeartRate(): Int?

    @Query("SELECT AVG(CAST(spo2Percent AS REAL)) FROM sensor_records WHERE spo2Percent > 10 AND spo2Percent <= 100")
    suspend fun getAvgSpo2(): Double?

    @Query("SELECT COUNT(*) FROM sensor_records WHERE timestamp >= :startOfDay")
    fun countTodayFlow(startOfDay: Long): Flow<Int>

    @Query("SELECT COUNT(*) FROM sensor_records WHERE timestamp >= :since AND timestamp > 0 AND timestamp < :maxValid")
    suspend fun countValidSince(since: Long, maxValid: Long): Int

    @Query("SELECT COUNT(DISTINCT timestamp / 86400) FROM sensor_records WHERE timestamp > 0")
    suspend fun countDaysWithData(): Int

    @Query("DELETE FROM sensor_records")
    suspend fun deleteAll()
}
