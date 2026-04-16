# Sandman — Android

**Bedtime nudge app for Android.** Sandman runs as a persistent foreground
service, watches which app is in the foreground via the Android Usage Stats
API, and during your configured wind-down window uses an LLM to send
personalized, behavioral-psychology-grounded nudges encouraging you to stop
and go to bed.

It doesn't just block apps. It recognizes "you're deep in a coding session at
11 PM" and sends a specific, persuasive message — and you can reply to it.

## How it works

```
┌─────────────────────┐   polls every 30s   ┌────────────────────┐
│  UsageStatsManager  │ ◄─────────────────── │   NudgeService     │
│  (Android system)   │   foreground app,    │  ─ foreground svc  │
│                     │ ─── screen state ──► │  ─ monitor loop    │
└─────────────────────┘                      │  ─ OpenAI nudger   │
                                             │  ─ notifications   │
                                             │  ─ chat screen     │
                                             └────────────────────┘
```

1. Every 30 seconds `NudgeService` queries the Android Usage Stats API for
   the currently foregrounded app and checks whether the screen is on.
2. If you're inside your active window, not paused, the screen is on, and
   you're not rate-limited — Sandman sends the current app to `gpt-5-nano`
   along with your preferences and the session's nudge count.
3. The model decides whether a nudge is warranted and returns a message
   matching your chosen style.
4. An Android notification appears with **I'm going to bed**, **5 more
   minutes**, and **30 more minutes** action buttons. You can also open the
   in-app chat screen to have a back-and-forth conversation with Sandman.
5. As nudges pile up the tone escalates, mirroring the Windows behaviour.

## Requirements

- Android 8.0 (API 26) or later
- An [OpenAI API key](https://platform.openai.com/api-keys)
- **Usage Access** permission granted to Sandman (Settings → Apps →
  Special app access → Usage access)

At typical usage (~48 nudges per night, 30 nights/month) the OpenAI cost is
well under $0.10/month using `gpt-5-nano`.

## Install & run

Build and install via Android Studio or Gradle (see [Building](#building)).
On first launch the app opens the Settings screen automatically — paste your
OpenAI key, set your wind-down hours, and tap **Save**. Then tap **Start
monitoring** on the main screen.

### Granting Usage Access

Because Android restricts access to foreground-app data, you must manually
grant the permission once:

1. Open **Settings → Apps → Special app access → Usage access**.
2. Find **Sandman** and toggle it on.

The app will remind you with an error notification if the permission is
missing.

## Building

Open the `android/` directory in **Android Studio** (Hedgehog or later) and
run the app, or build from the command line:

```bash
cd android
./gradlew assembleDebug
# APK written to app/build/outputs/apk/debug/app-debug.apk
```

To build a release APK, configure a signing key in `app/build.gradle.kts`
and run:

```bash
./gradlew assembleRelease
```

## Configuration

Settings are stored via **Jetpack DataStore** (no plain-text file). You can
change them at any time through the in-app Settings screen.

| Setting | Default | Description |
|---|---|---|
| OpenAI API key | — | Required for nudge generation |
| Model | `gpt-5-nano` | OpenAI model to use |
| Active from | `21:30` | Start of wind-down window |
| Active until | `02:00` | End of wind-down window |
| Active days | Every day | Days of the week to monitor |
| Wake time | `07:30` | Your usual wake time (for context) |
| Min interval | 60 s | Minimum seconds between nudges |
| Escalation | Enabled | Whether tone escalates with nudge count |
| Nudge style | `gentle` | Personality of the nudges |

`active_days` follows Python's `weekday()` convention (0 = Monday,
6 = Sunday) to stay consistent with the Windows app.
If `active_until < active_from`, the window crosses midnight (e.g.
`21:30 → 02:00`).

Nudge styles:

- `gentle` — gentle and supportive
- `direct` — direct and firm
- `humor` — uses humor
- `therapist` — coaches you like a therapist

## Architecture

```
android/app/src/main/java/com/sandman/android/
├── SandmanApp.kt                # Application class
├── MainActivity.kt              # Nav host; routes to main/settings/chat
├── service/
│   └── NudgeService.kt          # Foreground service — 30s poll loop
├── usage/
│   └── ActivityWatcher.kt       # UsageStats foreground-app detection
├── llm/
│   └── LlmClient.kt             # OpenAI REST client + prompt templates
├── notifications/
│   └── NudgeNotifier.kt         # Notification channels, nudge + status notifs
├── data/
│   └── AppPreferences.kt        # DataStore wrapper + schedule helpers
├── model/
│   └── Models.kt                # Data classes (ForegroundApp, NudgeDecision…)
├── receiver/
│   ├── BootReceiver.kt          # Restart service after device reboot
│   └── NotificationReceiver.kt  # Handle notification action button taps
└── ui/
    ├── MainScreen.kt            # Dashboard: status, start/stop, quick actions
    ├── settings/
    │   └── SettingsScreen.kt    # Full settings form (Compose)
    ├── chat/
    │   └── ChatScreen.kt        # Conversation UI (Compose)
    └── theme/
        └── Theme.kt             # Material 3 theme
```

Key design notes:

- **Foreground service.** `NudgeService` runs as a foreground service so
  Android doesn't kill it during the night. It posts a persistent status
  notification showing the current state (idle / active / nudging / error).
- **Screen-on as AFK substitute.** Android doesn't expose user-idleness the
  way ActivityWatch does. Sandman uses `PowerManager.isInteractive()` instead
  — no nudges while the screen is off.
- **UsageStats permission.** `PACKAGE_USAGE_STATS` cannot be requested at
  runtime; the user must grant it in system settings. The service checks on
  every tick and surfaces a clear error notification if it's missing.
- **Chat via SharedFlow.** `NudgeService.chatFlow` is a `MutableSharedFlow`
  that the chat UI collects. Both incoming nudges and user replies are
  routed through it so the UI stays decoupled from the service.
- **Midnight crossover & dedup.** Schedule logic and same-activity dedup are
  ported directly from the Python implementation to keep behaviour identical
  across platforms.
- **Boot persistence.** `BootReceiver` restarts the service after a device
  reboot if it was enabled when the phone was shut down.

## Privacy

- Your API key and settings live only in the app's private DataStore on
  this device.
- The foreground app name (package name + label) is sent to OpenAI so the
  model can classify your activity and write relevant nudges. Nothing else
  leaves your device.
- Conversation history is kept in memory only and cleared at the start of
  each session.

## License

See [LICENSE](../LICENSE).
