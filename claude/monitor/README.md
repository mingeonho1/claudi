# Claude session monitor + Slack digest notifications

Monitors multiple Claude Code sessions running in parallel, **in real time**. When a
session ends a turn (done/failed) or stops to wait for a permission or input, a hook
appends the event to a log (`events.jsonl`) and updates only `attention.json` (the
session to open on click) — it does not notify or debounce on its own.
A separate **monitor session** picks up those events, summarizes and analyzes the
corresponding session's transcript, reports to chat with a focus on "what needs a
decision", and sends notifications through `notify.py` at that point.

> Paths are written relative to the default config directory `~/.claude`. If the
> `CLAUDE_CONFIG_DIR` environment variable is set, its value replaces `~/.claude` (every
> script resolves data paths with the same rule). Runtime data (events.jsonl, etc.)
> always stays inside that config directory.

## Layout

```
~/.claude/monitor/
├── bin/
│   ├── notify.py            # Pet speech bubble (notice) + macOS notification. Stdlib only, always exits 0.
│   ├── notices.py           # Alerts-tab store (notices.json) module. notify upserts into it.
│   ├── slack_items.py       # Writes the Slack digest store (set/show/clear). Keeps the top 10 by importance.
│   ├── monitor-hook.py      # Stop/StopFailure/Notification hook. Records events + updates attention.json (no notifications).
│   ├── wait-event.py        # Blocks until a new event arrives → prints the new lines and exits.
│   ├── digest-transcript.py # transcript jsonl → digest for monitor analysis (within 15k chars).
│   └── swiftbar-sessions.10s.py  # (optional) SwiftBar menu bar plugin.
├── events.jsonl             # Collected event log (one line = one event). Appended by the hook.
├── cursor                   # Byte offset already read by wait-event.
├── monitor-session          # session_id of the monitor session (skipped to prevent loops).
├── attention.json           # Session to open when the pet/notification is clicked. Updated by the hook and monitor session.
├── notices.json             # Pet 'Alerts' tab (one row per session, persistent). Written by notify.
├── slack-state.json         # Slack polling state (last_ts). (Created once the Slack routine is connected.)
├── slack-items.json         # Top 10 for the 'Slack' section of the pet alerts tab (written by slack_items.py).
└── README.md                # This file.

~/.claude/skills/session-monitor/SKILL.md   # Skill for the monitor session (/session-monitor).
```

Data flow:

```
watched session ──(Stop/StopFailure/Notification hook)──▶ monitor-hook.py
                                                        │  ├─ append to events.jsonl (file lock)
                                                        │  └─ update attention.json (session to open on click; no notification)
                                                        ▼
monitor session (/session-monitor) ◀── wait-event.py (blocking wait) ◀── events.jsonl
       │  digest the session with digest-transcript.py
       └─ chat report (🟢 done / 🟡 needs decision / 🔴 failed) + notify.py notification for 🟡/🔴
```

## Getting started

1. **Watching** is done automatically by the hook (see "Registering the hook" below).
   Turn ends and notifications from every regular session are appended to
   `events.jsonl` and `attention.json` is updated (the hook sends no notifications).
2. Start one **monitor session**: run `/session-monitor` in any Claude Code session.
   - Recommended: set that session to `/model opus` + effort `high` (a skill frontmatter's
     model/effort override only applies to the first turn).
   - The monitor session registers itself in `monitor-session` and **skips its own events**
     (loop prevention).

### Registering the hook

Register the following under `hooks` in `~/.claude/settings.json` (for each of
`Notification`/`Stop`/`StopFailure`, timeout 5 seconds). The installer can do this for you.

```
python3 ~/.claude/monitor/bin/monitor-hook.py
```

## Notification behavior

> The hook currently has `HOOK_NOTIFY = False`, so it **does not send notifications**
> (it only records + updates `attention.json`). The behavior below applies when you set
> `HOOK_NOTIFY = True` so the hook notifies directly. Today, notifications are sent via
> `notify.py` by the monitor session (session-monitor), `launchd-check`, and `session-start-hook`.

- **Stop** → "✅ Done · <repo>" + a summary of the last message.
- **StopFailure** → "🛑 Failed · <repo>".
- **Notification**
  - `permission_prompt` / `agent_needs_input` / `elicitation_*` → "🟡 Needs input · <repo>".
  - `idle_prompt` → **recorded only**, no notification (noise).
  - anything else → "🔔 <repo>".
- **Debounce**: if the same (session, event, notification_type) repeats within 30 seconds,
  the notification is skipped (the record is kept).
- **Skipped (neither recorded nor notified)**: no session_id / the monitor session / subagents
  (`agent_id` present) / headless·SDK (`CLAUDE_CODE_ENTRYPOINT` ∈ {sdk-py, sdk-ts, sdk, headless}) /
  Stop·StopFailure with no transcript file / scheduled-task sessions.

## Pet speech bubble (claudlet) integration

`notify.py` reads the port from the `claudlet-<session_id>.port` file under
`$XDG_RUNTIME_DIR` (otherwise the OS temp dir, macOS=$TMPDIR), opens a TCP connection to
127.0.0.1, and sends one line `{"cmd":"notice","text":...}` (110-char limit). With
`--session` it targets only that session's pet; otherwise it broadcasts to every live pet.
If no pet is running (no .port file) it silently moves on. It reproduces only the
protocol without importing the claudlet package, so reinstalling/moving claudlet does
not affect it. With `CLAUDI_NO_DESKTOP_NOTIFY=1` both the speech bubble and the macOS
notification are skipped (for tests/headless — notices.json is still written).

## Turning it off

### Pause monitoring only
Stop the loop in the monitor session (any background wait-event still running exits on
its own after its max wait), then clear the monitor marker:
```bash
rm -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor/monitor-session"
```

### Fully disable the notification hook (stop watching)
In `~/.claude/settings.json` under `hooks`, remove only the groups that contain the command
below (leave other hooks alone):
```
python3 ~/.claude/monitor/bin/monitor-hook.py
```
Delete the group containing that command from each of the `Stop`, `StopFailure`, and
`Notification` arrays. Back up `settings.json` yourself before editing, and always check
that the JSON is valid afterwards:
```bash
python3 -c "import json;json.load(open('$HOME/.claude/settings.json'));print('OK')"
```

### Clearing the logs
```bash
M="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor"
: > "$M/events.jsonl"
rm -f "$M/cursor" "$M/.notify-state.json"
```

## Slack digest (separate routine)

The scheduled task **`slack-digest`** (hourly 08–21 on weekdays, cron `0 8-21 * * 1-5`)
scores "the Slack the user should see now" by importance (5 = unanswered request/question/decision,
4 = DM/mention, 3 = new reply in a thread you're in, 2 = your ticket/deadline announcement or
decision, 1 = includes FYI) and saves **at most 10** to `slack-items.json`. The write-only
helper `bin/slack_items.py` provides set (stdin JSON → sort by importance then ts descending,
keep top 10, file lock, atomic write), show, and clear. Previous items are deduplicated by id,
but kept if unresolved and within 48h. The pet 'Alerts' tab draws this list below the regular
alerts under a **divider + 'Slack' subheading** (enforce patch F `[enforce-alerts-slack]`).
Only when there is a new important item (4 or higher) does it send just a speech bubble via
`notify.py --title "Slack" --body …` (no key). The canonical prompt (template) is
`routines/slack-digest/SKILL.md` in the repository.
