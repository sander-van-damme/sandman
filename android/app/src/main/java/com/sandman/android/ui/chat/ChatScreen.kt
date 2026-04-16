package com.sandman.android.ui.chat

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sandman.android.model.ChatMessage
import com.sandman.android.service.NudgeService
import com.sandman.android.ui.theme.SandmanBlue
import com.sandman.android.ui.theme.UserOrange
import kotlinx.coroutines.launch

class ChatViewModel : ViewModel() {
    val messages = mutableStateListOf<ChatMessage>()

    init {
        // Collect messages from the service's shared flow
        viewModelScope.launch {
            NudgeService.chatFlow.collect { (role, text) ->
                messages.add(ChatMessage(role = role, text = text))
            }
        }
    }
}

@Composable
fun ChatScreen(vm: ChatViewModel = viewModel()) {
    val context = LocalContext.current
    val messages = vm.messages
    val listState = rememberLazyListState()
    var inputText by remember { mutableStateOf("") }

    // Auto-scroll to latest message
    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .imePadding(),
    ) {
        // Header
        Surface(tonalElevation = 2.dp) {
            Text(
                text = "Chat with Sandman",
                modifier = Modifier.padding(16.dp),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
        }

        // Message transcript
        LazyColumn(
            state = listState,
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
        ) {
            if (messages.isEmpty()) {
                item {
                    Text(
                        text = "Sandman will appear here once monitoring starts.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(16.dp),
                    )
                }
            }
            items(messages) { msg ->
                MessageBubble(msg)
            }
        }

        // Input row
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = inputText,
                onValueChange = { inputText = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Reply to Sandman…") },
                singleLine = true,
                shape = RoundedCornerShape(24.dp),
            )
            Spacer(modifier = Modifier.width(8.dp))
            Button(
                onClick = {
                    val text = inputText.trim()
                    if (text.isNotBlank()) {
                        sendReply(context, text)
                        inputText = ""
                    }
                },
                shape = RoundedCornerShape(24.dp),
            ) {
                Text("Send")
            }
        }
    }
}

@Composable
private fun MessageBubble(msg: ChatMessage) {
    val isSandman = msg.role == "sandman"
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isSandman) Arrangement.Start else Arrangement.End,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 280.dp)
                .background(
                    color = if (isSandman) SandmanBlue.copy(alpha = 0.15f)
                            else UserOrange.copy(alpha = 0.15f),
                    shape = RoundedCornerShape(
                        topStart = if (isSandman) 4.dp else 16.dp,
                        topEnd = if (isSandman) 16.dp else 4.dp,
                        bottomStart = 16.dp,
                        bottomEnd = 16.dp,
                    ),
                )
                .padding(horizontal = 12.dp, vertical = 8.dp),
        ) {
            Text(
                text = if (isSandman) "Sandman" else "You",
                style = MaterialTheme.typography.labelSmall,
                color = if (isSandman) SandmanBlue else UserOrange,
                fontWeight = FontWeight.SemiBold,
                fontSize = 11.sp,
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = msg.text,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onBackground,
            )
        }
    }
}

private fun sendReply(context: Context, text: String) {
    val intent = Intent(context, NudgeService::class.java).apply {
        action = NudgeService.ACTION_USER_REPLY
        putExtra(NudgeService.EXTRA_REPLY_TEXT, text)
    }
    ContextCompat.startForegroundService(context, intent)
}
