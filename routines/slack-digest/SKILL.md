---
name: slack-digest
description: Every hour, picks up to 10 "Slack items I should look at now" by importance and saves them to slack-items.json (Slack section of the pet's Alerts tab). Includes FYI (1) items; sends a speech bubble + macOS notification only for newly appeared important items (4+)
---

> This is a **template**. Replace `{{SLACK_USER_ID}}`, `{{SLACK_HANDLE}}`, and `{{DISPLAY_NAME}}`
> with your own values and register it as a scheduled task (see `routines/README.md`). Paths
> default to `~/.claude` and can be changed with `CLAUDE_CONFIG_DIR`.

Cost-saving rule: do not perform this task yourself. Use the Agent tool to spawn one subagent with subagent_type "general-purpose", model "haiku", run_in_background false, and pass the full [Task] text below verbatim as its prompt. Call no other tools. When the subagent finishes, output its final one-line output as-is ("-" on success, or a one-line error if saving failed) and stop.

[Task]
You are the Slack classifier for {{DISPLAY_NAME}} (Slack user ID: {{SLACK_USER_ID}}, handle {{SLACK_HANDLE}}). Quietly pick out only "what {{DISPLAY_NAME}} should look at now" and save it. If Slack tools are not visible, search ToolSearch for "slack" to load them. Use only read tools on Slack — sending messages, replying, emoji reactions, and drafting are strictly forbidden. Write `summary` text and the notification body in the user's configured output language.

1. Read state:
   - Collection start time (window start) = now - 48 hours. Every run rescans the last 48 hours (no collection cursor is kept in the state file).
   - Previous items: run `python3 ~/.claude/monitor/bin/slack_items.py show` to read the items (each id, ts, importance, summary, url) in the existing slack-items.json. Use them to decide which ids "already existed" (used in step 5 merge and step 7 notification).

2. Collect (read-only; keep the number of searches at the existing level — mention/keyword searches + reading DM and group-DM channels): from messages in the last 48 hours (after now - 48 hours), gather "what {{DISPLAY_NAME}} should look at now". Set each search's/channel read's oldest/after (or equivalent time filter) to the unix ts of 48 hours ago — widen only the time range, do not add more searches.
   - Messages that directly mention {{DISPLAY_NAME}} (<@{{SLACK_USER_ID}}>)
   - DMs to {{DISPLAY_NAME}} / group DMs {{DISPLAY_NAME}} belongs to
   - New replies in threads where {{DISPLAY_NAME}} has posted
   - Messages whose body explicitly names "{{DISPLAY_NAME}}" or "{{SLACK_HANDLE}}"
   - In channels {{DISPLAY_NAME}} is a member of: @channel/@here/@everyone, or messages addressed to everyone including {{DISPLAY_NAME}} (e.g. "everyone", "all of you", "each of you" — in Korean workspaces also "각자/모두/전원/다들/여러분") that require an action (accept, configure, submit, confirm, attend, etc.) or require reading an announcement
   - Announcements/decisions that affect {{DISPLAY_NAME}}'s Jira tickets (e.g. ABC-123) or deadlines

3. Importance (1–5, 5 is most important):
   - 5 = a request, question, or decision directed at {{DISPLAY_NAME}} that {{DISPLAY_NAME}} has not yet answered
   - 4 = DMs and mentions
   - 3 = new replies in threads {{DISPLAY_NAME}} took part in
   - 2 = announcements/decisions affecting {{DISPLAY_NAME}}'s tickets (e.g. ABC-123) or deadlines
   - 1 = plain FYI — only announcements/decisions in channels {{DISPLAY_NAME}} is a member of that relate to {{DISPLAY_NAME}}'s tickets/work. Exclude chit-chat and announcements unrelated to {{DISPLAY_NAME}}.

4. Exclude: messages sent by {{DISPLAY_NAME}}, bot noise, plain chit-chat, one-on-one-style conversations unrelated to {{DISPLAY_NAME}}, and "already resolved" items. Resolution rule: if {{DISPLAY_NAME}} replied to (or handled) that message/thread after the message, treat it as resolved and exclude it. A thread counts as resolved if, after {{DISPLAY_NAME}}'s last post, there is no new request awaiting {{DISPLAY_NAME}}'s response.

5. Merge previous items (dedupe by id in slack-items.json):
   - Of the previous items read in step 1, keep those still unresolved ({{DISPLAY_NAME}} has not replied/handled them since) and whose ts is within 48 hours of now.
   - Drop the ones that are resolved or older than 48 hours.
   - If the same id is caught again this run, update it with the new content (never insert the same id twice).

6. Save (always): combine kept + new items and pass the JSON below on stdin to save it. The helper sorts (importance descending, then ts descending) and cuts to the top 10, so passing more than 10 is fine. Include importance 1 (FYI) items in the list too — do not drop them for being 1 (drop only step 4's exclusions: bot noise, already resolved, already answered by {{DISPLAY_NAME}}, chit-chat).
   Each item: id="<channel_id>:<message_ts>", importance=1–5, summary=one line within 90 characters, channel="#channel-name" ("DM" for DMs), from="sender display name", ts=message time (float), url=Slack permalink, reason=request|mention|dm|thread|decision|fyi.
   ```
   printf '%s' '{"items":[{"id":"CXXXXXXX:1699900000.001","importance":5,"summary":"Request to upload seminar slides (unanswered)","channel":"#general","from":"Colleague","ts":1699900000.001,"url":"https://your-workspace.slack.com/archives/CXXXXXXX/p1699900000001","reason":"request"}]}' | python3 ~/.claude/monitor/bin/slack_items.py set
   ```
   If there is nothing to look at, save only the kept items (or `{"items":[]}`). For backward compatibility, never call `notify.py --key slack` (no Slack row may appear in the Alerts tab's general list).
   - On a successful save, `slack_items.py set` **automatically records** the current time (ISO8601) as `last_run` in `~/.claude/monitor/slack-state.json` — do not edit the state file directly (enforce uses this last_run to tell whether the routine is alive).
   - Check set's exit code. If it is non-zero (invalid input JSON, etc.), the existing file is preserved and nothing was saved — in the final response (step 8), output `slack_items set failed: <one line of stderr>` instead of "-".

7. Speech bubble + macOS notification (conditional, at most once): send only if at least one item that is "new" this run (its id was not among step 1's previous items) has importance 4 or higher. Always call it without --key/--session (so no row appears in the Alerts tab list):
   ```
   python3 ~/.claude/monitor/bin/notify.py --title "Slack" --body "<summary of the single most important item, within 100 characters>"
   ```
   If no new important item (4+) appeared, send no notification.

8. Final response: if the save succeeded (set exit code 0), output just the single character "-". If set failed, output only that one-line error (`slack_items set failed: <stderr>`). (set stamps last_run in slack-state.json automatically, so there is no separate state-update step.)
