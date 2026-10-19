#!/usr/bin/env python3
# ~/.claude/monitor/bin/monitor-hook.py
#
# Collector for Claude Code hooks (Stop, StopFailure, Notification).
#   - Reads the hook JSON from stdin and appends one line to ~/.claude/monitor/events.jsonl (file lock).
#   - Needs-decision/input waits and failures update attention.json (the session a pet click opens).
# Notifications are sent directly by the monitor session/pet via notify.py; the hook only records
# (HOOK_NOTIFY=False).
# Claude Code 훅(Stop, StopFailure, Notification)용 수집기.
#   - stdin 의 훅 JSON 을 읽어 ~/.claude/monitor/events.jsonl 에 한 줄 append(파일 락).
#   - 판단/입력 대기·실패는 attention.json 을 갱신(펫 클릭이 열 세션).
# 알림은 관제 세션/펫이 notify.py 로 직접 보낸다. 훅은 기록만(HOOK_NOTIFY=False).
#
# Must: be fast (<300ms), always exit 0, never inject anything into the session via hook stdout.
# 반드시: 빠르게(<300ms), 항상 exit 0, 훅 stdout 으로 세션에 아무것도 주입하지 않음.
#
# Skip rules (neither record nor notify):
#   - no session_id.
#   - monitor session: matches the session_id in ~/.claude/monitor/monitor-session (loop guard).
#   - subagent: agent_id present in the hook payload (per the official docs).
#   - headless/SDK: CLAUDE_CODE_ENTRYPOINT is non-interactive (sdk-*, headless).
#   - Stop/StopFailure whose transcript file does not exist (nothing to analyze).
# 스킵(기록·알림 모두 안 함) 규칙:
#   - session_id 없음.
#   - 관제 세션: ~/.claude/monitor/monitor-session 에 적힌 session_id 와 일치(루프 방지).
#   - 서브에이전트: 훅 payload 에 agent_id 존재(공식 문서 확인).
#   - 헤드리스/SDK: CLAUDE_CODE_ENTRYPOINT 가 비대화형(sdk-*, headless) 일 때.
#   - Stop/StopFailure 인데 transcript 파일이 실재하지 않음(분석할 게 없음).
#
# Debounce (record, but skip the notification):
#   - Notification with notification_type == idle_prompt → always skip (noise).
#   - same (session_id, event, notification_type) again within 30s → skip.
# 디바운스(기록은 하되 알림만 생략):
#   - Notification notification_type == idle_prompt → 항상 알림 생략(노이즈).
#   - 같은 (session_id, event, notification_type) 30초 내 재발 → 알림 생략.
#
# Path rules: data (events.jsonl etc.) lives under CLAUDE_CONFIG_DIR (default ~/.claude);
# sibling scripts (notify.py) and modules (enforce/bin/sessionkind.py) are relative to this file.
# 경로 규칙: 데이터(events.jsonl 등)는 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래,
# 형제 스크립트(notify.py)·형제 모듈(enforce/bin/sessionkind.py)은 이 파일 기준 상대경로.

import json
import os
import sys
import time

BIN = os.path.dirname(os.path.abspath(__file__))

