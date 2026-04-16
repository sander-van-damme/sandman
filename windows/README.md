# Sandman — Windows

**Bedtime nudge app for Windows.** Sandman runs in your system tray, watches
what you're doing via [ActivityWatch](https://activitywatch.net/), and during
your configured wind-down window uses an LLM to send personalized, behavioral-
psychology-grounded nudges encouraging you to stop and go to bed.

It doesn't just block apps. It recognizes "you're deep in a coding session at
11 PM" and sends a specific, persuasive message — and you can reply to it.

## How it works

```
┌─────────────────┐     polls every 30s     ┌────────────────────┐
│  ActivityWatch  │ ◄────────────────────── │     Sandman App    │
│  localhost:5600 │   window title, app,    │  ─ tray icon       │
│                 │ ──── AFK duration ────► │  ─ monitor loop    │
└─────────────────┘                         │  ─ OpenAI nudger   │
                                            │  ─ toast notifs    │
                                            │  ─ reply chat win  │
                                            └────────────────────┘
```

1. Every 30 seconds Sandman queries ActivityWatch for the current focused
   window and the AFK state.
2. If you're inside your active window, not paused, not AFK, not just woken
   up, and not rate-limited — Sandman sends the current activity to
   `gpt-5-nano` along with your preferences and the session's nudge count.
3. The model decides whether a nudge is warranted (booking an Uber at 11pm
   is fine; the third hour of doomscrolling is not) and returns a message
   matching your chosen style.
4. A Windows toast appears with **Reply**, **I'm going to bed**, and
   **5 more minutes** buttons. Replies open a small always-on-top chat
   window where you can have a quick back-and-forth with Sandman.
5. As nudges pile up, the tone escalates. At 7+ nudges in a session an
   always-on-top overlay appears that you must manually dismiss.

## Requirements

- Windows 10 or 11
- Python 3.11+
- [ActivityWatch](https://activitywatch.net/downloads/) installed and running
- An [OpenAI API key](https://platform.openai.com/api-keys)

At typical usage (~48 nudges per night, 30 nights/month) the OpenAI cost is
well under $0.10/month using `gpt-5-nano`.

## Install & run

The easiest way is to grab **`Sandman-Setup-<version>.exe`** from the
[latest release](https://github.com/sander-van-damme/sandman/releases/latest)
and run it. The installer:

- Places `Sandman.exe` in `%LOCALAPPDATA%\Programs\Sandman` (no admin
  required — choose *Install for all users* in the UAC prompt if you'd
  rather put it under `Program Files` instead).
- Adds a Start Menu entry.
- Offers optional Desktop shortcut and "Start with Windows" checkboxes.
- Registers a proper uninstaller in *Apps & features*.

Prefer to run from source? From the repository root:

```bash
pip install -r windows/requirements.txt
python -m windows.main
```

On first launch with no configured API key, the Settings window opens
automatically. Paste your OpenAI key, set your wind-down hours, and save.
Sandman will sit in the system tray until it's time.

## Building locally

From the repository root:

```bat
pip install pyinstaller
windows\build.bat
```

This produces:

- `dist\Sandman.exe` — a single-file windowed app.
- `installer\Sandman-Setup-<version>.exe` — the Windows installer
  (only if [Inno Setup 6](https://jrsoftware.org/isinfo.php) is
  installed; otherwise the installer step is skipped).

Tagged pushes (`git tag v0.1.2 && git push --tags`) trigger
`.github/workflows/build.yml`, which builds both artifacts on a Windows
runner and attaches them to a GitHub Release.

## Configuration

Settings live at `~/.sandman/config.json` (created on first run). Schema:

```json
{
  "openai_api_key": "sk-...",
  "model": "gpt-5-nano",
  "schedule": {
    "active_from": "21:30",
    "active_until": "02:00",
    "active_days": [0, 1, 2, 3, 4, 5, 6],
    "wake_time": "07:30"
  },
  "notifications": {
    "min_interval_seconds": 60,
    "escalation_enabled": true,
    "nudge_style": "gentle"
  },
  "start_with_windows": false,
  "state": {
    "paused_until": null,
    "total_nudges_sent": 0,
    "sessions": []
  }
}
```

`active_days` uses Python's `weekday()` convention: 0 = Monday, 6 = Sunday.
If `active_until < active_from`, the window crosses midnight (e.g.
`21:30 → 02:00`).

Nudge styles:

- `gentle` — gentle and supportive
- `direct` — direct and firm
- `humor` — uses humor
- `therapist` — coaches you like a therapist

## Tray menu

- **Status** — current state and whether ActivityWatch is connected
- **Settings…** — opens the settings window
- **Chat with Sandman** — opens the reply window to start a conversation
- **Pause for 30 min** — temporarily silence nudges (legitimate late work)
- **Pause until tomorrow** — silences until the next day's active window
- **Resume** — un-pause
- **Quit** — exit Sandman

Icon colors:

| Color  | Meaning                                    |
|--------|--------------------------------------------|
| Grey   | Idle / outside active hours                |
| Blue   | Active, monitoring                         |
| Orange | Active, just sent a nudge                  |
| Red    | Error (ActivityWatch down, missing key…)   |

## Privacy

- Your API key, settings, and persisted state live only in
  `~/.sandman/config.json` on this machine.
- Window titles and app names are sent to OpenAI so the model can classify
  your activity and write relevant nudges. Nothing else leaves your computer.
- Conversation history for the reply window is kept in memory only and
  cleared at the start of each session.

## Architecture

```
windows/
├── main.py           # Entry point. Wires tray, monitor, settings, LLM.
├── tray.py           # pystray icon + menu; state → icon color.
├── settings.py       # tkinter settings window.
├── config.py         # JSON config persistence, schedule logic.
├── activity_watch.py # ActivityWatch REST client.
├── monitor.py        # Background poll loop: rate limit, dedup, LLM call.
├── llm.py            # OpenAI client, prompt templates, conversation history.
├── notifications.py  # Toast notifications + reply chat window.
└── assets/           # Icon files (generated programmatically if missing).
```

Key design notes:

- **Threading.** Pystray owns the main thread. The monitor loop runs on a
  background thread with a `threading.Event` shutdown signal. Tkinter work
  is scheduled via a UI task queue.
- **Bucket discovery.** ActivityWatch bucket IDs include the hostname
  (`aw-watcher-window_my-laptop`). Sandman picks the one matching the local
  hostname when available.
- **Midnight crossover.** `is_within_active_window` handles overnight
  windows and checks active days against the day the window *started*.
- **Dedup.** If the exact same (app, title) was the subject of the last
  nudge, Sandman waits `3 × min_interval` before re-nudging about it — so
  it won't nag you about the same VS Code file.
- **Escalation.** The nudge count is included in the LLM prompt so the
  model naturally escalates tone. At 7+ nudges an overlay window appears.

## Running the tests

```bash
pip install pytest
python -m pytest windows/tests/
```

The tests cover config parsing & schedule logic, ActivityWatch client
(mocked HTTP), LLM prompt building & parsing (mocked OpenAI), sleep
detection, and the monitor's decision logic end-to-end.

## License

See [LICENSE](../LICENSE).
