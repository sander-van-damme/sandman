package com.sandman.android.model

/** Structured result returned by the LLM for each polling tick. */
data class NudgeDecision(
    val activityType: String,
    val shouldNudge: Boolean,
    val reason: String,
    val message: String,
    val followUpQuestion: String? = null,
    val extensionMinutes: Int? = null,
) {
    companion object {
        private val FALLBACK_MESSAGES = listOf(
            "Hey, it's getting late. Consider wrapping up.",
            "Time's slipping away — your future self will thank you for stopping now.",
            "Small step: close the app. Future you is cheering.",
            "You've earned rest. Wrap up the current thought and call it a night.",
        )

        fun fallback(nudgeCount: Int, reason: String = "LLM unavailable"): NudgeDecision {
            val msg = FALLBACK_MESSAGES[minOf(nudgeCount, FALLBACK_MESSAGES.size - 1)]
            return NudgeDecision(
                activityType = "other",
                shouldNudge = true,
                reason = reason,
                message = msg,
            )
        }
    }
}

/** The app currently in the foreground, as resolved from UsageStats. */
data class ForegroundApp(
    val packageName: String,
    val appLabel: String,
)

/** Mirror of the Python MonitorState enum. */
enum class MonitorState {
    IDLE,    // outside active window or paused
    ACTIVE,  // inside window, monitoring
    NUDGING, // recently sent a nudge
    ERROR,   // missing API key, usage-stats permission denied, etc.
}

/** In-memory conversation history for the current session (mirrors Python ConversationHistory). */
class ConversationHistory(private val maxMessages: Int = 20) {
    private val _messages: MutableList<Map<String, String>> = mutableListOf()
    val messages: List<Map<String, String>> get() = _messages.toList()

    fun add(role: String, content: String) {
        _messages.add(mapOf("role" to role, "content" to content))
        if (_messages.size > maxMessages) {
            val excess = _messages.size - maxMessages
            repeat(excess) { _messages.removeAt(0) }
        }
    }

    fun clear() {
        _messages.clear()
    }

    fun startSession() {
        clear()
    }
}
