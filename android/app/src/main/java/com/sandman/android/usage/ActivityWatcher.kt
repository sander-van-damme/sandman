package com.sandman.android.usage

import android.app.AppOpsManager
import android.app.KeyguardManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.PowerManager
import android.telephony.TelephonyManager
import android.util.Log
import com.sandman.android.model.ForegroundApp

private const val TAG = "ActivityWatcher"

object ActivityWatcher {

    /**
     * Returns true if the app has been granted the PACKAGE_USAGE_STATS permission.
     * This permission cannot be requested at runtime — the user must go to
     * Settings → Apps → Special app access → Usage access.
     */
    fun isPermissionGranted(context: Context): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = appOps.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS,
            android.os.Process.myUid(),
            context.packageName,
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    /**
     * Returns the app currently (or most recently) in the foreground by
     * querying UsageEvents over the last 10 seconds.
     *
     * Returns null if the permission is not granted or no event was found.
     */
    fun getForegroundApp(context: Context): ForegroundApp? {
        if (!isPermissionGranted(context)) {
            Log.w(TAG, "PACKAGE_USAGE_STATS permission not granted")
            return null
        }

        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val now = System.currentTimeMillis()
        val events = usm.queryEvents(now - 2 * 60 * 60 * 1000L, now)

        var latestPackage: String? = null
        val event = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            if (event.eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
                latestPackage = event.packageName
            }
        }

        if (latestPackage == null) return null

        val label = try {
            val pm = context.packageManager
            val info = pm.getApplicationInfo(latestPackage, 0)
            pm.getApplicationLabel(info).toString()
        } catch (e: PackageManager.NameNotFoundException) {
            latestPackage
        }

        return ForegroundApp(packageName = latestPackage, appLabel = label)
    }

    /**
     * Returns true if the screen is currently on and interactive.
     * Used as the Android equivalent of AFK detection.
     */
    fun isScreenOn(context: Context): Boolean {
        val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isInteractive
    }

    /** Returns true if the device is currently locked. */
    fun isDeviceLocked(context: Context): Boolean {
        val keyguard = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        return keyguard.isKeyguardLocked
    }

    /** Returns true if a phone call (incoming, outgoing, or ongoing) is active. */
    fun isInCall(context: Context): Boolean {
        val tm = context.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
        return tm.callState != TelephonyManager.CALL_STATE_IDLE
    }
}
