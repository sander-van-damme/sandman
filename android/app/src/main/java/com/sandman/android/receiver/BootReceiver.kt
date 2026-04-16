package com.sandman.android.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.ContextCompat
import com.sandman.android.service.NudgeService

private const val TAG = "BootReceiver"

/**
 * Starts [NudgeService] after the device boots (or after the app is updated),
 * but only if the user had previously enabled the service.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED &&
            intent.action != Intent.ACTION_MY_PACKAGE_REPLACED
        ) return

        Log.i(TAG, "Boot/update received — starting NudgeService")
        val serviceIntent = Intent(context, NudgeService::class.java).apply {
            action = NudgeService.ACTION_START
        }
        ContextCompat.startForegroundService(context, serviceIntent)
    }
}
