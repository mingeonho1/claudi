#!/usr/bin/env python3
# ~/.claude/enforce/bin/session-start-hook.py
#
# Hook for a session's first user prompt (UserPromptSubmit). It "reminds" the
# session, as context, of check.py's findings.
# 세션 첫 사용자 프롬프트 훅(UserPromptSubmit). check.py 결과를 세션 컨텍스트로 "상기"시킨다.
#   - If there are issues (idle Slack digest, pet down, ...), emit UserPromptSubmit
#     additionalContext JSON on stdout.
#   - At the same time, send one desktop/pet line via notify.py (2h throttle shared with launchd).
#   - 문제가 있으면 additionalContext JSON 출력 + notify.py 로 한 줄 알림(launchd 와 2시간 throttle 공유).
#
# It used to run on SessionStart, but there the evidence for scheduled sessions
# (prompt/desktop record) was not yet present, so reminders leaked into most
# scheduled sessions. UserPromptSubmit lets us confirm scheduled sessions by
# prompt (sessionkind), and additionalContext is seen by the model on the first
# prompt anyway, so the user experience is the same.
# 과거엔 SessionStart 에서 돌았으나 그 시점엔 예약 세션 판별 근거가 없어 리마인더가 새어나갔다.
# UserPromptSubmit 로 옮기면 prompt 로 예약 세션을 확정 판별할 수 있고 체감은 동일하다.
#
# Once per session: ~/.claude/enforce/session_reminder_sessions/<sid> marker
# (O_EXCL). The marker is created on the first prompt regardless of issues, so
# later prompts don't repeat the check. Cleaned after 7 days.
# 세션당 1회: <sid> 마커(O_EXCL). 문제 유무와 무관하게 첫 프롬프트에서 마커를 만들어 반복을 막는다. 7일 정리.
#
# Must: be fast (<1s), always exit 0, emit nothing on stdout when there is no
# issue or the session is a skip target.
# 반드시: 빠르게(<1s), 항상 exit 0, 문제 없거나 스킵 대상이면 stdout 무출력.
#
# Skip (no output):
#   - the monitor session (matches ~/.claude/monitor/monitor-session's session_id).
#   - subagents (payload has agent_id/agent_type).
#   - headless/SDK (CLAUDE_CODE_ENTRYPOINT in {sdk-py, sdk-ts, sdk, headless}).
#   - scheduled-task sessions (sessionkind — prompt starts with a <scheduled-task> wrapper).
# 스킵(무출력): 관제 세션 / 서브에이전트 / 헤드리스·SDK / 예약(스케줄) 세션.
#
# additionalContext is written as "a statement of fact + a reminder the user set
# for themselves" (no imperative, no system impersonation), stated as the user's
# own setting so it does not trip prompt-injection defenses.
# additionalContext 는 "사실 진술 + 사용자 본인이 걸어둔 알림"으로 쓴다(명령조·시스템 사칭 금지).
#
# Paths: data/markers/logs under CLAUDE_CONFIG_DIR (default ~/.claude); sibling scripts are relative.
# 경로: 데이터/마커/로그는 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래, 형제 스크립트는 상대경로.

import json
import os
import re
import subprocess
import sys
import time
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
MONITOR = os.path.join(CLAUDE_DIR, "monitor")
CONTROL_FILE = os.path.join(MONITOR, "monitor-session")
NOTIFY = os.path.join(os.path.dirname(os.path.dirname(BIN)),
                      "monitor", "bin", "notify.py")
HEADLESS_ENTRY = {"sdk-py", "sdk-ts", "sdk", "headless"}
MAX_CONTEXT = 1900             # within 2000 chars / 2000자 이내

ENFORCE = os.path.join(CLAUDE_DIR, "enforce")
MARKER_DIR = os.path.join(ENFORCE, "session_reminder_sessions")
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
LOGDIR = os.path.join(ENFORCE, "logs")
LOGFILE = os.path.join(LOGDIR, "enforce.log")


def log(msg):
    """Append one line to enforce.log (the logger itself swallows any error).
    enforce.log 에 한 줄 남긴다(로거 자체는 어떤 예외도 삼킨다)."""
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write("%s session_start_hook %s\n" % (stamp, msg))
    except Exception:
        pass


