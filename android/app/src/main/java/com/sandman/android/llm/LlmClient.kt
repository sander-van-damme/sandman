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

        fun buildSystemPrompt(): String = SYSTEM_PROMPT_TEMPLATE

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
