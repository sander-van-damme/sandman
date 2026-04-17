package com.sandman.android.service

import android.app.Service
import android.content.Intent
import android.os.BatteryManager
import android.os.IBinder
import android.util.Log
import androidx.core.content.ContextCompat
import com.sandman.android.data.AppPreferences
import com.sandman.android.data.Defaults
import com.sandman.android.data.isWithinActiveWindow
import com.sandman.android.data.minutesPastBedtime
import com.sandman.android.llm.LlmClient
import com.sandman.android.model.ConversationHistory
import com.sandman.android.model.MonitorState
import com.sandman.android.notifications.NudgeNotifier
import com.sandman.android.notifications.STATUS_NOTIF_ID
import com.sandman.android.usage.ActivityWatcher
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.format.DateTimeFormatter

private const val TAG = "NudgeService"
private const val POLL_INTERVAL_MS = 30_000L
private const val POLL_INTERVAL_SCREEN_OFF_MS = 60_000L
private const val NO_NUDGE_CACHE_TTL_MS = 5 * 60 * 1_000L
private const val BATTERY_CRITICAL_PERCENT = 10

/**
 * Foreground service that owns the 30-second polling loop — the Android
 * equivalent of monitor.py.  It intentionally mirrors the Python Monitor._tick()
 * logic step-for-step.
 */
class NudgeService : Service() {

    companion object {
        const val ACTION_START = "sandman.START"
        const val ACTION_STOP = "sandman.STOP"
        const val ACTION_PAUSE_FOR_MINUTES = "sandman.PAUSE_FOR_MINUTES"
        const val ACTION_PAUSE_UNTIL_TOMORROW = "sandman.PAUSE_UNTIL_TOMORROW"
        const val ACTION_RESUME = "sandman.RESUME"
        const val ACTION_USER_REPLY = "sandman.USER_REPLY"

        const val EXTRA_PAUSE_MINUTES = "pause_minutes"
        const val EXTRA_REPLY_TEXT = "reply_text"
    }

    private val scope = CoroutineScope(Dispatchers.Default + SupervisorJob())
    private lateinit var prefs: AppPreferences

    // Preference cache — started eagerly, read as .value in tick() to avoid
    // per-tick DataStore suspensions (~8 reads × 2 880 ticks/day eliminated)
    private lateinit var cachedApiKey: StateFlow<String>
    private lateinit var cachedModel: StateFlow<String>
    private lateinit var cachedActiveFrom: StateFlow<String>
    private lateinit var cachedActiveUntil: StateFlow<String>
    private lateinit var cachedActiveDays: StateFlow<String>
    private lateinit var cachedWakeTime: StateFlow<String>
    private lateinit var cachedMinInterval: StateFlow<Int>
    private lateinit var cachedEscalation: StateFlow<Boolean>
    private lateinit var cachedNudgeStyle: StateFlow<String>

    // Per-activity no-nudge cache: avoids redundant LLM calls for the same app
    private val noNudgeCache = mutableMapOf<String, Long>()

    // Monitor state (mirrors Python Monitor fields)
    private var nudgeCount = 0
    private var lastNudgeAtMs: Long? = null
    private var lastNudgeActivityKey: String? = null
    private var sessionDateKey: String? = null
    private var pausedUntilMs: Long? = null

    private val history = ConversationHistory()
    private var llmClient: LlmClient? = null

    private val timeFmt = DateTimeFormatter.ofPattern("HH:mm")

    // ---- lifecycle -------------------------------------------------------

