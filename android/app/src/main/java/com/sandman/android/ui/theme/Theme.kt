package com.sandman.android.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Matches the Python ReplyWindow palette
val SandmanBlue = Color(0xFFa0c4ff)    // Sandman messages
val UserOrange = Color(0xFFffd6a5)      // User messages
val BackgroundDark = Color(0xFF1e1e2f)  // Chat background
val SurfaceDark = Color(0xFF16162a)     // Cards / surfaces
val OnBackground = Color(0xFFe6e6f0)    // Primary text

private val DarkColors = darkColorScheme(
    primary = SandmanBlue,
    onPrimary = BackgroundDark,
    secondary = UserOrange,
    onSecondary = BackgroundDark,
    background = BackgroundDark,
    onBackground = OnBackground,
    surface = SurfaceDark,
    onSurface = OnBackground,
    surfaceVariant = Color(0xFF2a2a3f),
    onSurfaceVariant = Color(0xFFb0b0c8),
    error = Color(0xFFff6b6b),
)

@Composable
fun SandmanTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColors,
        content = content,
    )
}
