"""Monitor layer tests: monitor-hook / wait-event / notify+notices / slack_items / digest.
monitor 레이어 테스트."""

import json
import os
import shutil
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers
from helpers import MON_BIN, run_script, script, base_env, make_config_dir, monitor_path


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if ln.strip()]


class MonitorHookTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config_dir()
        self.env = base_env(self.cfg)
        self.events = monitor_path(self.cfg, "events.jsonl")
        # Stop/StopFailure are recorded only when the transcript exists.
        # transcript 가 실재해야 Stop/StopFailure 가 기록된다.
        self.transcript = os.path.join(self.cfg, "t.jsonl")
        write(self.transcript, '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}\n')
        # Fresh heartbeat = monitor alive, so the hook only records (no fallback notice).
        # heartbeat 가 최신이면 관제 살아있음 → 훅은 기록만(대체 알림 없음).
        self.heartbeat = monitor_path(self.cfg, "heartbeat")
        write(self.heartbeat, "")

    def tearDown(self):
        shutil.rmtree(self.cfg, ignore_errors=True)

    def _run(self, payload):
        return run_script(script(MON_BIN, "monitor-hook.py"), env=self.env,
                          input_text=json.dumps(payload))

    def test_monitor_alive_no_fallback_notice(self):
        self._run({"session_id": "sid-alive", "hook_event_name": "StopFailure",
                   "transcript_path": self.transcript, "cwd": "/tmp/repo"})
        time.sleep(1)
        self.assertFalse(os.path.exists(monitor_path(self.cfg, "notices.json")))

    def test_monitor_dead_fallback_notice(self):
        # Stale heartbeat → the hook writes the Alerts-tab row itself (async notify.py).
        # heartbeat 가 오래됨 → 훅이 알림 탭 행을 직접 쓴다(비동기 notify.py).
        old = time.time() - 600
        os.utime(self.heartbeat, (old, old))
        self._run({"session_id": "sid-dead", "hook_event_name": "StopFailure",
                   "transcript_path": self.transcript, "cwd": "/tmp/repo"})
        notices = monitor_path(self.cfg, "notices.json")
        data = {}
        for _ in range(50):
            try:
                with open(notices, encoding="utf-8") as f:
                    data = json.load(f)
                if "sid-dead" in data:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        self.assertIn("sid-dead", data)
        self.assertFalse(data["sid-dead"]["handled"])

    def test_stop_appends_event_no_attention(self):
        self._run({"session_id": "sid-stop", "hook_event_name": "Stop",
                   "transcript_path": self.transcript, "cwd": "/tmp/repo",
                   "last_assistant_message": "Finished the task."})
        lines = read_lines(self.events)
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["event"], "Stop")
        self.assertEqual(row["session_id"], "sid-stop")
        # Stop never touches attention.json.
        # Stop 은 attention.json 을 건드리지 않는다.
        self.assertFalse(os.path.exists(monitor_path(self.cfg, "attention.json")))

    def test_permission_sets_attention(self):
        self._run({"session_id": "sid-perm", "hook_event_name": "Notification",
                   "notification_type": "permission_prompt",
                   "message": "Permission required", "transcript_path": self.transcript,
                   "cwd": "/tmp/repo"})
        lines = read_lines(self.events)
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["event"], "Notification")
        att = monitor_path(self.cfg, "attention.json")
        self.assertTrue(os.path.exists(att))
        with open(att, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["cli_session"], "sid-perm")
        self.assertIn("reason", data)

    def test_stopfailure_sets_attention(self):
        self._run({"session_id": "sid-fail", "hook_event_name": "StopFailure",
                   "transcript_path": self.transcript, "cwd": "/tmp/repo",
                   "last_assistant_message": "failed"})
        self.assertEqual(len(read_lines(self.events)), 1)
        self.assertTrue(os.path.exists(monitor_path(self.cfg, "attention.json")))

    def test_subagent_payload_skipped(self):
        self._run({"session_id": "sid-sub", "hook_event_name": "Stop",
                   "transcript_path": self.transcript, "cwd": "/tmp/repo",
                   "agent_id": "agent-123"})
        self.assertEqual(read_lines(self.events), [])

    def test_monitor_session_loop_guard_skip(self):
        write(monitor_path(self.cfg, "monitor-session"), "sid-ctrl")
        self._run({"session_id": "sid-ctrl", "hook_event_name": "Stop",
                   "transcript_path": self.transcript, "cwd": "/tmp/repo"})
        self.assertEqual(read_lines(self.events), [])

    def test_stop_without_transcript_skipped(self):
        self._run({"session_id": "sid-x", "hook_event_name": "Stop",
                   "transcript_path": "/no/such/file.jsonl", "cwd": "/tmp/repo"})
        self.assertEqual(read_lines(self.events), [])

    def test_events_rotation_over_5mb(self):
        # events.jsonl over 5MB -> rotated to .1 on the next append; a fresh file starts.
        # 5MB 초과 events.jsonl → 새 이벤트 추가 시 .1 로 회전하고 새 파일로 시작.
        big = "x" * (5 * 1024 * 1024 + 16)
        write(self.events, big + "\n")
        self._run({"session_id": "sid-rot", "hook_event_name": "Stop",
                   "transcript_path": self.transcript, "cwd": "/tmp/repo",
                   "last_assistant_message": "done"})
        lines = read_lines(self.events)
        self.assertEqual(len(lines), 1)                 # only the one new line after rotation / 회전 후 새 줄 1개만.
        self.assertEqual(json.loads(lines[0])["session_id"], "sid-rot")
        self.assertTrue(os.path.exists(self.events + ".1"))  # old content moved to .1 / 옛 내용은 .1 로.
        self.assertGreater(os.path.getsize(self.events + ".1"), 5 * 1024 * 1024)


class WaitEventTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config_dir()
        self.env = base_env(self.cfg)
        self.events = monitor_path(self.cfg, "events.jsonl")
        self.cursor = monitor_path(self.cfg, "cursor")

    def tearDown(self):
        shutil.rmtree(self.cfg, ignore_errors=True)

    def test_returns_new_line_past_cursor(self):
        line = json.dumps({"event": "Stop", "session_id": "s1"}) + "\n"
        write(self.events, line)
        write(self.cursor, "0")
        r = run_script(script(MON_BIN, "wait-event.py"), args=["5"], env=self.env)
        self.assertEqual(r.stdout, line)
        # Cursor moved to the end.
        # 커서가 끝으로 이동했다.
        with open(self.cursor) as f:
            self.assertEqual(int(f.read().strip()), len(line.encode("utf-8")))

    def test_timeout_when_no_new_content(self):
        # No events + short wait -> TIMEOUT.
        # events 없음 + 짧은 대기 → TIMEOUT.
        r = run_script(script(MON_BIN, "wait-event.py"), args=["2"], env=self.env)
        self.assertEqual(r.stdout.strip(), "TIMEOUT")

    def test_cursor_past_size_resets_and_reads(self):
        # Cursor past EOF (e.g. right after rotation/replacement) -> reset to 0 and read the new file.
        # 회전/교체 직후처럼 커서가 파일 끝을 넘는 경우 → 0 으로 리셋해 새 파일을 읽는다.
        line = json.dumps({"event": "Stop", "session_id": "s-reset"}) + "\n"
        write(self.events, line)
        write(self.cursor, "999999")           # cursor larger than the file / 파일 크기보다 큰 커서.
        r = run_script(script(MON_BIN, "wait-event.py"), args=["5"], env=self.env)
        self.assertEqual(r.stdout, line)        # only the new line, no replay / 옛 내용 재생 없이 새 줄만.
        with open(self.cursor) as f:
            self.assertEqual(int(f.read().strip()), len(line.encode("utf-8")))


class NotifyNoticesTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config_dir()
        self.env = base_env(self.cfg)
        self.notices = monitor_path(self.cfg, "notices.json")

    def tearDown(self):
        shutil.rmtree(self.cfg, ignore_errors=True)

    def test_notify_upserts_notice_then_handled(self):
        r = run_script(script(MON_BIN, "notify.py"),
                       args=["--title", "🟡 Decision needed", "--body", "One-line decision",
                             "--session", "sid-note", "--rec", "Recommend option A"],
                       env=self.env)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(os.path.exists(self.notices))
        with open(self.notices, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("sid-note", data)
        row = data["sid-note"]
        self.assertFalse(row["handled"])
        self.assertEqual(row["summary"], "One-line decision")
        self.assertEqual(row["rec"], "Recommend option A")

        # --handled marks only that row as handled.
        # --handled 로 그 행만 처리됨 표시.
        run_script(script(MON_BIN, "notify.py"),
                   args=["--handled", "sid-note"], env=self.env)
        with open(self.notices, encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["sid-note"]["handled"])


class SlackItemsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config_dir()
        self.env = base_env(self.cfg)
        self.items = monitor_path(self.cfg, "slack-items.json")

    def tearDown(self):
        shutil.rmtree(self.cfg, ignore_errors=True)

    def _run(self, cmd, input_text=None):
        return run_script(script(MON_BIN, "slack_items.py"), args=[cmd],
                          env=self.env, input_text=input_text)

    def test_set_show_clear(self):
        payload = json.dumps({"items": [
            {"id": "chan:1", "importance": 2, "summary": "low", "ts": 100},
            {"id": "chan:2", "importance": 5, "summary": "high", "ts": 200},
        ]})
        self._run("set", input_text=payload)
        with open(self.items, encoding="utf-8") as f:
            data = json.load(f)
        # Sorted by importance, descending.
        # 중요도 내림차순 정렬.
        self.assertEqual([it["id"] for it in data["items"]], ["chan:2", "chan:1"])

        r = self._run("show")
        self.assertIn("high", r.stdout)

        self._run("clear")
        with open(self.items, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["items"], [])

    def test_set_invalid_json_nonzero_keeps_file(self):
        # Save a valid set first.
        # 먼저 유효 저장.
        self._run("set", input_text=json.dumps(
            {"items": [{"id": "a:1", "importance": 3, "summary": "x", "ts": 1}]}))
        with open(self.items, encoding="utf-8") as f:
            before = f.read()
        # Invalid JSON -> non-zero exit (2) and the existing file is kept.
        # 잘못된 JSON → 비정상 종료(2) + 기존 파일 보존.
        r = self._run("set", input_text="this is not json")
        self.assertNotEqual(r.returncode, 0)
        with open(self.items, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        # JSON without an 'items' list also exits non-zero.
        # 'items' 리스트가 없는 JSON 도 비정상 종료.
        r2 = self._run("set", input_text=json.dumps({"nope": 1}))
        self.assertNotEqual(r2.returncode, 0)

    def test_set_stamps_last_run_preserving_keys(self):
        from datetime import datetime
        state = monitor_path(self.cfg, "slack-state.json")
        write(state, json.dumps({"last_ts": 123.0, "foo": "bar"}))
        r = self._run("set", input_text=json.dumps({"items": []}))
        self.assertEqual(r.returncode, 0)
        with open(state, encoding="utf-8") as f:
            data = json.load(f)
        # last_run is stamped as ISO8601 and existing keys are preserved.
        # last_run 이 ISO8601 로 찍히고, 기존 키는 보존된다.
        self.assertIn("last_run", data)
        datetime.fromisoformat(data["last_run"])
        self.assertEqual(data["last_ts"], 123.0)
        self.assertEqual(data["foo"], "bar")


class DigestTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config_dir()
        self.env = base_env(self.cfg)
        self.t = os.path.join(self.cfg, "digest.jsonl")
        rows = [
            {"type": "user", "message": {"content": [
                {"type": "text", "text": "Fix the login bug (goal sentence)"}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Found the cause and fixed it"}]}},
        ]
        write(self.t, "\n".join(json.dumps(r) for r in rows) + "\n")

    def tearDown(self):
        shutil.rmtree(self.cfg, ignore_errors=True)

    def test_contains_goal_and_recent_sections(self):
        r = run_script(script(MON_BIN, "digest-transcript.py"),
                       args=[self.t, "--turns", "6"], env=self.env)
        self.assertIn("## GOAL", r.stdout)
        self.assertIn("## RECENT TURNS", r.stdout)
        self.assertIn("Fix the login bug", r.stdout)


if __name__ == "__main__":
    unittest.main()
