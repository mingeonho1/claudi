#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claudi installer / uninstaller (stdlib only, Python 3.9+).

claudi is a personal Claude Code kit centered on ~/.claude. This script copies
(default) or symlinks (``--link``) the repo's ``claude/**`` tree into the config
directory (``$CLAUDE_CONFIG_DIR`` or ``~/.claude``) and optionally wires up the
desktop pet (claudlet), launchd and hooks. Runtime
data is never kept in the repo, and every step is idempotent.

claudi 는 ~/.claude 를 중심으로 한 개인 Claude Code 키트입니다. 이 스크립트는
리포의 ``claude/**`` 트리를 설정 디렉터리(``$CLAUDE_CONFIG_DIR`` 또는 ``~/.claude``)로
복사(기본) 또는 심링크(``--link``)하고, 데스크톱 펫(claudlet) · launchd · 훅을
선택적으로 연결합니다. 런타임 데이터는 절대 리포에 두지
않으며, 모든 단계는 멱등(idempotent)합니다.

Config fragment layout (config/settings.claudi.json)
설정 프래그먼트 구조 (config/settings.claudi.json)
----------------------------------------------------
Sections are grouped under a top-level ``"sections"`` so the installer can apply
each independently / 설치기가 섹션별로 독립 적용할 수 있도록 ``"sections"`` 아래에
그룹화합니다::

    sections.hooks.enforce  -> UserPromptSubmit (session-start-hook)(--no-hooks)
    sections.hooks.monitor  -> Notification / Stop / StopFailure    (--no-hooks)

The ``{{CLAUDE_DIR}}`` placeholder in hook commands is replaced with the absolute
path of the target config directory at install time. Settings merge preserves
unknown keys, backs up the existing file, and merges hooks idempotently by
command string.
훅 커맨드의 ``{{CLAUDE_DIR}}`` 플레이스홀더는 설치 시 대상 설정 디렉터리의 절대
경로로 치환됩니다. 설정 병합은 기존 파일 백업, 알 수 없는 키 보존, 커맨드 문자열
기준 멱등 병합을 지킵니다.

Output language / 출력 언어
----------------------------------------------------
User-facing output and prompts are shown in English or Korean. The language is
chosen via ``--lang en|ko`` (or asked interactively) and persisted to
``<config>/claudi/config.json`` + ``<config>/claudi/lang.md``, which ``CLAUDE.md``
imports so Claude replies in that language.
사용자용 출력·프롬프트는 영어 또는 한국어로 표시됩니다. 언어는 ``--lang en|ko``
(또는 대화형 질문)로 고르고, ``<config>/claudi/config.json`` + ``lang.md`` 에
저장되며, ``CLAUDE.md`` 가 이를 import 해 Claude 응답 언어를 맞춥니다.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_CLAUDE = REPO_ROOT / "claude"
CONFIG_FRAGMENT = REPO_ROOT / "config" / "settings.claudi.json"
LAUNCHD_DIR = REPO_ROOT / "launchd"

CLAUDLET_VERSION = "2.6.0"

# Markers identifying claudi-managed hooks (stable regardless of config path).
# claudi 가 관리하는 훅을 식별하는 마커(설정 디렉터리 경로와 무관하게 안정적).
CLAUDI_HOOK_MARKERS = (
    "enforce/bin/session-start-hook.py",
    "monitor/bin/monitor-hook.py",
)

PLISTS = (
    ("com.claudi.pet", "com.claudi.pet.plist.tmpl"),
    ("com.claudi.enforcer", "com.claudi.enforcer.plist.tmpl"),
)

# CLAUDE.md import line managed by the installer (output language).
# 설치기가 관리하는 CLAUDE.md import 줄(출력 언어).
LANG_IMPORT_RE = re.compile(r"^@.*/claudi/lang\.md\s*$")

# Output language instruction written to <config>/claudi/lang.md per language.
# 언어별로 <config>/claudi/lang.md 에 쓰는 출력 언어 지시문.
LANG_INSTR = {
    "en": "Always respond to the user in English.\n",
    "ko": (
        "Always respond to the user in Korean (한국어). Code, identifiers, "
        "commit messages and file contents stay in English unless the user "
        "asks otherwise.\n"
    ),
}

# Active output language for t(); set once from the resolved --lang / config.
# t() 가 보는 현재 출력 언어; 해석된 --lang / config 로 1회 설정된다.
_LANG = "en"


def set_lang(lang):
    """Set the active output language ('en' or 'ko'). / 현재 출력 언어 설정."""
    global _LANG
    _LANG = "ko" if lang == "ko" else "en"


