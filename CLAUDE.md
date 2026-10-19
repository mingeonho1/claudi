# Notes for Claude working in the claudi repo

- When the user says "install it", follow the [INSTALL.md](INSTALL.md) procedure.
- `claude/` mirrors `~/.claude/`. Don't hardcode paths; compute them relative to `CLAUDE_CONFIG_DIR` (default `~/.claude`).
- Scripts use the Python 3.9+ standard library only. Write hooks fail-open (a failure never blocks Claude Code) and finishing within 5 seconds.
- Tests: `python3 -m unittest discover -s tests -v`. Tests use temp directories only and never touch the real `~/.claude`, the pet, or the network.
- To test an install without touching your real environment: `CLAUDI_NO_LAUNCHCTL=1 ./install.sh --yes --home <tempdir> --no-launchd`.
- Never commit personal or company identifying info (names, emails, Slack/Jira/Notion IDs, internal domains, tokens). Keep company-specific setups out of this repo.

## Language

- Write all docs, prompts, and skills in **English** (the project is English-first).
- **Code comments are bilingual: English first, then Korean.**
- Route user-facing strings through the output-language layer — `t(en, ko)` — so they follow the user's `--lang` choice. Never hardcode user-facing Korean or English.
