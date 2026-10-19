#!/usr/bin/env python3
# ~/.claude/enforce/bin/ensure-pet.py
#
# Make sure the standalone claudlet pet is alive, and launch exactly one if not.
# Stdlib only (does not import claudlet directly — liveness is delegated to the
# venv Python). Always exit 0.
# 독립(standalone) claudlet 펫이 살아 있는지 확인하고, 없으면 1마리만 기동한다. 표준
# 라이브러리만(claudlet 을 직접 import 하지 않는다 — 생존 판정만 venv 파이썬에 위임). 항상 exit 0.
#
# Callers:
#   - LaunchAgent com.claudi.pet       (periodic + RunAtLoad on login/boot)
#   - launchd-check.py (com.claudi.enforcer, hourly + RunAtLoad) also calls it.
# 호출 주체: LaunchAgent com.claudi.pet(주기+RunAtLoad), launchd-check.py 가 함께 호출.
#
# Policy: with no standalone-pet flag, do nothing (under the session-attached policy
# the standalone pet must not be launched). No duplicate launch: lock + liveness ping first.
# 정책: standalone-pet 플래그가 없으면 아무것도 하지 않는다. 중복 기동 방지: 락 + 생존 ping 후에만 기동.
#
# Note: `claudlet-attach --standalone` always launches a new one without a liveness
# check (per source). So we ping first and leave it alone when it is alive.
# 주의: `claudlet-attach --standalone` 은 생존 확인 없이 무조건 새로 띄운다. 그래서 먼저 ping 한다.
#
# Paths: flag/lock/log under CLAUDE_CONFIG_DIR (default ~/.claude). venv Python /
# claudlet-attach location is discovered by claudlet_paths (env/shebang/pipx default order).
# 경로: 플래그/락/로그는 CLAUDE_CONFIG_DIR(없으면 ~/.claude) 아래. venv 파이썬 위치는 claudlet_paths 가 발견.

import os
import socket
import subprocess
import sys
import tempfile
import time

BIN = os.path.dirname(os.path.abspath(__file__))
if BIN not in sys.path:
    sys.path.insert(0, BIN)
import claudlet_paths

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
ENFORCE = os.path.join(CLAUDE_DIR, "enforce")
STANDALONE_FLAG = os.path.join(ENFORCE, "standalone-pet")
ATTACH = claudlet_paths.claudlet_attach_path()
VENV_PY = claudlet_paths.venv_python()
PORT_NAME = "claudlet-default.port"      # the standalone pet uses session_id="default" / 독립 펫은 session_id="default"
LOGFILE = os.path.join(ENFORCE, "logs", "pet.log")
LOCKFILE = os.path.join(ENFORCE, ".ensure-pet.lock")

try:
    import fcntl
except Exception:
    fcntl = None


def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()


def _tcp_probe(path):
    """Fallback: just try to connect via the port file. True if it opens.
    포트 파일로 연결만 시도(폴백). 열리면 True."""
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


def pet_alive():
    """Standalone pet liveness. 1st: claudlet's hostinfo.pet_alive('default') handshake.
    If the venv Python is unusable, fall back to the port TCP probe.
    독립 펫 생존 판정. 1순위: hostinfo.pet_alive('default') 핸드셰이크, 안 되면 포트 TCP 프로브."""
    if VENV_PY and os.path.isfile(VENV_PY):
        try:
            code = ("import sys\n"
                    "from claudlet.core import hostinfo\n"
                    "sys.exit(0 if hostinfo.pet_alive('default') else 1)\n")
            r = subprocess.run([VENV_PY, "-c", code], timeout=8,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return r.returncode == 0
        except Exception:
            pass
    return _tcp_probe(os.path.join(runtime_dir(), PORT_NAME))


def launch():
    if not ATTACH:
        _log("launch failed: claudlet-attach not found")
        return False
    try:
        subprocess.Popen(
            [ATTACH, "--standalone"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception as e:
        _log("launch failed: %r" % e)
        return False


def main():
    if not os.path.exists(STANDALONE_FLAG):
        return                               # session-attached policy -> no standalone pet / 세션-부착 정책 → 독립 펫 안 띄움

    # Lock: prevent several launchd triggers from launching at once (two pets).
    # 락: 여러 launchd 트리거가 동시에 기동해 펫이 2마리 뜨는 것을 막는다.
    lock_f = None
    if fcntl is not None:
        try:
            os.makedirs(os.path.dirname(LOCKFILE), exist_ok=True)
            lock_f = open(LOCKFILE, "w")
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            return                           # another instance is handling it -> quit quietly / 다른 인스턴스 처리 중 → 조용히 종료

    try:
        if pet_alive():
            return
        if launch():
            # Hold the lock until the pet publishes its .port (up to ~10s), so a
            # closely following trigger does not misjudge it as "still dead" and
            # launch a second one.
            # 펫이 .port 를 발행할 때까지 락을 쥔 채 기다린다(최대 ~10초) — 2마리째를 막는다.
            up = False
            for _ in range(20):
                time.sleep(0.5)
                if pet_alive():
                    up = True
                    break
            _log("standalone pet launched (alive=%s)" % up)
    finally:
        if lock_f is not None:
            try:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                lock_f.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _log("exception: %r" % e)
    sys.exit(0)
