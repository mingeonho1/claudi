# Contributing

Thanks for your interest in claudi. Bug reports, doc fixes, feature ideas, and PRs are all welcome.
The project is English-first — please write issues, PRs, docs, prompts, and skills in English.

## Development setup

```bash
git clone https://github.com/mingeonho1/claudi.git
cd claudi
python3 -m unittest discover -s tests -v
```

- The only runtime dependency is the **Python 3.9+ standard library**. As a rule, don't add new dependencies.
- The pet (claudlet), launchd, and deep-link integrations are macOS-only. Unit tests must still pass on Linux.
- To try things without touching your real `~/.claude`, use a temp directory.

```bash
CLAUDI_NO_LAUNCHCTL=1 ./install.sh --yes --home /tmp/claudi-home --no-launchd
```

- To iterate on repo files and see changes immediately, install with `--link` (symlink instead of copy).

## Repo conventions

- `claude/` mirrors `~/.claude/`. The installer copies (or links) this tree as-is.
- Never put runtime data (`events.jsonl`, `notices.json`, logs, state files) in the repo.
- Don't hardcode paths; compute them relative to `CLAUDE_CONFIG_DIR` (default `~/.claude`).
- Write hook scripts fail-open, so a failure never blocks Claude Code. The hook timeout is 5 seconds.
- Write docs, prompts, and skills in English. **Code comments are bilingual: English first, then Korean.**
- Route user-facing strings through the output-language layer (`t(en, ko)`) so they follow the user's language choice. Don't hardcode user-facing Korean or English in code.
- Never commit personal or company info (names, emails, Slack/Jira/Notion IDs, internal domains).

## When upstream claudlet changes version

`patch-claudlet.py` finds specific strings (anchors) in claudlet's `pet.py` and patches them. To support a new claudlet version:

1. Grab the new wheel and verify with `CLAUDI_PRISTINE_PET_PY=<path>/pet.py python3 -m unittest tests.test_patch_claudlet -v`
2. If an anchor broke, fix the patch, and submit it together with the version the installer pins (`claudlet==2.6.0`) and the README table.

## PR checklist

- [ ] `python3 -m unittest discover -s tests -v` passes
- [ ] New behavior has tests
- [ ] User-facing changes are reflected in README / CHANGELOG
- [ ] No personal or company identifying info

## Code of conduct

Be respectful. Aggressive or discriminatory behavior may be removed from issues/PRs.

## License

By contributing, you agree that your code is distributed under this project's [MIT License](LICENSE).
