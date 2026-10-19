# routines — scheduled task templates

A collection of **Scheduled Task templates** used by claudi. Each folder's `SKILL.md` is
not a ready-to-use final version but a template: fill in your own details and register it
with the **Scheduled Tasks** feature of the Claude desktop app.

## slack-digest

Every hour, picks up to 10 "Slack items I should look at now", ranked by importance (1–5),
and saves them to `~/.claude/monitor/slack-items.json`. That list shows up as-is in the
Slack section of the pet chat window's **Alerts tab** (enforce patch F) and in the SwiftBar
plugin. It sends a speech bubble + macOS notification once, only when a new important item
(importance 4 or higher) appears.

- **Cost design**: the scheduled task itself does nothing; it spawns a single subagent on
  the `haiku` model to do the actual classification (cheap). It uses **read-only tools**
  on Slack (no sending, replying, or reacting).
- **Requirements**:
  - The **Scheduled Tasks** feature of the Claude desktop app.
  - A **Slack connector** (read permission). Without the connector, the subagent cannot
    find it via ToolSearch and saves nothing.
  - (Optional) To see it in the pet's Alerts tab, the enforce patch (`patch-claudlet.py`)
    must be applied.
- **Recommended cron**: `0 8-21 * * 1-5` (weekdays, on the hour, 08–21).

### How to register

Replace the placeholders in `routines/slack-digest/SKILL.md` with your own values and
register it as a scheduled task. You can edit it yourself or ask Claude to do it. Example:

> "Fill in routines/slack-digest/SKILL.md with my details and register it as an hourly
>  scheduled task. My Slack user ID is UXXXXXXX, my handle is myhandle, and my display name
>  is Jane Doe."

Placeholders to fill:

| Placeholder | Meaning | Example |
|---|---|---|
| `{{SLACK_USER_ID}}` | Your Slack user ID | `UXXXXXXXX` |
| `{{SLACK_HANDLE}}` | Your Slack handle | `myhandle` |
| `{{DISPLAY_NAME}}` | The name people call you | `Jane Doe` |

> You can get your Slack user ID from your Slack profile → "More (…)" → "Copy member ID".

After registering, `~/.claude/monitor/slack-state.json` is created, and enforce's `check.py`
recognizes the routine as "configured" and reminds you (weekdays 09–20) if it has not run
for too long.
