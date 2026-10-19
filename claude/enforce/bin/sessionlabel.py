#!/usr/bin/env python3
# ~/.claude/enforce/bin/sessionlabel.py  <session_id>
#
# Turn a session into a one-line, human-recognizable label. It is prefixed to
# monitor-session pet notification (notify) bodies to reveal "which session".
# 세션을 사람이 알아볼 한 줄 라벨로. 관제 세션 펫 알림(notify) 본문 앞에 붙여 "어느 세션인지"를 드러낸다.
#   - App (desktop) session: "[App · <title>]" (or "[Scheduled · <title>]" if scheduled).
#   - CLI session: "[CLI · <terminal app> · <cwd basename> · <branch>]".
#   - 앱 세션: "[App · <제목>]"(예약이면 "[Scheduled · <제목>]"). CLI: "[CLI · <터미널앱> · <cwd> · <브랜치>]".
# Evidence: the desktop session record (title/scheduledTaskId) and events.jsonl (cwd/focus).
# 근거: 데스크톱 세션 레코드(title/scheduledTaskId)와 events.jsonl(cwd/focus).
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
EVENTS = os.path.join(CLAUDE_DIR, "monitor", "events.jsonl")


def _latest_event(sid):
    out = {}
    try:
        with open(EVENTS, encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("session_id") == sid:
                    out = o
    except Exception:
        pass
    return out


def _git_branch(cwd):
    if not cwd or not os.path.isdir(cwd):
        return ""
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=1)
        return r.stdout.strip()
    except Exception:
        return ""


def label(sid):
    sid = (sid or "").strip()
    if not sid:
        return t("[session]", "[세션]")
    rec = sessionkind._desktop_record(sid) if sessionkind else None
    if rec and (rec.get("title") or rec.get("scheduledTaskId")):
        tag = t("Scheduled", "예약") if rec.get("scheduledTaskId") else t("App", "앱")
        title = (rec.get("title") or rec.get("scheduledTaskId") or "").strip()
        return "[%s · %s]" % (tag, title[:44])
    # CLI session / CLI 세션
    ev = _latest_event(sid)
    cwd = ev.get("cwd") or ""
    focus = ev.get("focus") or {}
    term = (focus.get("term_program") or "CLI")
    base = os.path.basename(cwd.rstrip("/")) or "?"
    parts = ["CLI", term, base]
    br = _git_branch(cwd)
    if br:
        parts.append(br)
    return "[%s]" % " · ".join(parts)


if __name__ == "__main__":
    print(label(sys.argv[1] if len(sys.argv) > 1 else ""))
