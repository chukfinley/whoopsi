package com.whoopcapture

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

class WhoopCaptureApp : Application() {
    override fun onCreate() {
        super.onCreate()
        val channel = NotificationChannel(
            "whoop_capture",
            "Whoop Capture",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Background BLE data capture"
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)

        // Schedule periodic DB siphon (every 6h = 4x/day, root only)
        SiphonWorker.schedule(this)
        // Schedule automatic BLE sync every 6 hours (no root needed)
        AutoSyncWorker.schedule(this)
    }
}
