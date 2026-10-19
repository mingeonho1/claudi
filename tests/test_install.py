#!/usr/bin/env python3
"""claudi 설치기 테스트 (stdlib unittest, 임시 디렉터리만 사용).

네트워크·launchctl 없음: CLAUDI_NO_LAUNCHCTL=1 + `--home <tmp> --no-claudlet
--no-patch` 로 격리한다. launchd plist 검증은 macOS 에서만 수행한다.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL_PY = REPO / "installer" / "install.py"


def run_cli(args, env_extra=None, input_text=None):
    env = dict(os.environ)
    env["CLAUDI_NO_LAUNCHCTL"] = "1"
    env.pop("CLAUDE_CONFIG_DIR", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(INSTALL_PY)] + args,
        capture_output=True, text=True, env=env, input=input_text)


def all_hook_commands(settings):
    cmds = []
    for event, groups in settings.get("hooks", {}).items():
        for group in groups:
            for h in group.get("hooks", []):
                if "command" in h:
                    cmds.append(h["command"])
    return cmds


class InstallTests(unittest.TestCase):
    def setUp(self):
        # resolve() 로 /var → /private/var 심링크를 미리 정규화해 경로 문자열 비교를
        # 안정화한다 (설치기는 --home 을 resolve 하므로 일치시켜야 한다).
        self.home = Path(tempfile.mkdtemp(prefix="claudi-test-")).resolve()
        self.cfg = self.home / ".claude"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def install(self, *extra):
        args = ["install", "--yes", "--home", str(self.home),
                "--no-claudlet", "--no-patch"] + list(extra)
        r = run_cli(args)
        self.assertEqual(r.returncode, 0,
                         "install failed:\n%s\n%s" % (r.stdout, r.stderr))
        return r

    # -- 1. fresh install ---------------------------------------------
    def test_fresh_install(self):
        self.install()
        cfg = self.cfg
        # files copied
        self.assertTrue((cfg / "enforce" / "bin" / "session-start-hook.py").is_file())
        self.assertTrue((cfg / "monitor" / "bin" / "monitor-hook.py").is_file())

        # claudi never touches the user's model / statusLine
        settings = json.loads((cfg / "settings.json").read_text())
        for key in ("model", "effortLevel", "modelSettings", "statusLine"):
            self.assertNotIn(key, settings)

        # both hook components, with absolute temp config paths
        cmds = all_hook_commands(settings)
        cfgstr = str(cfg)
        for marker in ("enforce/bin/session-start-hook.py",
                       "monitor/bin/monitor-hook.py"):
            self.assertTrue(
                any(marker in c and cfgstr in c for c in cmds),
                "missing hook with abs path for %s in %s" % (marker, cmds))
        self.assertFalse(any("{{CLAUDE_DIR}}" in c for c in cmds))

        # CLAUDE.md output-language import line
        self.assertIn("@~/.claude/claudi/lang.md",
                      (cfg / "CLAUDE.md").read_text())

        # manifest present
        self.assertTrue((cfg / ".claudi-manifest.json").is_file())

        # plists only on macOS (launchd auto-skipped elsewhere)
        if platform.system() == "Darwin":
            la = self.home / "Library" / "LaunchAgents"
            for label in ("com.claudi.pet", "com.claudi.enforcer"):
                pl = la / (label + ".plist")
                self.assertTrue(pl.is_file(), "missing plist %s" % pl)
                content = pl.read_text()
                self.assertNotIn("{{", content)
                self.assertIn(label, content)
                self.assertIn(str(cfg), content)
                if shutil.which("plutil"):
                    pr = subprocess.run(["plutil", "-lint", str(pl)],
                                        capture_output=True, text=True)
                    self.assertEqual(pr.returncode, 0,
                                     "plutil invalid: %s" % (pr.stdout + pr.stderr))

    # -- 2. preserve + idempotent -------------------------------------
    def test_preserve_and_idempotent(self):
        self.cfg.mkdir(parents=True)
        pre = {
            "myCustomKey": {"a": 1, "b": [1, 2, 3]},
            "theme": "dark",
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash",
                     "hooks": [{"type": "command", "command": "echo user-hook"}]}
                ]
            },
        }
        (self.cfg / "settings.json").write_text(json.dumps(pre))

        self.install()
        self.install()  # second run must be a no-op for hooks

        settings = json.loads((self.cfg / "settings.json").read_text())
        # unknown keys preserved
        self.assertEqual(settings["myCustomKey"], {"a": 1, "b": [1, 2, 3]})
        self.assertEqual(settings["theme"], "dark")

        cmds = all_hook_commands(settings)
        self.assertIn("echo user-hook", cmds)

        # no duplicate claudi hooks after two runs
        for marker in ("session-start-hook.py",
                       "monitor-hook.py"):
            n = sum(marker in c for c in cmds)
            if marker == "monitor-hook.py":
                self.assertEqual(n, 3, "monitor-hook should appear in 3 events")
            else:
                self.assertEqual(n, 1, "duplicate claudi hook: %s x%d" % (marker, n))

        # user PreToolUse hook untouched
        pre_cmds = [h["command"]
                    for g in settings["hooks"]["PreToolUse"]
                    for h in g["hooks"]]
        self.assertEqual(pre_cmds, ["echo user-hook"])

    # -- 2b. existing model is never changed --------------------------
    def test_keeps_user_model(self):
        self.cfg.mkdir(parents=True)
        (self.cfg / "settings.json").write_text(json.dumps({"model": "claude-opus-4-1"}))
        self.install()
        settings = json.loads((self.cfg / "settings.json").read_text())
        self.assertEqual(settings["model"], "claude-opus-4-1")
        self.assertNotIn("effortLevel", settings)
        # hooks still applied
        cmds = all_hook_commands(settings)
        self.assertTrue(any("monitor-hook.py" in c for c in cmds))

    # -- 2c. --dry-run writes nothing ---------------------------------
    def test_home_wins_over_claude_config_dir(self):
        # A test install with --home must never follow CLAUDE_CONFIG_DIR to a real config.
        # --home 을 준 테스트 설치는 CLAUDE_CONFIG_DIR 을 따라 실제 설정으로 가면 안 된다.
        decoy = self.home / "real-config"
        r = run_cli(["install", "--yes", "--home", str(self.home),
                     "--no-claudlet", "--no-patch"],
                    env_extra={"CLAUDE_CONFIG_DIR": str(decoy)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.cfg / "settings.json").is_file())
        self.assertFalse(decoy.exists())

    def test_dry_run_writes_nothing(self):
        r = run_cli(["install", "--yes", "--dry-run", "--home", str(self.home),
                     "--no-claudlet", "--no-patch"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.cfg.exists(),
                         "dry-run created config dir: %s" % self.cfg)

    # -- 3. uninstall --------------------------------------------------
    def test_uninstall(self):
        self.cfg.mkdir(parents=True)
        (self.cfg / "settings.json").write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash",
                     "hooks": [{"type": "command", "command": "echo user-hook"}]}
                ]
            }
        }))
        self.install()

        r = run_cli(["uninstall", "--yes", "--home", str(self.home), "--no-patch"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        settings = json.loads((self.cfg / "settings.json").read_text())
        cmds = all_hook_commands(settings)
        for marker in ("session-start-hook.py", "monitor-hook.py"):
            self.assertFalse(any(marker in c for c in cmds),
                             "claudi hook not removed: %s" % marker)
        self.assertIn("echo user-hook", cmds)

        # standalone-pet flag removed
        self.assertFalse((self.cfg / "enforce" / "standalone-pet").exists())

    # -- 7. broken settings.json aborts before ANY write --------------
    def test_broken_settings_aborts_before_write(self):
        self.cfg.mkdir(parents=True)
        broken = "{ not valid json "
        (self.cfg / "settings.json").write_text(broken)
        r = run_cli(["install", "--yes", "--home", str(self.home),
                     "--no-claudlet", "--no-patch"])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        # preflight aborts before copy_tree / flags / launchd / manifest
        self.assertFalse((self.cfg / "enforce" / "bin" / "check.py").exists(),
                         "copied files despite broken settings.json")
        self.assertFalse((self.cfg / ".claudi-manifest.json").exists())
        self.assertFalse((self.cfg / "enforce" / "standalone-pet").exists())
        # the broken file itself is untouched (no overwrite, no backup)
        self.assertEqual((self.cfg / "settings.json").read_text(), broken)
        self.assertEqual(list(self.cfg.glob("settings.json.bak-*")), [])

    # -- 9. --link must not chmod the repo source files ---------------
    def test_link_mode_does_not_chmod_repo(self):
        src_root = REPO / "claude"
        before = {}
        for p in src_root.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                before[p] = p.stat().st_mode
        self.assertTrue(before, "no source files found under claude/")
        self.install("--link")
        changed = [str(p) for p in before if p.stat().st_mode != before[p]]
        self.assertEqual(changed, [],
                         "link install changed repo file modes: %s" % changed)
        # sanity: link mode really linked into the repo
        linked = self.cfg / "enforce" / "bin" / "check.py"
        self.assertTrue(linked.is_symlink())

    # -- 12. output language ------------------------------------------
    def _lang_config(self):
        return json.loads((self.cfg / "claudi" / "config.json").read_text())

    def test_lang_ko_writes_config_langmd_and_import(self):
        # --lang ko writes config.json {"lang":"ko"} + a Korean lang.md, and the
        # CLAUDE.md import line so Claude replies in Korean.
        self.install("--lang", "ko")
        self.assertEqual(self._lang_config()["lang"], "ko")
        langmd = (self.cfg / "claudi" / "lang.md").read_text()
        self.assertIn("Korean", langmd)
        self.assertIn("한국어", langmd)
        self.assertIn("@~/.claude/claudi/lang.md",
                      (self.cfg / "CLAUDE.md").read_text())
        # tracked in manifest so --purge can remove it
        files = json.loads((self.cfg / ".claudi-manifest.json").read_text())["files"]
        self.assertIn(str(self.cfg / "claudi" / "config.json"), files)
        self.assertIn(str(self.cfg / "claudi" / "lang.md"), files)

    def test_yes_without_lang_defaults_en(self):
        # Fresh install, --yes and no --lang -> defaults to en.
        self.install()
        self.assertEqual(self._lang_config()["lang"], "en")
        self.assertEqual((self.cfg / "claudi" / "lang.md").read_text().strip(),
                         "Always respond to the user in English.")

    def test_yes_without_lang_keeps_existing(self):
        # Pre-existing config.json lang is preserved by --yes with no --lang,
        # and unrelated keys are merge-preserved.
        (self.cfg / "claudi").mkdir(parents=True)
        (self.cfg / "claudi" / "config.json").write_text(
            json.dumps({"lang": "ko", "other": 1}))
        self.install()  # --yes, no --lang
        cfg = self._lang_config()
        self.assertEqual(cfg["lang"], "ko")
        self.assertEqual(cfg["other"], 1)  # other keys preserved

    def test_switching_lang_updates_files(self):
        # Re-running with a different --lang updates both config.json and lang.md.
        self.install("--lang", "en")
        self.assertEqual(self._lang_config()["lang"], "en")
        self.install("--lang", "ko")
        self.assertEqual(self._lang_config()["lang"], "ko")
        self.assertIn("한국어", (self.cfg / "claudi" / "lang.md").read_text())

    def test_interactive_lang_prompt_ko(self):
        # No --yes and no --lang -> ask; "ko" on stdin selects Korean.
        r = run_cli(["install", "--home", str(self.home),
                     "--no-claudlet", "--no-patch", "--no-launchd"],
                    input_text="ko\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._lang_config()["lang"], "ko")

    def test_uninstall_removes_lang_import(self):
        self.install("--lang", "ko")
        self.assertIn("@~/.claude/claudi/lang.md",
                      (self.cfg / "CLAUDE.md").read_text())
        r = run_cli(["uninstall", "--yes", "--home", str(self.home), "--no-patch"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        md = self.cfg / "CLAUDE.md"
        if md.exists():
            self.assertNotIn("claudi/lang.md", md.read_text())

    def test_purge_removes_lang_files(self):
        self.install("--lang", "ko")
        self.assertTrue((self.cfg / "claudi" / "config.json").exists())
        r = run_cli(["uninstall", "--yes", "--purge", "--home", str(self.home),
                     "--no-patch"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.cfg / "claudi" / "config.json").exists())
        self.assertFalse((self.cfg / "claudi" / "lang.md").exists())


if __name__ == "__main__":
    unittest.main()
