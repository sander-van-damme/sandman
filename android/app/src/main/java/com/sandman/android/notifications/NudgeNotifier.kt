package com.sandman.android.notifications

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.RemoteInput
import com.sandman.android.MainActivity
import com.sandman.android.model.MonitorState
import com.sandman.android.receiver.NotificationReceiver

const val CHANNEL_NUDGE = "sandman_nudge"
const val CHANNEL_STATUS = "sandman_status"
const val STATUS_NOTIF_ID = 1
const val NUDGE_NOTIF_ID = 2

const val REMOTE_INPUT_KEY = "reply_text"
const val ACTION_REPLY = "sandman.ACTION_REPLY"

object NudgeNotifier {

    fun createChannels(context: Context) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_NUDGE,
                "Bedtime Nudges",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Sandman nudges to help you get to bed"
                enableLights(true)
                enableVibration(true)
            },
        )

        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_STATUS,
                "Sandman Status",
                NotificationManager.IMPORTANCE_MIN,
            ).apply {
                description = "Persistent indicator that Sandman is running"
            },
        )
    }

    fun buildStatusNotification(
        context: Context,
        state: MonitorState = MonitorState.IDLE,
        message: String = "Starting…",
    ): Notification {
        val openIntent = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val stateLabel = when (state) {
            MonitorState.IDLE -> "Idle"
            MonitorState.ACTIVE -> "Watching"
            MonitorState.NUDGING -> "Nudging"
            MonitorState.ERROR -> "Error"
        }

        return NotificationCompat.Builder(context, CHANNEL_STATUS)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle("Sandman — $stateLabel")
            .setContentText(message)
            .setOngoing(true)
            .setContentIntent(openIntent)
            .setSilent(true)
            .build()
    }

    fun updateStatus(context: Context, state: MonitorState, message: String) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(STATUS_NOTIF_ID, buildStatusNotification(context, state, message))
    }

    /**
     * Post a nudge notification with only an inline Reply action.
     * When nudgeCount >= 7 and escalation is enabled, adds a fullScreenIntent.
     */
    fun showNudge(
        context: Context,
        message: String,
        nudgeCount: Int,
        escalationEnabled: Boolean,
    ) {
        val remoteInput = RemoteInput.Builder(REMOTE_INPUT_KEY)
            .setLabel("Reply to Sandman…")
            .build()

        val replyIntent = PendingIntent.getBroadcast(
            context, 10,
            Intent(context, NotificationReceiver::class.java).setAction(ACTION_REPLY),
            PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val replyAction = NotificationCompat.Action.Builder(
            android.R.drawable.ic_menu_send,
            "Reply",
            replyIntent,
        ).addRemoteInput(remoteInput).build()

        val openIntent = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val builder = NotificationCompat.Builder(context, CHANNEL_NUDGE)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle("Sandman")
            .setContentText(message)
            .setStyle(NotificationCompat.BigTextStyle().bigText(message))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(false)
            .setContentIntent(openIntent)
            .addAction(replyAction)

        if (escalationEnabled && nudgeCount >= 7) {
            val fsIntent = PendingIntent.getActivity(
                context, 1,
                Intent(context, MainActivity::class.java).apply {
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                    putExtra("escalation", true)
                },
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            builder.setFullScreenIntent(fsIntent, true)
        }

        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NUDGE_NOTIF_ID, builder.build())
    }
}
