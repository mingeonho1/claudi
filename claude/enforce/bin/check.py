#!/usr/bin/env python3
# ~/.claude/enforce/bin/check.py
#
# Shared status checker for the enforce layer (standard library only).
# enforce 레이어 공통 상태 점검기(표준 라이브러리만).
#
# Checks, on a factual basis, whether something the user tends to forget has
# stalled, and returns a list of issues each carrying one human-readable line.
# The hook (session-start-hook.py) and launchd (launchd-check.py) both import it.
# "사용자가 까먹어서" 멈춰 있는지를 사실 기준으로 점검하고, 사람이 읽을 한 줄 메시지를
# 단 문제 목록을 돌려준다. 훅(session-start-hook.py)·launchd(launchd-check.py)가 공통 import.
#
# Checks:
#   a. slack_stale : the Slack digest routine is configured (state file exists)
#                    but idle for ~2h+. Weekdays 09-20 only. No state file
#                    (routine not set up) means no check.
#   b. pet_down    : the standalone pet is not running. Only when the standalone
#                    flag is set, weekdays 09-20 (info — launchd revives it within 1h).
# 점검 항목: a. slack_stale(Slack 요약 루틴이 설정돼 있는데 2시간+ 미동작, 평일 09~20시),
#          b. pet_down(독립 펫 미실행, standalone 플래그가 있을 때·평일 09~20시, launchd 가 복구).
#
# Design: never dies on malformed input (treats "cannot tell" conservatively as "no issue").
# 설계: 어떤 입력 이상에도 예외로 죽지 않는다(판별 불가하면 "문제 없음"으로 보수적 처리).
#
# Paths: state/flag/throttle resolve under CLAUDE_CONFIG_DIR (default ~/.claude).
# 경로: 상태/플래그/throttle 은 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래에서 해석한다.
#
# CLI:  python3 check.py  -> {"ok":bool,"count":n,"issues":[...]} as JSON.
# CLI:  python3 check.py  → {"ok":bool,"count":n,"issues":[...]} JSON 출력.

import json
import os
import sys
import time
from datetime import datetime

# Shared language helper (enforce/bin -> ../../claudi/lib); fail-safe: English.
# 공용 언어 헬퍼 로드(enforce/bin -> ../../claudi/lib, 실패 시 영어 폴백).
_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                    "claudi", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
try:
    from claudi_i18n import t
except Exception:
    def t(en, ko):  # fail-safe fallback: English / 폴백: 영어
        return en

CLAUDE = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
ENFORCE = os.path.join(CLAUDE, "enforce")
MONITOR = os.path.join(CLAUDE, "monitor")
SLACK_STATE = os.path.join(MONITOR, "slack-state.json")
THROTTLE_FILE = os.path.join(ENFORCE, ".notify-throttle.json")
STANDALONE_FLAG = os.path.join(ENFORCE, "standalone-pet")   # standalone pet flag / 독립 펫 모드 플래그
PET_PORT_NAME = "claudlet-default.port"                     # standalone pet (session=default) port / 독립 펫 포트

SLACK_STALE_SEC = 7500         # ~2h5m (matches the hourly Slack digest task) / 약 2시간 5분
THROTTLE_SEC = 7200            # 2h (min gap before re-notifying the same issue set) / 2시간

WORK_START_HOUR = 9            # weekday work window (issues count only inside it) / 평일 업무 구간
WORK_END_HOUR = 20            # 09:00 - 20:00


# -- common utilities / 공통 유틸 --------------------------------------------

