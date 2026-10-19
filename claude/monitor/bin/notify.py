#!/usr/bin/env python3
# ~/.claude/monitor/bin/notify.py
#
# Send a one-line notification: claudlet pet speech bubble (notice) + macOS notification.
# 한 줄 알림 보내기: claudlet 펫 말풍선(notice) + macOS 알림.
# Usage / 사용법:
#   notify.py --title "TITLE" --body "BODY" [--session SID]
#             [--no-say] [--no-notify]
#             [--open-url URL] [--focus SID]
#
# Design principles / 설계 원칙:
#   - Standard library only (no claudlet import -> unaffected by reinstall/move).
#     표준 라이브러리만 사용(claudlet import 안 함 → 재설치/이동에 영향 안 받음).
#   - No failure ever raises; always exit 0 quietly.
#     어떤 실패도 예외로 터뜨리지 않고 조용히 exit 0.
#   - Pet not running (= no .port file) -> skip quietly.
#     펫 미실행(=.port 파일 없음)이면 조용히 스킵.
#   - With --session and that session's pet alive, send only to it; otherwise
#     broadcast to every live claudlet-*.port pet.
#     --session 이 주어지고 그 세션 펫이 살아 있으면 그 펫에게만, 아니면
#     살아 있는 모든 claudlet-*.port 펫에게 브로드캐스트.
#
# claudlet display protocol (verified: claudlet/core/hostinfo.py, pet.py + enforce patch C):
# claudlet 표시 프로토콜(검증: claudlet/core/hostinfo.py, pet.py + enforce patch C):
#   Port file = <XDG_RUNTIME_DIR or OS temp>/claudlet-<session_id>.port (integer port)
#   Connect TCP to 127.0.0.1:<port>, send one line {"cmd":"notice","text":"..."}\n, close.
#   notice = display only (bubble only; no history/outbox/relay). The old "say" was
#   recorded in session history as an 'answer', polluting the chat and causing loops.
#   The pet hard-cuts at 120 chars (SAY_MAX) and shows for 12s -> we cut at a
#   sentence/clause boundary within 110 chars (SAY_LIMIT) to avoid mid-word cuts and raw markdown.
#   포트 파일 = <XDG_RUNTIME_DIR 또는 OS temp>/claudlet-<session_id>.port (정수 포트)
#   127.0.0.1:<port> 로 TCP 연결해 {"cmd":"notice","text":"..."}\n 한 줄 전송 후 닫기.
#   notice = 표시 전용(말풍선만, 내역/아웃박스/릴레이 없음). 옛 "say" 는 세션 내역에
#   '답'으로 기록돼 대화창을 오염시키고 루프를 만들었다.
#   펫은 120자(SAY_MAX)로 하드 컷하고 12초 표시 → 우리는 110자(SAY_LIMIT) 이내
#   문장/절 경계에서 끊어 단어 중간 잘림·마크다운 노출을 막는다.
#
# macOS notifications / macOS 알림:
#   - If terminal-notifier is installed and --open-url/--focus is given, show a clickable
#     notification (open deep link / run focus script).
#     terminal-notifier 가 설치돼 있고 --open-url/--focus 가 주어지면 클릭 가능한
#     알림(딥링크 열기 / focus 스크립트 실행)을 띄운다.
#   - Otherwise (including not installed) fall back to osascript display notification (not clickable).
#     아니면(미설치 포함) 기존 osascript display notification 으로 폴백(클릭 불가).
#
# Desktop suppression: CLAUDI_NO_DESKTOP_NOTIFY=1 skips both the bubble TCP send and the
# macOS notification (for tests/headless). The notices.json upsert still runs.
# 데스크톱 알림 억제: CLAUDI_NO_DESKTOP_NOTIFY=1 이면 말풍선 TCP 전송·macOS 알림을
# 모두 건너뛴다(테스트/헤드리스용). notices.json upsert 는 그대로 수행된다.
#
# Path rule: sibling (notices) and parent-sibling (enforce/bin) scripts are relative to this file.
# 경로 규칙: 형제(notices)·형제-of-parent(enforce/bin) 스크립트는 이 파일 기준 상대경로.

import argparse
import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile

SAY_MAX = 120          # pet's hard cut (reference) / 펫의 하드 컷(참고용)
SAY_LIMIT = 110        # max length we allow per say / 한 say 당 최대 길이
SAY_MAX_CHUNKS = 2     # max number of say messages / say 메시지 최대 개수
LOOPBACK = "127.0.0.1"

