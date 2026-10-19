#!/usr/bin/env python3
# ~/.claude/enforce/bin/sessionkind.py
#
# Scheduled-task session detection + hook session_id <-> desktop sessionId mapping.
# A single shared module for the enforce/monitor/claudlet wrappers. Stdlib only.
# 예약(스케줄) 작업 세션 판별 + hook session_id <-> 데스크톱 sessionId 매핑.
# enforce/monitor/claudlet 래퍼가 공유하는 단일 모듈. 표준 라이브러리만 사용.
#
# Evidence (macOS Claude desktop app session store):
#   ~/Library/Application Support/Claude/claude-code-sessions/**/local_<id>.json
#   each file: {"sessionId":"local_<uuid>", "cliSessionId":"<hook uuid>",
#              "scheduledTaskId":"<task name>"|"", ...}
#   - Scheduled sessions have a non-empty scheduledTaskId (authoritative). Normal: empty/absent.
#   - The hook payload's session_id == cliSessionId. The desktop deep-link id == sessionId.
# 근거(macOS Claude 데스크톱 앱 세션 저장소): 각 파일에 sessionId/cliSessionId/scheduledTaskId.
#   예약 세션은 scheduledTaskId 가 비어있지 않다(권위 필드). 훅 session_id == cliSessionId.
# Fallback (when the desktop record is not there yet, e.g. CLI-only/timing): the
# transcript's first user message starts with `<scheduled-task name="..." file="...SKILL.md">`.
# 폴백(데스크톱 레코드가 아직 없을 때): transcript 첫 사용자 메시지가 예약 래퍼로 시작하면 예약 세션.
#
# The verdict is invariant per session -> cached by session_id, but only when there
# is definitive evidence (otherwise treat as normal but do not cache -> re-judge next event).
# 판별 결과는 세션당 불변 → session_id 로 캐시한다. 단 "확정 근거"가 있을 때만 캐시한다.
#
# Paths: cache under CLAUDE_CONFIG_DIR (default ~/.claude). The desktop session
# store is where the macOS app writes, so it is accessed read-only.
# 경로: 캐시는 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래. 데스크톱 세션 저장소는 읽기 전용으로만 접근.

import glob
import json
import os
import re
import time

SESSIONS_DIR = os.path.expanduser(
    "~/Library/Application Support/Claude/claude-code-sessions")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
CACHE_FILE = os.path.join(CLAUDE_DIR, "enforce", ".sessionkind-cache.json")
NEG_TTL = 600                            # negative-cache TTL in sec (no desktop record) / 음성 캐시 유효시간(초)


def _sessions_dir():
    """Desktop session store path. Tests pass a fake dir via CLAUDI_DESKTOP_SESSIONS_DIR.
    데스크톱 세션 저장소 경로. 테스트는 CLAUDI_DESKTOP_SESSIONS_DIR 로 가짜 디렉터리를 준다."""
    return os.environ.get("CLAUDI_DESKTOP_SESSIONS_DIR") or SESSIONS_DIR

_ROLLOUT = re.compile(
    r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.I)
_SCHED_RE = re.compile(r'<scheduled-task\s+name="([^"]*)"')


def session_id_of(data):
    """Extract the hook session_id from a hook payload (dict) or a session_id string.
    훅 payload(dict) 또는 session_id 문자열에서 hook session_id 추출."""
    if not isinstance(data, dict):
        return str(data or "")
    sid = data.get("session_id")
    if sid:
        return str(sid)
    tp = str(data.get("transcript_path") or "").replace("\\", "/")
    m = _ROLLOUT.search(tp)
    return m.group(1) if m else ""


def _desktop_files():
    try:
        files = glob.glob(os.path.join(_sessions_dir(), "**", "local_*.json"),
                          recursive=True)
    except Exception:
        return []
    try:
        files.sort(key=os.path.getmtime, reverse=True)   # most recent first / 최근 세션 먼저
    except Exception:
        pass
    return files


def _desktop_record(cli_session_id):
    """The desktop session record (dict) whose cliSessionId matches, or None.
    cliSessionId 가 일치하는 데스크톱 세션 레코드(dict) 또는 None."""
    if not cli_session_id:
        return None
    for f in _desktop_files():
        try:
            o = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if o.get("cliSessionId") == cli_session_id:
            return o
    return None


