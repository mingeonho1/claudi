#!/usr/bin/env python3
# ~/.claude/enforce/bin/launchd-check.py
#
# Backup checker run by launchd (com.claudi.enforcer) hourly + at load. Reminds
# via macOS notification even when the Claude app is closed (so the session-start
# hook never gets a chance to run).
# launchd(com.claudi.enforcer)가 1시간마다 + 로드시 실행하는 백업 점검기. Claude 앱이 꺼져
# 있어도 macOS 알림으로 상기시킨다.
#   - Keep the standalone pet up (calls ensure-pet.py). / 독립 펫 상시 유지(ensure-pet.py 호출).
#   - Check for issues via check.collect_issues(). / check.collect_issues() 로 문제 점검.
#   - If issues exist, notify via notify.py (and the pet bubble if up) — 2h throttle shared with the hook.
#   - 문제가 있으면 notify.py 로 macOS 알림 — 훅과 공유하는 2시간 throttle.
#   - Log every run to ~/.claude/enforce/logs/enforce.log. Always exit 0. / 모든 실행을 로그에 남긴다.
#
# Paths: data/logs under CLAUDE_CONFIG_DIR (default ~/.claude); sibling scripts are relative.
# 경로: 데이터/로그는 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래, 형제 스크립트는 상대경로.

import os
import subprocess
import sys
from datetime import datetime

BIN = os.path.dirname(os.path.abspath(__file__))
if BIN not in sys.path:
    sys.path.insert(0, BIN)

# Shared language helper (enforce/bin -> ../../claudi/lib); fail-safe: English.
# 공용 언어 헬퍼 로드(실패 시 영어 폴백).
_LIB = os.path.join(os.path.dirname(BIN), "..", "claudi", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
try:
    from claudi_i18n import t
except Exception:
    def t(en, ko):  # fail-safe fallback: English / 폴백: 영어
        return en

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
ENFORCE = os.path.join(CLAUDE_DIR, "enforce")
LOGDIR = os.path.join(ENFORCE, "logs")
LOGFILE = os.path.join(LOGDIR, "enforce.log")
NOTIFY = os.path.join(os.path.dirname(os.path.dirname(BIN)),
                      "monitor", "bin", "notify.py")
ENSURE_PET = os.path.join(BIN, "ensure-pet.py")


def ensure_pet():
    """Make sure the standalone pet is up, launch it if not (best-effort, async).
    With no standalone flag, ensure-pet.py does nothing on its own.
    독립 펫이 떠 있는지 확인하고 없으면 기동(best-effort, 비동기). 플래그 없으면 아무것도 안 함."""
    try:
        subprocess.Popen(
            [sys.executable, ENSURE_PET],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:
        log("ensure-pet launch failed: %r" % e)


def log(msg):
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (stamp, msg))
    except Exception:
        pass


def main():
    # Keep the standalone pet up — revived here on login/boot RunAtLoad and hourly.
    # 독립 펫 상시 유지 — 로그인/부팅 RunAtLoad·1시간 주기 모두 이 지점에서 되살린다.
    ensure_pet()

    try:
        import check
    except Exception as e:
        log("check import failed: %r" % e)
        return

    try:
        issues = check.collect_issues()
    except Exception as e:
        log("collect_issues failed: %r" % e)
        return

    types = [i.get("type") for i in issues]
    if not issues:
        log("check: no issues")
        return

    body = check.summary_line(issues, maxlen=220)

    if not check.should_notify(check.throttle_key(issues)):
        log("check: %d issue(s) %s (throttle skipped)" % (len(issues), types))
        return

    try:
        subprocess.run(
            [sys.executable, NOTIFY, "--title",
             t("⏰ Work reminder", "⏰ 작업 리마인더"), "--body", body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        log("notified: %d issue(s) %s" % (len(issues), types))
    except Exception as e:
        log("notify failed: %r | %d issue(s) %s" % (e, len(issues), types))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("exception: %r" % e)
    sys.exit(0)
