package com.whoopcapture

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Ensures AutoSyncWorker and SiphonWorker are re-scheduled after device reboot.
 * WorkManager normally persists jobs across reboots, but this is a safety net.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            Log.i("BootReceiver", "Device booted — scheduling Whoop sync workers")
            AutoSyncWorker.schedule(context)
            SiphonWorker.schedule(context)
        }
    }
}
