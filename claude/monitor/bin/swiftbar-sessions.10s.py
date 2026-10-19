#!/usr/bin/env python3
# ~/.claude/monitor/bin/swiftbar-sessions.10s.py
#
# SwiftBar plugin: shows the live status of parallel Claude Code sessions in the
# menu bar. (Safe under python3 -I; standard library only.)
# SwiftBar 플러그인: 병렬로 돌아가는 Claude Code 세션들의 실시간 상태를
# 메뉴 막대에 보여준다. (python3 -I 로 실행해도 안전, 표준 라이브러리만.)
#
#   The state (❓ waiting on a question / 🔴 failed / 🟢 working / 🟡 needs reply /
#   ✔ done) is computed LIVE from the transcript tail, not from the latest
#   event in events.jsonl.
#   상태(❓ 질문대기 / 🔴 실패 / 🟢 진행중 / 🟡 답필요 / ✔ 끝남)는
#   events.jsonl 의 최신 이벤트가 아니라 transcript 꼬리를 읽어 LIVE 로 계산한다.
#
# Data sources:
# 데이터 소스:
#   ~/.claude/monitor/events.jsonl   — per-session events (Stop/StopFailure/Notification)
#                                      / 세션별 이벤트
#   ~/.claude/monitor/monitor-session — monitor session id (excluded from the list)
#                                      / 관제 세션 id(목록에서 제외)
#   ~/.claude/monitor/board.json     — summary/recommendation written by the monitor session (optional)
#                                      / 관제 세션이 써 주는 요약/추천(선택)
#   <transcript_path>                — session transcript (JSONL; only the ~200KB tail is read)
#                                      / 세션 대화 기록(JSONL, 꼬리 ~200KB만 읽음)
#   ~/.claude/enforce/bin/sessionlabel.py — session label ("[App · title]"), result cached
#                                      / 세션 라벨, 결과 캐시
#
# <xbar.title>Claude session monitor</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>claudi</xbar.author>
# <xbar.desc>Shows the status of parallel Claude Code sessions (waiting/working/needs reply/done) in the menu bar</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <bitbar.title>Claude session monitor</bitbar.title>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>false</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>false</swiftbar.hideSwiftBar>

import concurrent.futures
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

# Shared language helper (monitor/bin -> ../../claudi/lib); fail-safe: English.
# 공용 언어 헬퍼 로드(monitor/bin -> ../../claudi/lib, 실패 시 영어 폴백).
_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                    "claudi", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
try:
    from claudi_i18n import t
except Exception:
    def t(en, ko):  # fail-safe fallback: English / 폴백: 영어
        return en

HOME = os.path.expanduser("~")
BIN = os.path.dirname(os.path.abspath(__file__))
# Data lives under the config dir (CLAUDE_CONFIG_DIR, default ~/.claude); sibling scripts are relative.
# 데이터는 설정 디렉터리(CLAUDE_CONFIG_DIR, 없으면 ~/.claude) 아래, 형제 스크립트는 상대경로.
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
MON = os.path.join(CLAUDE_DIR, "monitor")
EVENTS = os.path.join(MON, "events.jsonl")
MONITOR_SESSION_FILE = os.path.join(MON, "monitor-session")
BOARD = os.path.join(MON, "board.json")
LABEL_CACHE = os.path.join(MON, ".label-cache.json")
SESSIONLABEL = os.path.join(os.path.dirname(os.path.dirname(BIN)),
                            "enforce", "bin", "sessionlabel.py")
# Desktop session records (for the isArchived check). Same location as sessionkind.py.
# 데스크톱 세션 레코드(보관 여부 isArchived 판정용). sessionkind.py 와 동일 위치.
DESKTOP_SESSIONS_DIR = os.path.join(
    HOME, "Library", "Application Support", "Claude", "claude-code-sessions")