    override fun onCreate() {
        super.onCreate()
        prefs = AppPreferences(applicationContext)
        cachedApiKey = prefs.apiKey.stateIn(scope, SharingStarted.Eagerly, "")
        cachedModel = prefs.model.stateIn(scope, SharingStarted.Eagerly, Defaults.MODEL)
        cachedActiveFrom = prefs.activeFrom.stateIn(scope, SharingStarted.Eagerly, Defaults.ACTIVE_FROM)
        cachedActiveUntil = prefs.activeUntil.stateIn(scope, SharingStarted.Eagerly, Defaults.ACTIVE_UNTIL)
        cachedActiveDays = prefs.activeDays.stateIn(scope, SharingStarted.Eagerly, Defaults.ACTIVE_DAYS)
        cachedWakeTime = prefs.wakeTime.stateIn(scope, SharingStarted.Eagerly, Defaults.WAKE_TIME)
        cachedMinInterval = prefs.minIntervalSeconds.stateIn(scope, SharingStarted.Eagerly, Defaults.MIN_INTERVAL_SECONDS)
        cachedEscalation = prefs.escalationEnabled.stateIn(scope, SharingStarted.Eagerly, Defaults.ESCALATION_ENABLED)
        cachedNudgeStyle = prefs.nudgeStyle.stateIn(scope, SharingStarted.Eagerly, Defaults.NUDGE_STYLE)
        NudgeNotifier.createChannels(applicationContext)
        startForeground(
            STATUS_NOTIF_ID,
            NudgeNotifier.buildStatusNotification(applicationContext, MonitorState.IDLE, "Starting…"),
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                Log.i(TAG, "Stop requested")
                scope.launch { prefs.setServiceEnabled(false) }
                stopSelf()
                return START_NOT_STICKY
            }

            ACTION_PAUSE_FOR_MINUTES -> {
                val mins = intent.getIntExtra(EXTRA_PAUSE_MINUTES, 5)
                pausedUntilMs = System.currentTimeMillis() + mins * 60_000L
                Log.i(TAG, "Paused for $mins minutes")
                NudgeNotifier.updateStatus(
                    applicationContext, MonitorState.IDLE, "Paused for $mins min"
                )
            }

            ACTION_PAUSE_UNTIL_TOMORROW -> {
                // Pause until midnight + active_from time
                val now = LocalDateTime.now()
                val tomorrow = now.toLocalDate().plusDays(1).atStartOfDay()
                pausedUntilMs = java.time.ZoneId.systemDefault()
                    .let { zoneId ->
                        tomorrow.atZone(zoneId).toInstant().toEpochMilli()
                    }
                Log.i(TAG, "Paused until tomorrow")
                NudgeNotifier.updateStatus(
                    applicationContext, MonitorState.IDLE, "Paused until tomorrow"
                )
            }

            ACTION_RESUME -> {
                pausedUntilMs = null
                NudgeNotifier.updateStatus(applicationContext, MonitorState.IDLE, "Resumed")
            }

            ACTION_USER_REPLY -> {
                val text = intent.getStringExtra(EXTRA_REPLY_TEXT) ?: return START_STICKY
                scope.launch { handleUserReply(text) }
            }

            else -> {
                // ACTION_START or null — ensure the polling loop is running
                Log.i(TAG, "Starting monitor loop")
                scope.launch { prefs.setServiceEnabled(true) }
                startMonitorLoop()
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ---- monitor loop ----------------------------------------------------

    private var monitorJob: Job? = null

    private fun startMonitorLoop() {
        if (monitorJob?.isActive == true) return
        monitorJob = scope.launch {
            while (isActive) {
                try {
                    tick()
                } catch (e: Exception) {
                    Log.e(TAG, "Tick failed", e)
                    emitStatus(MonitorState.ERROR, "Internal error")
                }
                delay(nextPollDelayMs())
            }
        }
    }

    /**
     * One polling iteration — mirrors Python Monitor._tick() step by step.
     */
    private suspend fun tick() {
        val now = LocalDateTime.now()

        // 1) Configuration check
        val apiKey = cachedApiKey.value
        if (apiKey.isBlank()) {
            emitStatus(MonitorState.ERROR, "OpenAI API key not set")
            return
        }
        ensureLlmClient(apiKey)

        // 2) Usage-stats permission check
        if (!ActivityWatcher.isPermissionGranted(applicationContext)) {
            emitStatus(MonitorState.ERROR, "Usage access permission required")
            return
        }

        // 3) Pause check
        val pUntil = pausedUntilMs
        if (pUntil != null) {
            if (System.currentTimeMillis() < pUntil) {
                emitStatus(MonitorState.IDLE, "Paused")
                return
            } else {
                pausedUntilMs = null
            }
        }

        // 3b) Battery critical — skip LLM calls to preserve remaining charge
        if (isBatteryCritical()) {
            emitStatus(MonitorState.IDLE, "Battery critical — paused")
            return
        }

        // 4) Active window check (mirrors is_within_active_window)
        val activeFrom = cachedActiveFrom.value
        val activeUntil = cachedActiveUntil.value
        val activeDays = cachedActiveDays.value
        val nowDayOfWeek = (now.dayOfWeek.value - 1) // 0=Mon…6=Sun, matching Python
        if (!isWithinActiveWindow(now.toLocalTime(), nowDayOfWeek, activeFrom, activeUntil, activeDays)) {
            maybeEndSession()
            emitStatus(MonitorState.IDLE, "Outside active hours")
            return
        }

        // 5) Start new session if needed
        maybeStartSession(now, activeFrom)

        // 6) Screen-off check (replaces AFK detection)
        if (!ActivityWatcher.isScreenOn(applicationContext)) {
            emitStatus(MonitorState.ACTIVE, "Screen off — holding off")
            return
        }
        if (ActivityWatcher.isDeviceLocked(applicationContext)) {
            emitStatus(MonitorState.ACTIVE, "Device locked — holding off")
            return
        }
        if (ActivityWatcher.isInCall(applicationContext)) {
            emitStatus(MonitorState.ACTIVE, "On a call — holding off")
            return
        }

        // 7) Rate limit
        val minIntervalMs = cachedMinInterval.value * 1_000L
        val lastNudge = lastNudgeAtMs
        if (lastNudge != null && System.currentTimeMillis() - lastNudge < minIntervalMs) {
            emitStatus(MonitorState.NUDGING, "Rate limited")
            return
        }

        // 8) Fetch foreground app
        val app = ActivityWatcher.getForegroundApp(applicationContext)
            ?: run {
                emitStatus(MonitorState.ACTIVE, "No app data yet")
                return
            }

        if (app.packageName == applicationContext.packageName) {
            emitStatus(MonitorState.ACTIVE, "Sandman is open")
            return
        }

        val activityKey = "${app.packageName}:${app.appLabel}"

        // 9) Same-activity dedup (3× min interval)
        if (lastNudgeActivityKey == activityKey && lastNudge != null) {
            if (System.currentTimeMillis() - lastNudge < 3 * minIntervalMs) {
                emitStatus(MonitorState.ACTIVE, "Same activity, waiting longer")
                return
            }
        }

        // 9b) No-nudge activity cache — skip LLM if we recently decided not to nudge this app
        if ((noNudgeCache[activityKey] ?: 0L) > System.currentTimeMillis() - NO_NUDGE_CACHE_TTL_MS) {
            emitStatus(MonitorState.ACTIVE, "Watching (${app.appLabel})")
            return
        }

        // 10) Ask the LLM
        val wakeTime = cachedWakeTime.value
        val nudgeStyle = cachedNudgeStyle.value
        val minutesPast = minutesPastBedtime(now.hour, now.minute, activeFrom)

        val systemPrompt = LlmClient.buildSystemPrompt()
        val turnContext = LlmClient.buildTurnContextMessage(
            now = now,
            bedtime = activeFrom,
            wakeTime = wakeTime,
            minutesPast = minutesPast,
            appName = app.appLabel,
            windowTitle = app.packageName,
            nudgeCount = nudgeCount,
            nudgeStyle = nudgeStyle,
        )

        val decision = llmClient!!.classifyAndNudge(
            systemPrompt = systemPrompt,
            history = history,
            userMessage = turnContext,
            nudgeCount = nudgeCount,
        )

        if (!decision.shouldNudge || decision.message.isBlank()) {
            noNudgeCache[activityKey] = System.currentTimeMillis()
            Log.d(TAG, "LLM declined to nudge: ${decision.reason}")
            emitStatus(MonitorState.ACTIVE, "Watching (${decision.activityType})")
            return
        }

        // 11) Fire the nudge
        nudgeCount++
        lastNudgeAtMs = System.currentTimeMillis()
        lastNudgeActivityKey = activityKey
        history.add("assistant", decision.message)
        prefs.incrementTotalNudges()

        emitStatus(MonitorState.NUDGING, "Nudge #$nudgeCount")

        val escalation = cachedEscalation.value
        NudgeNotifier.showNudge(applicationContext, decision.message, nudgeCount, escalation)

    }

    // ---- reply handling --------------------------------------------------

    private suspend fun handleUserReply(text: String) {
        val apiKey = cachedApiKey.value
        if (apiKey.isBlank()) return
        ensureLlmClient(apiKey)

        val app = ActivityWatcher.getForegroundApp(applicationContext)
        val appName = app?.appLabel ?: "unknown"
        val now = LocalDateTime.now()
        val activeFrom = cachedActiveFrom.value
        val wakeTime = cachedWakeTime.value
        val nudgeStyle = cachedNudgeStyle.value
        val minutesPast = minutesPastBedtime(now.hour, now.minute, activeFrom)

        val systemPrompt = LlmClient.buildSystemPrompt()
        val turnContext = LlmClient.buildTurnContextMessage(
            now = now,
            bedtime = activeFrom,
            wakeTime = wakeTime,
            minutesPast = minutesPast,
            appName = appName,
            windowTitle = app?.packageName ?: "",
            nudgeCount = nudgeCount,
            nudgeStyle = nudgeStyle,
            userReply = text,
        )

        val decision = llmClient!!.classifyAndNudge(
            systemPrompt = systemPrompt,
            history = history,
            userMessage = turnContext,
            nudgeCount = nudgeCount,
        )

        history.add("user", text)
        if (decision.message.isNotBlank()) {
            history.add("assistant", decision.message)

            val escalation = cachedEscalation.value
            NudgeNotifier.showNudge(applicationContext, decision.message, nudgeCount, escalation)
        }

        val requestedExtension = decision.extensionMinutes
        if (requestedExtension != null) {
            val maxAllowed = computeRemainingActiveWindowMinutes(now, activeFrom, cachedActiveUntil.value)
            val granted = minOf(requestedExtension, maxAllowed).coerceAtLeast(0)
            if (granted > 0) {
                pausedUntilMs = System.currentTimeMillis() + granted * 60_000L
                emitStatus(MonitorState.IDLE, "Extension granted: ${granted}m")
            }
        }
    }

    // ---- helpers ---------------------------------------------------------

    private fun ensureLlmClient(apiKey: String) {
        val existing = llmClient
        if (existing == null || existing.apiKey != apiKey) {
            llmClient = LlmClient(apiKey, cachedModel.value)
        }
    }

    private fun maybeStartSession(now: LocalDateTime, activeFrom: String) {
        val startParts = activeFrom.split(":").map { it.toInt() }
        val startTime = LocalTime.of(startParts[0], startParts[1])
        val dateKey = if (now.toLocalTime() < startTime) {
            now.toLocalDate().minusDays(1).toString()
        } else {
            now.toLocalDate().toString()
        }
        if (sessionDateKey != dateKey) {
            Log.i(TAG, "New nudge session: $dateKey")
            sessionDateKey = dateKey
            nudgeCount = 0
            lastNudgeAtMs = null
            lastNudgeActivityKey = null
            noNudgeCache.clear()
            history.startSession()
        }
    }

    private fun maybeEndSession() {
        if (sessionDateKey != null) {
            Log.i(TAG, "Ending session: $sessionDateKey")
        }
        sessionDateKey = null
        nudgeCount = 0
        lastNudgeAtMs = null
        lastNudgeActivityKey = null
        noNudgeCache.clear()
        history.clear()
    }

    private fun computeRemainingActiveWindowMinutes(
        now: LocalDateTime,
        activeFrom: String,
        activeUntil: String,
    ): Int {
        val start = LocalTime.parse(activeFrom, timeFmt)
        val end = LocalTime.parse(activeUntil, timeFmt)
        val nowTime = now.toLocalTime()

        val endDateTime = if (start <= end) {
            now.toLocalDate().atTime(end)
        } else if (nowTime >= start) {
            now.toLocalDate().plusDays(1).atTime(end)
        } else {
            now.toLocalDate().atTime(end)
        }

        val diffMs = java.time.Duration.between(now, endDateTime).toMillis()
        return (diffMs / 60_000L).toInt().coerceAtLeast(0)
    }

    private fun emitStatus(state: MonitorState, message: String) {
        NudgeNotifier.updateStatus(applicationContext, state, message)
    }

    private fun nextPollDelayMs(): Long {
        if (!ActivityWatcher.isScreenOn(applicationContext)) return POLL_INTERVAL_SCREEN_OFF_MS
        if (ActivityWatcher.isDeviceLocked(applicationContext)) return POLL_INTERVAL_SCREEN_OFF_MS
        return POLL_INTERVAL_MS
    }

    private fun isBatteryCritical(): Boolean {
        val bm = getSystemService(BatteryManager::class.java)
        val level = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        return level in 1..BATTERY_CRITICAL_PERCENT
    }
}
