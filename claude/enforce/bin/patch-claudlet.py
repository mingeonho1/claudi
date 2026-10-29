#!/usr/bin/env python3
# ~/.claude/enforce/bin/patch-claudlet.py
#
# (Re)applies "minimal monkey-patches" to claudlet's pet.py. claudlet-install /
# claudlet-update overwrite pet.py with the original, so running this afterward
# restores the patches (idempotent — does nothing if already patched).
# claudlet 의 pet.py 에 "최소 멍키패치" 들을 (재)적용한다. claudlet-install/update 가 pet.py 를
# 원본으로 덮으므로, 그 뒤 이 스크립트를 다시 실행하면 패치가 복원된다(멱등).
#
#   patch A [enforce-click-patch]  : on pet click, call enforce/bin/claudlet_click.
#       open_attention() first -> open the session that needs attention via app
#       deep-link/terminal focus. Falls back to the original app-activate on failure.
#   patch B [enforce-noreply-patch]: neuter _await_reply with an immediate return ->
#       the pet does not relay a session's "last reply" (transcript tailing) into its bubble.
#   patch C [enforce-notice-patch]  : add a {"cmd":"notice"} branch to _handle_event ->
#       display-only one line. Bubble only; leaves nothing in askhistory/outbox/relay.
#       notify.py sends this instead of say (say -> _deliver_answer -> record_answer
#       polluted the chat as an "answer" and seeded loops).
#   patch D [enforce-noactivity-patch]: make the chat timeline() return [] -> the
#       'session activity' tab does not show raw transcript (the monitor pet shows only one notify line).
#   patch E [enforce-alerts-*]      : reuse the empty 'session activity' tab slot as
#       an 'Alerts' tab -> render notices.json newest-first (icon/session name/summary/rec/time,
#       handled dimmed). Row click -> open-link.sh (deep link) or focus_session.py (CLI) + mark that row handled.
#       Separate from the user chat (With the pet), unrelated to raw transcript/askhistory/outbox (keeps loops blocked).
#   patch F [enforce-alerts-slack]  : a divider + 'Slack' subheading + up to 10 at the
#       bottom of the Alerts tab. Renders slack-items.json by importance. key=="slack"
#       rows are excluded from the general list. Item click -> open-link.sh when url exists.
#   patch G [enforce-portrefresh-patch]: a 1-hour timer in _init_socket rewrites the .port
#       file. macOS dirhelper deletes $TMPDIR files untouched for 3+ days, so a long-running
#       pet loses its .port and the liveness check (check.py)/motions/hooks can't find it.
# 패치 요약: A 펫 클릭→주의 세션 열기, B 마지막 응답 릴레이 차단, C notice 표시 전용,
#   D 세션활동 탭 비움, E 세션활동 탭 자리를 '알림' 탭으로(notices.json), F 알림 탭 Slack 영역,
#   G .port 파일 1시간 주기 재기록(macOS $TMPDIR 3일 정리 대응).
#
# Injected code never hardcodes paths — at runtime it computes the enforce/monitor
# directories from CLAUDE_CONFIG_DIR (default ~/.claude).
# 주입된 코드는 경로를 하드코딩하지 않는다 — 실행 시점에 CLAUDE_CONFIG_DIR 로부터 계산한다.
#
# Safety: after patching, validate with py_compile; on failure restore the in-memory
# original immediately. If any anchor is missing, write nothing and stop with a clear message.
# 안전장치: 패치 후 py_compile 검증, 실패 시 메모리 원본으로 즉시 복원. 앵커가 없으면 아무것도 안 쓰고 중단.
#
# Usage:  python3 ~/.claude/enforce/bin/patch-claudlet.py [--revert] [--check]
#         python3 ~/.claude/enforce/bin/patch-claudlet.py --pet-py <path>   (explicit test target)
#   --check : exit 0 if all patches are applied, else 1 (writes nothing).
#   --revert: restore from backup (pet.py.enforce-orig).
#   --pet-py: name the target pet.py explicitly (skips venv autodetect — used by test fixtures).
# 사용: --check(적용 여부만), --revert(백업 원복), --pet-py(대상 명시, 테스트용).

