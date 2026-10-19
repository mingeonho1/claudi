"""enforce 레이어 테스트: check.py 판정 + session-start-hook 1회성."""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import time
import types
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers
from helpers import ENF_BIN, make_config_dir

# 업무 구간(월요일 10:00)과 비업무 구간(일요일 10:00) — 벽시계와 무관하게 주입한다.
MONDAY_10 = datetime(2026, 1, 5, 10, 0, 0)    # weekday 0
SUNDAY_10 = datetime(2026, 1, 4, 10, 0, 0)    # weekday 6

check_mod = None
CFG = None
_SAVED_ENV = {}


def setUpModule():
    global check_mod, CFG
    CFG = make_config_dir()
    for k in ("CLAUDE_CONFIG_DIR", "XDG_RUNTIME_DIR", "CLAUDI_NO_DESKTOP_NOTIFY"):
        _SAVED_ENV[k] = os.environ.get(k)
    os.environ["CLAUDE_CONFIG_DIR"] = CFG
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(CFG, "run")   # 빈 디렉터리 → 펫 포트 없음
    os.environ["CLAUDI_NO_DESKTOP_NOTIFY"] = "1"
    if ENF_BIN not in sys.path:
        sys.path.insert(0, ENF_BIN)
    import check as _c
    check_mod = _c


def tearDownModule():
    shutil.rmtree(CFG, ignore_errors=True)
    # 공유 프로세스(unittest discover)의 다른 모듈에 env 누수가 없도록 원복.
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.flag = os.path.join(CFG, "enforce", "standalone-pet")
        self.slack = os.path.join(CFG, "monitor", "slack-state.json")

    def tearDown(self):
        for p in (self.flag, self.slack):
            if os.path.exists(p):
                os.remove(p)

    # ── pet_down ──
    def test_no_issues_when_nothing_enabled(self):
        self.assertEqual(check_mod.collect_issues(now=MONDAY_10), [])

    def test_pet_down_when_flag_and_no_pet(self):
        open(self.flag, "w").close()
        issues = check_mod.check_pet(now=MONDAY_10)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "pet_down")

    def test_pet_check_skipped_off_hours(self):
        open(self.flag, "w").close()
        self.assertEqual(check_mod.check_pet(now=SUNDAY_10), [])

    def test_pet_not_checked_without_flag(self):
        self.assertEqual(check_mod.check_pet(now=MONDAY_10), [])

    # ── slack_stale ──
    def test_slack_absent_not_checked(self):
        self.assertEqual(check_mod.check_slack(now=MONDAY_10), [])

    def test_slack_stale_when_enabled_and_old(self):
        with open(self.slack, "w", encoding="utf-8") as f:
            json.dump({"last_ts": time.time() - 10000}, f)   # 2시간 초과
        issues = check_mod.check_slack(now=MONDAY_10)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "slack_stale")

    def test_slack_fresh_ok(self):
        with open(self.slack, "w", encoding="utf-8") as f:
            json.dump({"last_ts": time.time()}, f)
        self.assertEqual(check_mod.check_slack(now=MONDAY_10), [])

    def test_slack_last_run_preferred_over_last_ts(self):
        # last_ts 는 오래됐어도(새 메시지 없음) last_run 이 최신이면 stale 아님.
        with open(self.slack, "w", encoding="utf-8") as f:
            json.dump({"last_ts": time.time() - 100000,
                       "last_run": datetime.now().astimezone().isoformat()}, f)
        self.assertEqual(check_mod.check_slack(now=MONDAY_10), [])

    def test_slack_stale_uses_last_run(self):
        # last_run(ISO8601)이 오래되면 stale.
        old_iso = (datetime.now().astimezone() - timedelta(seconds=100000)).isoformat()
        with open(self.slack, "w", encoding="utf-8") as f:
            json.dump({"last_run": old_iso}, f)
        issues = check_mod.check_slack(now=MONDAY_10)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "slack_stale")


def _check_stub():
    m = types.ModuleType("check")
    m.collect_issues = lambda now=None: [{"type": "pet_down", "msg": "독립 펫 미실행"}]
    m.should_notify = lambda key, *a, **k: False       # 알림 subprocess 안 뜨게
    m.throttle_key = lambda issues: "k"
    m.summary_line = lambda issues, maxlen=200: "독립 펫 미실행"
    return m


def _sessionkind_stub():
    m = types.ModuleType("sessionkind")
    m.is_scheduled_task = lambda data: False
    return m


class SessionStartHookTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config_dir()

    def tearDown(self):
        shutil.rmtree(self.cfg, ignore_errors=True)

    def _load(self):
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        path = os.path.join(ENF_BIN, "session-start-hook.py")
        spec = importlib.util.spec_from_file_location("ssh_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _main(self, mod, payload):
        buf = io.StringIO()
        saved = {k: sys.modules.get(k) for k in ("check", "sessionkind")}
        old_stdin = sys.stdin
        try:
            sys.modules["check"] = _check_stub()
            sys.modules["sessionkind"] = _sessionkind_stub()
            sys.stdin = io.StringIO(json.dumps(payload))
            with contextlib.redirect_stdout(buf):
                mod.main()
        finally:
            sys.stdin = old_stdin
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        return buf.getvalue()

    def test_first_prompt_emits_second_does_not(self):
        mod = self._load()
        payload = {"hook_event_name": "UserPromptSubmit",
                   "session_id": "sid-ssh-1", "prompt": "안녕"}
        first = self._main(mod, payload)
        self.assertIn("additionalContext", first)
        self.assertIn("독립 펫 미실행", first)
        # 같은 세션의 두 번째 프롬프트는 마커 때문에 무출력.
        second = self._main(mod, payload)
        self.assertEqual(second.strip(), "")


class SessionkindTest(unittest.TestCase):
    """음성(데스크톱 레코드 없음) 캐시 — 반복 조회가 재스캔하지 않는다."""

    def setUp(self):
        if ENF_BIN not in sys.path:
            sys.path.insert(0, ENF_BIN)
        import sessionkind
        self.sk = sessionkind
        self.fake = os.path.join(CFG, "fake-sessions")
        os.makedirs(self.fake, exist_ok=True)
        os.environ["CLAUDI_DESKTOP_SESSIONS_DIR"] = self.fake  # 가짜(빈) 저장소

    def tearDown(self):
        os.environ.pop("CLAUDI_DESKTOP_SESSIONS_DIR", None)

    def test_desktop_negative_result_cached(self):
        calls = {"n": 0}
        orig = self.sk._desktop_files

        def counting():
            calls["n"] += 1
            return orig()

        self.sk._desktop_files = counting
        try:
            sid = "sid-neg-%d" % int(time.time() * 1000)
            self.assertEqual(self.sk.desktop_session_id(sid), "")
            self.assertEqual(self.sk.desktop_session_id(sid), "")
        finally:
            self.sk._desktop_files = orig
        # 첫 조회만 스캔하고, 둘째는 음성 캐시(TTL)를 써서 재스캔하지 않는다.
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
