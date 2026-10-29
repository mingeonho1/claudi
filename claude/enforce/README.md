# enforce — pet / notification / session-classification layer

A shared layer that fixes the noise the desktop pet (claudlet), the session
monitor (monitor), and the session-start reminder (enforce) used to make because
of scheduled-task sessions. **Policy (default): if the
`~/.claude/enforce/standalone-pet` flag exists, the on-screen pet is a single
"standalone" pet that does not attach to any session** — launchd keeps it alive
from login/boot. Without the flag, no standalone pet is started. The pet stays
quiet normally and only announces meaningful events (needs a decision / failure /
relevant Slack) through our notify (label + summary). A session's raw replies are
never relayed to the pet.

> Paths assume the default config directory `~/.claude`. If you set the
> `CLAUDE_CONFIG_DIR` environment variable, that value replaces `~/.claude` (all
> scripts follow the same rule).

Notifications show up in two ways: (1) speech bubble / macOS notification
(momentary) (2) the **"Alerts" tab** of the pet's chat window =
`~/.claude/monitor/notices.json` (one row per session, persistent). The Alerts tab
is separate from the user conversation (the With the pet tab) and never ends up in
outbox/askhistory/additionalContext (this breaks feedback loops).

## Files (`~/.claude/enforce/bin/`)

