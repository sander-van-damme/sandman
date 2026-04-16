package com.sandman.android.llm

import android.util.Log
import com.sandman.android.model.ConversationHistory
import com.sandman.android.model.NudgeDecision
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit

private const val TAG = "LlmClient"
private const val OPENAI_URL = "https://api.openai.com/v1/chat/completions"

private const val SYSTEM_PROMPT_TEMPLATE = """You are Sandman, a bedtime coach built on behavioral psychology principles \
(BJ Fogg's Behavior Model, habit stacking, commitment devices, implementation \
intentions). The user has asked you to help them get to bed on time.

Your job:
1. CLASSIFY what the user is doing (programming, social media, entertainment, \
communication, productive work, etc.)
2. DECIDE if this activity warrants a nudge. Some activities are legitimate \
late-night needs (e.g., booking an Uber, responding to an urgent message, \
setting an alarm). Use good judgment.
3. If nudging, generate a SHORT message (2-3 sentences max) that:
   - Acknowledges what they're doing specifically
   - Uses a behavioral psychology technique (e.g., "just one tiny step: put the \
phone face-down", "your future self will thank you", "you've been scrolling for \
20 minutes — diminishing returns have kicked in")
   - Rotates in health-oriented nudges over time: sleep quality, next-day \
focus, mood regulation, stress load, eye strain, posture tension, hydration \
timing, and circadian consistency
   - Frequently reinforce that sleep is essential for good health, and that \
tomorrow starts now
   - Gets more urgent as nudge_count increases
   - Matches the user's preferred nudge style
4. If the user has replied to a previous nudge, respond to their reply \
conversationally while still guiding them toward bed.
5. If the user asks for an extension, you may grant one only when justified. \
When granting, include "extension_minutes" as a positive integer.

Respond in JSON format:
{
  "activity_type": "programming|social_media|entertainment|communication|productive|utility|other",
  "should_nudge": true/false,
  "reason": "brief explanation of why or why not",
  "message": "the nudge message to show (only if should_nudge is true)",
  "follow_up_question": "optional question to engage the user, e.g. 'What is keeping you going right now?'",
  "extension_minutes": 0
}"""