_BIN = os.path.dirname(os.path.abspath(__file__))
_ENFORCE_BIN = os.path.join(os.path.dirname(os.path.dirname(_BIN)), "enforce", "bin")
FOCUS_SCRIPT = os.path.join(_ENFORCE_BIN, "focus_session.py")
OPEN_LINK = os.path.join(_ENFORCE_BIN, "open-link.sh")

# Disable desktop side effects (pet TCP, macOS notification) for tests/headless.
# 테스트/헤드리스에서 데스크톱 부작용(펫 TCP·macOS 알림)을 끈다.
NO_DESKTOP = os.environ.get("CLAUDI_NO_DESKTOP_NOTIFY") == "1"

# Sentence/clause boundary priority (each marker includes one trailing space, which is dropped).
# The first two are Korean sentence endings ("-da.", "-yo.") — matching data, kept as-is.
# 문장/절 경계 우선순위(각 마커는 뒤에 공백 1칸을 포함; 공백은 버리고 앞부분만 남김).
# 앞의 두 개는 한국어 문장 종결 — 매칭용 데이터라 그대로 둔다.
SAY_BOUNDARIES = ("다. ", "요. ", ". ", "! ", "? ", "; ", ", ")


# ──────────────────────────────────────────────────────────────────────────
# Pure functions: markdown stripping + sentence-boundary split (unit tested)
# 순수 함수: 마크다운 제거 + 문장 경계 분할 (단위 테스트 대상)
# ──────────────────────────────────────────────────────────────────────────

def _strip_leading_marks(line):
    """Remove a line's leading heading (#), quote (>), and bullet (- * 1.) markers.
    한 줄의 선두 제목(#)·인용(>)·불릿(- * 1.) 마커를 제거."""
    s = line.lstrip()
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r'^#{1,6}\s*', '', s)      # heading / 제목  # ##
        s = re.sub(r'^(?:>\s?)+', '', s)       # quote / 인용  > >
        s = re.sub(r'^[-*]\s+', '', s)         # bullet / 불릿  "- " "* "
        s = re.sub(r'^\d+\.\s+', '', s)        # numbered / 번호  "1. "
        s = s.lstrip()
    return s


def strip_markdown(raw):
    """Return plain text with markdown decoration stripped and whitespace collapsed (pure).
    마크다운 장식을 벗기고 공백을 단일 스페이스로 접은 평문을 반환(순수 함수).

    Handles: **bold**/__bold__, *italic*/_italic_ -> text, `code` -> code,
    leading #/> and bullet markers removed, [text](url) -> text, all whitespace -> one space.
    처리: **bold**/__bold__·*italic*/_italic_ → 텍스트, `code` → code,
    선두 #/>·불릿 마커 제거, [text](url) → text, 모든 공백/개행 → 단일 스페이스.
    """
    text = raw or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 1) Strip per-line leading markers. / 줄 단위 선두 마커 제거.
    lines = [_strip_leading_marks(ln) for ln in text.split("\n")]
    text = "\n".join(lines)

    # 2) Strip inline markdown (link -> code -> bold -> italic).
    # 인라인 마크다운 제거(링크 → 코드 → 굵게 → 기울임 순).
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)   # [text](url) → text
    text = re.sub(r'`([^`]+)`', r'\1', text)               # `code` → code
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)         # **bold** → bold
    text = re.sub(r'__([^_]+)__', r'\1', text)             # __bold__ → bold
    text = re.sub(r'\*([^*]+)\*', r'\1', text)             # *italic* → italic
    text = re.sub(r'_([^_]+)_', r'\1', text)               # _italic_ → italic

    # 3) Collapse whitespace/newlines to one space and trim. / 공백/개행을 단일 스페이스로 접고 트림.
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _cut_one(text, limit=SAY_LIMIT):
    """Split text (assumed len > limit) into (head, rest) at a boundary within limit.
    text(길이 > limit 가정)를 limit 이하 경계에서 (head, rest)로 분할.

    Tries priority markers, then the last space; never cuts mid-word.
    우선순위 마커 → 마지막 공백 순으로 시도하며 단어 중간을 자르지 않는다.
    """
    for marker in SAY_BOUNDARIES:
        idx = text.rfind(marker, 0, limit + 1)
        if idx != -1:
            # Keep the marker's punctuation in head; drop the trailing space.
            # 마커의 구두점은 head 에 남기고 뒤따르는 공백은 버린다.
            head = text[:idx + len(marker) - 1]
            rest = text[idx + len(marker):]
            return head, rest
    # No boundary: cut at the last space within limit.
    # 경계가 없으면 limit 이내 마지막 공백에서 자른다.
    idx = text.rfind(" ", 0, limit + 1)
    if idx != -1:
        return text[:idx], text[idx + 1:]
    # Pathological input with no spaces: hard cut to guarantee length (mid-word, exceptional).
    # 공백조차 없는 병적 입력: 길이 보장을 위해 하드 컷(단어 중간, 예외적).
    return text[:limit], text[limit:]