| File | Role |
|---|---|
| `sessionkind.py` | Detects scheduled sessions + maps hook ↔ desktop session ids (shared module) |
| `claudlet_paths.py` | Finds the claudlet venv python and `claudlet-attach` (shared module) |
| `ensure-pet.py` | Pings the standalone pet → starts one if missing (lock + duplicate guard). Called by launchd |
| `claudlet_click.py` | (injected into the pet) pet click → jump to the session that needs attention |
| `focus_session.py` | Jump to a session: app = deep link, CLI = terminal focus (best-effort) |
| `sessionlabel.py` | One-line session label (app = title / CLI = terminal·cwd·branch) |
| `patch-claudlet.py` | Minimal claudlet `pet.py` patch (re)applier (A–G, idempotent) |
| `check.py` | enforce health check (Slack summary not running / standalone pet down) |
| `session-start-hook.py` | UserPromptSubmit (once, on a session's first prompt) reminder. Skips scheduled sessions |
| `launchd-check.py` | launchd backup checker (reminds even when the app is closed) |
| `open-link.sh` | Opens a deep link when a notification is clicked (leaves a log) |

Notification storage/sending lives under `~/.claude/monitor/bin/`: the
`notices.json` store module `notices.py` and the sender `notify.py` (notices
upsert + speech bubble + macOS notification).

### Keeping the standalone pet alive (launchd)

| LaunchAgent | Interval | What it does |
|---|---|---|
| `com.claudi.pet` | 1 hour + RunAtLoad | `ensure-pet.py` (starts the standalone pet if missing) |
| `com.claudi.enforcer` | 1 hour + RunAtLoad | `launchd-check.py` (health check + calls `ensure-pet.py`) |

Enable: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.claudi.pet.plist`
(RunAtLoad starts the pet immediately). Re-check now: `launchctl kickstart gui/$(id -u)/com.claudi.pet`.
The standalone pet runs as `claudlet-attach --standalone` (session_id=`default`,
port `claudlet-default.port`), and `ensure-pet.py` pings first and only starts it
when missing.
(The installer fills in the labels/paths when it creates these plists.)

## 1) Session classification — `sessionkind.py`

Basis (the macOS Claude desktop app's session store):
`~/Library/Application Support/Claude/claude-code-sessions/**/local_<id>.json`
Each file has `sessionId` (= `local_<uuid>`, the desktop/deep-link id),
`cliSessionId` (= the hook payload's `session_id`), and `scheduledTaskId`
(non-empty for scheduled tasks).

- `is_scheduled_task(data)` — True for a scheduled session. The primary evidence is
  `scheduledTaskId` (the authoritative field). If the desktop record doesn't exist
  yet, it falls back to checking whether the transcript's first user message, or
  the UserPromptSubmit payload's prompt, starts with
  `<scheduled-task name="..." file="...SKILL.md">`. The result is cached per
  session (immutable) in `~/.claude/enforce/.sessionkind-cache.json`.
- `desktop_session_id(data)` / `deeplink(data)` — hook session_id → `local_<uuid>`
  → `claude://claude.ai/epitaxy/<local_...>`.

This store is a location the macOS app writes to, so it is accessed **read-only**.

## 2) claudlet venv discovery — `claudlet_paths.py`

A shared helper that `ensure-pet.py` and `patch-claudlet.py` use to find
claudlet's pipx venv. Venv python search order: env var `CLAUDI_CLAUDLET_PYTHON` →
the shebang of `claudlet-attach` on PATH/`~/.local/bin` →
`~/Library/Application Support/pipx/venvs/claudlet/bin/python` →
`~/.local/pipx/venvs/claudlet/bin/python`. It only locates paths and never imports
the claudlet package, so reinstalling/updating claudlet doesn't affect it.

## 3) claudlet source patch — `patch-claudlet.py`

claudlet's settings/hook extension points can't change the behavior below, so a
**minimal, idempotent patch** is applied to `pet.py`. (Re)apply with
`python3 ~/.claude/enforce/bin/patch-claudlet.py`, revert with `--revert` (backup
`pet.py.enforce-orig`), and check only whether it is applied with `--check`
(exit 0 = all applied, 1 = some not applied, writes nothing). For tests, pass the
target pet.py explicitly with `--pet-py <path>`. After applying, it verifies with
`py_compile` and, on failure, restores the original from memory immediately. If
any anchor is missing it **writes nothing** and aborts. If the installed claudlet
differs from the verified version (2.6.0), it only warns and keeps trying.

The injected code does not hardcode paths; at run time it computes the
enforce/monitor directories from `CLAUDE_CONFIG_DIR` (or `~/.claude` if unset).

- `[enforce-click-patch]` — calls `claudlet_click.open_attention()` at the start of
  `pet._activate_claude`. Pet click → jump to the session that needs attention
  (see 5); on failure, falls back to the original app-activate.
- `[enforce-noreply-patch]` — neutralizes `pet._await_reply` with an immediate
  `return` → the pet no longer relays a session's "last reply" into the speech
  bubble.
- `[enforce-notice-patch]` — adds a `{"cmd":"notice","text":...}` branch to
  `pet._handle_event`. **Display only**: shows a speech bubble and leaves nothing
  in askhistory/outbox/relay. `notify.py` sends this instead of `say`.
- `[enforce-noactivity-patch]` — makes the chat-window widget `timeline()` return
  `[]` → the transcript's raw text is no longer drawn in the "Session activity"
  tab.
- `[enforce-alerts-*]` (patch E) — repurposes the empty "Session activity" tab as
  the **"Alerts" tab**, rendering `~/.claude/monitor/notices.json` newest-first.
  Clicking a row → `open-link.sh <deep link>` if it has a `url`, or
  `focus_session.py <sid>` for CLI, and that row is set to `handled=true`. It is
  fully separate from the user conversation.
- `[enforce-alerts-slack]` (patch F) — at the bottom of the Alerts tab: a divider +
  a "Slack" subheading + up to 10 items (`slack-items.json`, by importance).
  Clicking an item → `open-link.sh` if it has a `url`.
- `[enforce-portrefresh-patch]` (patch G) — adds a 1-hour `QTimer` to
  `pet._init_socket` that rewrites the `.port` file. macOS `dirhelper` deletes files in
  `$TMPDIR` untouched for 3+ days; a long-running pet then lost its `.port`, so the pet
  was alive but `check.py` raised `pet_down`, and the launchd restart was blocked by the
  lock and couldn't fix it.

> Applying the patch only changes **`pet.py` on disk**. **A pet process that is
> already running holds the old code in memory, so you must stop the standalone pet
> and start it again** for the patch to take effect:
> `kill $(pgrep -f '[-]m claudlet')` then `launchctl kickstart gui/$(id -u)/com.claudi.pet`
> (or `claudlet-attach --standalone`).

### Reinstall checklist

After `pipx install --force ... claudlet && claudlet-install` (= updating
claudlet), restore two things:

1. **Restore the pet.py patch** — `python3 ~/.claude/enforce/bin/patch-claudlet.py`
   (because the installer overwrites `pet.py` with the original).
2. **Restart the pet** — `launchctl kickstart -k gui/$(id -u)/com.claudi.pet`
   (reloads the on-disk patch into memory).

## 4) Health check — `check.py`

Only on weekdays 09–20h, it checks the following against facts (if it can't tell,
it reports "no problem"):

- `slack_stale` — the Slack summary routine is **configured** (
  `~/.claude/monitor/slack-state.json` exists) **but** hasn't run for about 2+
  hours. If the state file is missing (routine not configured), it doesn't check.
- `pet_down` — the standalone pet isn't running. Only when the `standalone-pet`
  flag exists (info level, since launchd revives it within an hour at most).

`session-start-hook.py` (once, on a session's first prompt) and `launchd-check.py`
(launchd every hour, works even when the app is closed) both import this, and
they share the same 2-hour throttle so the same set of problems isn't announced
too often.

## 5) Click / notification → jump to session

`~/.claude/monitor/attention.json` = the single source for "which session to open
on click":
`{"open":"local_<uuid>", "cli_session":"<uuid>", "reason":"...", "ts":...}`.
Updated by: monitor-hook (automatically on needs-decision/failure events) and the
session-monitor skill (when it reports).

- **Pet click**: `claudlet_click.open_attention()` → attention.json → (if absent)
  the monitor session → (if absent) the pet's own session → `focus_session.go()`.
- **Notification click**: `notify.py` passes terminal-notifier
  `-execute "open-link.sh <deep link>"` (app) or
  `-execute "python3 focus_session.py <sid>"` (CLI).
- `focus_session.go(sid)`: app session = `open claude://claude.ai/epitaxy/<local>`,
  CLI session = pick the iTerm2 session / activate the terminal app using the
  terminal hint in events.jsonl (**best-effort**).

## 6) Notifications — `~/.claude/monitor/bin/notify.py`

- If terminal-notifier (`brew install terminal-notifier`) is present, only that is
  used (`-group claude-monitor`). Otherwise it falls back to osascript
  `display notification` (not clickable).
- To the pet it sends `{"cmd":"notice",...}` (display only, patch C) — only a
  speech bubble appears and nothing is left in session history/outbox/relay. If
  there's no `--session` pet, it broadcasts to every live `claudlet-*.port`
  (= the standalone pet).
- **notices.json upsert** — if `--session` (or `--key`) is given, that row of the
  Alerts-tab store is updated with this notification (one row per session; a new
  notification sets `handled=false`).
- Arguments: `--title --body --session --open-url URL --focus SID --no-say --no-notify`
  plus `--icon`, `--rec`, `--label`, `--key`, and `--handled <key>`.
- With `CLAUDI_NO_DESKTOP_NOTIFY=1`, the speech bubble and macOS notification are
  skipped (the notices record is kept).

## No session-attached pet mode

There used to be a hook wrapper (`claudlet-gate.py`) for a policy of "attach the pet
to one monitor session", but claudi only provides the **single standalone pet kept
alive by launchd** mode (that wrapper is not included). The pet's notifications and
click-to-jump work through `notify.py` (→ notices.json/notice) and `monitor-hook`
(→ attention.json), independent of claudlet's own hooks.

## Undo summary
- Stop keeping the standalone pet alive:
  `launchctl bootout gui/$(id -u)/com.claudi.pet` + kill the running pet with `kill $(pgrep -f '[-]m claudlet')`.
- Remove the pet patch: `python3 ~/.claude/enforce/bin/patch-claudlet.py --revert`
  (restart the running pet afterwards so the reverted code takes effect).
- End monitoring: `rm -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor/monitor-session"`.