// Rotated daily by day-of-year so the model approaches each session through a
// different behavioral-psychology lens. One full cycle = 14 days.
private val DAILY_FOCUS_INJECTIONS = arrayOf(
    // 0 — sleep architecture / memory consolidation
    "Today's lens — sleep architecture: Memory consolidation and metabolic waste " +
    "clearance happen almost exclusively in deep slow-wave sleep, which is " +
    "front-loaded in the night. Frame every lost hour now as cutting the most " +
    "restorative phase first.",
    // 1 — identity-based habits (James Clear)
    "Today's lens — identity: Each step toward bed is a vote for who the user is " +
    "becoming. Reinforce that sleeping on time is not a sacrifice — it is evidence " +
    "of someone who respects their own recovery.",
    // 2 — emotional regulation (amygdala reactivity)
    "Today's lens — emotional resilience: Sleep deprivation amplifies amygdala " +
    "reactivity by up to 60%, making tomorrow's frustrations feel " +
    "disproportionate. Frame going to bed as protecting tomorrow's patience, " +
    "empathy, and calm under pressure.",
    // 3 — social performance (warmth / trust perception)
    "Today's lens — social ability: Sleep-deprived people are rated as less warm, " +
    "less trustworthy, and harder to connect with. Frame sleeping now as an " +
    "investment in tomorrow's relationships, conversations, and the impression " +
    "they make on others.",
    // 4 — cognitive peak performance
    "Today's lens — cognitive peak: Even mild sleep debt measurably reduces " +
    "working memory, attention, and creative output. Frame bedtime as priming " +
    "tomorrow's sharpest thinking — the work is not done until the brain is " +
    "recharged.",
    // 5 — implementation intentions (Gollwitzer)
    "Today's lens — implementation intentions: 'When X happens, I will do Y' " +
    "plans double follow-through rates. Encourage the user to commit to one " +
    "specific trigger-action right now, e.g. 'When I put the phone face-down, I " +
    "will plug it in and close my eyes.'",
    // 6 — physical recovery (HGH, immune, repair)
    "Today's lens — physical recovery: Growth hormone peaks in the first sleep " +
    "cycle; muscles repair and immune cells activate during deep sleep. Staying " +
    "up is skipping the body's nightly maintenance window — with compounding " +
    "interest.",
    // 7 — loss aversion (Kahneman)
    "Today's lens — loss aversion: Losses hurt roughly twice as much as " +
    "equivalent gains feel good. Reframe staying up not as gaining time, but as " +
    "actively losing cognitive capacity, mood stability, and immune defence — " +
    "concrete assets being eroded right now.",
    // 8 — circadian alignment / consistency
    "Today's lens — circadian alignment: The body clock governs cortisol, " +
    "melatonin, body temperature, and hundreds of downstream processes. Irregular " +
    "sleep timing shifts the circadian phase and compounds into chronic fatigue. " +
    "Frame consistent timing as a long-term competitive advantage.",
    // 9 — social proof / high-performer norms
    "Today's lens — high-performer norms: Elite athletes, surgeons, and peak " +
    "performers treat sleep as non-negotiable. Going to bed on time is the quiet " +
    "discipline behind sustained excellence. Frame it as joining that standard.",
    // 10 — temptation bundling (Milkman)
    "Today's lens — temptation bundling: Pair winding down with something " +
    "genuinely enjoyable — a favourite podcast, a chapter of a book, a breathing " +
    "exercise. Nudge the user toward associating bedtime with reward and pleasure, " +
    "not loss.",
    // 11 — stress physiology (cortisol / HPA axis)
    "Today's lens — stress regulation: Late-night screens keep cortisol elevated " +
    "and the nervous system in low-level alert mode, making sleep harder and " +
    "amplifying next-day anxiety. Frame winding down as actively down-regulating " +
    "the stress response.",
    // 12 — future self continuity (Hershfield)
    "Today's lens — future self: People treat their future self like a stranger. " +
    "Help the user feel connected to the person waking up tomorrow — choices made " +
    "now are gifts or burdens left for that person. Make tonight's choice a gift.",
    // 13 — habit loop (Duhigg, cue-routine-reward)
    "Today's lens — habit loops: Every consistent bedtime strengthens the " +
    "cue-routine-reward circuit that makes future sleep feel natural and " +
    "automatic. Tonight's action is not just about tonight — it is training the " +
    "brain to crave sleep at this hour.",
)

private const val TURN_CONTEXT_TEMPLATE = """Current context (latest only):
- It is %s
- The user's bedtime goal is %s (they need to wake at %s)
- They have been active past their wind-down time for %d minutes
- Current application: %s
- Current window title: %s
- Number of nudges sent this session: %d
- Nudge style preference: %s
%s

Return ONLY JSON matching the schema from the system prompt."""

