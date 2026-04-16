package com.sandman.android.ui.settings

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sandman.android.data.*
import com.sandman.android.service.NudgeService
import com.sandman.android.usage.ActivityWatcher
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class SettingsViewModel : ViewModel() {
    private lateinit var prefs: AppPreferences

    fun init(context: android.content.Context) {
        if (::prefs.isInitialized) return
        prefs = AppPreferences(context)
    }

    // Expose flows as StateFlows for Compose
    val apiKey by lazy { prefs.apiKey.stateIn(viewModelScope, SharingStarted.Eagerly, "") }
    val model by lazy { prefs.model.stateIn(viewModelScope, SharingStarted.Eagerly, Defaults.MODEL) }
    val activeFrom by lazy { prefs.activeFrom.stateIn(viewModelScope, SharingStarted.Eagerly, Defaults.ACTIVE_FROM) }
    val activeUntil by lazy { prefs.activeUntil.stateIn(viewModelScope, SharingStarted.Eagerly, Defaults.ACTIVE_UNTIL) }
    val activeDays by lazy { prefs.activeDays.stateIn(viewModelScope, SharingStarted.Eagerly, Defaults.ACTIVE_DAYS) }
    val wakeTime by lazy { prefs.wakeTime.stateIn(viewModelScope, SharingStarted.Eagerly, Defaults.WAKE_TIME) }
    val minInterval by lazy { prefs.minIntervalSeconds.stateIn(viewModelScope, SharingStarted.Eagerly, Defaults.MIN_INTERVAL_SECONDS) }
    val escalationEnabled by lazy { prefs.escalationEnabled.stateIn(viewModelScope, SharingStarted.Eagerly, Defaults.ESCALATION_ENABLED) }
    val nudgeStyle by lazy { prefs.nudgeStyle.stateIn(viewModelScope, SharingStarted.Eagerly, Defaults.NUDGE_STYLE) }
    val serviceEnabled by lazy { prefs.serviceEnabled.stateIn(viewModelScope, SharingStarted.Eagerly, false) }

    fun save(
        apiKey: String? = null,
        model: String? = null,
        activeFrom: String? = null,
        activeUntil: String? = null,
        activeDays: String? = null,
        wakeTime: String? = null,
        minInterval: Int? = null,
        escalationEnabled: Boolean? = null,
        nudgeStyle: String? = null,
    ) {
        viewModelScope.launch {
            apiKey?.let { prefs.setApiKey(it) }
            model?.let { prefs.setModel(it) }
            activeFrom?.let { prefs.setActiveFrom(it) }
            activeUntil?.let { prefs.setActiveUntil(it) }
            activeDays?.let { prefs.setActiveDays(it) }
            wakeTime?.let { prefs.setWakeTime(it) }
            minInterval?.let { prefs.setMinIntervalSeconds(it) }
            escalationEnabled?.let { prefs.setEscalationEnabled(it) }
            nudgeStyle?.let { prefs.setNudgeStyle(it) }
        }
    }
}

private val DAY_LABELS = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