WINDOW_SEC = 12 * 3600       # only events from the last 12h / 최근 12시간 이벤트만
SLACK_WINDOW_SEC = 2 * 3600  # Slack summary section: runs from the last 2h only / Slack 요약 섹션: 최근 2시간 실행만
# Aging rules for CLI sessions without a desktop record (can't be archived) — based on real activity.
# 데스크톱 레코드가 없는 CLI 세션(보관 판정 불가) 노화 규칙 — 실제 활동 기준.
CLI_IDLE_DONE_SEC = 2 * 3600    # 2h idle → demoted to 'done' / 2시간 무활동 → '끝난 세션'으로 강등
CLI_IDLE_DROP_SEC = 12 * 3600   # 12h idle → dropped from the list / 12시간 무활동 → 목록에서 제외
TAIL_BYTES = 200 * 1024      # read only the last 200KB of the transcript / transcript 꼬리 200KB만 읽기
WORKING_MTIME_SEC = 60       # transcript mtime within 60s → working / transcript mtime 이 60초 이내면 진행중
LABEL_TTL = 600              # label cache TTL (seconds) / 라벨 캐시 유효기간(초)
NOW = time.time()

# State icons.
# 상태 아이콘.
Q, FAIL, YELLOW, GREEN, DONE = "❓", "🔴", "🟡", "🟢", "✔"

# Heuristic phrases meaning the last assistant message asks for a reply (per spec).
# Korean matching data — kept as-is because the assistant may reply in Korean.
# 답을 요구하는 마지막 어시스턴트 문구 휴리스틱(스펙 지정). 한국어 매칭 데이터라 유지.
ASK_PHRASES = ("할까요", "정해주", "알려주세요", "눌러 주세요", "해 주세요", "부탁", "선택")

# Wrapper prefixes borrowed from digest-transcript.py (to detect real user prompts).
# digest-transcript.py 에서 가져온 래퍼 프리픽스(실제 사용자 발화 판별용).
WRAP_PREFIXES = (
    "<local-command-caveat>", "<local-command-stdout>", "<local-command-stderr>",
    "<command-name>", "<command-message>", "<command-args>",
    "<command-stdout>", "<command-contents>",
    "<user-prompt-submit-hook>", "<system-reminder>",
)

# Phrases marking a Slack summary as 'no news' (unimportant) to drop; compared lowercased.
# Includes Korean matching data — kept because summaries may be written in Korean.
# Slack 요약에서 '새 소식 없음' 류(중요하지 않음)로 버릴 문구(소문자 비교). 한국어 매칭 데이터 포함.
NO_NEWS = (
    "nothing new", "no new", "no relevant", "all quiet", "조용",
    "관련 메시지가 없", "메시지가 없", "알림을 보내지 않", "알림은 보내지 않",
    "보내지 않았", "새 소식 없", "새 소식이 없", "새 메시지", "별다른",
)