# Shared language helper (monitor/bin -> ../../claudi/lib); fail-safe: English.
# 공용 언어 헬퍼 로드(monitor/bin -> ../../claudi/lib, 실패 시 영어 폴백).
_LIB = os.path.join(BIN, "..", "..", "claudi", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
try:
    from claudi_i18n import t
except Exception:
    def t(en, ko):  # fail-safe fallback: English / 폴백: 영어
        return en

# Data dir is relative to the config dir (CLAUDE_CONFIG_DIR changes it in tests/custom installs).
# 데이터 디렉터리는 설정 디렉터리 기준(테스트/커스텀 설치에서 CLAUDE_CONFIG_DIR 로 바뀜).
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
MONITOR = os.path.join(CLAUDE_DIR, "monitor")
EVENTS = os.path.join(MONITOR, "events.jsonl")
CONTROL_FILE = os.path.join(MONITOR, "monitor-session")
NOTIFY = os.path.join(BIN, "notify.py")                     # sibling script / 형제 스크립트
STATE_FILE = os.path.join(MONITOR, ".notify-state.json")
ATTENTION_FILE = os.path.join(MONITOR, "attention.json")   # session a pet click opens / 펫 클릭이 열 세션

# Immediate notifications from the hook on/off. When False the hook only records
# (append_event) and updates attention; it touches neither notify.py nor the debounce
# state file. Set True to revert.
# 훅에서의 즉시 알림 on/off. False 면 훅은 기록(append_event)·attention 갱신만 하고
# 알림(notify.py)·디바운스 상태파일은 건드리지 않는다. 되돌리려면 True.
HOOK_NOTIFY = False

# Scheduled-task session detection + desktop deeplink mapping (shared with enforce).
# Works even if the import fails. enforce/bin is the parent's sibling (claude/enforce/bin).
# 예약 작업 세션 판별 + 데스크톱 딥링크 매핑(enforce 와 공유). import 실패해도 동작.
# enforce/bin 은 이 파일 기준 형제-of-parent(claude/enforce/bin).
ENFORCE_BIN = os.path.join(os.path.dirname(os.path.dirname(BIN)), "enforce", "bin")
if ENFORCE_BIN not in sys.path:
    sys.path.insert(0, ENFORCE_BIN)
try:
    import sessionkind
except Exception:
    sessionkind = None


def first_sentence(text, limit=90):
    """First plain sentence of a reply, skipping tables/headings/code (markdown stripped,
    sentence boundary).
    응답 원문에서 표·제목·코드를 빼고 첫 평문 문장만(마크다운 제거, 문장 경계)."""
    import re
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith(("|", "#", "```", ">", "---")):
            continue
        s = re.sub(r"[*`]+", "",re.sub(r"^[-*\d.)\s]+", "", s)).strip()
        if not s:
            continue
        # Sentence-boundary regex; the Korean endings (요|다) match Korean sentence ends.
        # 문장 경계 정규식 — (요|다)는 한국어 문장 끝을 잡는 데이터.
        m = re.search(r"^.+?[.!?。](?=\s|$)|^.+?(요|다)\.?(?=\s|$)", s)
        s = m.group(0) if m and len(m.group(0)) <= limit else s
        return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0]
    return ""

DEBOUNCE_SEC = 30.0
# Notification types worth notifying about (need a decision/action).
# 알림까지 보낼 가치가 있는(판단/행동 필요) Notification 유형.
ACT_TYPES = {
    "permission_prompt", "agent_needs_input", "elicitation_dialog",
    "elicitation_url_dialog",
}
# Non-interactive entrypoints (headless/SDK) → skip.
# 비대화형 엔트리포인트(헤드리스/SDK) → 스킵.
HEADLESS_ENTRY = {"sdk-py", "sdk-ts", "sdk", "headless"}