def split_say_text(cleaned, limit=SAY_LIMIT, max_chunks=SAY_MAX_CHUNKS):
    """Split plain text into at most max_chunks say chunks (pure).
    평문을 최대 max_chunks 개의 say 청크로 분할(순수 함수).

    - Within limit: one chunk as-is.
    - Longer: cut at a sentence/clause boundary; a meaningful remainder becomes chunk 2.
    - No ellipsis ('…') is appended. Anything beyond is dropped from say.
    - limit 이하이면 그대로 1개.
    - 더 길면 문장/절 경계에서 끊고, 의미 있는 나머지가 있으면 2번째 청크까지.
    - '…' 같은 생략부호는 붙이지 않는다. 그 이상 나머지는 say 에서 버린다.
    """
    cleaned = (cleaned or "").strip()
    if not cleaned:
        return []
    chunks = []
    rest = cleaned
    while rest and len(chunks) < max_chunks:
        if len(rest) <= limit:
            chunks.append(rest)
            rest = ""
            break
        head, rest = _cut_one(rest, limit)
        head = head.strip()
        rest = rest.strip()
        if head:
            chunks.append(head)
        # Stop if the remainder has no real content (word characters).
        # 남은 조각에 실제 내용(단어 문자)이 없으면 더 보내지 않는다.
        if not any(ch.isalnum() for ch in rest):
            break
    return chunks


# ──────────────────────────────────────────────────────────────────────────
# Pet say delivery / 펫 say 전송
# ──────────────────────────────────────────────────────────────────────────

def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()


def port_file(session_id):
    return os.path.join(runtime_dir(), "claudlet-%s.port" % session_id)


