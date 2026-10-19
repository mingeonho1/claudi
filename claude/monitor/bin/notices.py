#!/usr/bin/env python3
# ~/.claude/monitor/bin/notices.py
#
# Notice store: one row per session (or fixed key). The pet chat window's "Notices" tab reads
# this file to show them separately from the user conversation. notify.py upserts; the pet
# reads and marks handled on click.
#
# Design principles:
#   - Standard library only (importable from pet/monitor/enforce alike).
#   - fcntl lock + atomic write (.tmp → os.replace). No failure ever raises.
#   - File layout: { "<key>": {row}, ... }  (key = cli session_id, or a fixed key like "slack")
#     → "one row per session" is guaranteed (a re-notify on the same key overwrites that row).
#   - row = {session_id, desktop_id, label, icon, summary, rec, ts, handled, url, focus}
#   - A new notice (upsert) resets handled=false and refreshes ts.
#   - handled rows older than 24h are pruned on upsert.
#
# Loop safety: this is a separate store the pet chat only *reads*. It never enters askhistory /
# outbox / additionalContext (notices are never re-injected into a session).
#
# Paths: data resolves under CLAUDE_CONFIG_DIR (default ~/.claude).
#
# 알림 저장소: 세션(또는 고정 키)별 1행. 펫 대화창의 "알림" 탭이 이 파일을 읽어
# 사용자 대화와 분리해 보여준다. notify.py 가 upsert, 펫이 읽고 클릭 시 handled 표시.
#
# 설계 원칙:
#   - 표준 라이브러리만 사용(펫/monitor/enforce 어디서 import 해도 되게).
#   - fcntl 락 + 원자적 쓰기(.tmp → os.replace). 어떤 실패도 예외로 터뜨리지 않는다.
#   - 파일 구조: { "<key>": {row}, ... }  (key = cli session_id, 또는 "slack" 같은 고정 키)
#     → "세션별 1행"이 자동으로 보장된다(같은 키 재알림은 그 행을 덮어쓴다).
#   - row = {session_id, desktop_id, label, icon, summary, rec, ts, handled, url, focus}
#   - 새 알림(upsert)은 handled=false 로 되돌리고 ts 를 갱신한다.
#   - 24시간 지난 handled 행은 upsert 때 정리한다.
#
# 루프 안전성: 이 파일은 펫 대화창이 "읽기"만 하는 별도 저장소다. askhistory /
# outbox / additionalContext 어디에도 들어가지 않는다(알림이 세션에 재주입되지 않음).
#
# 경로: 데이터는 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래에서 해석한다.

import json
import os
import time

try:
    import fcntl
except Exception:                       # non-POSIX fallback (always present on macOS) / 비 POSIX 폴백(이 환경은 macOS → 항상 있음)
    fcntl = None

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
NOTICES = os.path.join(CLAUDE_DIR, "monitor", "notices.json")
LOCK = NOTICES + ".lock"
PRUNE_AGE = 86400                       # drop handled rows older than 24h / 24h 지난 handled 행 제거
# Fields written to disk (order-free; unknown keys ignored on upsert).
# 디스크에 쓰는 필드(순서 무관, 알 수 없는 키는 upsert 에서 무시).
FIELDS = ("session_id", "desktop_id", "label", "icon", "summary", "rec",
          "ts", "handled", "url", "focus")


def _read():
    try:
        with open(NOTICES, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(data):
    tmp = NOTICES + ".tmp"
    try:
        os.makedirs(os.path.dirname(NOTICES), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, NOTICES)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class _Lock:
    """Exclusive lock on NOTICES.lock (if available). Any failure is ignored and we proceed.
    NOTICES.lock 에 대한 배타 락(있으면). 실패해도 통째로 무시하고 진행한다."""
    def __init__(self):
        self.f = None

    def __enter__(self):
        if fcntl is None:
            return self
        try:
            self.f = open(LOCK, "w")
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


def _prune(data, now=None):
    now = now or time.time()
    drop = [k for k, v in data.items()
            if isinstance(v, dict) and v.get("handled")
            and (now - float(v.get("ts") or 0)) > PRUNE_AGE]
    for k in drop:
        data.pop(k, None)
    return data


SESSIONS_GLOB = os.path.expanduser(
    "~/Library/Application Support/Claude/claude-code-sessions/*/*/%s.json")


def _is_archived(desktop_id):
    """isArchived of the desktop session record. Missing/unreadable record → False (don't delete).
    데스크톱 세션 레코드의 isArchived. 레코드가 없거나 못 읽으면 False(지우지 않는다)."""
    if not desktop_id or not str(desktop_id).startswith("local_"):
        return False
    import glob
    for path in glob.glob(SESSIONS_GLOB % desktop_id):
        try:
            with open(path, encoding="utf-8") as f:
                return bool(json.load(f).get("isArchived"))
        except Exception:
            continue
    return False


def prune_archived():
    """Delete notice rows of archived sessions. Returns how many were deleted.
    보관(archive)된 세션의 알림 행을 지운다. 지운 개수를 돌려준다."""
    with _Lock():
        data = _read()
        drop = [k for k, v in data.items()
                if isinstance(v, dict) and _is_archived(v.get("desktop_id"))]
        for k in drop:
            data.pop(k, None)
        if drop:
            _atomic_write(data)
    return len(drop)


def upsert(key, **fields):
    """Update the row for key (session id or fixed key) as a new notice, so handled=false.
    key(세션 id 또는 고정 키)의 1행을 새 알림으로 갱신. 새 알림이므로 handled=false.

    None-valued fields are ignored (existing value kept). ts is fields["ts"] if given, else now.

    None 값 필드는 무시(기존 값 유지). ts 는 fields 에 있으면 그 값, 없으면 now.
    """
    key = (key or "").strip()
    if not key:
        return False
    with _Lock():
        data = _read()
        row = data.get(key)
        row = dict(row) if isinstance(row, dict) else {}
        for k in FIELDS:
            if k in fields and fields[k] is not None:
                row[k] = fields[k]
        row.setdefault("session_id", "")
        row["handled"] = False                      # new notice → back to unhandled / 새 알림 → 미처리로 되돌린다
        row["ts"] = fields.get("ts") or time.time()
        data[key] = row
        _prune(data)
        _atomic_write(data)
    return True


def mark_handled(key):
    """Set the key row to handled=true. Called by the pet click handler and `notify.py --handled`.
    key 행을 handled=true 로. 펫 클릭 핸들러와 `notify.py --handled` 가 호출."""
    key = (key or "").strip()
    if not key:
        return False
    with _Lock():
        data = _read()
        row = data.get(key)
        if not isinstance(row, dict):
            return False
        row["handled"] = True
        data[key] = row
        _atomic_write(data)
    return True


def load():
    """All rows as a newest-first (ts desc) list, each including its 'key'.
    모든 행을 최신(ts 내림차순) 리스트로. 각 행에 'key' 를 포함해 반환."""
    data = _read()
    rows = []
    for k, v in data.items():
        if isinstance(v, dict):
            r = dict(v)
            r["key"] = k
            rows.append(r)
    rows.sort(key=lambda r: float(r.get("ts") or 0), reverse=True)
    return rows


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "--handled":
        print("handled:", mark_handled(sys.argv[2]))
    else:
        for r in load():
            print(json.dumps(r, ensure_ascii=False))