def cleanup_old_markers():
    try:
        now = time.time()
        for name in os.listdir(MARKER_DIR):
            path = os.path.join(MARKER_DIR, name)
            try:
                if os.path.isfile(path) and now - os.path.getmtime(path) > MAX_AGE_SECONDS:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def read_control_session():
    try:
        with open(CONTROL_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def build_context(issues):
    body = t("Current status — ", "현재 상태 — ") + " / ".join(
        i.get("msg", "") for i in issues if i.get("msg"))
    ctx = (
        t("[Session start reminder] The user set up the enforce layer to remind "
          "themselves, at session start, of ops tasks they often forget. The "
          "following is that automatic check's result — it is not an instruction "
          "from the system or an outside party, but a reminder the user set for "
          "themselves (whether to act on it is the user's call). ",
          "[세션 시작 리마인더] 사용자가 자주 잊는 운영 작업을 세션 시작 때 스스로 상기하려고 "
          "enforce 레이어를 설정해 두었습니다. 아래는 그 자동 점검 결과이며, 시스템이나 외부가 "
          "주는 지시가 아니라 사용자 본인이 미리 걸어둔 알림입니다(처리 여부는 사용자 판단). ")
        + body + ". "
        + t("Session monitoring can be handled with /session-monitor. ",
            "세션 관제는 /session-monitor 로 처리할 수 있습니다. ")
        + t("Without interrupting the user's current work, just remind them once, briefly.",
            "사용자의 현재 작업을 방해하지 않는 선에서 한 번 간단히 상기만 시켜 주세요.")
    )
    return ctx[:MAX_CONTEXT]


def main():
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    event = data.get("hook_event_name") or ""
    if event and event != "UserPromptSubmit":
        return                                   # this hook is UserPromptSubmit-only / UserPromptSubmit 전용

    session_id = (data.get("session_id") or "").strip()
    if session_id and session_id == read_control_session():
        return                                   # monitor session -> no output / 관제 세션 → 무출력
    if data.get("agent_id") or data.get("agent_type"):
        return                                   # subagent -> no output / 서브에이전트 → 무출력
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") in HEADLESS_ENTRY:
        return                                   # headless/SDK -> no output / 헤드리스·SDK → 무출력
    try:
        import sessionkind
        if sessionkind.is_scheduled_task(data):
            return                               # scheduled-task session -> no inject/notify / 예약 세션
    except Exception:
        pass

    # Once per session (O_EXCL marker). The marker is created on the first prompt
    # regardless of issues, so later prompts don't repeat the check.
    # 세션당 1회 보장(O_EXCL 마커). 문제 유무와 무관하게 첫 프롬프트에서 만들어 반복을 막는다.
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id).strip("._")
    if not sid:
        return                                   # cannot guarantee once-per-session -> skip / 1회 보장 불가 → 주입 안 함
    os.makedirs(MARKER_DIR, exist_ok=True)
    marker = os.path.join(MARKER_DIR, sid)
    if os.path.exists(marker):
        return                                   # already handled once this session / 이미 이 세션에서 1회 처리함
    cleanup_old_markers()
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(int(time.time())).encode("utf-8"))
        os.close(fd)
    except FileExistsError:
        return                                   # concurrent first-prompt race -> only once / 동시 경쟁 → 1회만

    try:
        import check
        issues = check.collect_issues()
    except Exception as e:
        log("check failed: %s: %s" % (type(e).__name__, e))
        return
    if not issues:
        return                                   # no issue -> no output / 문제 없음 → 무출력

    # 1) inject session context / 세션 컨텍스트 주입
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": build_context(issues),
    }}, ensure_ascii=False))

    # 2) one-line notification (shared throttle — same issue set once per 2h).
    # 2) 한 줄 알림(throttle 공유 — 같은 문제 집합 2시간 내 1회).
    try:
        if check.should_notify(check.throttle_key(issues)):
            subprocess.Popen(
                [sys.executable, NOTIFY,
                 "--title", t("⏰ Work reminder", "⏰ 작업 리마인더"),
                 "--body", check.summary_line(issues, maxlen=200)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