class LlmClient(
    val apiKey: String,
    private val model: String = "gpt-5-mini",
) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true }

    companion object {
        private val TIME_FMT = DateTimeFormatter.ofPattern("HH:mm")

        fun buildSystemPrompt(today: LocalDate = LocalDate.now()): String {
            val idx = today.dayOfYear % DAILY_FOCUS_INJECTIONS.size
            return SYSTEM_PROMPT_TEMPLATE + "\n" + DAILY_FOCUS_INJECTIONS[idx] + "\n"
        }

        fun buildTurnContextMessage(
            now: LocalDateTime,
            bedtime: String,
            wakeTime: String,
            minutesPast: Int,
            appName: String,
            windowTitle: String,
            nudgeCount: Int,
            nudgeStyle: String,
            userReply: String? = null,
        ): String = TURN_CONTEXT_TEMPLATE.format(
            now.format(TIME_FMT),
            bedtime,
            wakeTime,
            minutesPast,
            appName.ifBlank { "unknown" },
            windowTitle,
            nudgeCount,
            nudgeStyle,
            userReply?.let { "\nLatest user reply: $it" } ?: "",
        )
    }

    suspend fun classifyAndNudge(
        systemPrompt: String,
        history: ConversationHistory,
        userMessage: String? = null,
        nudgeCount: Int = 0,
    ): NudgeDecision = withContext(Dispatchers.IO) {
        val messages = buildJsonArray {
            addJsonObject {
                put("role", "system")
                put("content", systemPrompt)
            }
            for (msg in history.messages) {
                addJsonObject {
                    put("role", msg["role"] ?: "user")
                    put("content", msg["content"] ?: "")
                }
            }
            if (!userMessage.isNullOrBlank()) {
                addJsonObject {
                    put("role", "user")
                    put("content", userMessage)
                }
            }
        }

        val body = buildJsonObject {
            put("model", model)
            put("messages", messages)
            put("response_format", buildJsonObject { put("type", "json_object") })
        }.toString()

        try {
            val request = Request.Builder()
                .url(OPENAI_URL)
                .addHeader("Authorization", "Bearer $apiKey")
                .addHeader("Content-Type", "application/json")
                .post(body.toRequestBody("application/json".toMediaType()))
                .build()

            http.newCall(request).execute().use { response ->
                val responseBody = response.body?.string() ?: "{}"

                if (!response.isSuccessful) {
                    Log.w(TAG, "OpenAI returned ${response.code}: $responseBody")
                    return@withContext NudgeDecision.fallback(nudgeCount, "api_error: ${response.code}")
                }

                val root = json.parseToJsonElement(responseBody).jsonObject
                val content = extractResponseContent(root)
                if (content == null) {
                    Log.w(TAG, "LLM returned empty content")
                    return@withContext NudgeDecision.fallback(nudgeCount, "empty_response")
                }

                parseDecision(content, nudgeCount)
            }
        } catch (e: Exception) {
            Log.w(TAG, "OpenAI call failed", e)
            NudgeDecision.fallback(nudgeCount, "exception: ${e.message}")
        }
    }

    private fun extractResponseContent(root: JsonObject): JsonElement? {
        val choice = root["choices"]?.jsonArray?.firstOrNull()?.jsonObject ?: return null
        val message = choice["message"]?.jsonObject ?: return null

        val parsed = message["parsed"]
        if (parsed is JsonObject && parsed.isNotEmpty()) {
            return parsed
        }

        val content = message["content"] ?: return null
        return when (content) {
            is JsonObject -> content
            is JsonPrimitive -> content.contentOrNull?.takeIf { it.isNotBlank() }?.let { JsonPrimitive(it) }
            is JsonArray -> {
                val text = content.mapNotNull { part ->
                    val obj = part as? JsonObject ?: return@mapNotNull null
                    obj["text"]?.jsonPrimitive?.contentOrNull
                        ?: obj["content"]?.jsonPrimitive?.contentOrNull
                }.joinToString("\n").trim()
                text.takeIf { it.isNotBlank() }?.let { JsonPrimitive(it) }
            }

            else -> null
        }
    }

    private fun parseDecision(content: JsonElement, nudgeCount: Int): NudgeDecision {
        val obj = try {
            when (content) {
                is JsonObject -> content
                is JsonPrimitive -> json.parseToJsonElement(content.content).jsonObject
                else -> return NudgeDecision.fallback(nudgeCount, "invalid_json")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse LLM response", e)
            return NudgeDecision.fallback(nudgeCount, "invalid_json")
        }

        if (obj.isEmpty()) {
            return NudgeDecision.fallback(nudgeCount, "empty_json")
        }

        return NudgeDecision(
            activityType = obj["activity_type"]?.jsonPrimitive?.content ?: "other",
            shouldNudge = obj["should_nudge"]?.jsonPrimitive?.booleanOrNull ?: false,
            reason = obj["reason"]?.jsonPrimitive?.content ?: "",
            message = obj["message"]?.jsonPrimitive?.content ?: "",
            followUpQuestion = obj["follow_up_question"]?.jsonPrimitive?.contentOrNull,
            extensionMinutes = parseExtensionMinutes(obj["extension_minutes"]),
        )
    }

    private fun parseExtensionMinutes(value: JsonElement?): Int? {
        val primitive = value as? JsonPrimitive ?: return null
        val minutes = primitive.intOrNull ?: primitive.contentOrNull?.toIntOrNull() ?: return null
        return minutes.takeIf { it > 0 }
    }
}
