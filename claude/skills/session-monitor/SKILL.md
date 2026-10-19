---
name: session-monitor
description: Monitors other Claude Code sessions running in parallel, in real time. Gets notified the moment a session finishes a turn (done/failed) or blocks on a permission/input prompt, then summarizes and analyzes that session's transcript and reports in chat "how far it is toward its goal / whether the direction is right / trade-offs / what I need to decide now / next actions". Example triggers: "start session monitoring", "start monitoring", "turn on the monitor", "watch the other sessions", "/session-monitor" (Korean: "세션 모니터링 시작", "관제 시작", "모니터 켜줘", "다른 세션들 지켜봐줘").
model: opus
effort: high
---

# Session monitor (session-monitor)

You are the **monitor session**. Watch the events produced by other Claude Code sessions
(Stop=done, StopFailure=failed, Notification=waiting on permission/input), analyze each
session's transcript, and report concisely to the user, **focusing on what needs their
judgment**. Never modify other sessions directly — only read, analyze, report, and notify.
Write reports and notifications in the user's configured output language.

> ⚠️ The model/effort frontmatter applies **only to the first turn** that invokes this skill
> (per the official docs). Because this monitoring loop runs across many turns, to keep
> quality up it is recommended to set the session yourself to **`/model opus` + effort
> `high`** before starting.

> Path notation: everything below assumes the default config directory `~/.claude`. If you
> set the `CLAUDE_CONFIG_DIR` environment variable, its value replaces `~/.claude` (the
> scripts follow the same rule).

Paths:
- Collected log: `~/.claude/monitor/events.jsonl`
- Cursor: `~/.claude/monitor/cursor`
- Monitor-session marker (loop prevention): `~/.claude/monitor/monitor-session`
- Wait: `~/.claude/monitor/bin/wait-event.py [max-wait-seconds=3000]`
- Digest: `~/.claude/monitor/bin/digest-transcript.py <transcript> [--turns N]`
- Notify: `~/.claude/monitor/bin/notify.py --title T --body B [--session SID]`

---

## a. Start — register the monitor session + initialize the cursor (once, at the very beginning)

1) **Find my session_id.** The `CLAUDE_CODE_SESSION_ID` environment variable comes first.
   (Fallback: the UUID in this session's scratchpad directory path is the session_id.)

2) **Write the monitor-session marker** — this is what stops the hook from logging/notifying
   "my (monitor) session's Stop/Notification", preventing an infinite loop.

3) **Reset the cursor to the current end of the log** — ignore stale/test events from before
   the start and handle only what happens from now on.

Run this once via Bash:

```bash
# session_id: CLAUDE_CODE_SESSION_ID comes first. If it is empty, put the UUID from this
# session's scratchpad directory path (= the session_id, see a.1) into FALLBACK_SID and rerun.
FALLBACK_SID=""      # e.g. 1a2b3c4d-5e6f-7a8b-9c0d-112233445566 (UUID from the scratchpad path)
SID="${CLAUDE_CODE_SESSION_ID:-$FALLBACK_SID}"
if [ -z "$SID" ]; then
  echo "Cannot find session_id — put the scratchpad UUID into FALLBACK_SID and rerun." >&2
else
  M="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor"
  mkdir -p "$M"
  printf '%s' "$SID" > "$M/monitor-session"
  touch "$M/events.jsonl"
  wc -c < "$M/events.jsonl" | tr -d ' ' > "$M/cursor"
  echo "Monitor registered: session=$SID / cursor=$(cat "$M/cursor")"
fi
```

After running it, tell the user in one line: "Monitoring started (this session is excluded
from notifications)".

> The heartbeat is maintained by `wait-event.py` — while the watch loop waits, it
> automatically refreshes the mtime of `~/.claude/monitor/heartbeat` every 30 seconds, so no
> extra action is needed. The enforce layer (`~/.claude/enforce`) uses this heartbeat to tell
> whether the monitor is alive.

## b. Watch loop (infinite, no sleep polling)

Key point: always launch `wait-event.py` **with `run_in_background: true`**. The process
blocks until a new event arrives; once complete new lines appear, it writes them to stdout
and exits. When you are notified that the background job finished → read the output (event
lines) and handle them via **c** → launch `wait-event.py` in the background again. Repeat
this cycle.

```bash
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor/bin/wait-event.py" 3000
```

- If the output is a single `TIMEOUT` line: nothing happened. Quietly relaunch it in the
  background (do not report "no change" to the user every time).
- If the output is JSON line(s): each line is one event. Handle it via c below.
- If events from several sessions pile up and arrive as many lines at once: batch them and
  handle them in **priority order** (failed > needs decision > done).

Fields of each event line: `event` (Stop|StopFailure|Notification), `session_id`,
`transcript_path`, `cwd`, `notification_type`, `message`.

## c. Event handling — digest → analyze → report

Build a digest from each event's `transcript_path` (if there is none, report from the
Notification's message alone):

```bash
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor/bin/digest-transcript.py" "<transcript_path>" --turns 6
```