def read_port(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def send_to_port(port, line, timeout=0.3):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((LOOPBACK, port))
        s.sendall(line.encode("utf-8"))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _send_all(port, lines):
    """Judge liveness by whether the first line (probe) sends; if so, send the rest best-effort.
    첫 줄(프로브) 전송 성공 여부로 생존 판정. 성공 시 나머지도 best-effort 전송."""
    if not lines:
        return False
    if not send_to_port(port, lines[0]):
        return False
    for ln in lines[1:]:
        send_to_port(port, ln)
    return True


def claudlet_say(text, session_id=None):
    if NO_DESKTOP:
        return 0                     # tests/headless: no pet TCP / 테스트/헤드리스: 펫 TCP 전송 안 함
    chunks = split_say_text(strip_markdown(text))
    if not chunks:
        return 0
    # "notice" is display-only (enforce patch C): the pet only shows a bubble and leaves
    # nothing in askhistory/outbox/relay. The old "say" went through _deliver_answer and
    # was recorded in session history as an 'answer' (polluting the chat), seeding loops.
    # Unpatched (old) pets ignore notice as an unknown cmd — nothing shows, nothing leaks.
    # "notice" 는 표시 전용(enforce patch C): 펫이 말풍선만 띄우고 askhistory/outbox/
    # 릴레이에 남기지 않는다. 예전의 "say" 는 _deliver_answer 를 거쳐 세션 내역에 '답'
    # 으로 기록됐고(대화창 오염) 루프의 씨앗이 됐다. 패치 안 된(옛) 펫은 notice 를
    # 모르는 cmd 로 무시한다 — 아무것도 안 뜰 뿐 원문이 새지는 않는다.
    lines = [json.dumps({"cmd": "notice", "text": c}, ensure_ascii=False) + "\n"
             for c in chunks]

    # If the specific session's pet is alive, send only there.
    # 특정 세션 펫이 살아 있으면 거기로만.
    if session_id:
        port = read_port(port_file(session_id))
        if port is not None and _send_all(port, lines):
            return 1

    # Otherwise broadcast to every live pet.
    # 없으면 살아 있는 모든 펫에게 브로드캐스트.
    n = 0
    for path in glob.glob(os.path.join(runtime_dir(), "claudlet-*.port")):
        port = read_port(path)
        if port is not None and _send_all(port, lines):
            n += 1
    return n


# ──────────────────────────────────────────────────────────────────────────
# macOS notifications / macOS 알림
# ──────────────────────────────────────────────────────────────────────────

def _strip_nl(s):
    return (s or "").replace("\r", " ").replace("\n", " ")


def _esc(s):
    # AppleScript string literal: escape backslashes/quotes, drop newlines.
    # AppleScript 문자열 리터럴: 역슬래시·따옴표 이스케이프, 개행 제거.
    s = (s or "").replace("\\", "\\\\").replace('"', '\\"')
    return s.replace("\n", " ").replace("\r", " ")


def _osascript_notify(title, body):
    title = _esc(title)[:200]
    body = _esc(body)[:480]
    script = ('display notification "%s" with title "%s" sound name "Glass"'
              % (body, title))
    try:
        subprocess.run(["osascript", "-e", script],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5)
    except Exception:
        pass


def macos_notify(title, body, open_url=None, focus=None):
    """Prefer a clickable notification (terminal-notifier); fall back to osascript if missing.
    클릭 가능한 알림(terminal-notifier) 우선, 실패/미설치 시 osascript 폴백.

    - open_url given: clicking opens that deep link.
    - focus given without open_url: clicking runs the focus-session script.
    - terminal-notifier not installed: osascript fallback (not clickable).
    - open_url 지정 시: 클릭하면 해당 딥링크를 연다.
    - open_url 없이 focus 지정 시: 클릭하면 focus-session 스크립트를 실행한다.
    - 둘 다 없거나 terminal-notifier 미설치/오류: 기존 osascript(클릭 불가) 폴백.
    """
    if NO_DESKTOP:
        return                       # tests/headless: no macOS notification / 테스트/헤드리스: macOS 알림 안 보냄
    # Hooks/launchd may lack /opt/homebrew/bin in PATH, so append it explicitly.
    # 훅·launchd 는 PATH 에 /opt/homebrew/bin 이 없을 수 있어 경로를 직접 붙여 찾는다.
    tn = shutil.which("terminal-notifier",
                      path=os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin")
    if tn:
        # If terminal-notifier exists, ALWAYS use only it — osascript display notification's
        # sender app is Script Editor, so clicking it would launch that app.
        # terminal-notifier 가 있으면 "항상" 이것만 쓴다 — osascript display
        # notification 은 발신 앱이 '스크립트 편집기'라 클릭 시 그 앱이 떠서다.
        t = _strip_nl(title)[:256]
        b = _strip_nl(body)
        cmd = [tn, "-title", t, "-message", b, "-group", "claude-monitor"]
        if open_url:                 # click -> open deep link / 클릭 → 딥링크 열기
            # -open ignores custom-scheme (claude://) clicks on macOS 26, so use -execute.
            # Script path is shlex.quote'd too — safe even if CLAUDE_CONFIG_DIR has spaces.
            # -open 은 macOS 26에서 커스텀 스킴(claude://) 클릭이 무시돼 -execute 로 연다.
            # 스크립트 경로도 shlex.quote — CLAUDE_CONFIG_DIR 에 공백이 있어도 안전하게.
            cmd += ["-execute", "%s %s"
                    % (shlex.quote(OPEN_LINK), shlex.quote(open_url))]
        elif focus:                  # click -> run focus-session script / 클릭 → focus-session 스크립트 실행
            execute = "python3 %s %s" % (shlex.quote(FOCUS_SCRIPT),
                                         shlex.quote(focus))
            cmd += ["-execute", execute]
        # No link/focus: no -open/-execute — clicking opens nothing (never Script Editor).
        # 링크/포커스가 없으면 -open/-execute 없이 — 클릭해도 아무 것도 안 열린다
        # (스크립트 편집기 절대 안 뜸).
        cmd += ["-sound", "Glass"]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass                     # no osascript fallback even on error / 실행 오류여도 osascript 로는 폴백하지 않는다
        return
    # osascript fallback (not clickable) only when terminal-notifier is not installed.
    # terminal-notifier 미설치일 때만 osascript 폴백(클릭 불가).
    _osascript_notify(title, body)


# ──────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────
# notices.json store (pet 'Notices' tab) + session label — all best-effort, harmless on failure.
# notices.json 저장소(펫 '알림' 탭) + 세션 라벨 — 모두 best-effort, 실패해도 무해.
# ──────────────────────────────────────────────────────────────────────────

def _notices():
    """The notices module (notification store) in this directory; None if import fails.
    같은 디렉터리의 notices 모듈(알림 저장소). import 실패하면 None."""
    try:
        if _BIN not in sys.path:
            sys.path.insert(0, _BIN)
        import notices
        return notices
    except Exception:
        return None


def _session_label(sid):
    """Session label via enforce/bin/sessionlabel ('[App · title]' / '[CLI · …]'); '' on failure.
    enforce/bin/sessionlabel 로 '[앱 · 제목]' / '[CLI · …]' 라벨. 실패하면 ''."""
    sid = (sid or "").strip()
    if not sid:
        return ""
    try:
        if _ENFORCE_BIN not in sys.path:
            sys.path.insert(0, _ENFORCE_BIN)
        import sessionlabel
        return sessionlabel.label(sid) or ""
    except Exception:
        return ""


def _lead_icon(title):
    """Use the title's leading emoji (✅/🟡/🛑/🔔/💬 …) as the icon; '' if it starts with ASCII.
    title 앞머리 이모지(✅/🟡/🛑/🔔/💬 …)를 아이콘으로. ASCII 로 시작하면 ''."""
    t = (title or "").strip()
    if not t:
        return ""
    first = t.split()[0]
    return first if first and not first[0].isascii() else ""


def _desktop_from_url(url):
    u = url or ""
    if "/epitaxy/" in u:
        return u.rsplit("/epitaxy/", 1)[-1].strip()
    return ""


def notices_upsert(key, session, label, icon, summary, rec, url, focus):
    """Update notices.json row `key` with this notice (one row per session; new notice -> unhandled).
    notices.json 의 key 행을 이 알림으로 갱신(세션별 1행, 새 알림이면 미처리로)."""
    mod = _notices()
    if mod is None or not key:
        return
    try:
        mod.upsert(
            key,
            session_id=session or "",
            desktop_id=_desktop_from_url(url),
            label=label or "",
            icon=icon or "💬",
            summary=(summary or "").strip()[:160],
            rec=(rec or "").strip()[:160],
            url=(url or "").strip(),
            focus=(focus or "").strip(),
        )
    except Exception:
        pass


def notices_mark_handled(key):
    mod = _notices()
    if mod is None or not key:
        return
    try:
        mod.mark_handled(key)
    except Exception:
        pass


def main():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--title", default="Claude")
    p.add_argument("--body", default="")
    p.add_argument("--session", default=None)
    p.add_argument("--no-say", action="store_true")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--open-url", default=None)
    p.add_argument("--focus", default=None)
    p.add_argument("--icon", default=None)     # icon; default: title's leading emoji / 알림 아이콘, 없으면 title 앞 이모지
    p.add_argument("--rec", default="")        # recommendation (optional) / 추천(선택) — 알림 탭 "→ 추천"
    p.add_argument("--label", default=None)    # session label; default: computed / 세션 라벨, 없으면 sessionlabel 로 계산
    p.add_argument("--key", default=None)      # notices row key; default --session / notices 행 키, 없으면 --session (예: "slack")
    p.add_argument("--handled", default=None)  # only mark this key handled / 이 키 알림을 처리됨으로만 표시
    args, _ = p.parse_known_args()

    # Mark-handled-only mode: set the notices row handled=true, no bubble/notification.
    # 처리 표시 전용 모드: 말풍선/알림 없이 notices 행만 handled=true 로.
    if args.handled:
        notices_mark_handled(args.handled)
        return

    # Session label: explicit value first, else computed (app: title; CLI: terminal/cwd/branch).
    # 세션 라벨: 명시값 우선, 없으면 세션에서 계산(앱이면 제목, CLI면 터미널·cwd·브랜치).
    label = args.label if args.label is not None else _session_label(args.session)
    icon = args.icon or _lead_icon(args.title) or "💬"

    # Bubble/notification body: with a label, "label summary" shows which session it is.
    # 말풍선/알림 본문: 라벨이 있으면 "라벨 요약" 으로 어느 세션인지 드러낸다.
    body = args.body or ""
    disp_body = ("%s %s" % (label, body)).strip() if label else body.strip()
    say_source = disp_body or args.title

    if not args.no_say:
        try:
            claudlet_say(say_source, args.session)
        except Exception:
            pass
    if not args.no_notify:
        try:
            # Notification body: full plain text with markdown stripped (each backend limits length).
            # 알림 본문에는 마크다운만 벗긴 전체 평문(길이 제한은 각 백엔드에서).
            cleaned_body = strip_markdown(disp_body or args.title)
            macos_notify(args.title, cleaned_body,
                         open_url=args.open_url, focus=args.focus)
        except Exception:
            pass

    # notices.json upsert — persistent store for the pet's 'Notices' tab (always written).
    # Key: --key first, else session id; sessionless notices pass a fixed --key ("slack" etc.).
    # notices.json upsert — 펫 '알림' 탭의 영속 저장소(펫 생존과 무관하게 항상 기록).
    # 키: --key 우선, 없으면 세션 id. 세션 없는 알림은 --key 로 고정 키("slack" 등)를 준다.
    key = (args.key or args.session or "").strip()
    if key:
        notices_upsert(key, args.session, label, icon, args.body, args.rec,
                       args.open_url, args.focus)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
