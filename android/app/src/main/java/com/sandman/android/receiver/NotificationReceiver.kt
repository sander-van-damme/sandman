package com.sandman.android.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.RemoteInput
import androidx.core.content.ContextCompat
import com.sandman.android.notifications.ACTION_REPLY
import com.sandman.android.notifications.REMOTE_INPUT_KEY
import com.sandman.android.service.NudgeService

private const val TAG = "NotificationReceiver"

/**
 * Receives taps on the nudge notification reply action and forwards them
 * to [NudgeService].
 */
class NotificationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_REPLY) return

        val results = RemoteInput.getResultsFromIntent(intent)
        val replyText = results?.getCharSequence(REMOTE_INPUT_KEY)?.toString()
        if (replyText.isNullOrBlank()) {
            Log.d(TAG, "Reply action received but text was empty")
            return
        }

        Log.d(TAG, "Reply received: $replyText")
        val svcIntent = Intent(context, NudgeService::class.java).apply {
            action = NudgeService.ACTION_USER_REPLY
            putExtra(NudgeService.EXTRA_REPLY_TEXT, replyText)
        }
        ContextCompat.startForegroundService(context, svcIntent)
    }
}