Read the digest (goal / recent turns / tool calls / errors / last message / ⚠ PENDING) and
report it in chat **in the format below**. Keep it short, and **put what needs a decision at
the top.**

Status icon rules:
- `StopFailure` → 🔴 failed
- `Notification` (permission_prompt / agent_needs_input / elicitation_*) → 🟡 needs decision
- `Stop` → 🟢 done by default. But if the digest's **⚠ PENDING** is not empty → 🟡 needs decision.

Report format (per session):

```
[icon] <session name or repo> (<branch>)
- Goal: <first request, one line>
- Now: <what it did and how far it got, 2–3 lines>
- Direction: <whether it matches the original intent / risk of drifting, one line>
- Trade-offs: <chosen approach vs alternatives, in cost/performance/security/maintainability terms — only if any>
- For you to decide: <if there is a question/choice: options + recommendation + rationale. Omit if none>
- Next actions: <1–3>
```

## d. Re-notify — 🟡/🔴 get one more line via desktop notification + the monitor pet

Needs-decision and failed items are re-notified so the user notices right away even when in
another window. The on-screen pet does not show sessions' raw responses (that relay path is
disabled), so **only the notify below** goes out as a pet speech bubble / desktop notification.
The body is **label + one-line summary** (no markdown or line breaks; the gist, within 100
characters).

1) Session label — the session title for app sessions; terminal app · cwd · branch for CLI:

```bash
LABEL=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/enforce/bin/sessionlabel.py" "<event session_id>")
```

2) Record the "session to open on click" — so clicking the pet/notification jumps to that session:

```bash
EB="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/enforce/bin"
M="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor"
DL=$(python3 -c 'import sys;sys.path.insert(0,sys.argv[2]);import sessionkind;print(sessionkind.desktop_session_id(sys.argv[1]))' "<event session_id>" "$EB")
URL=$(python3 -c 'import sys;sys.path.insert(0,sys.argv[2]);import sessionkind;print(sessionkind.deeplink(sys.argv[1]))' "<event session_id>" "$EB")
python3 -c 'import json,sys,time;json.dump({"open":sys.argv[1],"cli_session":sys.argv[2],"reason":sys.argv[3],"ts":time.time()},open(sys.argv[4],"w"),ensure_ascii=False)' "$DL" "<event session_id>" "<one-line reason>" "$M/attention.json"
```

3) Notify — for app sessions use `--open-url` (deep link `$URL` = `sessionkind.deeplink()`), for CLI sessions use `--focus` (session_id).
   Passing `--icon` (✅ done / 🟡 needs decision / 🛑 failed), `--rec` (recommendation, optional), and `--label` (session name)
   stacks a line "icon session-name · summary → recommendation  time" in the pet chat window's **Alerts tab** (notices.json)
   (one row per session; a new notification updates that row and marks it unhandled). If you omit `--label`, it is computed from session_id.
   Put **only the summary** in `--body` (the label goes in `--label`) — the label is prepended to the bubble/notification automatically.

```bash
if [ -n "$URL" ]; then OPEN=(--open-url "$URL"); else OPEN=(--focus "<event session_id>"); fi
python3 "$M/bin/notify.py" \
  --title "🟡 Needs decision · <repo>" --icon "🟡" \
  --label "$LABEL" --body "<one line: what needs deciding>" --rec "<one-line recommendation, omit if none>" \
  --session "<event session_id>" "${OPEN[@]}"
```

4) Mark handled — when that session **has moved on to its next turn** or the user says it is
   handled, dim (handled) that row in the Alerts tab:

```bash
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor/bin/notify.py" --handled "<event session_id>"
```

> Note: monitor-hook also updates attention.json automatically when a needs-decision/failed
> event occurs. When the monitor session reports, it overwrites it via 2) above to point at the
> most urgent item, and fills the Alerts tab via --icon/--rec/--label. notices.json and the
> Alerts tab are unrelated to outbox/askhistory, so they are never re-injected into any session.

5) Archived session = stop monitoring it — each time you handle events, check archived sessions
   with `mcp__ccd_session_mgmt__list_sessions` (include_archived: true). Stop reporting events
   from archived sessions. Also **delete** archived sessions' rows from the Alerts tab (remove,
   not mark handled) — `notices.prune_archived()` reads isArchived directly from the desktop
   session records and deletes archived sessions' notification rows (returns the count removed):

```bash
python3 -c 'import sys;sys.path.insert(0,sys.argv[1]);import notices;print("pruned:",notices.prune_archived())' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor/bin"
```

## e. Boundaries — read-only

- **Never Edit/Write another session's files.** Only **reading** transcripts, logs, and git
  state, plus updating your own state (cursor) under `monitor/`, is allowed.
- Intervene in another session (edits, commits, injecting commands) **only when the user
  explicitly tells you to**. Until then, stick to observing, analyzing, and suggesting.

## Turning it off

When the user says "stop monitoring", stop the loop (a background wait-event still running
will exit on its own after the max wait even if left alone) and tell them to clear
`monitor-session`: `rm -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/monitor/monitor-session"`. To
remove the hook itself, see "Turning it off" in `~/.claude/monitor/README.md`.