_MD_LEAD = re.compile(r"^\s*(?:[-*•>]\s+|#{1,6}\s+|\d+[.)]\s+)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


# ---------- Shared helpers (some are minimal copies of digest-transcript.py logic) ----------
# ---------- 공용 헬퍼 (일부는 digest-transcript.py 의 최소 로직 복사) ----------

def iso_to_unix(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def text_from_content(content):
    """Only the human-readable text from content (str|list).
    content(str|list) 에서 사람이 읽는 텍스트만."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
        return "\n".join(out)
    return ""


def is_real_user_prompt(row):
    """Is this a real human-typed prompt (not a wrapper/meta/tool_result-only row)?
    래퍼/메타/tool_result 전용이 아닌 사람이 친 실제 프롬프트인가."""
    if row.get("type") != "user":
        return False
    if row.get("isMeta") or row.get("isSidechain"):
        return False
    msg = row.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, list):
        has_text = any(isinstance(b, dict) and b.get("type") == "text"
                       and b.get("text", "").strip() for b in content)
        if not has_text:
            return False
    txt = text_from_content(content).strip()
    if not txt:
        return False
    if txt.lstrip().startswith(WRAP_PREFIXES):
        return False
    return True


def tool_use_blocks(row):
    msg = row.get("message")
    if not isinstance(msg, dict):
        return []
    c = msg.get("content")
    if not isinstance(c, list):
        return []
    return [b for b in c if isinstance(b, dict) and b.get("type") == "tool_use"]


def load_tail(path, maxbytes=TAIL_BYTES):
    """Read only the last ~maxbytes of the transcript as a list of JSON rows.
    transcript 의 마지막 ~maxbytes 만 읽어 JSON 행 리스트로."""
    rows = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > maxbytes:
                f.seek(size - maxbytes)
                f.readline()  # drop the truncated first line / 잘린 첫 줄 버림
            data = f.read()
    except OSError:
        return rows
    for ln in data.split(b"\n"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln.decode("utf-8", "replace")))
        except ValueError:
            continue
    return rows


def clean(s, n=None):
    """Strip the SwiftBar param separator ('|')/newlines, collapse spaces, truncate.
    SwiftBar 파라미터 구분자('|')/개행 제거 + 공백 정리 + 자르기."""
    s = (s or "")
    s = s.replace("|", "¦").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = " ".join(s.split())
    if n and len(s) > n:
        s = s[:n - 1] + "…"
    return s


def first_line(s, n):
    # Pick the first 'meaningful' line (one with at least one letter/digit/Hangul).
    # Lines that are only a markdown bullet like "-" are skipped.
    # 첫 '의미 있는' 줄(알파벳/숫자/한글이 하나라도 있는 줄)을 고른다.
    # "-" 같은 마크다운 불릿만 있는 줄은 건너뛴다.
    for ln in (s or "").splitlines():
        ln = ln.strip()
        if ln and any(c.isalnum() for c in ln):
            return clean(ln, n)
    return ""


def is_asking(text):
    t = (text or "").strip()
    if not t:
        return False
    # Look only at the ending (last 240 chars) — avoids false hits from words mid-body.
    # '끝맺음'(마지막 240자)만 본다 — 긴 본문 중간의 단어로 오탐하지 않도록.
    tail = t[-240:]
    if "?" in tail or "？" in tail:
        return True
    return any(p in tail for p in ASK_PHRASES)


def ago(sec):
    if sec < 0:
        sec = 0
    if sec < 60:
        return t("just now", "방금")
    m = int(sec // 60)
    if m < 60:
        return t("%dm ago", "%d분 전") % m
    h = m // 60
    if h < 24:
        return t("%dh ago", "%d시간 전") % h
    return t("%dd ago", "%d일 전") % (h // 24)


def hhmm(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
    except Exception:
        return "--:--"


def strip_md(s):
    """Strip markdown decoration (bullets/headings/numbers/bold/links) into one plain line.
    마크다운 장식(불릿/헤딩/번호/굵게/링크)을 벗겨 한 줄 평문으로."""
    s = s or ""
    s = _MD_LEAD.sub("", s)
    s = _MD_LINK.sub(r"\1", s)
    s = s.replace("**", "").replace("__", "").replace("`", "").replace("*", "")
    return " ".join(s.split())


def md_lines(text, limit=0):
    """Only the 'meaningful' lines of the body, markdown stripped, as a list.
    본문에서 '의미 있는' 줄만 마크다운 제거해 리스트로."""
    out = []
    for ln in (text or "").splitlines():
        s = strip_md(ln)
        if s and any(c.isalnum() for c in s):
            out.append(s)
            if limit and len(out) >= limit:
                break
    return out


def is_no_news(text):
    """True (drop it) if the Slack summary is 'no news'-like or empty.
    Slack 요약이 '새 소식 없음' 류이거나 내용이 없으면 True(버림)."""
    t = (text or "").strip()
    if not t:
        return True
    low = t.lower()
    return any(p in low for p in NO_NEWS)


def last_assistant_text(tp):
    """The last assistant text in the transcript tail and its time (unix).
    transcript 꼬리에서 마지막 어시스턴트 텍스트와 그 시각(unix)."""
    text, ts = "", None
    for r in load_tail(tp):
        if r.get("type") == "assistant":
            t = text_from_content(r.get("message", {}).get("content")).strip()
            if t:
                text, ts = t, iso_to_unix(r.get("timestamp"))
    return text, ts


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_desktop_index():
    """cliSessionId -> desktop session record (dict), for the isArchived check.
    Reads the whole directory once to allow O(1) lookups.
    cliSessionId -> 데스크톱 세션 레코드(dict). 보관(isArchived) 판정용.
    디렉터리 전체를 한 번만 읽어 O(1) 조회를 만든다."""
    idx = {}
    try:
        files = glob.glob(
            os.path.join(DESKTOP_SESSIONS_DIR, "**", "local_*.json"), recursive=True)
    except Exception:
        return idx
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                o = json.load(fh)
        except Exception:
            continue
        if isinstance(o, dict):
            cli = o.get("cliSessionId")
            if cli:
                idx[cli] = o
    return idx


# ---------- events.jsonl ----------

def read_events():
    """Collect the latest event and latest Stop time per session.
    세션별 최신 이벤트와 최신 Stop 시각을 수집."""
    latest = {}      # sid -> latest event dict / 가장 최신 이벤트
    laststop = {}    # sid -> latest Stop/StopFailure ts / 최신 Stop/StopFailure ts
    try:
        with open(EVENTS, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                sid = o.get("session_id")
                ts = o.get("ts")
                if not sid or ts is None:
                    continue
                cur = latest.get(sid)
                if cur is None or ts >= cur.get("ts", 0):
                    latest[sid] = o
                if o.get("event") in ("Stop", "StopFailure"):
                    if ts > laststop.get(sid, 0):
                        laststop[sid] = ts
    except OSError:
        pass
    return latest, laststop


# ---------- Session labels (sessionlabel.py, result cached) ----------
# ---------- 세션 라벨(sessionlabel.py, 결과 캐시) ----------

def _fallback_label(ev):
    cwd = ev.get("cwd") or ""
    base = os.path.basename(cwd.rstrip("/")) or t("session", "세션")
    return "[? · %s]" % base


def resolve_labels(sessions, cache):
    """Call sessionlabel.py in parallel only for sessions missing from or expired in the cache.
    캐시에 없거나 만료된 세션만 sessionlabel.py 를 병렬 호출."""
    need = []
    for sid, ev in sessions:
        ent = cache.get(sid)
        if ent and (NOW - ent.get("ts", 0)) < LABEL_TTL and ent.get("label"):
            continue
        need.append((sid, ev))

    def _call(sid):
        try:
            r = subprocess.run([sys.executable, SESSIONLABEL, sid],
                               capture_output=True, text=True, timeout=4)
            out = (r.stdout or "").strip()
            return sid, out
        except Exception:
            return sid, ""

    if need and os.path.exists(SESSIONLABEL):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for sid, out in ex.map(lambda t: _call(t[0]), need):
                if out:
                    cache[sid] = {"label": out, "ts": NOW}

    labels = {}
    for sid, ev in sessions:
        ent = cache.get(sid)
        labels[sid] = (ent.get("label") if ent else "") or _fallback_label(ev)
    return labels


def save_cache(cache):
    try:
        tmp = LABEL_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, LABEL_CACHE)
    except Exception:
        pass


# ---------- State computation ----------
# ---------- 상태 계산 ----------

def analyze(ev, laststop, board):
    tp = ev.get("transcript_path") or ""
    etype = ev.get("event") or ""
    ntype = ev.get("notification_type") or ""
    event_ts = ev.get("ts") or 0

    try:
        tmtime = os.path.getmtime(tp) if tp else 0
    except OSError:
        tmtime = 0

    rows = load_tail(tp)

    last_asst_row = None       # last assistant row (incl. tools) — for pending / 마지막 assistant 행(툴 포함) — pending 판정용
    last_asst_text = ""        # last non-empty assistant text / 마지막 비어있지 않은 assistant 텍스트
    last_text_ts = None        # time of that text row / 그 텍스트 행의 시각
    last_activity_ts = None    # newest timestamp in the tail / 꼬리 전체 중 최신 timestamp
    last_user_ts = None        # time of the last real user prompt / 마지막 실제 사용자 프롬프트 시각
    answered = set()           # tool_use_ids that got a tool_result / 응답(tool_result) 받은 tool_use_id

    for r in rows:
        rtype = r.get("type")
        ts = iso_to_unix(r.get("timestamp"))
        # Count only 'real activity' (user prompts/tool_results, assistant).
        # Meta rows (system/ai-title/custom-title/cost-state, ...) keep getting
        # appended after Stop and would inflate last_activity, so they're excluded.
        # '실제 활동'(사용자 프롬프트/tool_result, 어시스턴트)만 센다.
        # system/ai-title/custom-title/cost-state 등 메타 행은 Stop 이후에도
        # 덧붙어 last_activity 를 부풀리므로 제외한다.
        is_activity = (rtype == "assistant") or (
            rtype == "user" and not r.get("isMeta") and not r.get("isSidechain"))
        if is_activity and ts is not None and (
                last_activity_ts is None or ts > last_activity_ts):
            last_activity_ts = ts
        if rtype == "user":
            msg = r.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), list):
                for b in msg["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tid = b.get("tool_use_id")
                        if tid:
                            answered.add(tid)
            if is_real_user_prompt(r) and ts is not None:
                last_user_ts = ts
        elif rtype == "assistant":
            last_asst_row = r
            txt = text_from_content(r.get("message", {}).get("content")).strip()
            if txt:
                last_asst_text = txt
                last_text_ts = ts

    # (1) Waiting on a question/input — ❓
    # (1) 질문/입력 대기 — ❓
    pending = False
    qtext = ""
    if last_asst_row:
        for b in tool_use_blocks(last_asst_row):
            nm = b.get("name")
            bid = b.get("id")
            if nm in ("AskUserQuestion", "ExitPlanMode") and bid not in answered:
                pending = True
                if nm == "AskUserQuestion":
                    qs = (b.get("input") or {}).get("questions") or []
                    if qs and isinstance(qs[0], dict):
                        qtext = (qs[0].get("question") or qs[0].get("header")
                                 or t("Please answer the question", "질문에 답해 주세요"))
                    else:
                        qtext = t("Please answer the question", "질문에 답해 주세요")
                else:
                    qtext = t("Waiting for plan approval", "계획 승인을 기다리는 중")
                break
    if not pending and etype == "Notification" and ("permission" in ntype or "elicitation" in ntype):
        # 'Waiting' only if the transcript has no newer activity after the event.
        # 이벤트 이후 transcript 에 더 새로운 활동이 없을 때만 '대기'.
        if last_activity_ts is None or last_activity_ts <= event_ts + 2:
            pending = True
            qtext = clean(ev.get("message") or "") or (
                t("Waiting for input", "입력 대기") if "elicitation" in ntype
                else t("Waiting for permission", "권한 승인 대기"))

    # (2) Failed — 🔴 (latest event is StopFailure + no new user message since)
    # (2) 실패 — 🔴 (최신 이벤트 StopFailure + 이후 새 사용자 메시지 없음)
    failure = (not pending and etype == "StopFailure"
               and (last_user_ts is None or last_user_ts <= event_ts + 2))

    # (3) Working — 🟢 (mtime within 60s OR new activity since the last Stop)
    # (3) 진행중 — 🟢 (mtime 60초 이내 OR 마지막 Stop 이후 새 활동)
    stop_ts = laststop  # may be None / None 가능
    working = (tmtime > NOW - WORKING_MTIME_SEC) or (
        stop_ts is not None and last_activity_ts is not None
        and last_activity_ts > stop_ts + 1)

    # Decide the final state.
    # 최종 상태 결정.
    if pending:
        state = Q
    elif failure:
        state = FAIL
    elif working:
        state = GREEN
    else:
        # Idle session: the last assistant message asks something, no user reply since → 🟡
        # 멈춘(idle) 세션: 마지막 어시스턴트가 뭔가 묻고, 이후 사용자 응답 없음 → 🟡
        no_user_after = (last_user_ts is None or last_text_ts is None
                         or last_user_ts <= last_text_ts + 2)
        if last_asst_text and is_asking(last_asst_text) and no_user_after:
            state = YELLOW
        else:
            state = DONE

    # Summary/recommendation: prefer board.json when fresh, else fall back.
    # 요약/추천: board.json 이 신선하면 우선, 아니면 폴백.
    sid = ev.get("session_id")
    be = board.get(sid) if isinstance(board, dict) else None
    board_fresh = False
    if isinstance(be, dict):
        upd = be.get("updated")
        if upd is not None and (stop_ts is None or upd >= stop_ts - 5):
            board_fresh = True
    summary = clean(be.get("summary", ""), 120) if (board_fresh and be) else ""
    recommend = clean(be.get("recommend", ""), 160) if (board_fresh and be) else ""
    if not summary:
        base = qtext if pending else last_asst_text
        summary = first_line(base, 60) or t("(no content)", "(내용 없음)")

    # Real 'activity' time (user/assistant rows), used for aging and the 'ago' display.
    # The transcript mtime is noisy (meta rows get appended), so it's left out of ref_ts.
    # 실제 '활동' 시각(사용자/어시스턴트 행). 노화 판정과 '분 전' 표시에 쓴다.
    # transcript mtime 은 meta 행 덧붙임으로 노이즈가 많아 ref_ts 에서 제외한다.
    activity_ts = last_activity_ts or event_ts
    ref_ts = max(event_ts, last_activity_ts or 0)

    return {
        "sid": sid,
        "state": state,
        "summary": summary,
        "recommend": recommend,
        "deeplink": ev.get("deeplink") or "",
        "cwd": ev.get("cwd") or "",
        "ref_ts": ref_ts,
        "activity_ts": activity_ts,
    }


# ---------- SwiftBar output assembly ----------
# ---------- SwiftBar 출력 조립 ----------

def line(text, prefix="", **params):
    s = prefix + text
    ps = " ".join("%s=%s" % (k, v) for k, v in params.items()
                  if v is not None and v != "")
    return s + " | " + ps if ps else s


def item_lines(s, depth=0):
    """One session's main line + sub-lines (cwd/recommendation).
    한 세션의 메인 라인 + 서브라인(cwd/추천)."""
    pfx = "--" * depth + (" " if depth else "")
    sub = "--" * (depth + 1) + " "
    out = []
    head = "%s %s — %s (%s)" % (s["state"], clean(s["label"], 54),
                                s["summary"], ago(NOW - s["ref_ts"]))
    if s["deeplink"].startswith("claude://"):
        out.append(line(head, prefix=pfx, **{
            "bash": "/usr/bin/open", "param1": s["deeplink"], "terminal": "false"}))
    else:
        out.append(line(head, prefix=pfx))
        # CLI session without a deeplink → show cwd (no action)
        # deeplink 없는 CLI 세션 → cwd 표시(동작 없음)
        if s["cwd"]:
            out.append(line("📁 " + clean(s["cwd"], 70),
                            prefix=sub, color="#808080", size="11"))
    if s["recommend"]:
        out.append(line("💡 " + s["recommend"], prefix=sub, color="#808080", size="11"))
    return out


def build_slack_items(scheduled):
    """'Important' summary items from scheduled Slack sessions in the last 2h + last check time.
    Drops 'no news'-like ones and dedupes identical content (after normalization).
    Slack 예약 세션들에서 최근 2시간 '중요' 요약 항목 + 마지막 확인 시각.
    '새 소식 없음' 류는 버리고, 같은 내용(정규화 기준)은 중복 제거한다."""
    runs = [(sid, ev) for sid, ev, rec in scheduled
            if "slack" in (rec.get("title") or "").lower()]
    runs.sort(key=lambda t: t[1].get("ts", 0), reverse=True)
    last_check = runs[0][1].get("ts") if runs else None
    items, seen = [], set()
    for sid, ev in runs:
        ts0 = ev.get("ts") or 0
        if NOW - ts0 > SLACK_WINDOW_SEC:   # older than 2h is hidden automatically / 2시간 지난 건 자동으로 안 보임
            continue
        text, ats = last_assistant_text(ev.get("transcript_path"))
        if is_no_news(text):
            continue
        lines = md_lines(text)
        if not lines:
            continue
        summary = clean(lines[0], 80)
        key = re.sub(r"\s+", "", summary.lower())
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "ts": ats or ts0,
            "deeplink": ev.get("deeplink") or "",
            "summary": summary,
            "body": [clean(b, 110) for b in lines[1:5]],
        })
    return items, last_check


def main():
    # This plugin runs every 10s, so it also prunes pet notice rows of archived sessions.
    # 10초마다 도는 이 플러그인에서 보관된 세션의 펫 알림 행을 정리한다.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import notices
        notices.prune_archived()
    except Exception:
        pass
    latest, laststop = read_events()

    monitor_sid = ""
    try:
        with open(MONITOR_SESSION_FILE, encoding="utf-8") as f:
            monitor_sid = f.read().strip()
    except OSError:
        pass

    # Last 12h, excluding the monitor session and archived (isArchived) sessions.
    # Scheduled (scheduledTaskId) sessions are left out of the work list but collected for the bottom section.
    # 최근 12h + 관제 세션 제외 + 보관(isArchived) 제외.
    # 예약(scheduledTaskId) 세션은 작업 목록에서 빼되, 하단 전용 섹션용으로 수집.
    desktop_idx = load_desktop_index()
    active = []
    scheduled = []   # (sid, ev, rec) — scheduled sessions / 예약 세션
    for sid, ev in latest.items():
        if sid == monitor_sid:
            continue
        if NOW - (ev.get("ts") or 0) > WINDOW_SEC:
            continue
        if not ev.get("transcript_path"):
            continue
        rec = desktop_idx.get(sid)
        if rec and rec.get("isArchived"):
            continue
        # Scheduled-run session — reuses sessionkind's authoritative field scheduledTaskId.
        # 예약(스케줄) 실행 세션 — sessionkind 의 권위 필드 scheduledTaskId 재사용.
        if rec and rec.get("scheduledTaskId"):
            scheduled.append((sid, ev, rec))
            continue
        active.append((sid, ev))

    board = load_json(BOARD, {})
    cache = load_json(LABEL_CACHE, {})
    if not isinstance(cache, dict):
        cache = {}

    labels = resolve_labels(active, cache)
    save_cache(cache)

    sessions = []
    for sid, ev in active:
        label = labels.get(sid, _fallback_label(ev))
        # Fallback scheduled check: skip if the label prefix is "[Scheduled" even without a
        # desktop record. sessionlabel.py localizes the tag, so match both languages.
        # 폴백 예약 판별: 데스크톱 레코드가 없어도 라벨 접두사가 "[Scheduled"/"[예약"이면 제외.
        if label.startswith(("[Scheduled", "[예약")):
            continue
        info = analyze(ev, laststop.get(sid), board)
        info["label"] = label
        # Aging for CLI sessions without a record (can't be archived): based on real activity.
        # 레코드 없는 CLI 세션(보관으로 뺄 수 없음) 노화: 실제 활동 기준.
        if desktop_idx.get(sid) is None:
            idle = NOW - info["activity_ts"]
            if idle > CLI_IDLE_DROP_SEC:      # 12h idle → drop / 12시간 무활동 → 제외
                continue
            if idle > CLI_IDLE_DONE_SEC and info["state"] not in (Q, FAIL):
                info["state"] = DONE          # 2h idle → done / 2시간 무활동 → 끝난 세션으로
        sessions.append(info)

    groups = {Q: [], FAIL: [], YELLOW: [], GREEN: [], DONE: []}
    for s in sessions:
        groups[s["state"]].append(s)
    for g in groups.values():
        g.sort(key=lambda x: x["ref_ts"], reverse=True)

    counts = {k: len(v) for k, v in groups.items()}

    # --- Scheduled section data (Slack summary) ---
    # --- 예약 섹션 데이터(Slack 요약) ---
    slack_items, slack_last_check = build_slack_items(scheduled)

    # --- Menu bar title ---
    # --- 메뉴 막대 타이틀 ---
    title_parts = []
    for ic in (Q, FAIL, YELLOW, GREEN):
        if counts[ic]:
            title_parts.append("%s%d" % (ic, counts[ic]))
    if slack_items:
        title_parts.append("💬%d" % len(slack_items))
    title = " ".join(title_parts) if title_parts else "✔"

    out = [title, "---"]

    # --- ❓/🔴 Needs attention ---
    # --- ❓/🔴 확인 필요 ---
    urgent = groups[Q] + groups[FAIL]
    if urgent:
        out.append(line(t("Needs attention %d", "확인 필요 %d") % len(urgent), color="#808080", size="11"))
        for s in urgent:
            out.extend(item_lines(s))
        out.append("---")

    # --- 🟡 Needs reply ---
    # --- 🟡 답 필요 ---
    if groups[YELLOW]:
        out.append(line(t("🟡 Needs reply %d", "🟡 답 필요 %d") % counts[YELLOW], color="#808080", size="11"))
        for s in groups[YELLOW]:
            out.extend(item_lines(s))
        out.append("---")

    # --- 🟢 Working ---
    # --- 🟢 진행 중 ---
    if groups[GREEN]:
        out.append(line(t("🟢 Working %d", "🟢 진행 중 %d") % counts[GREEN], color="#808080", size="11"))
        for s in groups[GREEN]:
            out.extend(item_lines(s))
        out.append("---")

    # --- ✔ Done sessions (collapsed submenu) ---
    # --- ✔ 끝난 세션(접힌 서브메뉴) ---
    if groups[DONE]:
        out.append(t("Done sessions %d", "끝난 세션 %d") % counts[DONE])
        for s in groups[DONE]:
            out.extend(item_lines(s, depth=1))

    # --- 💬 Slack summary (last 2h) — fixed section ---
    # --- 💬 Slack 요약 (최근 2시간) — 고정 섹션 ---
    out.append("---")
    out.append(line(t("💬 Slack summary (last 2h)", "💬 Slack 요약 (최근 2시간)"), color="#808080", size="11"))
    if slack_items:
        for it in slack_items:
            head = "💬 %s  %s" % (hhmm(it["ts"]), it["summary"])
            if it["deeplink"].startswith("claude://"):
                out.append(line(head, **{"bash": "/usr/bin/open",
                                         "param1": it["deeplink"], "terminal": "false"}))
            else:
                out.append(line(head))
            for b in it["body"]:
                out.append(line(b, prefix="-- ", color="#808080", size="11"))
    else:
        msg = t("No important alerts in the last 2h", "최근 2시간 중요 알림 없음")
        if slack_last_check:
            msg += t(" · last checked %s", " · 마지막 확인 %s") % hhmm(slack_last_check)
        out.append(line(msg, color="#808080", size="11"))

    # --- Footer ---
    # --- 푸터 ---
    out.append("---")
    out.append(line(t("Refresh", "새로 고침"), refresh="true"))
    out.append(line(t("Open events log", "events 로그 열기"), **{
        "bash": "/usr/bin/open", "param1": EVENTS, "terminal": "false"}))

    # Collapse consecutive separators ('---' '---') left by empty groups into one.
    # 빈 그룹으로 인해 생긴 연속 구분선('---' '---')을 하나로 축약.
    dedup = []
    for ln in out:
        if ln == "---" and dedup and dedup[-1] == "---":
            continue
        dedup.append(ln)

    sys.stdout.write("\n".join(dedup) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Minimal output so SwiftBar doesn't break.
        # SwiftBar 가 죽지 않도록 최소 출력.
        sys.stdout.write(t("⚠\n---\nError: %s\n", "⚠\n---\n에러: %s\n") % clean(str(e), 120))
        sys.exit(0)
