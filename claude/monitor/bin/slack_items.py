#!/usr/bin/env python3
# ~/.claude/monitor/bin/slack_items.py
#
# Slack summary item store (the Slack area of the pet's "Notices" tab). Separate from
# notices.json; holds only the top 10 "Slack the user should see now". The slack-digest job
# writes it via `set`; the pet chat only reads it (never enters askhistory/outbox/additionalContext).
#
# Design principles (same as notices.py):
#   - Standard library only.
#   - fcntl lock (.lock) + atomic write (.tmp → os.replace). No failure ever raises.
#   - File layout: {"updated_ts": <float>, "items": [ {item}, ... ]}  (at most 10 items)
#     item = {id, importance(1~5), summary, channel, from, ts, url, reason}
#   - Sort: importance desc, then ts desc. Keep only the top 10.
#
# Usage:
#   echo '<JSON>' | python3 slack_items.py set   # validate stdin JSON, sort, save top 10
#   python3 slack_items.py show                   # print current contents
#   python3 slack_items.py clear                  # empty it
#
# Paths: data resolves under CLAUDE_CONFIG_DIR (default ~/.claude).
#
# Slack 요약 항목 저장소(펫 '알림' 탭의 Slack 영역). notices.json 과는 별개 파일이며
# "지금 사용자가 봐야 할 Slack" 상위 10개만 담는다. slack-digest 작업이 `set` 으로 쓰고,
# 펫 대화창이 읽기만 한다(askhistory/outbox/additionalContext 어디에도 안 들어감).
#
# 설계 원칙(notices.py 와 동일):
#   - 표준 라이브러리만 사용.
#   - fcntl 락(.lock) + 원자적 쓰기(.tmp → os.replace). 어떤 실패도 예외로 터뜨리지 않는다.
#   - 파일 구조: {"updated_ts": <float>, "items": [ {item}, ... ]}  (items 는 최대 10개)
#     item = {id, importance(1~5), summary, channel, from, ts, url, reason}
#   - 정렬: importance 내림차순, 같으면 ts 내림차순. 상위 10개만 남긴다.
#
# 사용:
#   echo '<JSON>' | python3 slack_items.py set   # stdin JSON 검증 후 정렬·상위 10개 저장
#   python3 slack_items.py show                   # 현재 내용 출력
#   python3 slack_items.py clear                  # 비우기
#
# 경로: 데이터는 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래에서 해석한다.

import json
import os
import sys
import time
from datetime import datetime

try:
    import fcntl
except Exception:                       # non-POSIX fallback (always present on macOS) / 비 POSIX 폴백(이 환경은 macOS → 항상 있음)
    fcntl = None

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
ITEMS = os.path.join(CLAUDE_DIR, "monitor", "slack-items.json")
LOCK = ITEMS + ".lock"
# slack-digest run time (last_run). A successful set stamps ISO8601 now here →
# enforce/check.py uses last_run to judge whether the routine is running (staleness).
# slack-digest 실행 시각(last_run). set 성공 시 여기에 ISO8601 now 를 찍는다 →
# enforce/check.py 가 "루틴이 돌고 있나"(staleness)를 last_run 으로 판별한다.
STATE = os.path.join(CLAUDE_DIR, "monitor", "slack-state.json")
STATE_LOCK = STATE + ".lock"
MAX_ITEMS = 10
SUMMARY_MAX = 90
FIELDS = ("id", "importance", "summary", "channel", "from", "ts", "url", "reason")


class _Lock:
    """Exclusive lock on the given path (if available). Any failure is ignored and we proceed.
    지정 경로의 배타 락(있으면). 실패해도 통째로 무시하고 진행한다."""
    def __init__(self, path=LOCK):
        self.path = path
        self.f = None

    def __enter__(self):
        if fcntl is None:
            return self
        try:
            self.f = open(self.path, "w")
            fcntl.flock(self.f.fileno(), fcntl.LOCK_EX)
        except Exception:
            self.f = None
        return self

    def __exit__(self, *a):
        if self.f is not None:
            try:
                fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self.f.close()
            except Exception:
                pass
        return False


