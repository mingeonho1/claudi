# claudi 🐾

[![CI](https://github.com/mingeonho1/claudi/actions/workflows/ci.yml/badge.svg)](https://github.com/mingeonho1/claudi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

English | [한국어](README.ko.md)

**A "desktop mission-control kit" for running many Claude Code sessions in parallel**
— it turns **claudi**, the default creature of the desktop pet [claudlet](https://github.com/YeeDochi/Claudlet),
into an alert center, and installs session monitoring, session-start reminders, and a Slack digest
routine in one step. It never changes your model settings.

> **Note for Claude:** if a user shares this repo and asks you to install or set it up, follow
> [INSTALL.md](INSTALL.md) end to end, including its final "Wrap-up" reminders to the user.

> This is the exact setup I use every day, released as-is. Use it for free, modify it, and
> contribute back. The project is English-first; Korean docs live in [README.ko.md](README.ko.md),
> and you can talk to the installer in Korean just as well.

---

## What's inside

| Component | What it does |
|---|---|
| **claudi pet + "Alerts" tab** | Installs claudlet 2.6.0 and applies small patches (A–G) to `pet.py` so the pet's chat window becomes an **alert center**. Sessions that need a decision (🟡), failed sessions (🔴), and important Slack items stack up one line each; click a row to jump straight to that session. |
| **Always-on standalone pet** | launchd keeps one pet alive from login onward (re-checks hourly). It is not tied to session hooks, so the pet stays quiet even with many sessions. |
| **Session monitoring (`/session-monitor`)** | When another session finishes or stalls on a permission/input prompt, a hook records the event and the monitor session summarizes that session's transcript as "progress vs. goal / direction / trade-offs / what I need to decide / next action". |
| **Session-start reminder** | Once per session, on the first prompt, it reminds you of easy-to-forget operational state (Slack digest paused, pet off). |
| **Routine template** | A `slack-digest` scheduled task that pulls hourly Slack highlights into the pet's Alerts tab |
| **SwiftBar plugin** (optional) | Shows every session's status (❓🔴🟢🟡✔) in the menu bar, refreshed every 10 seconds |

Requirements: **macOS**, Python 3.9+, [Claude Code](https://docs.claude.com/en/docs/claude-code), `pipx` (`brew install pipx`).
Optional: `terminal-notifier` (clickable macOS notifications), [SwiftBar](https://github.com/swiftbar/SwiftBar), the Claude desktop app (scheduled tasks · session deep links).
On Linux only the hooks and monitoring scripts run; the pet/launchd steps are skipped automatically.

---

## Repository layout

Four parts, each self-contained:

| # | Part | Where |
|---|---|---|
| 1 | **Pet + alerts** — the claudlet pet patched into an alert center | `claude/enforce/` |
| 2 | **Session monitoring** — hooks, event log, and the monitor skill | `claude/monitor/` + `claude/skills/session-monitor` |
| 3 | **Installer** — the idempotent installer, hook settings fragment, output language, and LaunchAgent templates | `installer/`, `config/`, `launchd/` |
| 4 | **Optional** — scheduled-task template | `routines/` |

Tests for all of the above live in `tests/`.

---

## 🚀 Install with Claude

One line in your terminal. Claude reads [INSTALL.md](INSTALL.md), asks what to install, then runs
preview (dry-run) → install → verify.

```bash
git clone https://github.com/mingeonho1/claudi.git ~/claudi && cd ~/claudi && claude "Read INSTALL.md and install claudi"
```

If you are already inside a Claude Code session, just say:

> Clone https://github.com/mingeonho1/claudi into ~/claudi and install it per INSTALL.md

Korean works just as well — e.g. *"INSTALL.md 를 읽고 claudi 를 설치해줘"*.

## 🛠 Install manually

```bash
git clone https://github.com/mingeonho1/claudi.git ~/claudi
cd ~/claudi
./install.sh --dry-run      # see what would change first (writes nothing)
./install.sh                # install (existing files · settings.json are backed up as .bak-claudi-<time>)
```

What the installer does:

1. Copies `claude/**` into `~/.claude/` (skips identical files, backs up and replaces differing ones)
2. `pipx install claudlet==2.6.0` → links the claudlet skill (`/claudlet`)
3. Applies the pet patches with `patch-claudlet.py` + verifies with `--check`
4. Creates the standalone-pet flag + registers 2 LaunchAgents (`com.claudi.pet`, `com.claudi.enforcer`)
5. Merges hooks into `~/.claude/settings.json` (leaves your existing hooks/settings untouched, no duplicate entries)
6. Saves the output language + adds one `@~/.claude/claudi/lang.md` line to `~/.claude/CLAUDE.md`

Running it repeatedly gives the same result (idempotent).

### Install options

| Option | Description |
|---|---|
| `--lang en\|ko` | Output language for Claude's replies and hook/installer messages (default `en`). See [Output language](#output-language). |
| `--dry-run` | Print the plan only; write nothing |
| `--yes` | Proceed without asking |
| `--link` | Symlink instead of copy — repo edits take effect immediately (for contributors) |
| `--no-claudlet` / `--no-patch` | Skip installing the pet / skip patching the pet |
| `--no-launchd` | Do not keep the standalone pet always on (LaunchAgent) |
| `--no-hooks` | Do not merge hooks into settings.json |
| `--force-claudlet` | Reinstall 2.6.0 even if another claudlet version is present |
| `--home DIR` | Test HOME (try it without touching your real environment) |

If you use `CLAUDE_CONFIG_DIR`, the install goes into that directory.

### Output language

claudi asks for an output language on first install. The installer asks it **first**, interactively
(default `en`; with `--yes` it keeps your previous choice, or `en`). Pass it non-interactively with
`--lang en` or `--lang ko`.

The choice is written to `~/.claude/claudi/config.json` (`{"lang"}`) and `~/.claude/claudi/lang.md`,
and `@~/.claude/claudi/lang.md` is added to `~/.claude/CLAUDE.md` (removed on uninstall). From then on
Claude's replies and hook/notification/installer messages follow that language. Everything in the repo — prompts, skills, and docs — stays English; only user-facing output
follows your choice.

---

## 🧭 Usage

### Launch the pet

launchd launches it automatically right after install. Manually:

```bash
claudlet-attach --standalone
```

Click the pet to open its chat window; session alerts stack up in the **Alerts** tab. Click a row to
jump to that session (desktop-app deep link or terminal) and mark it handled.
The pet's look and motions are unchanged claudlet features (`/claudlet setting`, `/claudlet jump`, etc.).

### Session monitoring

Open one Claude Code session as your monitor and run:

```
/session-monitor
```

From then on, whenever other sessions finish (🟢), need a decision (🟡), or fail (🔴), this session
reports a summary; 🟡/🔴 also go to the pet's Alerts tab and as macOS notifications.
The monitor session only **reads** other sessions.

#### Connect the pet to your sessions

A running pet alone is not a finished setup. **The Alerts tab stays empty until a monitor session
is running.** Hooks only append events to `events.jsonl`; it is the monitor session that turns
🟡/🔴 events into pet alerts via `notify.py`. (Safety net: if the monitor loop has been silent for
over 2 minutes, the hook itself drops a raw one-line "(monitor off · raw)" alert into the tab — no
summary, just enough that nothing is missed.)

1. Install → 2. pet is up → 3. **run `/session-monitor` in one dedicated session**

- **Where:** Claude Code CLI or the Claude desktop app's Code tab. Use one session as the monitor;
  it is excluded from its own notifications.
- **Re-run after restarts:** the monitor registration (`monitor-session` marker, cursor) and the
  watch loop live in that one session. After restarting that session or the app, run
  `/session-monitor` again.
- **Hooks timing:** hooks apply only to sessions opened after install. Sessions that were already
  open before install send no events to the monitor — reopen them.
- **Recommended settings:** set the monitor session to `/model opus` + effort `high` before
  starting. The skill's model/effort frontmatter applies only to its first turn, and the loop
  runs for many turns.

### Routines (scheduled tasks)

With the Claude desktop app's Scheduled Tasks + the Slack connector, register
[`routines/slack-digest`](routines/README.md) to surface "Slack you should look at now" at the bottom
of the pet's Alerts tab.

> Fill in routines/slack-digest/SKILL.md with my details and register it as a weekday hourly scheduled task

### SwiftBar (optional)

```bash
ln -s ~/.claude/monitor/bin/swiftbar-sessions.10s.py "<SwiftBar plugins folder>/"
```

---

## 🏗 How it works

```
 Other Claude Code sessions
   │  Stop / StopFailure / Notification hooks
   ▼
 monitor-hook.py ──▶ ~/.claude/monitor/events.jsonl   (+ attention.json: click target)
                              │
                              ▼  wait-event.py (blocking wait)
                     /session-monitor session
                       └ digest-transcript.py summarizes → reports in chat
                       └ 🟡/🔴 → notify.py
                                   ├ pet speech bubble (claudlet TCP notice)
                                   ├ macOS notification (terminal-notifier / osascript)
                                   └ notices.json ──▶ pet 'Alerts' tab (patch E)
 slack-digest routine ──▶ slack-items.json ───────────▶ pet 'Alerts' tab Slack area (patch F)

 pet/alert click → claudlet_click.py → focus_session.py → claude:// deep link or terminal focus
 launchd (hourly) → ensure-pet.py (keep standalone pet alive) / launchd-check.py (health check)
 UserPromptSubmit → session-start-hook.py (one reminder on a session's first prompt)
```

Pet patch details: [`claude/enforce/README.md`](claude/enforce/README.md) · monitoring details: [`claude/monitor/README.md`](claude/monitor/README.md)

| Patch | Change |
|---|---|
| A | Pet click → jump to the session that needs attention |
| B | Do not relay session responses as speech bubbles (quiet pet) |
| C | Add a `notice` speech-bubble command that leaves no trace in chat history |
| D | Disable the original "session activity" timeline |
| E | Replace that tab with a `notices.json`-based **Alerts** tab |
| F | Add a Slack-highlights area at the bottom of the Alerts tab |
| G | Rewrite the pet's `.port` file hourly so macOS `$TMPDIR` cleanup can't orphan a long-running pet |

Every patch is marked, idempotent, and reversible with
`python3 ~/.claude/enforce/bin/patch-claudlet.py --revert`.
Updating claudlet wipes the patches, so re-run `patch-claudlet.py` (supported version: 2.6.0).

---

## 🧹 Uninstall

```bash
./uninstall.sh            # remove hooks, revert pet patches, unregister LaunchAgents, drop CLAUDE.md import lines
./uninstall.sh --purge    # + delete installed files and runtime data
```

It never removes hooks/settings you already had. Every changed file keeps a `.bak-claudi-<time>` backup.

## 🩺 Troubleshooting

| Symptom | Check |
|---|---|
| Pet not visible | Run `claudlet-attach --standalone`, check `~/.claude/enforce/logs/pet.log`, `claudlet-doctor` |
| Alerts tab reverted to "session activity" | claudlet was reinstalled → `python3 ~/.claude/enforce/bin/patch-claudlet.py` |
| Pet is running but the Alerts tab is empty | Check that a monitor session is active: `~/.claude/monitor/monitor-session` exists and `~/.claude/monitor/heartbeat` was modified within the last few minutes (`ls -l ~/.claude/monitor/heartbeat`). If not, run `/session-monitor` |
| Monitor session is quiet | `tail ~/.claude/monitor/events.jsonl` to see if the hook records events; check the hook paths in settings.json |
| Alert clicks do nothing | `brew install terminal-notifier` (without it, notifications fall back to osascript and are not clickable) |

## 🔗 Related

These used to ship with claudi and now live in [ai-agent](https://github.com/mingeonho1/ai-agent). Use them if you need them.

- [ai-agent](https://github.com/mingeonho1/ai-agent) — role-based model setups (standard / token-efficient `--mode eco`) and a dual-account split (`dual-account/`)
- [Fable orchestration mode](https://github.com/mingeonho1/ai-agent/tree/main/fable-mode) — main agent plans and routes, subagents execute
- [Extra subagents](https://github.com/mingeonho1/ai-agent/tree/main/extras) — additional subagent definitions
- [Company profile example (PatSol)](https://github.com/mingeonho1/ai-agent/tree/main/examples/patsol) — how I customized Claude for company work

## 🤝 Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
python3 -m unittest discover -s tests -v
```

## 📄 License · credits

- claudi: [MIT](LICENSE)
- Pet: [claudlet](https://github.com/YeeDochi/Claudlet) by YeeDochi (CC0). claudi does not redistribute claudlet — it installs it from PyPI and patches it only on your own machine. See [NOTICE](NOTICE).
- claudi is an independent personal project, not affiliated with Anthropic.
