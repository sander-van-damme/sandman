package com.sandman.android.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.*
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter

val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "sandman_prefs")

/** Keys for all persisted settings. */
object PrefKeys {
    val OPENAI_API_KEY = stringPreferencesKey("openai_api_key")
    val MODEL = stringPreferencesKey("model")
    val ACTIVE_FROM = stringPreferencesKey("active_from")     // "HH:mm"
    val ACTIVE_UNTIL = stringPreferencesKey("active_until")   // "HH:mm"
    val ACTIVE_DAYS = stringPreferencesKey("active_days")     // "0,1,2,3,4,5,6"
    val WAKE_TIME = stringPreferencesKey("wake_time")         // "HH:mm"
    val MIN_INTERVAL_SECONDS = intPreferencesKey("min_interval_seconds")
    val ESCALATION_ENABLED = booleanPreferencesKey("escalation_enabled")
    val NUDGE_STYLE = stringPreferencesKey("nudge_style")
    val PAUSED_UNTIL = stringPreferencesKey("paused_until")   // ISO-8601 or ""
    val TOTAL_NUDGES_SENT = intPreferencesKey("total_nudges_sent")
    val SERVICE_ENABLED = booleanPreferencesKey("service_enabled")
}

/** Default values mirroring the Python DEFAULT_CONFIG. */
object Defaults {
    const val MODEL = "gpt-5-mini"
    const val ACTIVE_FROM = "21:30"
    const val ACTIVE_UNTIL = "02:00"
    const val ACTIVE_DAYS = "0,1,2,3,4,5,6"
    const val WAKE_TIME = "07:30"
    const val MIN_INTERVAL_SECONDS = 60
    const val ESCALATION_ENABLED = true
    const val NUDGE_STYLE = "gentle"
}

val NUDGE_STYLES = listOf("gentle", "direct", "humor", "therapist")
val MODELS = listOf("gpt-5-mini", "gpt-4o-mini", "gpt-4o", "gpt-4.1-nano")

private val TIME_FMT = DateTimeFormatter.ofPattern("HH:mm")

/** Thin wrapper over DataStore for reading settings. */
class AppPreferences(private val context: Context) {

    private val store = context.dataStore

    val apiKey: Flow<String> = store.data.map { it[PrefKeys.OPENAI_API_KEY] ?: "" }
    val model: Flow<String> = store.data.map { it[PrefKeys.MODEL] ?: Defaults.MODEL }
    val activeFrom: Flow<String> = store.data.map { it[PrefKeys.ACTIVE_FROM] ?: Defaults.ACTIVE_FROM }
    val activeUntil: Flow<String> = store.data.map { it[PrefKeys.ACTIVE_UNTIL] ?: Defaults.ACTIVE_UNTIL }
    val activeDays: Flow<String> = store.data.map { it[PrefKeys.ACTIVE_DAYS] ?: Defaults.ACTIVE_DAYS }
    val wakeTime: Flow<String> = store.data.map { it[PrefKeys.WAKE_TIME] ?: Defaults.WAKE_TIME }
    val minIntervalSeconds: Flow<Int> = store.data.map { it[PrefKeys.MIN_INTERVAL_SECONDS] ?: Defaults.MIN_INTERVAL_SECONDS }
    val escalationEnabled: Flow<Boolean> = store.data.map { it[PrefKeys.ESCALATION_ENABLED] ?: Defaults.ESCALATION_ENABLED }
    val nudgeStyle: Flow<String> = store.data.map { it[PrefKeys.NUDGE_STYLE] ?: Defaults.NUDGE_STYLE }
    val pausedUntil: Flow<String> = store.data.map { it[PrefKeys.PAUSED_UNTIL] ?: "" }
    val totalNudgesSent: Flow<Int> = store.data.map { it[PrefKeys.TOTAL_NUDGES_SENT] ?: 0 }
    val serviceEnabled: Flow<Boolean> = store.data.map { it[PrefKeys.SERVICE_ENABLED] ?: false }

    suspend fun update(block: suspend MutablePreferences.() -> Unit) {
        store.edit { prefs -> prefs.block() }
    }

    suspend fun setApiKey(v: String) = update { this[PrefKeys.OPENAI_API_KEY] = v }
    suspend fun setModel(v: String) = update { this[PrefKeys.MODEL] = v }
    suspend fun setActiveFrom(v: String) = update { this[PrefKeys.ACTIVE_FROM] = v }
    suspend fun setActiveUntil(v: String) = update { this[PrefKeys.ACTIVE_UNTIL] = v }
    suspend fun setActiveDays(v: String) = update { this[PrefKeys.ACTIVE_DAYS] = v }
    suspend fun setWakeTime(v: String) = update { this[PrefKeys.WAKE_TIME] = v }
    suspend fun setMinIntervalSeconds(v: Int) = update { this[PrefKeys.MIN_INTERVAL_SECONDS] = v }
    suspend fun setEscalationEnabled(v: Boolean) = update { this[PrefKeys.ESCALATION_ENABLED] = v }
    suspend fun setNudgeStyle(v: String) = update { this[PrefKeys.NUDGE_STYLE] = v }
    suspend fun setPausedUntil(v: String) = update { this[PrefKeys.PAUSED_UNTIL] = v }
    suspend fun incrementTotalNudges() = update {
        this[PrefKeys.TOTAL_NUDGES_SENT] = (this[PrefKeys.TOTAL_NUDGES_SENT] ?: 0) + 1
    }
    suspend fun setServiceEnabled(v: Boolean) = update { this[PrefKeys.SERVICE_ENABLED] = v }
}

// ---------------------------------------------------------------------------
// Schedule helpers (ported from Python config.py)
// ---------------------------------------------------------------------------

private fun parseTime(hhmm: String): LocalTime =
    LocalTime.parse(hhmm, TIME_FMT)

/**
 * Returns true if [now] falls inside the active window defined by [activeFrom]/[activeUntil].
 * Handles midnight crossover exactly like the Python implementation.
 */
fun isWithinActiveWindow(
    now: LocalTime,
    nowDayOfWeek: Int,          // 0=Monday … 6=Sunday (java.time DayOfWeek - 1)
    activeFrom: String,
    activeUntil: String,
    activeDays: String,
): Boolean {
    val start = parseTime(activeFrom)
    val end = parseTime(activeUntil)
    val days = activeDays.split(",").mapNotNull { it.trim().toIntOrNull() }.toSet()

    return if (start <= end) {
        // Same-day window
        if (nowDayOfWeek !in days) false
        else now >= start && now <= end
    } else {
        // Crosses midnight
        when {
            now >= start -> nowDayOfWeek in days
            now <= end -> {
                val yesterday = (nowDayOfWeek - 1 + 7) % 7
                yesterday in days
            }
            else -> false
        }
    }
}

/** Minutes elapsed since active_from started (handles overnight windows). */
fun minutesPastBedtime(
    nowHour: Int,
    nowMinute: Int,
    activeFrom: String,
): Int {
    val start = parseTime(activeFrom)
    val nowMins = nowHour * 60 + nowMinute
    val startMins = start.hour * 60 + start.minute
    val diff = nowMins - startMins
    return if (diff >= 0) diff else diff + 24 * 60
}