import os
import py_compile
import shutil
import subprocess
import sys

BIN = os.path.dirname(os.path.abspath(__file__))
if BIN not in sys.path:
    sys.path.insert(0, BIN)
import claudlet_paths

# Shared language helper (enforce/bin -> ../../claudi/lib); fail-safe: English.
# 공용 언어 헬퍼 로드(실패 시 영어 폴백). CLI 메시지에 쓴다.
_LIB = os.path.join(os.path.dirname(BIN), "..", "claudi", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
try:
    from claudi_i18n import t
except Exception:
    def t(en, ko):  # fail-safe fallback: English / 폴백: 영어
        return en

VENV_PY = claudlet_paths.venv_python()
TESTED_VERSION = "2.6.0"          # claudlet version these patches are verified against / 검증된 claudlet 버전

CLICK_MARK = "[enforce-click-patch]"
NOREPLY_MARK = "[enforce-noreply-patch]"

CLICK_ANCHOR = "    def _activate_claude(self):\n"
CLICK_BLOCK = (
    "        # " + CLICK_MARK + " On pet click, open the session that needs attention.\n"
    "        try:\n"
    "            import os as _o, sys as _s\n"
    "            _cd = _o.environ.get(\"CLAUDE_CONFIG_DIR\") or _o.path.expanduser(\"~/.claude\")\n"
    "            _p = _o.path.join(_cd, \"enforce\", \"bin\")\n"
    "            if _p not in _s.path:\n"
    "                _s.path.insert(0, _p)\n"
    "            import claudlet_click as _cc\n"
    "            if _cc.open_attention(getattr(self, \"session_id\", \"\")):\n"
    "                return\n"
    "        except Exception:\n"
    "            pass\n"
)

NOREPLY_ANCHOR = "    def _await_reply(self, path, tries=None, whole=False):\n"
NOREPLY_BLOCK = (
    "        # " + NOREPLY_MARK + " Do not relay a session's last reply into the pet (pet is notify-only).\n"
    "        return\n"
)

NOTICE_MARK = "[enforce-notice-patch]"
# Display-only notify path. {"cmd":"notice","text":...} shows the bubble only and
# leaves nothing in askhistory/outbox/relay (say went _show_say->_deliver_answer->
# record_answer, which pinned it into the chat as an "answer" and seeded loops).
NOTICE_ANCHOR = (
    "                self._show_say()\n"
    "            return\n"
)
NOTICE_BLOCK = (
    "        if ev.get(\"cmd\") == \"notice\":\n"
    "            # " + NOTICE_MARK + " Display-only one line — bubble only, nothing in history/outbox/relay.\n"
    "            text = ev.get(\"text\")\n"
    "            if isinstance(text, str) and text.strip():\n"
    "                self._say = text.strip()[:outbox.SAY_MAX]\n"
    "                self._say_until = time.monotonic() + SAY_SEC\n"
    "                self.say(self._say)\n"
    "            return\n"
)

NOACTIVITY_MARK = "[enforce-noactivity-patch]"
# The chat's 'session activity' tab draws the raw transcript (tool calls/assistant
# messages). The monitor pet should show only one notify line, so empty the timeline.
NOACTIVITY_ANCHOR = "    def timeline(self):\n"
NOACTIVITY_BLOCK = (
    "        # " + NOACTIVITY_MARK + " Do not draw raw transcript in the activity tab (monitor pet is notify-only).\n"
    "        return []\n"
)

# -- patch E [enforce-alerts-patch]: an 'Alerts' area in the chat -------------
# Reuse the empty 'Session activity' tab slot to render notices.json as an 'Alerts'
# tab. Fully separate from the user chat (With the pet tab = askhistory) — only
# alerts here, raw transcript shows nowhere. Each row: "icon session name · one-line
# summary -> rec  HH:MM" (rec omitted if empty). Newest-first, refreshed when
# notices.json changes (reuses the existing _live polling), handled rows dimmed.
# Row click -> open-link.sh if url, focus_session.py if CLI, and that row handled=true.
# notices.json goes into no outbox/askhistory/additionalContext (keeps loops blocked).

ALERTS_TAB_MARK = "[enforce-alerts-tab]"
ALERTS_TAB_OLD = '        self._tabs.addTab("Session activity" if en else "세션 활동")\n'
# The tab label follows claudlet's own locale (en) as before; the Korean label uses
# an escape so this file stays ASCII-only outside comments/docstrings/t().
# 탭 라벨은 claudlet 자체 locale(en)을 따른다. 한국어("알림")는 이스케이프로 둔다.
ALERTS_TAB_NEW = ('        self._tabs.addTab("Alerts" if en else "\\uc54c\\ub9bc")'
                  '  # ' + ALERTS_TAB_MARK + ' reuse the activity tab slot as the Alerts tab\n')

ALERTS_WIRE_MARK = "[enforce-alerts-wire]"
ALERTS_WIRE_ANCHOR = "        self._view.setOpenExternalLinks(False)\n"
ALERTS_WIRE_BLOCK = (
    "        # " + ALERTS_WIRE_MARK + " Alert row click -> open link/focus + mark handled.\n"
    "        self._view.setOpenLinks(False)\n"
    "        self._view.anchorClicked.connect(self._on_alert_click)\n"
)

ALERTS_REFRESH_MARK = "[enforce-alerts-refresh]"
ALERTS_REFRESH_ANCHOR = '        if self.view() == "session":\n'
ALERTS_REFRESH_BLOCK = (
    "            # " + ALERTS_REFRESH_MARK + " activity tab slot -> alerts view (notices.json).\n"
    "            self._render_alerts()\n"
    "            return\n"
)

ALERTS_POLL_MARK = "[enforce-alerts-poll]"
ALERTS_POLL_ANCHOR = "    def _poll_session(self):\n"
ALERTS_POLL_BLOCK = (
    "        # " + ALERTS_POLL_MARK + " Alerts view polling — redraw when notices.json changes.\n"
    "        if not self.isVisible() or self.view() != \"session\":\n"
    "            self._live.stop()\n"
    "            return\n"
    "        if self._notices_stamp() != self._seen_stamp:\n"
    "            self.refresh()\n"
    "        return\n"
)

# Injected empty-state strings use \\u escapes so this file carries no bare Hangul
# outside comments/docstrings/t(). ko strings are "아직 알림이 없어요" (alerts empty)
# and "지금 확인할 슬랙 없음" (slack empty); they follow our config lang at runtime.
ALERTS_METHODS_MARK = "[enforce-alerts-methods]"
ALERTS_METHODS_ANCHOR = "    def _set_full(self, on):\n"
ALERTS_METHODS_BLOCK = '''\
    def _render_alerts(self):
        # [enforce-alerts-methods] Render notices.json as 'Alerts' in the activity tab slot.
        # Separate from the user chat (With the pet tab) — alerts only, no raw transcript.
        self._stack.setCurrentWidget(self._view)
        self._seen_stamp = self._notices_stamp()
        self._toggle.setEnabled(False)
        self._clear.setEnabled(False)
        html = self._alerts_html()
        self._view_stick.around(lambda: self._view.setHtml(html))
        self._live.start()

    def _claude_config_dir(self):
        import os as _os
        return _os.environ.get("CLAUDE_CONFIG_DIR") or _os.path.expanduser("~/.claude")

    def _claudi_ko(self):
        # [enforce-alerts-methods] Our output language from <config>/claudi/config.json.
        # Read at runtime; default English (returns False) on any error.
        import os as _os, json as _json
        try:
            _p = _os.path.join(self._claude_config_dir(), "claudi", "config.json")
            with open(_p, encoding="utf-8") as _f:
                return _json.load(_f).get("lang") == "ko"
        except Exception:
            return False

    def _notices_path(self):
        import os as _os
        return _os.path.join(self._claude_config_dir(), "monitor", "notices.json")

    def _notices_stamp(self):
        # Watch both notices.json + slack-items.json stamps (either change refreshes).
        import os as _os
        def _st(p):
            try:
                s = _os.stat(p)
                return (s.st_size, s.st_mtime)
            except OSError:
                return None
        return (_st(self._notices_path()), _st(self._slack_items_path()))

    def _load_notices(self):
        import json as _json
        try:
            with open(self._notices_path(), encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        rows = []
        for k, v in data.items():
            if isinstance(v, dict):
                r = dict(v)
                r["key"] = k
                rows.append(r)
        rows.sort(key=lambda r: float(r.get("ts") or 0), reverse=True)
        return rows

    def _alerts_html(self):
        import html as _html, time as _time
        ko = self._claudi_ko()
        # Slack rows (key=="slack") excluded from the general list (back-compat).
        rows = [r for r in self._load_notices() if r.get("key") != "slack"]
        out = ['<div style="font-family:-apple-system,Helvetica,sans-serif;'
               'font-size:13px;line-height:1.5;">']
        if not rows:
            msg = "\\uc544\\uc9c1 \\uc54c\\ub9bc\\uc774 \\uc5c6\\uc5b4\\uc694" if ko else "No alerts yet"
            out.append('<div style="color:#6E6E7C;padding:12px 0;">%s</div>' % msg)
        for r in rows:
            handled = bool(r.get("handled"))
            icon = _html.escape(str(r.get("icon") or "💬"))
            label = _html.escape(str(r.get("label") or ""))
            summary = _html.escape(str(r.get("summary") or ""))
            rec = str(r.get("rec") or "").strip()
            key = _html.escape(str(r.get("key") or ""))
            try:
                _lt = _time.localtime(float(r.get("ts") or 0))
                _now = _time.localtime()
                if (_lt.tm_year, _lt.tm_yday) == (_now.tm_year, _now.tm_yday):
                    hhmm = _time.strftime("%H:%M", _lt)          # today: HH:MM
                else:
                    hhmm = _time.strftime("%m/%d %H:%M", _lt)    # not today: MM/DD HH:MM
            except Exception:
                hhmm = ""
            main = "#6E6E7C" if handled else "#ECECF0"
            sub = "#55555f" if handled else "#9A9AA8"
            meta = "#44444f" if handled else "#6E6E7C"
            line = ('%s&nbsp;<b style="color:%s;">%s</b> '
                    '<span style="color:%s;">\\u00b7 %s</span>'
                    % (icon, main, label, main, summary))
            if rec:
                line += ('  <span style="color:%s;">\\u2192 %s</span>'
                         % (sub, _html.escape(rec)))
            out.append(
                '<div style="margin:0 0 11px 0;">'
                '<a href="notice:%s" style="text-decoration:none;color:%s;">%s</a>'
                '<span style="color:%s;">&nbsp;&nbsp;%s</span></div>'
                % (key, main, line, meta, hhmm))
        out.append(self._slack_html())      # Slack divider + list at the bottom
        out.append("</div>")
        return "".join(out)

    def _on_alert_click(self, qurl):
        # [enforce-alerts-methods] Alert row click -> open-link.sh if url, else
        # focus_session.py to open that session, and mark the row handled=true.
        import os as _os, subprocess as _sp, sys as _sys
        _cd = self._claude_config_dir()
        _eb = _os.path.join(_cd, "enforce", "bin")
        _openlink = _os.path.join(_eb, "open-link.sh")
        _focus = _os.path.join(_eb, "focus_session.py")
        try:
            s = qurl.toString()
        except Exception:
            return
        if s.startswith("http://") or s.startswith("https://"):
            # Slack item (http link) -> open via open-link.sh (not marked handled).
            try:
                _sp.Popen([_openlink, s],
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            except Exception:
                pass
            return
        if not s.startswith("notice:"):
            return
        key = s[len("notice:"):]
        row = None
        for r in self._load_notices():
            if r.get("key") == key:
                row = r
                break
        if row is None:
            return
        url = (row.get("url") or "").strip()
        # notice url only opens the scheme allowlist (claude://, http://, https://).
        # Anything else is ignored and falls back to focus — never handed to open-link.
        if not (url.startswith("claude://") or url.startswith("http://")
                or url.startswith("https://")):
            url = ""
        focus = (row.get("focus") or row.get("session_id") or "").strip()
        try:
            if url:
                _sp.Popen([_openlink, url],
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            elif focus:
                _sp.Popen([_sys.executable, _focus, focus],
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:
            pass
        try:
            mp = _os.path.join(_cd, "monitor", "bin")
            if mp not in _sys.path:
                _sys.path.insert(0, mp)
            import notices as _n
            _n.mark_handled(key)
        except Exception:
            pass
        self.refresh()

'''

# -- patch F [enforce-alerts-slack]: a 'Slack' area at the bottom of Alerts ----
# Read slack-items.json (written by slack_items.py) -> divider + 'Slack' subheading
# + up to 10 by importance. Dots/summary/#channel/from/time. Item click -> open-link.sh
# if url (not marked handled). These methods are inserted right after the patch E
# method block (= before _set_full). The Slack wiring in _alerts_html/_notices_stamp/
# _on_alert_click is already in the patch E block — revert + re-apply to get both.

ALERTS_SLACK_MARK = "[enforce-alerts-slack]"
ALERTS_SLACK_ANCHOR = "    def _set_full(self, on):\n"
ALERTS_SLACK_BLOCK = '''\
    def _slack_items_path(self):
        # [enforce-alerts-slack] Slack digest store (written by slack_items.py).
        import os as _os
        return _os.path.join(self._claude_config_dir(), "monitor", "slack-items.json")

    def _load_slack_items(self):
        import json as _json
        try:
            with open(self._slack_items_path(), encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            return []
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        rows = [it for it in items if isinstance(it, dict)]
        def _imp(it):
            try:
                return int(it.get("importance") or 0)
            except Exception:
                return 0
        rows.sort(key=lambda it: (_imp(it), float(it.get("ts") or 0)), reverse=True)
        return rows[:10]

    def _slack_html(self):
        # Bottom of the list: a thin divider + "Slack" subheading + up to 10
        # (importance desc, then ts desc).
        import html as _html, time as _time
        ko = self._claudi_ko()
        items = self._load_slack_items()
        parts = ['<hr style="border:none;border-top:1px solid #3A3A44;'
                 'margin:14px 0 10px 0;">'
                 '<div style="color:#9A9AA8;font-weight:600;letter-spacing:0.3px;'
                 'margin:0 0 9px 0;">Slack</div>']
        if not items:
            msg = "\\uc9c0\\uae08 \\ud655\\uc778\\ud560 \\uc2ac\\ub799 \\uc5c6\\uc74c" if ko else "Nothing to check on Slack"
            parts.append('<div style="color:#6E6E7C;">%s</div>' % msg)
            return "".join(parts)
        for it in items:
            try:
                n = int(it.get("importance") or 0)
            except Exception:
                n = 0
            n = 5 if n > 5 else (0 if n < 0 else n)
            dots = "\\u25cf" * n + "\\u25cb" * (5 - n)
            summary = _html.escape(str(it.get("summary") or ""))
            channel = str(it.get("channel") or "").strip()
            frm = str(it.get("from") or "").strip()
            url = str(it.get("url") or "").strip()
            try:
                _lt = _time.localtime(float(it.get("ts") or 0))
                _now = _time.localtime()
                if (_lt.tm_year, _lt.tm_yday) == (_now.tm_year, _now.tm_yday):
                    hhmm = _time.strftime("%H:%M", _lt)          # today: HH:MM
                else:
                    hhmm = _time.strftime("%m/%d %H:%M", _lt)    # not today: MM/DD HH:MM
            except Exception:
                hhmm = ""
            meta = _html.escape(" \\u00b7 ".join(x for x in (channel, frm) if x))
            inner = ('<span style="color:#6E6E7C;letter-spacing:1px;">%s</span>'
                     '&nbsp;<span style="color:#ECECF0;">%s</span>'
                     '<div style="color:#9A9AA8;margin:1px 0 0 0;">%s'
                     '<span style="color:#6E6E7C;">&nbsp;&nbsp;%s</span></div>'
                     % (dots, summary, meta, hhmm))
            if url:
                parts.append(
                    '<div style="margin:0 0 11px 0;">'
                    '<a href="%s" style="text-decoration:none;color:inherit;">%s</a>'
                    '</div>' % (_html.escape(url, quote=True), inner))
            else:
                parts.append('<div style="margin:0 0 11px 0;">%s</div>' % inner)
        return "".join(parts)

'''

# -- patch G [enforce-portrefresh-patch]: rewrite the .port file periodically ---------
# .port is written once at startup and closed, so a pet left running 3+ days gets it
# deleted by macOS temp cleanup (lock/slot survive because the pet holds them open). Then
# even the launchd restart is blocked by the lock. Rewrite hourly to refresh atime and
# restore it if already gone.
# .port 는 기동 때 한 번 쓰고 닫혀, 펫을 3일 넘게 켜 두면 macOS 임시폴더 정리가 지운다
# (lock/slot 은 펫이 열어 두고 있어 살아남는다). 그러면 launchd 재기동도 lock 에 막혀
# 못 고친다. 1시간마다 다시 써서 atime 을 갱신하고, 이미 지워졌으면 복구한다.
PORTREFRESH_MARK = "[enforce-portrefresh-patch]"
PORTREFRESH_ANCHOR = (
    "        hostinfo.write_session_port(self.session_id, self.srv.getsockname()[1])\n")
PORTREFRESH_BLOCK = (
    "        # " + PORTREFRESH_MARK + " rewrite .port hourly (macOS $TMPDIR 3-day cleanup).\n"
    "        def _refresh_port():\n"
    "            try:\n"
    "                hostinfo.write_session_port(self.session_id, self.srv.getsockname()[1])\n"
    "            except OSError:\n"
    "                pass\n"
    "        self._port_refresh = QTimer(self)\n"
    "        self._port_refresh.timeout.connect(_refresh_port)\n"
    "        self._port_refresh.start(60 * 60 * 1000)\n"
)

# (mark, description) in application order — used by --check and the summary output.
# 적용 순서대로의 마커 목록 — --check 와 요약 출력에 쓴다.
ALL_MARKS = [
    CLICK_MARK, NOREPLY_MARK, NOTICE_MARK, NOACTIVITY_MARK,
    ALERTS_TAB_MARK, ALERTS_WIRE_MARK, ALERTS_REFRESH_MARK,
    ALERTS_POLL_MARK, ALERTS_METHODS_MARK, ALERTS_SLACK_MARK, PORTREFRESH_MARK,
]


def claudlet_version():
    """Installed claudlet version string (queried via venv). '' if unavailable.
    설치된 claudlet 버전 문자열(venv 로 조회). 못 구하면 ''."""
    if not VENV_PY or not os.path.isfile(VENV_PY):
        return ""
    try:
        out = subprocess.run(
            [VENV_PY, "-c",
             "import importlib.metadata as m; print(m.version('claudlet'))"],
            capture_output=True, text=True, timeout=10)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def resolve_petpy():
    # 1) Ask the venv Python directly (safe even if the Python version changes).
    # 1) venv 파이썬에게 직접 물어본다(파이썬 버전이 바뀌어도 안전).
    if VENV_PY and os.path.isfile(VENV_PY):
        try:
            out = subprocess.run(
                [VENV_PY, "-c", "import claudlet.pet as p; print(p.__file__)"],
                capture_output=True, text=True, timeout=10)
            p = out.stdout.strip()
            if p and os.path.isfile(p):
                return p
        except Exception:
            pass
    # 2) Fallback: pipx venv site-packages glob (platform default locations).
    # 2) 폴백: pipx venv site-packages 글롭(플랫폼별 기본 위치).
    import glob
    for pat in claudlet_paths.petpy_glob_candidates():
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return ""


def _insert_after(content, anchor, block, mark):
    if mark in content:
        return content, False          # already patched / 이미 패치됨
    i = content.find(anchor)
    if i < 0:
        raise RuntimeError("anchor not found: %r" % anchor.strip())
    j = i + len(anchor)
    return content[:j] + block + content[j:], True


def _insert_before(content, anchor, block, mark):
    if mark in content:
        return content, False          # already patched / 이미 패치됨
    i = content.find(anchor)
    if i < 0:
        raise RuntimeError("anchor not found: %r" % anchor.strip())
    return content[:i] + block + content[i:], True


def _replace_once(content, old, new, mark):
    if mark in content:
        return content, False          # already patched / 이미 패치됨
    if old not in content:
        raise RuntimeError("replace target not found: %r" % old.strip())
    return content.replace(old, new, 1), True


def _apply_all(content):
    """Return (content, changed) with every patch applied in memory. RuntimeError if an anchor is missing.
    모든 패치를 메모리에서 적용한 (content, changed) 반환. 앵커 없으면 RuntimeError."""
    content, c1 = _insert_after(content, CLICK_ANCHOR, CLICK_BLOCK, CLICK_MARK)
    content, c2 = _insert_after(content, NOREPLY_ANCHOR, NOREPLY_BLOCK, NOREPLY_MARK)
    content, c3 = _insert_after(content, NOTICE_ANCHOR, NOTICE_BLOCK, NOTICE_MARK)
    content, c4 = _insert_after(content, NOACTIVITY_ANCHOR, NOACTIVITY_BLOCK,
                                NOACTIVITY_MARK)
    # patch E: chat 'Alerts' tab (notices.json) — 5 sites (tab label/wire/refresh/poll/methods).
    # patch E: 대화창 '알림' 탭 — 5개 지점(탭 라벨/와이어/refresh/poll/메서드).
    content, c5 = _replace_once(content, ALERTS_TAB_OLD, ALERTS_TAB_NEW,
                                ALERTS_TAB_MARK)
    content, c6 = _insert_after(content, ALERTS_WIRE_ANCHOR, ALERTS_WIRE_BLOCK,
                                ALERTS_WIRE_MARK)
    content, c7 = _insert_after(content, ALERTS_REFRESH_ANCHOR,
                                ALERTS_REFRESH_BLOCK, ALERTS_REFRESH_MARK)
    content, c8 = _insert_after(content, ALERTS_POLL_ANCHOR, ALERTS_POLL_BLOCK,
                                ALERTS_POLL_MARK)
    content, c9 = _insert_before(content, ALERTS_METHODS_ANCHOR,
                                 ALERTS_METHODS_BLOCK, ALERTS_METHODS_MARK)
    # patch F: Alerts 'Slack' area methods — inserted right after the patch E block.
    # patch F: 알림 탭 'Slack' 영역 메서드 — patch E 메서드 블록 바로 뒤에 삽입.
    content, c10 = _insert_before(content, ALERTS_SLACK_ANCHOR,
                                  ALERTS_SLACK_BLOCK, ALERTS_SLACK_MARK)
    # patch G: rewrite .port hourly. / patch G: .port 파일 1시간 주기 재기록.
    content, c11 = _insert_after(content, PORTREFRESH_ANCHOR, PORTREFRESH_BLOCK,
                                 PORTREFRESH_MARK)
    flags = (c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11)
    return content, flags


def revert(petpy):
    orig = petpy + ".enforce-orig"
    if os.path.isfile(orig):
        shutil.copy2(orig, petpy)
        print("reverted from backup:", orig)
        return 0
    print("no backup to revert:", orig)
    return 1


def check_only(content):
    """0 if every patch marker is present, else 1 (writes nothing).
    모든 패치 마커가 있으면 0, 아니면 1(아무것도 쓰지 않음)."""
    missing = [m for m in ALL_MARKS if m not in content]
    if missing:
        print(t("Patches not applied (%d/%d): %s",
                "미적용 패치(%d/%d): %s")
              % (len(missing), len(ALL_MARKS), ", ".join(missing)))
        return 1
    print(t("All patches applied (%d).", "모든 패치 적용됨(%d개).") % len(ALL_MARKS))
    return 0


def _parse_args(argv):
    pet_py = None
    if "--pet-py" in argv:
        i = argv.index("--pet-py")
        if i + 1 < len(argv):
            pet_py = argv[i + 1]
    return {
        "pet_py": pet_py,
        "revert": "--revert" in argv,
        "check": "--check" in argv,
    }


def main():
    args = _parse_args(sys.argv[1:])

    explicit = bool(args["pet_py"])
    petpy = args["pet_py"] if explicit else resolve_petpy()
    if not petpy or not os.path.isfile(petpy):
        print("ERROR: claudlet pet.py not found", file=sys.stderr)
        return 2
    print("pet.py:", petpy)

    # Version warning (only when not explicitly targeted — test fixtures have no
    # venv). Warn and keep going.
    # 버전 경고(명시 지정이 아닐 때만). 경고만 하고 계속.
    if not explicit:
        ver = claudlet_version()
        if ver and ver != TESTED_VERSION:
            print(t("WARN: claudlet %s installed (verified version %s). If anchors "
                    "differ, nothing is written and it stops.",
                    "WARN: claudlet %s 설치됨(검증 버전 %s). 앵커가 다르면 아무것도 쓰지 않고 "
                    "중단합니다.") % (ver, TESTED_VERSION),
                  file=sys.stderr)

    if args["revert"]:
        return revert(petpy)

    with open(petpy, encoding="utf-8") as f:
        content = f.read()
    orig_content = content

    if args["check"]:
        return check_only(content)

    try:
        content, flags = _apply_all(content)
    except RuntimeError as e:
        # If any anchor is missing, write nothing and stop. / 앵커가 없으면 아무것도 안 쓰고 중단.
        print("ERROR:", e, file=sys.stderr)
        return 2
    changed = any(flags)

    if not changed:
        print("already patched (click + noreply + notice + noactivity + alerts "
              "+ slack + portrefresh) — nothing to do.")
        return 0

    # Backup (once). / 백업(최초 1회).
    backup = petpy + ".enforce-orig"
    if not os.path.isfile(backup):
        try:
            shutil.copy2(petpy, backup)
            print("backup:", backup)
        except Exception as e:
            print("WARN: backup failed:", e, file=sys.stderr)

    # Atomic write. / 원자적 쓰기.
    tmp = petpy + ".enforce-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, petpy)

    # Syntax check — restore the in-memory original immediately on failure.
    # 문법 검증 — 실패하면 메모리의 원본으로 즉시 복원.
    try:
        py_compile.compile(petpy, doraise=True)
    except Exception as e:
        with open(petpy, "w", encoding="utf-8") as f:
            f.write(orig_content)
        print("ERROR: patched file failed to compile, reverted:", e, file=sys.stderr)
        return 3

    c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11 = flags
    print("patched: click=%s noreply=%s notice=%s noactivity=%s "
          "alerts(tab=%s wire=%s refresh=%s poll=%s methods=%s slack=%s) portrefresh=%s"
          % (c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11))
    return 0


if __name__ == "__main__":
    sys.exit(main())
