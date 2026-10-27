# claudi install guide — a document Claude reads and runs

> This document is the **procedure Claude Code follows** when the user says "Read INSTALL.md and install claudi".
> A human can read it too. Run every command from this repo's root.

## Principles

- Respect the user's existing setup. The installer backs up every file it changes as `.bak-claudi-<time>`, but **it shows what will change and gets confirmation before changing anything.**
- Ask the user everything at once, in the "Questions" step below. Otherwise proceed without asking.
- Never take secrets such as tokens, passwords, or webhook URLs. If one is needed, only name the environment variable to set.
- On failure, show the output as-is and point out the cause. Never claim success by guessing.

## 1. Preflight

Run the following and review the results.

```bash
uname -s                      # must be Darwin for the pet/launchd to install (on Linux, hooks/monitoring only)
python3 --version             # 3.9 or newer
command -v pipx || echo "no pipx"
command -v claudlet-attach && claudlet-version || echo "no claudlet"
command -v terminal-notifier || echo "no terminal-notifier (optional)"
python3 - <<'EOF'
import json, os, pathlib
p = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home()/".claude")/"settings.json"
s = json.loads(p.read_text()) if p.exists() else {}
print("hooks:", list((s.get("hooks") or {}).keys()))
EOF
```

- If pipx is missing, propose `brew install pipx && pipx ensurepath` (install only after the user agrees).
- If claudlet is already present and not version 2.6.0, include that fact in the Questions step.

## 2. Questions (all at once)

Use an AskUserQuestion-style tool if one is available; otherwise ask in chat. Parentheses show the recommended default.

1. **Output language**: Which output language do you want, English or Korean? (`en`) — this sets Claude's replies and hook/notification/installer messages. Maps to `--lang en` or `--lang ko`.
2. **Always-on pet**: Launch the pet automatically at every login? (yes) — No → `--no-launchd`.
3. Only if claudlet is not 2.6.0: Reinstall it as 2.6.0? (yes → `--force-claudlet`). If no, warn that patching may fail.

## 3. Preview

Combine the options from the answers, run a dry-run first, and show the user a summary.

```bash
./install.sh --dry-run --yes --lang <en|ko> [--no-launchd] [--force-claudlet]
```

Summarize what will change (number of files to copy, files to back up, hooks to add to settings.json) in 3–5 lines and confirm once: "Install with these?"

## 4. Install

Run the same options with `--dry-run` removed.

```bash
./install.sh --yes --lang <en|ko> [other options...]
```

## 5. Verify

Run all of the following and report the results as a table.

```bash
python3 ~/.claude/enforce/bin/patch-claudlet.py --check && echo "patch OK"     # if on macOS + claudlet installed
python3 - <<'EOF'
import json, pathlib, os
d = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home()/".claude")
s = json.loads((d/"settings.json").read_text())
for ev, groups in (s.get("hooks") or {}).items():
    for g in groups:
        for h in g.get("hooks", []):
            if any(k in h.get("command", "") for k in ("monitor-hook", "session-start-hook")):
                print(ev, "->", h["command"])
EOF
echo '{"session_id":"claudi-selftest","hook_event_name":"Stop","transcript_path":"/dev/null","cwd":"'"$PWD"'"}' \
  | python3 ~/.claude/monitor/bin/monitor-hook.py && tail -n 1 ~/.claude/monitor/events.jsonl
python3 ~/.claude/monitor/bin/notify.py --title "claudi" --body "Install complete! Alerts tab test" --session claudi-selftest
```

After the last command, if the pet is up you should see an "Install complete" line in a speech bubble and the Alerts tab. If there's no pet, tell the user to launch it with `claudlet-attach --standalone`.
Mark the test alert handled with `python3 ~/.claude/monitor/bin/notify.py --handled claudi-selftest`.

## 6. Wrap-up (for the user)

Many users only hand Claude this repo's link and never read the docs, so **always end the install by telling the user the following**, in their language. Don't skip it even if everything succeeded.

1. **The pet alone does nothing yet.** The Alerts tab stays empty until a monitor session is running: open one dedicated session (Claude Code CLI or the desktop app's Code tab) and run `/session-monitor`. That session is excluded from its own notifications.
2. **Re-run after restarts.** The monitor registration lives in that one session. After restarting that session or the app, run `/session-monitor` again.
3. **Reopen existing sessions.** Hooks and the output language apply only to Claude Code sessions opened after install (only partly in the current one). Sessions opened before install send no events to the monitor.
4. **Recommended monitor settings:** `/model opus` + effort `high` before starting `/session-monitor` (the skill's frontmatter applies only to its first turn).
5. **If the Alerts tab is empty:** check that `~/.claude/monitor/monitor-session` exists and `~/.claude/monitor/heartbeat` was modified within the last few minutes; if not, run `/session-monitor`.
6. Optional: the Slack digest routine — see `routines/README.md` (needs the desktop app's scheduled tasks + the Slack connector).
7. To revert: `./uninstall.sh`

## If something goes wrong

- Patch failure (no anchor): the claudlet version is probably different. Check `claudlet-version`, then propose reinstalling 2.6.0 with `--force-claudlet`.
- `pipx install` failure: check network/Python version. If the user prefers, install the rest without the pet via `--no-claudlet`.
- settings.json parse failure: the installer does not overwrite broken JSON. Show the file and fix it together with the user.
