package com.sandman.android.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.sandman.android.ui.chat.ChatScreen
import com.sandman.android.ui.settings.SettingsScreen

private sealed class Screen(val route: String, val label: String) {
    object Settings : Screen("settings", "Settings")
    object Chat : Screen("chat", "Chat")
}

@Composable
fun MainScreen() {
    val navController = rememberNavController()
    val navBackStack by navController.currentBackStackEntryAsState()
    val currentDest = navBackStack?.destination

    Scaffold(
        bottomBar = {
            NavigationBar {
                listOf(Screen.Settings, Screen.Chat).forEach { screen ->
                    NavigationBarItem(
                        selected = currentDest?.hierarchy?.any { it.route == screen.route } == true,
                        onClick = {
                            navController.navigate(screen.route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = {
                            when (screen) {
                                Screen.Settings -> Icon(Icons.Filled.Settings, contentDescription = null)
                                Screen.Chat -> Icon(Icons.Filled.Chat, contentDescription = null)
                            }
                        },
                        label = { Text(screen.label) },
                    )
                }
            }
        },
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = Screen.Settings.route,
            modifier = Modifier.padding(innerPadding),
        ) {
            composable(Screen.Settings.route) { SettingsScreen() }
            composable(Screen.Chat.route) { ChatScreen() }
        }
    }
}
