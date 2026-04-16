package com.sandman.android

import android.app.Application
import com.sandman.android.notifications.NudgeNotifier

class SandmanApp : Application() {
    override fun onCreate() {
        super.onCreate()
        NudgeNotifier.createChannels(this)
    }
}