def _atomic_write(data, path=ITEMS):
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _stamp_last_run():
    """Stamp last_run in slack-state.json with now (ISO8601), keeping other keys (locked, atomic).
    Stamped here on a successful set so the routine (LLM) never edits the state JSON itself;
    enforce/check.py uses this last_run to judge slack-digest staleness.

    slack-state.json 의 last_run 을 현재 시각(ISO8601)으로 갱신(기존 키 보존, 락+원자적).

    루틴(LLM)이 상태 JSON 을 직접 편집하지 않도록 set 성공 시 여기서 찍는다.
    enforce/check.py 가 이 last_run 으로 slack-digest staleness 를 판별한다."""
    try:
        with _Lock(STATE_LOCK):
            try:
                with open(STATE, encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}
            if not isinstance(state, dict):
                state = {}
            state["last_run"] = datetime.now().astimezone().isoformat()
            _atomic_write(state, STATE)
    except Exception:
        pass


def _read():
    try:
        with open(ITEMS, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"updated_ts": 0, "items": []}
    if not isinstance(data, dict):
        return {"updated_ts": 0, "items": []}
    data.setdefault("items", [])
    data.setdefault("updated_ts", 0)
    return data


def _clamp_importance(v):
    try:
        n = int(v)
    except Exception:
        return 1
    return 5 if n > 5 else (1 if n < 1 else n)


def _to_float(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _normalize(item):
    """Normalize an input item (dict) to a dict of known fields only. None if not a dict.
    입력 item(dict)을 알려진 필드만 담은 정규화 dict 로. dict 가 아니면 None."""
    if not isinstance(item, dict):
        return None
    out = {
        "id": str(item.get("id") or "").strip(),
        "importance": _clamp_importance(item.get("importance")),
        "summary": str(item.get("summary") or "").strip()[:SUMMARY_MAX],
        "channel": str(item.get("channel") or "").strip(),
        "from": str(item.get("from") or "").strip(),
        "ts": _to_float(item.get("ts")),
        "url": str(item.get("url") or "").strip(),
        "reason": str(item.get("reason") or "").strip(),
    }
    return out


def _coerce_items(data):
    """stdin JSON → item list. Accepts a dict ({items:[...]}) or a bare list.
    stdin JSON 을 item 리스트로. dict({items:[...]}) 또는 bare 리스트 모두 허용."""
    if isinstance(data, dict):
        items = data.get("items")
    elif isinstance(data, list):
        items = data
    else:
        items = None
    if not isinstance(items, list):
        return None
    out = []
    for it in items:
        norm = _normalize(it)
        if norm is not None:
            out.append(norm)
    return out


def set_from_stdin():
    """Validate stdin JSON, sort, save the top 10, and stamp last_run.

    Returns 0 on success; on bad input (unparseable / no 'items' list) keeps the existing file
    and returns 2 — so failure is visible (the routine reports an error instead of '-').

    stdin 의 JSON 을 검증·정렬·상위 10개로 저장하고 last_run 을 찍는다.

    성공 0, 입력이 잘못되면(파싱 불가/‘items’ 리스트 없음) 기존 파일을 그대로 두고 2를
    돌려준다 — 실패가 보이게(루틴이 '-' 대신 오류를 보고하도록)."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        sys.stderr.write("slack_items: invalid JSON on stdin; kept existing file\n")
        return 2
    items = _coerce_items(data)
    if items is None:
        sys.stderr.write("slack_items: no 'items' list found; kept existing file\n")
        return 2
    items.sort(key=lambda it: (it["importance"], it["ts"]), reverse=True)
    items = items[:MAX_ITEMS]
    with _Lock():
        _atomic_write({"updated_ts": time.time(), "items": items})
    _stamp_last_run()
    return 0


def show():
    data = _read()
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def clear():
    with _Lock():
        _atomic_write({"updated_ts": time.time(), "items": []})


def main():
    """Return the exit code: 2 for set input errors, otherwise 0.
    종료코드를 돌려준다: set 은 입력 오류 시 2, 그 외 0."""
    cmd = sys.argv[1] if len(sys.argv) >= 2 else ""
    if cmd == "set":
        return set_from_stdin()
    elif cmd == "show":
        show()
        return 0
    elif cmd == "clear":
        clear()
        return 0
    else:
        sys.stderr.write("usage: slack_items.py {set|show|clear}\n")
        return 0


if __name__ == "__main__":
    # Expose set's input-error exit code (2) as is. Any other unexpected exception is
    # swallowed to 0 by best-effort design (a failed save keeps the existing file).
    # set 의 입력-오류 종료코드(2)는 그대로 노출한다. 그 외 예기치 못한 예외는
    # best-effort 설계상 삼키고 0 으로(저장 실패는 기존 파일 보존).
    try:
        rc = main() or 0
    except Exception:
        rc = 0
    sys.exit(rc)