def read_control_session():
    try:
        with open(CONTROL_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def repo_name(cwd, transcript_path):
    if cwd:
        base = os.path.basename(cwd.rstrip("/"))
        if base:
            return base
    if transcript_path:
        # Last token of the project dir name (flattened path).
        # 프로젝트 디렉터리 이름(플랫된 경로)에서 마지막 토큰.
        d = os.path.basename(os.path.dirname(transcript_path))
        if d:
            return d.rsplit("-", 1)[-1] or d
    return t("session", "세션")


MAX_EVENTS_BYTES = 5 * 1024 * 1024         # rotate to events.jsonl.1 over 5MB / 5MB 초과면 events.jsonl.1 로 회전


def append_event(entry):
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        import fcntl
        # Use a separate lock file so rotate+append is one critical section (rotation
        # replaces events.jsonl itself, so an fd lock on it would break serialization).
        # 회전+추가를 하나의 임계구역으로 묶기 위해 별도 락파일을 쓴다(회전 시
        # events.jsonl 자체가 교체되므로 파일 fd 락으로는 직렬화가 깨진다).
        with open(EVENTS + ".lock", "w") as lk:
            try:
                fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
            # Over 5MB, move to events.jsonl.1 (overwriting the old .1) and start fresh.
            # 5MB 초과면 events.jsonl.1 로 넘기고(기존 .1 덮어씀) 새로 시작.
            try:
                if os.path.getsize(EVENTS) > MAX_EVENTS_BYTES:
                    os.replace(EVENTS, EVENTS + ".1")
            except OSError:
                pass
            with open(EVENTS, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
    except Exception:
        # On lock/file trouble accept losing the event and continue (no rotation).
        # 락/파일 문제라도 이벤트 유실만 감수하고 계속(회전은 생략).
        try:
            with open(EVENTS, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def debounced(key):
    """True means "the same notification was sent recently" → skip this one.
    True 면 '최근에 같은 알림을 보냈다' → 이번 알림은 생략."""
    now = time.time()
    state = {}
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    last = state.get(key, 0)
    hit = (now - last) < DEBOUNCE_SEC
    if not hit:
        state[key] = now
        # Prune old keys (24h). / 오래된 키 정리(24h).
        state = {k: v for k, v in state.items() if now - v < 86400}
        try:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, STATE_FILE)
        except Exception:
            pass
    return hit


def fire_notify(title, body, session_id, open_url="", focus_sid=""):
    try:
        import subprocess
        argv = [sys.executable, NOTIFY, "--title", title, "--body", body,
                "--session", session_id or ""]
        if open_url:                 # app session: click → deeplink / 앱 세션: 알림 클릭 → 딥링크
            argv += ["--open-url", open_url]
        elif focus_sid:              # CLI session: click → focus terminal / CLI 세션: 알림 클릭 → 터미널 포커스
            argv += ["--focus", focus_sid]
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except Exception:
        pass


def write_attention(session_id, desktop, reason):
    """Record "the session to open on click now" (read by the pet click handler). Harmless on failure.
    '지금 클릭하면 열 세션'을 기록한다(펫 클릭 핸들러가 읽음). 실패해도 무해."""
    try:
        data = {"open": desktop or "", "cli_session": session_id or "",
                "reason": (reason or "")[:200], "ts": time.time()}
        tmp = ATTENTION_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, ATTENTION_FILE)
    except Exception:
        pass


def enrich_focus(entry, data):
    """Attach "where a click goes" info to an events line (harmless on failure).
      - App (desktop) session: deeplink (claude://.../epitaxy/<local id>) + desktop id.
      - CLI session: terminal focus hint (terminal app/tab identifiers), used by the click handler.
    events 줄에 '클릭 시 어디로 갈지' 정보를 붙인다(실패해도 무해).
      - 앱(데스크톱) 세션: deeplink(claude://.../epitaxy/<local id>) + desktop id.
      - CLI 세션: 터미널 포커스 힌트(터미널앱/탭 식별자). 클릭 핸들러가 사용."""
    if sessionkind is not None:
        try:
            dl = sessionkind.deeplink(data)
            if dl:
                entry["deeplink"] = dl
                entry["desktop"] = sessionkind.desktop_session_id(data)
        except Exception:
            pass
    focus = {}
    for out_key, env_key in (("entrypoint", "CLAUDE_CODE_ENTRYPOINT"),
                             ("term_program", "TERM_PROGRAM"),
                             ("term_session_id", "TERM_SESSION_ID"),
                             ("iterm_session_id", "ITERM_SESSION_ID")):
        val = os.environ.get(env_key)
        if val:
            focus[out_key] = val
    if focus:
        entry["focus"] = focus


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:
        return

    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return

    event = data.get("hook_event_name") or "Unknown"
    transcript_path = data.get("transcript_path") or ""
    cwd = data.get("cwd") or ""
    ntype = data.get("notification_type") or ""
    message = data.get("message") or ""
    title_in = data.get("title") or ""
    last_msg = data.get("last_assistant_message") or ""

    # --- skip checks / 스킵 판단 ---
    # Monitor session (loop guard). / 관제 세션(루프 방지).
    if session_id == read_control_session():
        return
    # Subagent. / 서브에이전트.
    if data.get("agent_id"):
        return
    # Headless/SDK. / 헤드리스/SDK.
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") in HEADLESS_ENTRY:
        return
    # Scheduled-task session: its Stop/Notification every 5 minutes is neither recorded nor notified.
    # 예약(스케줄) 작업 세션: 5분마다 뜨는 Stop/Notification 은 기록·알림 모두 생략.
    if sessionkind is not None and sessionkind.is_scheduled_task(data):
        return
    # A stop event without a transcript has nothing to analyze.
    # 종료류인데 transcript 가 없으면 분석 대상이 아님.
    if event in ("Stop", "StopFailure"):
        if not transcript_path or not os.path.exists(transcript_path):
            return

    # --- record (always) / 기록(항상) ---
    entry = {
        "ts": time.time(),
        "event": event,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": cwd,
        "notification_type": ntype,
        "message": message,
    }
    if title_in:
        entry["title"] = title_in
    try:
        enrich_focus(entry, data)
    except Exception:
        pass
    append_event(entry)

    # --- notify-worthiness / debounce / 알림 가치/디바운스 판단 ---
    repo = repo_name(cwd, transcript_path)
    notify = True
    title = ""
    body = ""

    if event == "Stop":
        title = t("✅ Done · %s", "✅ 작업 완료 · %s") % repo
        body = first_sentence(last_msg) or t("Turn finished.", "턴이 끝났습니다.")
    elif event == "StopFailure":
        title = t("🛑 Failed · %s", "🛑 작업 실패 · %s") % repo
        body = first_sentence(last_msg) or t("Turn ended in failure.", "턴이 실패로 종료됐습니다.")
    elif event == "Notification":
        if ntype == "idle_prompt":
            notify = False  # idle: record only, skip notifying (noise) / 유휴 알림은 기록만, 노이즈라 알림 생략.
        elif ntype in ACT_TYPES:
            title = t("🟡 Needs input · %s", "🟡 판단/입력 대기 · %s") % repo
            body = (message or title_in or t("Input needed.", "입력이 필요합니다."))[:200]
        else:
            title = "🔔 %s" % repo
            body = (message or title_in or ntype or t("Notification", "알림"))[:200]
    else:
        title = "🔔 %s · %s" % (event, repo)
        body = (message or last_msg or "")[:200]

    if not notify:
        return

    # Click target: deeplink for app sessions, else CLI session focus (session_id).
    # 클릭 대상: 앱 세션이면 딥링크, 아니면 CLI 세션 포커스(session_id).
    open_url = ""
    desktop = ""
    try:
        if sessionkind is not None:
            open_url = sessionkind.deeplink(data) or ""
            desktop = sessionkind.desktop_session_id(data) or ""
    except Exception:
        pass
    # Needs-decision/failure is recorded as the session to open on click (pet click goes there).
    # Kept regardless of notify on/off — attention.json for pet clicks keeps updating.
    # 판단필요/실패는 '클릭 시 열 세션'으로 기록(펫 클릭이 이 세션으로 이동).
    # 알림 on/off 와 무관하게 유지 — 펫 클릭용 attention.json 은 계속 갱신한다.
    if event == "StopFailure" or (event == "Notification" and ntype in ACT_TYPES):
        write_attention(session_id, desktop, "%s · %s" % (title, body))

    # Immediate hook notifications are off (HOOK_NOTIFY). The debounce state write sits in the
    # same branch so .notify-state.json is untouched while off. Set HOOK_NOTIFY=True to revert.
    # 훅에서의 즉시 알림은 끈다(HOOK_NOTIFY). 디바운스 상태파일 쓰기도 같은 조건 안에 둬서
    # 알림이 꺼진 동안 .notify-state.json 을 건드리지 않는다. 되돌리려면 HOOK_NOTIFY=True.
    if HOOK_NOTIFY:
        key = "%s|%s|%s" % (session_id, event, ntype)
        if debounced(key):
            return
        fire_notify(title, body, session_id, open_url=open_url, focus_sid=session_id)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