def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json_atomic(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_epoch(v):
    """Parse several formats into epoch seconds (float); None on failure.
    여러 형식을 epoch 초(float)로 관대하게 파싱. 실패 시 None.
    Accepts: unix seconds (number or string) and ISO8601 (with/without offset, 'Z').
    허용: 유닉스 초(숫자/문자열), ISO8601('...+09:00' / 'Z' / 오프셋 미포함)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def in_work_window(now=None):
    """True on weekdays (Mon-Fri) 09:00-20:00.
    평일(월~금) 09:00~20:00 이면 True."""
    now = now or datetime.now()
    if now.weekday() >= 5:       # Sat(5)/Sun(6) / 토(5)·일(6)
        return False
    return WORK_START_HOUR <= now.hour < WORK_END_HOUR


# -- a) Slack digest / Slack 요약 --------------------------------------------

def check_slack(now=None):
    """Check whether the configured Slack digest routine has been idle too long.
    Slack 요약 루틴이 설정돼 있는데(상태파일 존재) 오래 미동작인지 점검."""
    now = now or datetime.now()
    if not in_work_window(now):
        return []
    data = _load_json(SLACK_STATE)
    if not data:
        return []                                 # routine not set up (no state file) / 루틴 미설정
    # Prefer last_run (set by slack_items.py); fall back to last_ts for old files,
    # tolerant about format. last_ts (latest message ts) stops advancing when there
    # are no new messages, so it falsely trips slack_stale and is no longer primary.
    # last_run(루틴 실행 시각) 우선, 없으면 last_ts(옛 호환)로 폴백. last_ts 는 새 메시지가
    # 없으면 안 올라가 오탐을 내므로 더는 1순위가 아니다.
    last = _parse_epoch(data.get("last_run"))
    if last is None:
        last = _parse_epoch(data.get("last_ts"))
    if last is None:
        return []                                 # cannot tell format -> stay quiet / 판별 불가 → 조용히
    age = time.time() - last
    if age > SLACK_STALE_SEC:
        return [{"type": "slack_stale", "severity": "info",
                 "msg": t("Slack digest idle for %d min (check connector/scheduled task)",
                          "Slack 요약 %d분째 미동작(커넥터/예약작업 확인)") % int(age // 60)}]
    return []


# -- b) standalone pet liveness / 독립 펫 생존 -------------------------------

def _pet_port_alive():
    """Light liveness probe: can we open a TCP connection to the pet's port file.
    독립 펫 포트 파일로 TCP 연결이 열리는지(가벼운 생존 프로브)."""
    import socket
    import tempfile
    rd = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    path = os.path.join(rd, PET_PORT_NAME)
    try:
        with open(path) as f:
            port = int(f.read().strip())
    except (OSError, ValueError):
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def check_pet(now=None):
    """Only in standalone mode: check during work hours that the pet is up (info if down).
    독립 펫 모드일 때만, 업무 구간에 펫이 떠 있는지 점검(미실행이면 info)."""
    now = now or datetime.now()
    if not os.path.exists(STANDALONE_FLAG):
        return []                                 # session-attached policy -> not our concern / 세션-부착 정책 → 점검 대상 아님
    if not in_work_window(now):
        return []
    if _pet_port_alive():
        return []
    return [{"type": "pet_down", "severity": "info",
             "msg": t("Standalone pet not running (launchd revives it within 1h "
                      "— manual: claudlet-attach --standalone)",
                      "독립 펫 미실행(launchd 가 최대 1시간 내 되살립니다 — 수동: "
                      "claudlet-attach --standalone)")}]


# -- aggregation / 종합 ------------------------------------------------------

def collect_issues(now=None):
    now = now or datetime.now()
    issues = []
    try:
        issues += check_slack(now)
    except Exception:
        pass
    try:
        issues += check_pet(now)
    except Exception:
        pass
    return issues


def summary_line(issues, maxlen=360):
    s = " / ".join(i.get("msg", "") for i in issues if i.get("msg"))
    if len(s) > maxlen:
        s = s[:maxlen - 1] + "…"
    return s


# -- notify throttle (shared by hook + launchd) / 알림 throttle(훅·launchd 공유) --

def throttle_key(issues):
    return "|".join(sorted({i.get("type", "") for i in issues})) or "none"


def should_notify(key, ttl=THROTTLE_SEC, now=None):
    """True if the same key was not notified within the last ttl seconds (and record 'just notified').
    지난 ttl 초 안에 같은 key 로 알린 적 없으면 True(그리고 '방금 알림'으로 기록).

    The hook and launchd share one THROTTLE_FILE, so whichever notifies first,
    only one notification goes out within ttl.
    훅과 launchd 가 같은 THROTTLE_FILE 을 공유하므로, 어느 쪽이 먼저 알리든 ttl 안엔 한 번만."""
    now = now if now is not None else time.time()
    data = _load_json(THROTTLE_FILE) or {}
    if now - _as_float(data.get(key, 0)) < ttl:
        return False
    data[key] = now
    cutoff = now - 86400
    data = {k: v for k, v in data.items() if _as_float(v) >= cutoff}
    _write_json_atomic(THROTTLE_FILE, data)
    return True


def main():
    issues = collect_issues()
    print(json.dumps(
        {"ok": not issues, "count": len(issues), "issues": issues},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print('{"ok": true, "count": 0, "issues": []}')
        sys.exit(0)