def t(en, ko):
    """Return the English or Korean string for the active language.
    현재 언어에 맞는 영어/한국어 문자열을 반환한다."""
    return ko if _LANG == "ko" else en


def read_existing_lang(config_dir):
    """Return the saved lang from <config>/claudi/config.json, or None.
    <config>/claudi/config.json 에 저장된 lang 을 반환, 없으면 None."""
    path = Path(config_dir) / "claudi" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        lang = data.get("lang")
        if lang in ("en", "ko"):
            return lang
    except (OSError, ValueError, AttributeError):
        pass
    return None


def ask_lang():
    """Prompt (bilingually) for the output language; default 'en'.
    출력 언어를 (양국어로) 묻는다; 기본값 'en'."""
    prompt = (
        "Output language for Claude and claudi messages? [en/ko] (default en)\n"
        "Claude·claudi 메시지 출력 언어? [en/ko] (기본값 en)\n> "
    )
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        ans = ""
    return "ko" if ans in ("ko", "k", "kor", "korean") else "en"


def resolve_install_lang(ctx, args):
    """Resolve the install output language.
    설치 출력 언어를 결정한다.

    --lang wins; otherwise ask when interactive (not --yes); with --yes keep the
    existing <config>/claudi/config.json lang, else 'en'.
    --lang 우선; 없으면 대화형(비 --yes)일 때 질문; --yes 면 기존 config 의 lang,
    없으면 'en'.
    """
    if args.lang:
        return args.lang
    if not args.yes:
        return ask_lang()
    return read_existing_lang(ctx.config_dir) or "en"


