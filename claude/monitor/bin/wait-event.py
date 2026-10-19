#!/usr/bin/env python3
# ~/.claude/monitor/bin/wait-event.py
#
# Block until a new event line appears in events.jsonl (polling every 1s).
#   - Watches past the byte offset in the cursor file (~/.claude/monitor/cursor).
#   - When complete new line(s) appear, print them to stdout, move the cursor to their end
#     and exit (the monitor skill launches this with run_in_background and re-invokes it after).
#   - If nothing arrives within max_wait seconds (arg 1, default 3000), print "TIMEOUT" and exit.
#
# Usage: wait-event.py [max_wait_seconds]
# This process owns the waiting so the skill never runs its own polling sleep.
# Data paths resolve under CLAUDE_CONFIG_DIR (default ~/.claude).
#
# events.jsonl 에 새 이벤트 줄이 생길 때까지 블로킹 대기한다(폴링 1초 간격).
#   - 커서 파일(~/.claude/monitor/cursor)의 바이트 오프셋 이후를 감시.
#   - 완결된 새 줄(들)이 생기면 그 줄들을 stdout 에 출력하고 커서를 그 끝으로
#     옮긴 뒤 종료(관제 스킬이 run_in_background 로 띄우고, 끝나면 재호출).
#   - max_wait 초(인자1, 기본 3000) 안에 아무것도 없으면 "TIMEOUT" 출력 후 종료.
#
# 사용법: wait-event.py [max_wait_seconds]
# 폴링용 sleep 을 스킬이 직접 돌리지 않게 하려고, 이 프로세스가 대기를 전담한다.
# 데이터 경로는 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래에서 해석한다.

import os
import sys
import time

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
MONITOR = os.path.join(CLAUDE_DIR, "monitor")
EVENTS = os.path.join(MONITOR, "events.jsonl")
CURSOR = os.path.join(MONITOR, "cursor")
HEARTBEAT = os.path.join(MONITOR, "heartbeat")   # enforce layer uses it to tell "monitor alive" / enforce 레이어가 "관제 살아있음" 판별에 사용
HEARTBEAT_INTERVAL = 30.0                         # seconds; mtime refresh interval while waiting / 초. 대기 루프 중 이 간격으로 mtime 갱신.


def touch_heartbeat():
    """Touch the heartbeat file mtime to signal the monitor is waiting.
    Failure does not affect waiting (silently ignored).
    관제가 대기 중임을 알리는 하트비트 파일의 mtime 을 지금으로 갱신.
    실패해도 대기 동작에는 영향 없음(조용히 무시)."""
    try:
        with open(HEARTBEAT, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def read_cursor():
    try:
        with open(CURSOR) as f:
            return int((f.read().strip() or "0"))
    except (OSError, ValueError):
        return 0


def write_cursor(n):
    try:
        tmp = CURSOR + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(n))
        os.replace(tmp, CURSOR)
    except OSError:
        pass


def main():
    max_wait = 3000.0
    if len(sys.argv) > 1:
        try:
            max_wait = float(sys.argv[1])
        except ValueError:
            pass

    start = time.monotonic()
    cur = read_cursor()

    touch_heartbeat()
    last_hb = time.monotonic()

    while True:
        now = time.monotonic()
        if now - last_hb >= HEARTBEAT_INTERVAL:
            touch_heartbeat()
            last_hb = now

        try:
            size = os.path.getsize(EVENTS)
        except OSError:
            size = 0

        # Cursor past EOF: the file was rotated (events.jsonl.1)/replaced → restart from the
        # beginning of the new file. Old content went to .1, so it is not replayed.
        # 커서가 파일 끝을 넘음: 회전(events.jsonl.1)/교체됨 →
        # 새 파일의 처음부터. 옛 내용은 .1 로 갔으니 재생되지 않는다.
        if cur > size:
            cur = 0

        if size > cur:
            try:
                with open(EVENTS, "rb") as f:
                    f.seek(cur)
                    data = f.read()
            except OSError:
                data = b""
            nl = data.rfind(b"\n")
            if nl != -1:         # at least one complete line / 완결된 줄이 하나 이상.
                chunk = data[:nl + 1]
                sys.stdout.write(chunk.decode("utf-8", "replace"))
                sys.stdout.flush()
                write_cursor(cur + nl + 1)
                return 0
            # No newline yet (still being written): wait for the next tick.
            # 개행이 아직 없으면(쓰는 중) 다음 틱까지 대기.

        if time.monotonic() - start >= max_wait:
            sys.stdout.write("TIMEOUT\n")
            sys.stdout.flush()
            return 0

        time.sleep(1.0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
