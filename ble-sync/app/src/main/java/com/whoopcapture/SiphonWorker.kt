package com.whoopcapture

import android.content.Context
import android.util.Log
import androidx.work.*
import java.util.concurrent.TimeUnit

class SiphonWorker(
    context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    companion object {
        private const val TAG = "SiphonWorker"
        private const val WORK_NAME = "whoop_siphon_periodic"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<SiphonWorker>(
                15, TimeUnit.MINUTES  // every 15 min = catch data before upload
            )
                .setConstraints(
                    Constraints.Builder()
                        .setRequiresBatteryNotLow(true)
                        .build()
                )
                .build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request
            )
            Log.i(TAG, "Scheduled periodic siphon every 15min")
        }
    }

    override suspend fun doWork(): Result {
        Log.i(TAG, "Starting siphon...")
        val result = WhoopDbSiphon.siphon(applicationContext)
        Log.i(TAG, "Siphon done: read=${result.totalRead}, new=${result.newRecords}, status=${result.status}")

        return if (result.status == "OK") Result.success() else Result.retry()
    }
}
