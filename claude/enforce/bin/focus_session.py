#!/usr/bin/env python3
# ~/.claude/enforce/bin/focus_session.py  <session_id | local_<desktop id>>
#
# Takes the user to a given session.
# 주어진 세션으로 사용자를 데려간다.
#   - Desktop app session: `open claude://claude.ai/epitaxy/<desktop sessionId>` deep link.
#     (The app bundle's claudeURLHandler is confirmed to handle /epitaxy/ deep links.)
#   - CLI session (claude run in a terminal): focus the terminal app/tab using the
#     terminal hints (TERM_PROGRAM / ITERM_SESSION_ID / TERM_SESSION_ID) recorded in
#     events.jsonl (best-effort). iTerm2 tries tab selection by session id, others fall
#     back to app activate.
# 앱 세션=딥링크, CLI 세션=events.jsonl 의 터미널 힌트로 터미널 앱/탭 포커스(best-effort).
#
# Always exit 0, swallow exceptions. Called by the pet click handler (claudlet_click)
# and by macOS notifications (terminal-notifier -execute).
# 항상 exit 0, 예외는 삼킨다. 펫 클릭 핸들러와 macOS 알림이 공통으로 호출한다.
#
# Paths: events.jsonl under CLAUDE_CONFIG_DIR (default ~/.claude); sessionkind is a sibling module.
# 경로: events.jsonl 은 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래, sessionkind 는 형제 모듈.

import json
import os
import subprocess
import sys

BIN = os.path.dirname(os.path.abspath(__file__))
if BIN not in sys.path:
    sys.path.insert(0, BIN)
try:
    import sessionkind
except Exception:
    sessionkind = None

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
EVENTS = os.path.join(CLAUDE_DIR, "monitor", "events.jsonl")
_DEVNULL = subprocess.DEVNULL


def _run(cmd, timeout=4):
    try:
        subprocess.run(cmd, timeout=timeout, stdout=_DEVNULL, stderr=_DEVNULL)
        return True
    except Exception:
        return False


def open_app(desktop_id):
    if not desktop_id:
        return False
    return _run(["/usr/bin/open", "claude://claude.ai/epitaxy/%s" % desktop_id])


def _latest_focus(cli_sid):
    """The most recent focus block (terminal hints) for that session in events.jsonl.
    events.jsonl 에서 그 세션의 가장 최신 focus(터미널 힌트) 블록."""
    focus = {}
    try:
        with open(EVENTS, encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("session_id") == cli_sid and isinstance(o.get("focus"), dict):
                    focus = o["focus"]
    except Exception:
        pass
    return focus


def focus_terminal(focus):
    """Focus the terminal app/tab (best-effort). True if it probably succeeded.
    터미널 앱/탭 포커스(best-effort). 성공 추정이면 True."""
    tp = (focus.get("term_program") or "").lower()
    iterm = focus.get("iterm_session_id") or ""
    if "iterm" in tp and iterm:
        # iTerm2: try selecting the tab/session by id; if not found, just activate.
        # AppleScript string literal: escape backslash first, then the quote.
        # iTerm2: 세션 id 로 탭/세션 선택 시도, 못 찾으면 그냥 activate. 역슬래시→따옴표 순으로 이스케이프.
        esc = iterm.replace("\\", "\\\\").replace('"', '\\"')
        js = ('tell application "iTerm2"\n activate\n'
              ' repeat with w in windows\n repeat with t in tabs of w\n'
              '  repeat with s in sessions of t\n'
              '   if (id of s) is "%s" then\n select t\n select s\n return\n end if\n'
              '  end repeat\n end repeat\n end repeat\n'
              'end tell') % esc
        if _run(["/usr/bin/osascript", "-e", js]):
            return True
    # General fallback: activate the terminal app by TERM_PROGRAM.
    # 일반 폴백: TERM_PROGRAM 으로 터미널 앱 activate.
    appmap = {"iterm.app": "iTerm", "apple_terminal": "Terminal",
              "ghostty": "Ghostty", "warpterminal": "Warp",
              "wezterm": "WezTerm", "vscode": "Visual Studio Code"}
    app = appmap.get(tp)
    if app:
        return _run(["/usr/bin/osascript", "-e",
                     'tell application "%s" to activate' % app])
    return False


def go(sid):
    sid = (sid or "").strip()
    if not sid:
        return False
    # 1) app (desktop) session -> deep link / 앱(데스크톱) 세션 → 딥링크
    desktop = sid if sid.startswith("local_") else (
        sessionkind.desktop_session_id(sid) if sessionkind else "")
    if desktop and open_app(desktop):
        return True
    # 2) CLI session -> terminal focus / CLI 세션 → 터미널 포커스
    return focus_terminal(_latest_focus(sid))


def main():
    go(sys.argv[1] if len(sys.argv) > 1 else "")
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
