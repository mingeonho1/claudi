# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-19

First public release — the setup the author actually uses, released as-is.

### Added
- claudlet 2.6.0 pet patches A–F (Alerts tab, click-to-jump, quiet pet, Slack area) + `--check` / `--revert`
- Always-on standalone pet (LaunchAgents `com.claudi.pet`, `com.claudi.enforcer`)
- Session monitoring: hook (`monitor-hook.py`) → `events.jsonl` → `/session-monitor` skill → `notify.py`
- Session-start reminder (`session-start-hook.py`), health check (`check.py`)
- Output-language choice (`--lang en|ko`): first install writes `~/.claude/claudi/config.json` + `lang.md` and imports it into `~/.claude/CLAUDE.md`, so replies and hook/installer messages follow the chosen language
- Routine template: slack-digest (subagent on `haiku`)
- SwiftBar session-status plugin
- Installer (`install.sh` / `uninstall.sh`, idempotent · backups · dry-run), CI (ubuntu/macOS, Python 3.9/3.13)
- claudi never changes the user's model, effort, or status line
- English-first documentation (README + README.ko.md, INSTALL, CONTRIBUTING); code comments are bilingual (English + Korean), prompts/skills/docs English

### Moved out
- Fable orchestration mode, extra subagents, model/effort presets + status line, and company profiles (PatSol example) moved to [ai-agent](https://github.com/mingeonho1/ai-agent) before release; claudi links to them under "Related"
