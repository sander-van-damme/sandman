package com.sandman.android.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.RemoteInput
import androidx.core.content.ContextCompat
import com.sandman.android.notifications.ACTION_BED
import com.sandman.android.notifications.ACTION_REPLY
import com.sandman.android.notifications.ACTION_SNOOZE
import com.sandman.android.notifications.REMOTE_INPUT_KEY
import com.sandman.android.service.NudgeService

private const val TAG = "NotificationReceiver"

/**
 * Receives taps on the nudge notification's action buttons and forwards them
 * to [NudgeService] as intents.
 */
class NotificationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            ACTION_REPLY -> {
                val results = RemoteInput.getResultsFromIntent(intent)
                val replyText = results?.getCharSequence(REMOTE_INPUT_KEY)?.toString()
                if (replyText.isNullOrBlank()) {
                    Log.d(TAG, "Reply action received but text was empty — opening chat")
                    // Fall through to open the app if no inline text was provided
                } else {
                    Log.d(TAG, "Reply received: $replyText")
                    val svcIntent = Intent(context, NudgeService::class.java).apply {
                        action = NudgeService.ACTION_USER_REPLY
                        putExtra(NudgeService.EXTRA_REPLY_TEXT, replyText)
                    }
                    ContextCompat.startForegroundService(context, svcIntent)
                    return
                }
            }

            ACTION_BED -> {
                Log.d(TAG, "Going to bed action received")
                val svcIntent = Intent(context, NudgeService::class.java).apply {
                    action = NudgeService.ACTION_PAUSE_UNTIL_TOMORROW
                }
                ContextCompat.startForegroundService(context, svcIntent)
                return
            }

            ACTION_SNOOZE -> {
                Log.d(TAG, "Snooze 5 min action received")
                val svcIntent = Intent(context, NudgeService::class.java).apply {
                    action = NudgeService.ACTION_PAUSE_FOR_MINUTES
                    putExtra(NudgeService.EXTRA_PAUSE_MINUTES, 5)
                }
                ContextCompat.startForegroundService(context, svcIntent)
                return
            }
        }
    }
}