def _first_user_text(transcript_path, max_records=50):
    """First user message text in the transcript. None if file/message not there yet (unknown).
    transcript 첫 사용자 메시지 텍스트. 파일/메시지가 아직 없으면 None(미확정)."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= max_records:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "user":
                    continue
                msg = o.get("message") or {}
                c = msg.get("content")
                if isinstance(c, list):
                    txt = "".join(x.get("text", "") for x in c
                                  if isinstance(x, dict))
                else:
                    txt = str(c or "")
                txt = txt.strip()
                if txt:
                    return txt
    except Exception:
        return None
    return None


def classify(data):
    """(scheduled: bool, desktop_id: str, definitive: bool) — judged immediately, no cache.
    (scheduled, desktop_id, definitive) — 캐시 없이 즉시 판정."""
    # If the UserPromptSubmit payload's prompt starts with the scheduled wrapper
    # (<scheduled-task name="...">), that alone confirms a scheduled session. Early on,
    # the desktop record / transcript first user message are not there yet (unknown),
    # but the prompt is available right then.
    # prompt 가 예약 래퍼로 시작하면 그 자체로 예약 세션 확정(세션 초반엔 다른 근거가 아직 없다).
    if isinstance(data, dict):
        prompt = data.get("prompt")
        if isinstance(prompt, str) and _SCHED_RE.match(prompt.lstrip()):
            return (True, "", True)
    sid = session_id_of(data)
    rec = _desktop_record(sid)
    if rec is not None:
        return (bool(rec.get("scheduledTaskId")), rec.get("sessionId") or "", True)
    txt = _first_user_text(data.get("transcript_path") if isinstance(data, dict)
                           else None)
    if txt is not None:
        return (bool(_SCHED_RE.search(txt)), "", True)
    return (False, "", False)


def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def _cached(data):
    sid = session_id_of(data)
    if not sid:
        return (False, "", False)
    cache = _load_cache()
    ent = cache.get(sid)
    if isinstance(ent, dict) and "scheduled" in ent:
        return (bool(ent["scheduled"]), ent.get("desktop") or "", True)
    scheduled, desktop, definitive = classify(data)
    if definitive:
        cache[sid] = {"scheduled": scheduled, "desktop": desktop, "ts": time.time()}
        _save_cache(cache)
    return (scheduled, desktop, definitive)


def is_scheduled_task(data):
    """True if a scheduled-task session. Unknown (no evidence) -> False (conservative: treat as normal).
    예약 작업 세션이면 True. 미확정(근거 없음)이면 False(보수적: 일반 취급)."""
    return _cached(data)[0]


def desktop_session_id(data):
    """This session's Claude desktop deep-link id ('local_<uuid>'), or ''.
    이 세션의 Claude 데스크톱 딥링크 id('local_<uuid>') 또는 ''.
    data is a hook payload (dict) or a hook session_id string.
    data 는 훅 payload(dict) 또는 hook session_id 문자열."""
    sid = session_id_of(data)
    if not sid:
        return ""
    # Already a desktop id -> return as is. / 이미 데스크톱 id 를 넘겨준 경우 그대로.
    if sid.startswith("local_"):
        return sid
    cache = _load_cache()
    ent = cache.get(sid)
    if isinstance(ent, dict):
        if ent.get("desktop"):
            return ent["desktop"]
        # Negative cache: recently (within NEG_TTL) scanned and confirmed 'no record' -> skip rescan.
        # 음성 캐시: 최근(NEG_TTL 내)에 스캔해 '데스크톱 레코드 없음'으로 확인됨 → 재스캔 안 함.
        chk = ent.get("desktop_checked")
        if chk is not None:
            try:
                if (time.time() - float(chk)) < NEG_TTL:
                    return ""
            except (TypeError, ValueError):
                pass
    rec = _desktop_record(sid)
    if rec and rec.get("sessionId"):
        # Mapping cache (leave the verdict untouched when unknown).
        # 매핑 캐시(판별값은 모르면 건드리지 않음).
        try:
            ent = cache.get(sid) if isinstance(cache.get(sid), dict) else {}
            ent["desktop"] = rec["sessionId"]
            if "scheduled" not in ent:
                ent["scheduled"] = bool(rec.get("scheduledTaskId"))
            cache[sid] = ent
            _save_cache(cache)
        except Exception:
            pass
        return rec["sessionId"]
    # No record -> negative cache (TTL). Avoids a full glob+json.load on every repeated event.
    # 레코드 없음 → 음성 캐시(TTL). 반복 이벤트마다 전체 글롭+json.load 하지 않게 한다.
    try:
        ent = cache.get(sid) if isinstance(cache.get(sid), dict) else {}
        ent["desktop_checked"] = time.time()
        cache[sid] = ent
        _save_cache(cache)
    except Exception:
        pass
    return ""


def deeplink(data):
    """Claude desktop deep-link URL for a click/notification, or ''.
    클릭/알림에서 쓸 Claude 데스크톱 딥링크 URL, 또는 ''."""
    d = desktop_session_id(data)
    return "claude://claude.ai/epitaxy/%s" % d if d else ""


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg:
        print("session_id   :", arg)
        print("scheduled    :", is_scheduled_task({"session_id": arg}))
        print("desktop id   :", desktop_session_id(arg))
        print("deeplink     :", deeplink(arg))