@Composable
fun SettingsScreen(vm: SettingsViewModel = viewModel()) {
    val context = LocalContext.current
    vm.init(context)

    val apiKey by vm.apiKey.collectAsState()
    val model by vm.model.collectAsState()
    val activeFrom by vm.activeFrom.collectAsState()
    val activeUntil by vm.activeUntil.collectAsState()
    val activeDays by vm.activeDays.collectAsState()
    val wakeTime by vm.wakeTime.collectAsState()
    val minInterval by vm.minInterval.collectAsState()
    val escalationEnabled by vm.escalationEnabled.collectAsState()
    val nudgeStyle by vm.nudgeStyle.collectAsState()
    val serviceEnabled by vm.serviceEnabled.collectAsState()

    val usagePermGranted = ActivityWatcher.isPermissionGranted(context)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Text(
            text = "Settings",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
        )

        // ---- Permissions banner ------------------------------------------
        if (!usagePermGranted) {
            PermissionBanner(
                text = "Usage access permission is required to detect the active app.",
                buttonLabel = "Grant permission",
            ) {
                context.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
            }
        }

        // ---- API Key --------------------------------------------------------
        SectionHeader("OpenAI")
        var showKey by remember { mutableStateOf(false) }
        OutlinedTextField(
            value = apiKey,
            onValueChange = { vm.save(apiKey = it) },
            label = { Text("API Key") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            visualTransformation = if (showKey) VisualTransformation.None else PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            trailingIcon = {
                IconButton(onClick = { showKey = !showKey }) {
                    Icon(
                        imageVector = if (showKey) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                        contentDescription = if (showKey) "Hide key" else "Show key",
                    )
                }
            },
        )

        DropdownSetting(
            label = "Model",
            selected = model,
            options = MODELS,
            onSelect = { vm.save(model = it) },
        )

        // ---- Schedule -------------------------------------------------------
        SectionHeader("Schedule")
        TimeField(label = "Active from (bedtime)", value = activeFrom, onSave = { vm.save(activeFrom = it) })
        TimeField(label = "Active until", value = activeUntil, onSave = { vm.save(activeUntil = it) })
        TimeField(label = "Wake time", value = wakeTime, onSave = { vm.save(wakeTime = it) })

        // Active days checkboxes
        Text("Active days", style = MaterialTheme.typography.labelMedium)
        val selectedDays = activeDays.split(",").mapNotNull { it.trim().toIntOrNull() }.toSet()
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            DAY_LABELS.forEachIndexed { idx, label ->
                val checked = idx in selectedDays
                FilterChip(
                    selected = checked,
                    onClick = {
                        val newDays = if (checked) selectedDays - idx else selectedDays + idx
                        vm.save(activeDays = newDays.sorted().joinToString(","))
                    },
                    label = { Text(label, style = MaterialTheme.typography.labelSmall) },
                )
            }
        }

        // ---- Notifications --------------------------------------------------
        SectionHeader("Notifications")

        var intervalText by remember(minInterval) { mutableStateOf(minInterval.toString()) }
        OutlinedTextField(
            value = intervalText,
            onValueChange = { v ->
                intervalText = v
                v.toIntOrNull()?.let { vm.save(minInterval = it) }
            },
            label = { Text("Min interval between nudges (seconds)") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        )

        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text("Escalation (full-screen at 7+ nudges)", style = MaterialTheme.typography.bodyMedium)
            Switch(
                checked = escalationEnabled,
                onCheckedChange = { vm.save(escalationEnabled = it) },
            )
        }

        DropdownSetting(
            label = "Nudge style",
            selected = nudgeStyle,
            options = NUDGE_STYLES,
            onSelect = { vm.save(nudgeStyle = it) },
        )

        // ---- Start / Stop ---------------------------------------------------
        SectionHeader("Monitoring")
        Button(
            modifier = Modifier.fillMaxWidth(),
            onClick = {
                val intent = Intent(context, NudgeService::class.java)
                if (serviceEnabled) {
                    intent.action = NudgeService.ACTION_STOP
                    context.startService(intent)
                } else {
                    intent.action = NudgeService.ACTION_START
                    ContextCompat.startForegroundService(context, intent)
                }
            },
            colors = if (serviceEnabled)
                ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)
            else
                ButtonDefaults.buttonColors(),
        ) {
            Text(if (serviceEnabled) "Stop monitoring" else "Start monitoring")
        }

        Spacer(modifier = Modifier.height(24.dp))
    }
}

// ---- Small reusable composables ------------------------------------------

@Composable
private fun SectionHeader(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.primary,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(top = 4.dp),
    )
    HorizontalDivider()
}

@Composable
private fun PermissionBanner(text: String, buttonLabel: String, onClick: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                text = text,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f),
            )
            Spacer(modifier = Modifier.width(8.dp))
            TextButton(onClick = onClick) { Text(buttonLabel) }
        }
    }
}

@Composable
private fun TimeField(label: String, value: String, onSave: (String) -> Unit) {
    var text by remember(value) { mutableStateOf(value) }
    OutlinedTextField(
        value = text,
        onValueChange = { v ->
            text = v
            if (v.matches(Regex("\\d{2}:\\d{2}"))) onSave(v)
        },
        label = { Text(label) },
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
        placeholder = { Text("HH:MM") },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DropdownSetting(
    label: String,
    selected: String,
    options: List<String>,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = it },
        modifier = Modifier.fillMaxWidth(),
    ) {
        OutlinedTextField(
            value = selected,
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            modifier = Modifier
                .menuAnchor()
                .fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option) },
                    onClick = {
                        onSelect(option)
                        expanded = false
                    },
                )
            }
        }
    }
}