SKIP_NAMES = {".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def skip_source(rel):
    """Never copy repo build artifacts / caches / backups into the config dir.
    리포 산출물/캐시/백업은 설정 디렉터리로 복사하지 않는다."""
    if "__pycache__" in rel.parts:
        return True
    if rel.name in SKIP_NAMES:
        return True
    if rel.suffix in SKIP_SUFFIXES:
        return True
    if ".bak-" in rel.name:
        return True
    return False


def files_equal(a, b):
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


class Ctx:
    """I/O helper aware of install state + dry-run. / 설치 상태·dry-run 을 아는 I/O 헬퍼."""

    def __init__(self, args):
        self.dry = args.dry_run
        self.yes = args.yes
        self.link = args.link
        self.no_claudlet = args.no_claudlet
        self.no_patch = args.no_patch
        self.no_launchd = args.no_launchd
        self.no_hooks = args.no_hooks
        self.purge = args.purge
        self.force_claudlet = args.force_claudlet
        self.lang = "en"  # resolved later by main(); feeds t() + config writes.

        self.home = (Path(args.home).expanduser().resolve()
                     if args.home else Path.home())
        # --home is a sandbox: it wins over CLAUDE_CONFIG_DIR so a test install can never touch the real config.
        # --home 은 샌드박스라 CLAUDE_CONFIG_DIR 보다 우선한다(테스트 설치가 실사용 설정을 건드리지 않게).
        env_cfg = None if args.home else os.environ.get("CLAUDE_CONFIG_DIR")
        if env_cfg:
            self.config_dir = Path(env_cfg).expanduser().resolve()
            self.default_config = False
        else:
            self.config_dir = (self.home / ".claude").resolve()
            self.default_config = True

        self.is_macos = platform.system() == "Darwin"
        self.python = shutil.which("python3") or sys.executable
        self.use_launchctl = (
            self.is_macos
            and not args.home
            and os.environ.get("CLAUDI_NO_LAUNCHCTL") != "1"
        )
        self.ts = time.strftime("%Y%m%d-%H%M%S")
        self.manifest_extra = []
        self.warnings = []
        self.next_steps = []

    # ---- logging -----------------------------------------------------
    def log(self, msg):
        print("  %s" % msg)

    def warn(self, msg):
        self.warnings.append(msg)
        print("  ! %s" % msg)

    def act(self, msg):
        print("  %s %s" % ("[dry]" if self.dry else "[+]", msg))

    def skip(self, what):
        print("  = %s%s" % (what, t(" (unchanged, skipped)", " (동일, 건너뜀)")))

    def step(self, title):
        print("\n== %s ==" % title)

    def confirm(self, q):
        if self.yes:
            return True
        try:
            ans = input("  %s [y/N] " % q)
        except EOFError:
            return False
        return ans.strip().lower() in ("y", "yes")

    # ---- filesystem --------------------------------------------------
    def ensure_dir(self, path):
        if path.exists():
            return
        self.act("mkdir %s" % path)
        if not self.dry:
            path.mkdir(parents=True, exist_ok=True)

    def write_text(self, path, text):
        self.act("write %s" % path)
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def write_json(self, path, obj):
        self.write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

    def backup(self, path):
        if path.is_symlink():
            return  # A symlink is replaceable — nothing to preserve. / 심링크는 보존 불필요.
        bak = path.with_name(path.name + ".bak-claudi-" + self.ts)
        self.act("backup %s" % path.name)
        if not self.dry:
            shutil.copy2(path, bak)

    def symlink(self, src, dst):
        self.act("link %s -> %s" % (dst, src))
        if not self.dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            dst.symlink_to(src)

    def copy(self, src, dst):
        self.act("copy %s -> %s" % (src, dst))
        if not self.dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_symlink():
                dst.unlink()
            shutil.copyfile(src, dst)

    def remove(self, path):
        self.act("remove %s" % path)
        if not self.dry and (path.exists() or path.is_symlink()):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()

    def run(self, cmd, check=False, quiet_fail=False, env=None):
        self.act("run: %s" % " ".join(cmd))
        sys.stdout.flush()  # Keep ordering with child output when piped. / 파이프 시 순서 유지.
        if self.dry:
            return 0
        try:
            res = subprocess.run(cmd, env=env)
            if res.returncode != 0 and not quiet_fail:
                self.warn(t("command failed (%d): %s", "명령 실패(%d): %s")
                          % (res.returncode, " ".join(cmd)))
            return res.returncode
        except Exception as exc:  # noqa: BLE001
            if not quiet_fail:
                self.warn(t("command raised: %s (%s)", "명령 실행 예외: %s (%s)")
                          % (" ".join(cmd), exc))
            return 1


# ---------------------------------------------------------------------
# install steps
# ---------------------------------------------------------------------
def validate_existing_settings(ctx):
    """Abort before any write if an existing settings.json is broken JSON.
    기존 settings.json 이 깨진 JSON 이면 아무것도 쓰기 전에 중단한다.

    merge_settings only reads settings.json near the end, so discovering a parse
    failure there would leave a half install. Catch it in preflight so no write
    happens (merge_settings keeps its own check as a safety net).
    merge_settings 는 막바지에야 읽으므로, 거기서 처음 실패를 발견하면 '반쪽 설치'가
    된다. preflight 에서 미리 걸러 어떤 쓰기도 일어나지 않게 한다(병합 검사는 안전망).
    """
    path = ctx.config_dir / "settings.json"
    if not path.exists():
        return
    try:
        json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        sys.stderr.write(t(
            "error: existing settings.json is not valid JSON — aborting install "
            "(nothing written). Fix the file and re-run: %s\n",
            "오류: 기존 settings.json 이 올바른 JSON 이 아닙니다 — 설치를 중단합니다"
            "(아무것도 쓰지 않음). 파일을 고친 뒤 다시 실행하세요: %s\n") % path)
        sys.exit(1)


def preflight(ctx):
    ctx.step("preflight")
    if sys.version_info < (3, 9):
        print(t("python 3.9+ is required (have %s)",
                "python 3.9+ 가 필요합니다 (현재 %s)") % platform.python_version())
        sys.exit(1)
    ctx.log("python %s OK" % platform.python_version())
    if not ctx.is_macos:
        ctx.warn(t("not macOS (%s) — auto-skipping launchd/claudlet/patch steps",
                   "macOS 아님(%s) — launchd/claudlet/patch 단계 자동 건너뜀")
                 % platform.system())
        ctx.no_launchd = True
        ctx.no_claudlet = True
        ctx.no_patch = True
    if not ctx.no_claudlet and shutil.which("pipx") is None and \
            shutil.which("claudlet-attach") is None:
        ctx.warn(t(
            "pipx not found — skipping claudlet. Run `brew install pipx` and re-run.",
            "pipx 없음 — claudlet 설치 건너뜀. `brew install pipx` 후 재실행하세요."))
        ctx.no_claudlet = True
        ctx.no_patch = True
    ctx.log(t("config dir: %s", "설정 디렉터리: %s") % ctx.config_dir)
    validate_existing_settings(ctx)


def ensure_exec(ctx, src, dst, rel):
    if ctx.link:
        # Symlinks point at the repo original (already +x in git) — do not touch it.
        # 심링크는 리포 원본(git 에 이미 +x)을 가리킨다 — 원본 파일 권한을 건드리지 않는다.
        return
    parts = rel.parts
    want = (os.access(src, os.X_OK) or "bin" in parts or "hooks" in parts
            or rel.name.endswith(".sh"))
    if not want:
        return
    ctx.act("chmod +x %s" % rel)
    if ctx.dry:
        return
    try:
        mode = os.stat(dst).st_mode
        os.chmod(dst, mode | 0o111)
    except OSError:
        pass


def copy_tree(ctx):
    ctx.step("claude/** -> %s" % (
        t("config dir (symlink)", "설정 디렉터리 (심링크)") if ctx.link
        else t("config dir (copy)", "설정 디렉터리 (복사)")))
    installed = []
    if not SRC_CLAUDE.is_dir():
        ctx.warn(t("no claude/ tree in repo — skipping copy",
                   "리포에 claude/ 트리가 없습니다 — 복사 생략"))
        return installed
    for src in sorted(SRC_CLAUDE.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(SRC_CLAUDE)
        if skip_source(rel):
            continue
        dst = ctx.config_dir / rel
        installed.append(dst)
        if ctx.link:
            if dst.is_symlink() and Path(os.readlink(dst)) == src:
                ctx.skip(str(rel))
                continue
            if dst.exists() or dst.is_symlink():
                ctx.backup(dst)
            ctx.symlink(src, dst)
        else:
            if dst.exists() and not dst.is_symlink() and files_equal(src, dst):
                ctx.skip(str(rel))
                continue
            if dst.exists() or dst.is_symlink():
                ctx.backup(dst)
            ctx.copy(src, dst)
        ensure_exec(ctx, src, dst, rel)
    return installed


def claudlet_version(ctx):
    if shutil.which("pipx") is None:
        return None
    try:
        out = subprocess.run(["pipx", "list", "--short"], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "claudlet":
            return parts[1]
    return None


def pipx_venvs():
    try:
        out = subprocess.run(["pipx", "environment", "--value", "PIPX_LOCAL_VENVS"],
                             capture_output=True, text=True, timeout=30)
        v = out.stdout.strip()
        if v:
            return Path(v)
    except Exception:  # noqa: BLE001
        pass
    for c in (Path.home() / "Library/Application Support/pipx/venvs",
              Path.home() / ".local/pipx/venvs"):
        if c.exists():
            return c
    return None


def link_claudlet_skill(ctx):
    skill_link = ctx.config_dir / "skills" / "claudlet"
    if skill_link.exists() or skill_link.is_symlink():
        ctx.log(t("claudlet skill link already present",
                  "claudlet 스킬 링크 이미 존재"))
        # Keep it in the manifest on re-install so --purge cleans it up.
        # 재설치 때도 매니페스트에 유지 → --purge 가 정리.
        ctx.manifest_extra.append(skill_link)
        return
    venvs = pipx_venvs()
    if venvs is None:
        ctx.warn(t("could not find pipx venvs path — skipping claudlet skill link",
                   "pipx venvs 경로 확인 실패 — claudlet 스킬 링크 건너뜀"))
        return
    venv_py = venvs / "claudlet" / "bin" / "python"
    if not venv_py.exists():
        ctx.warn(t("no claudlet venv python — skipping skill link",
                   "claudlet venv python 없음 — 스킬 링크 건너뜀"))
        return
    try:
        pkg_dir = subprocess.run(
            [str(venv_py), "-c",
             "import claudlet,os;print(os.path.dirname(claudlet.__file__))"],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        ctx.warn(t("failed to query claudlet package path — skipping skill link",
                   "claudlet 패키지 경로 조회 실패 — 스킬 링크 건너뜀"))
        return
    skill_src = Path(pkg_dir) / "skill" if pkg_dir else None
    if not skill_src or not skill_src.exists():
        ctx.warn(t("no bundled claudlet skill — skipping skill link",
                   "claudlet 번들 스킬 없음 — 스킬 링크 건너뜀"))
        return
    ctx.symlink(skill_src, skill_link)
    ctx.manifest_extra.append(skill_link)


def step_claudlet(ctx):
    ctx.step("claudlet")
    if ctx.no_claudlet:
        ctx.log(t("skipped (--no-claudlet or preflight)",
                  "건너뜀 (--no-claudlet 또는 preflight)"))
        return
    attach = shutil.which("claudlet-attach")
    if attach is None:
        ctx.run(["pipx", "install", "claudlet==%s" % CLAUDLET_VERSION])
    else:
        ver = claudlet_version(ctx)
        if ver == CLAUDLET_VERSION:
            ctx.log(t("claudlet %s confirmed", "claudlet %s 확인") % ver)
        elif ver and ctx.force_claudlet:
            ctx.run(["pipx", "install", "--force", "claudlet==%s" % CLAUDLET_VERSION])
        elif ver:
            ctx.warn(t(
                "claudlet %s installed (expected %s). Use --force-claudlet to "
                "reinstall; continuing as-is.",
                "claudlet %s 설치됨 (기대 %s). 재설치하려면 --force-claudlet, "
                "그대로 진행합니다.") % (ver, CLAUDLET_VERSION))
        else:
            ctx.log(t("claudlet installed (version unknown), continuing",
                      "claudlet 설치됨 (버전 확인 불가), 그대로 진행"))
    link_claudlet_skill(ctx)


def step_patch(ctx):
    ctx.step("patch-claudlet")
    if ctx.no_patch:
        ctx.log(t("skipped (--no-patch or preflight)",
                  "건너뜀 (--no-patch 또는 preflight)"))
        return
    script = ctx.config_dir / "enforce" / "bin" / "patch-claudlet.py"
    if not script.exists():
        ctx.warn(t("patch-claudlet.py missing — skipping patch step",
                   "patch-claudlet.py 없음 — patch 단계 건너뜀"))
        return
    if shutil.which("claudlet-attach") is None:
        ctx.warn(t("claudlet not installed — skipping patch step",
                   "claudlet 미설치 — patch 단계 건너뜀"))
        return
    ctx.run([ctx.python, str(script)])
    ctx.run([ctx.python, str(script), "--check"])


def step_flag_logs(ctx):
    ctx.step(t("standalone-pet flag + log dir",
               "standalone-pet 플래그 + 로그 디렉터리"))
    enforce = ctx.config_dir / "enforce"
    ctx.ensure_dir(enforce / "logs")
    flag = enforce / "standalone-pet"
    if flag.exists():
        ctx.log(t("standalone-pet flag already present",
                  "standalone-pet 플래그 이미 존재"))
    else:
        ctx.write_text(flag, "")
    ctx.manifest_extra.append(flag)


def step_launchd(ctx):
    ctx.step("launchd (LaunchAgents)")
    if ctx.no_launchd:
        ctx.log(t("skipped (--no-launchd or preflight)",
                  "건너뜀 (--no-launchd 또는 preflight)"))
        return
    la_dir = ctx.home / "Library" / "LaunchAgents"
    ctx.ensure_dir(la_dir)
    uid = os.getuid()
    for label, tmpl_name in PLISTS:
        tmpl = (LAUNCHD_DIR / tmpl_name).read_text(encoding="utf-8")
        rendered = (tmpl.replace("{{PYTHON}}", ctx.python)
                        .replace("{{CLAUDE_DIR}}", str(ctx.config_dir)))
        dst = la_dir / (label + ".plist")
        if dst.exists() and dst.read_text(encoding="utf-8") == rendered:
            ctx.skip(label + ".plist")
        else:
            if dst.exists():
                ctx.backup(dst)
            ctx.write_text(dst, rendered)
        ctx.manifest_extra.append(dst)
        if ctx.use_launchctl:
            ctx.run(["launchctl", "bootout", "gui/%d/%s" % (uid, label)],
                    quiet_fail=True)
            ctx.run(["launchctl", "bootstrap", "gui/%d" % uid, str(dst)])
    if not ctx.use_launchctl:
        ctx.log(t("skipping launchctl calls (--home or CLAUDI_NO_LAUNCHCTL=1)",
                  "launchctl 호출 건너뜀 (--home 또는 CLAUDI_NO_LAUNCHCTL=1)"))


# ---- settings merge --------------------------------------------------
def load_sections(config_dir):
    data = json.loads(CONFIG_FRAGMENT.read_text(encoding="utf-8"))
    sections = data["sections"]

    def sub(obj):
        if isinstance(obj, str):
            return obj.replace("{{CLAUDE_DIR}}", str(config_dir))
        if isinstance(obj, list):
            return [sub(x) for x in obj]
        if isinstance(obj, dict):
            return {k: sub(v) for k, v in obj.items()}
        return obj

    return sub(sections)


def existing_commands(settings, event):
    cmds = set()
    for group in settings.get("hooks", {}).get(event, []):
        for h in group.get("hooks", []):
            if "command" in h:
                cmds.add(h["command"])
    return cmds


def merge_hooks(ctx, settings, comp_sections):
    changed = False
    hooks = settings.setdefault("hooks", {})
    for event, groups in comp_sections.items():
        existing = existing_commands(settings, event)
        event_list = hooks.setdefault(event, [])
        for group in groups:
            new_cmds = [h.get("command") for h in group.get("hooks", [])]
            if all(c in existing for c in new_cmds):
                continue
            event_list.append(json.loads(json.dumps(group)))
            existing.update(c for c in new_cmds if c)
            changed = True
    return changed


def merge_settings(ctx):
    ctx.step(t("settings.json merge", "settings.json 병합"))
    sections = load_sections(ctx.config_dir)
    path = ctx.config_dir / "settings.json"
    settings = {}
    existed = path.exists()
    if existed:
        try:
            settings = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            # Overwriting broken JSON with an empty config would wipe the user's
            # settings — abort. / 깨진 JSON 을 빈 설정으로 덮어쓰면 사용자 설정이 사라진다 — 중단.
            ctx.warn(t(
                "failed to parse existing settings.json — aborting without "
                "overwrite. Fix the file and re-run: %s",
                "기존 settings.json 파싱 실패 — 덮어쓰지 않고 중단합니다. 파일을 "
                "고친 뒤 다시 실행하세요: %s") % path)
            sys.exit(1)
    changed = False
    if not ctx.no_hooks:
        for comp in ("enforce", "monitor"):
            changed |= merge_hooks(ctx, settings, sections["hooks"][comp])
    else:
        ctx.log(t("hooks section skipped (--no-hooks)",
                  "hooks 섹션 건너뜀 (--no-hooks)"))
    if not changed:
        ctx.log(t("no change (already applied)", "변경 없음 (이미 반영됨)"))
        return
    if existed:
        ctx.backup(path)
    ctx.write_json(path, settings)


# ---- output language -------------------------------------------------
def lang_import_line(ctx):
    if ctx.default_config:
        return "@~/.claude/claudi/lang.md"
    return "@%s/claudi/lang.md" % ctx.config_dir


def write_lang_config(ctx):
    """Write <config>/claudi/config.json {"lang"} (merge-preserving) + lang.md.
    <config>/claudi/config.json {"lang"} (다른 키 보존) + lang.md 를 쓴다."""
    claudi_dir = ctx.config_dir / "claudi"
    cfg_path = claudi_dir / "config.json"
    data = {}
    if cfg_path.exists():
        try:
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except ValueError:
            ctx.warn(t(
                "claudi/config.json was not valid JSON — rewriting it",
                "claudi/config.json 이 올바른 JSON 이 아니라 새로 씁니다"))
    data["lang"] = ctx.lang
    ctx.write_json(cfg_path, data)
    ctx.write_text(claudi_dir / "lang.md", LANG_INSTR[ctx.lang])
    ctx.manifest_extra.append(cfg_path)
    ctx.manifest_extra.append(claudi_dir / "lang.md")


def add_import_line(ctx, line, line_re, present_msg):
    """Append an import line to CLAUDE.md idempotently (backup; create if absent).
    CLAUDE.md 에 import 줄을 멱등 추가한다(백업; 없으면 생성)."""
    md = ctx.config_dir / "CLAUDE.md"
    if md.exists():
        text = md.read_text(encoding="utf-8")
        if any(line_re.match(l) for l in text.splitlines()):
            ctx.log(present_msg)
            return
        ctx.backup(md)
        if text and not text.endswith("\n"):
            text += "\n"
        ctx.write_text(md, text + line + "\n")
    else:
        ctx.write_text(md, line + "\n")


def step_lang(ctx):
    """Persist the output language and import it from CLAUDE.md.
    출력 언어를 저장하고 CLAUDE.md 에서 import 한다."""
    ctx.step(t("output language (%s)", "출력 언어 (%s)") % ctx.lang)
    write_lang_config(ctx)
    add_import_line(ctx, lang_import_line(ctx), LANG_IMPORT_RE,
                    t("CLAUDE.md lang import already present",
                      "CLAUDE.md lang import 이미 존재"))


def write_manifest(ctx, installed):
    files = sorted(set(str(p) for p in installed)
                   | set(str(p) for p in ctx.manifest_extra))
    manifest = {
        "installed_at": ctx.ts,
        "config_dir": str(ctx.config_dir),
        "lang": ctx.lang,
        "linked": ctx.link,
        "files": files,
    }
    ctx.write_json(ctx.config_dir / ".claudi-manifest.json", manifest)


def summary(ctx, installed):
    ctx.step(t("summary", "요약"))
    done = []
    done.append(t("claude/** %s: %d files", "claude/** %s: %d개 파일")
                % (t("linked", "링크") if ctx.link else t("copied", "복사"),
                   len(installed)))
    done.append("claudlet: %s" % (t("skipped", "건너뜀") if ctx.no_claudlet
                                   else t("installed/verified", "설치/확인")))
    done.append("patch-claudlet: %s" % (t("skipped", "건너뜀") if ctx.no_patch
                                        else t("applied/verified", "적용/검증")))
    done.append("launchd: %s" % (t("skipped", "건너뜀") if ctx.no_launchd
                                 else t("registered", "등록")))
    done.append("hooks: %s" % (t("omitted", "생략") if ctx.no_hooks
                               else t("merged", "병합")))
    done.append("lang: %s" % ctx.lang)
    for d in done:
        ctx.log("- " + d)
    if ctx.warnings:
        print(t("\n  warnings(%d):", "\n  경고(%d):") % len(ctx.warnings))
        for w in ctx.warnings:
            print("    ! " + w)
    print(t("\n  next steps:", "\n  다음 단계:"))
    print(t("    - launch the pet: claudlet-attach --standalone",
            "    - 펫 띄우기: claudlet-attach --standalone"))
    print(t("    - session monitor skill: /session-monitor",
            "    - 세션 관제 스킬: /session-monitor"))
    for s in ctx.next_steps:
        print("    - " + s)
    if ctx.dry:
        print(t("\n  (dry-run: nothing was actually written)",
                "\n  (dry-run: 실제로 쓴 파일 없음)"))


def do_install(ctx):
    print(t("claudi install starting (%s)%s", "claudi 설치 시작 (%s)%s")
          % (ctx.config_dir, " [dry-run]" if ctx.dry else ""))
    preflight(ctx)
    installed = copy_tree(ctx)
    step_claudlet(ctx)
    step_patch(ctx)
    step_flag_logs(ctx)
    step_launchd(ctx)
    merge_settings(ctx)
    step_lang(ctx)
    write_manifest(ctx, installed)
    summary(ctx, installed)


# ---------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------
def is_claudi_cmd(cmd):
    return any(m in (cmd or "") for m in CLAUDI_HOOK_MARKERS)


def uninstall_hooks(ctx):
    ctx.step(t("remove claudi hooks from settings.json",
               "settings.json 에서 claudi 훅 제거"))
    path = ctx.config_dir / "settings.json"
    if not path.exists():
        ctx.log(t("no settings.json — skipping", "settings.json 없음 — 건너뜀"))
        return
    try:
        settings = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        ctx.warn(t("failed to parse settings.json — skipping hook removal",
                   "settings.json 파싱 실패 — 훅 제거 건너뜀"))
        return
    hooks = settings.get("hooks", {})
    changed = False
    for event in list(hooks.keys()):
        new_groups = []
        for group in hooks[event]:
            orig = group.get("hooks", [])
            kept = [h for h in orig if not is_claudi_cmd(h.get("command", ""))]
            if len(kept) != len(orig):
                changed = True
            if not orig:
                new_groups.append(group)  # Keep hook-less groups. / 훅 없는 그룹은 그대로 둠.
            elif kept:
                g = dict(group)
                g["hooks"] = kept
                new_groups.append(g)
            # If kept is empty and the group held only claudi hooks, drop it.
            # kept 비어있고 원래 claudi 훅만 있었으면 그룹 통째로 제거.
        if new_groups:
            hooks[event] = new_groups
        else:
            del hooks[event]
            changed = True
    if not hooks:
        settings.pop("hooks", None)
    if changed:
        ctx.backup(path)
        ctx.write_json(path, settings)
    else:
        ctx.log(t("no claudi hooks to remove", "제거할 claudi 훅 없음"))


def uninstall_launchd(ctx):
    ctx.step(t("remove launchd", "launchd 제거"))
    la_dir = ctx.home / "Library" / "LaunchAgents"
    uid = os.getuid() if ctx.is_macos else 0
    for label, _ in PLISTS:
        dst = la_dir / (label + ".plist")
        if ctx.use_launchctl:
            ctx.run(["launchctl", "bootout", "gui/%d/%s" % (uid, label)],
                    quiet_fail=True)
        if dst.exists():
            ctx.remove(dst)
        else:
            ctx.log(t("%s missing", "%s 없음") % dst.name)


def uninstall_patch(ctx):
    ctx.step("patch-claudlet --revert")
    if ctx.no_patch or not ctx.is_macos:
        ctx.log(t("skipped", "건너뜀"))
        return
    script = ctx.config_dir / "enforce" / "bin" / "patch-claudlet.py"
    if script.exists() and shutil.which("claudlet-attach"):
        ctx.run([ctx.python, str(script), "--revert"])
    else:
        ctx.log(t("patch-claudlet.py or claudlet missing — skipping",
                  "patch-claudlet.py 또는 claudlet 없음 — 건너뜀"))


def uninstall_claude_md(ctx):
    """Strip the claudi-managed lang import line from CLAUDE.md.
    CLAUDE.md 에서 claudi 가 관리하는 lang import 줄을 제거한다."""
    ctx.step(t("remove CLAUDE.md import lines", "CLAUDE.md import 줄 제거"))
    md = ctx.config_dir / "CLAUDE.md"
    if not md.exists():
        ctx.log(t("no CLAUDE.md", "CLAUDE.md 없음"))
        return
    lines = md.read_text(encoding="utf-8").splitlines()
    kept = [l for l in lines if not LANG_IMPORT_RE.match(l)]
    if len(kept) == len(lines):
        ctx.log(t("no import lines", "import 줄 없음"))
        return
    ctx.backup(md)
    ctx.write_text(md, "\n".join(kept) + ("\n" if kept else ""))


def do_uninstall(ctx):
    print(t("claudi uninstall starting (%s)%s", "claudi 제거 시작 (%s)%s")
          % (ctx.config_dir, " [dry-run]" if ctx.dry else ""))
    uninstall_hooks(ctx)
    uninstall_launchd(ctx)
    uninstall_patch(ctx)
    ctx.step(t("remove standalone-pet flag", "standalone-pet 플래그 제거"))
    flag = ctx.config_dir / "enforce" / "standalone-pet"
    if flag.exists():
        ctx.remove(flag)
    else:
        ctx.log(t("no flag", "플래그 없음"))
    uninstall_claude_md(ctx)
    if ctx.purge:
        purge_files(ctx)
    else:
        ctx.step(t("copied scripts / runtime data", "복사된 스크립트/런타임 데이터"))
        ctx.log(t("preserved (use --purge to fully remove)",
                  "보존됨 (완전 삭제하려면 --purge)"))
    ctx.step(t("done", "완료"))
    if ctx.warnings:
        for w in ctx.warnings:
            print("  ! " + w)
    if ctx.dry:
        print(t("  (dry-run: nothing was changed)",
                "  (dry-run: 실제로 바꾼 것 없음)"))


def purge_files(ctx):
    ctx.step(t("--purge: remove claudi install files + runtime data",
               "--purge: claudi 설치 파일 + 런타임 데이터 삭제"))
    manifest_path = ctx.config_dir / ".claudi-manifest.json"
    files = []
    if manifest_path.exists():
        try:
            files = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])
        except ValueError:
            ctx.warn(t("failed to parse manifest — skipping tracked file removal",
                       "매니페스트 파싱 실패 — 추적된 파일 삭제 건너뜀"))
    for f in files:
        p = Path(f)
        if p.exists() or p.is_symlink():
            ctx.remove(p)
    for extra in (ctx.config_dir / "claudi" / "config.json",
                  ctx.config_dir / "claudi" / "lang.md",
                  ctx.config_dir / "enforce" / "logs",
                  manifest_path):
        if extra.exists() or extra.is_symlink():
            ctx.remove(extra)


# ---------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="install.py",
        description="claudi install/uninstall (stdlib only, Python 3.9+)")
    p.add_argument("action", choices=["install", "uninstall"],
                   help="install / uninstall")
    p.add_argument("--lang", choices=["en", "ko"],
                   help="output language for Claude and claudi messages "
                        "(default: ask, or existing/en with --yes)")
    p.add_argument("--yes", action="store_true",
                   help="non-interactive (overwrite on conflict)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan only; write nothing")
    p.add_argument("--link", action="store_true",
                   help="symlink instead of copy (for contributors)")
    p.add_argument("--home", metavar="DIR", help="HOME override (for tests)")
    p.add_argument("--no-claudlet", action="store_true")
    p.add_argument("--no-patch", action="store_true")
    p.add_argument("--no-launchd", action="store_true")
    p.add_argument("--no-hooks", action="store_true")
    p.add_argument("--force-claudlet", action="store_true",
                   help="reinstall claudlet even on a version mismatch")
    p.add_argument("--purge", action="store_true",
                   help="on uninstall, also delete copied files + runtime data")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    ctx = Ctx(args)
    if args.action == "install":
        ctx.lang = resolve_install_lang(ctx, args)
        set_lang(ctx.lang)
        do_install(ctx)
    else:
        ctx.lang = args.lang or read_existing_lang(ctx.config_dir) or "en"
        set_lang(ctx.lang)
        do_uninstall(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
