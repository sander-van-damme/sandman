# Sandman

**Cross-platform bedtime nudge app.** Sandman monitors what you're doing on
your device and, during your configured wind-down window, uses an LLM to send
personalized, behavioral-psychology-grounded nudges encouraging you to stop
and go to bed.

It doesn't just block apps. It recognizes "you're deep in a coding session at
11 PM" and sends a specific, persuasive message — and you can reply to it.

## Platforms

| Platform | Language | Directory |
|----------|----------|-----------|
| [Windows](windows/README.md) | Python | `windows/` |
| [Android](android/README.md) | Kotlin | `android/` |

See the platform-specific README for install instructions, build steps, and
architecture details.

## How it works

Every 30 seconds Sandman checks which app or window is in focus:

- On **Windows** it queries [ActivityWatch](https://activitywatch.net/)
  (localhost) for the focused window title, app name, and AFK state.
- On **Android** it uses the system Usage Stats API to find the foreground
  app and checks whether the screen is on.

If you're inside your active wind-down window, not paused, not idle, and not
rate-limited, Sandman sends the activity context to an OpenAI model along
with your preferences and how many nudges have already been sent this session.

The model decides whether a nudge is warranted — booking a taxi at 11 pm is
fine; the third hour of doomscrolling is not — and returns a message matching
your chosen style. The nudge is delivered as a system notification, and you
can reply to start a short back-and-forth conversation. As nudges accumulate
the tone escalates.

## Common concepts

**Schedule.** You set an *active from* / *active until* time window and the
days of the week it applies to. Overnight windows (e.g. `21:30 → 02:00`)
are supported on all platforms.

**Nudge styles.**

- `gentle` — gentle and supportive
- `direct` — direct and firm
- `humor` — uses humor
- `therapist` — coaches you like a therapist

**Escalation.** The nudge count is passed to the LLM so the model naturally
escalates tone over a session.

**Dedup.** If the same app/activity was the subject of the last nudge, Sandman
waits three times the normal interval before nudging about it again.

**Pause.** You can pause nudges for a fixed number of minutes or until the
next day's active window.

## Cost

At typical usage (~48 nudges per night, 30 nights/month) the OpenAI cost is
well under $0.10/month using `gpt-5-nano`.

## Privacy

Only the current foreground app name (and on Windows the window title) is
sent to OpenAI. Your API key, settings, and conversation history never leave
your device.

## License

See [LICENSE](LICENSE).
