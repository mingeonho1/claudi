# ~/.claude/enforce/bin/claudlet_click.py
#
# patch-claudlet.py wires a call to this into pet._activate_claude. Clicking the
# pet moves the user to "the session that needs attention right now". All the
# actual move logic lives in focus_session.py, so changing only this/that file
# changes the behavior (no need to re-patch the pet source).
# patch-claudlet.py 가 pet._activate_claude 안에 끼워 호출한다. 펫을 클릭하면 "지금 주의가
# 필요한 세션"으로 이동시킨다. 실제 이동 로직은 focus_session.py 에 있어 이 파일만 고치면 된다.
#
# Target resolution order:
#   1) "open" (app deep-link id) or "cli_session" in ~/.claude/monitor/attention.json.
#      The monitor skill / monitor-hook refresh "the session to open on click" there.
#   2) else the monitor session itself (~/.claude/monitor/monitor-session).
#   3) else the pet's own session.
# If any one succeeds -> True (-> the pet skips its original app-activate behavior).
# 대상 결정 순서: 1) attention.json 의 open/cli_session, 2) 관제 세션, 3) 펫 자신의 세션.
# 하나라도 성공하면 True(→ pet 은 원래 앱-activate 동작을 건너뛴다).
#
# Paths: attention.json/monitor-session under CLAUDE_CONFIG_DIR (default ~/.claude).
# 경로: attention.json/monitor-session 은 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래.

import json
import os
import sys

BIN = os.path.dirname(os.path.abspath(__file__))
if BIN not in sys.path:
    sys.path.insert(0, BIN)
try:
    import focus_session
except Exception:
    focus_session = None

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
ATTENTION = os.path.join(CLAUDE_DIR, "monitor", "attention.json")
MONITOR_SESSION = os.path.join(CLAUDE_DIR, "monitor", "monitor-session")


def _go(sid):
    return bool(sid) and focus_session is not None and focus_session.go(sid)


def open_attention(my_session_id=""):
    # 1) attention.json
    try:
        with open(ATTENTION, encoding="utf-8") as f:
            t = json.load(f)
        if isinstance(t, dict):
            target = (t.get("open") or t.get("cli_session") or "").strip()
            if _go(target):
                return True
    except Exception:
        pass
    # 2) monitor session / 관제 세션
    try:
        mon = open(MONITOR_SESSION, encoding="utf-8").read().strip()
    except Exception:
        mon = ""
    if _go(mon):
        return True
    # 3) the pet's own session / 펫 자신의 세션
    return _go((my_session_id or "").strip())
