"""claudi 테스트 공용 헬퍼 (표준 라이브러리만).

모든 테스트는 CLAUDE_CONFIG_DIR 를 임시 디렉터리로 두고 돌린다 — 실제 ~/.claude 는
절대 건드리지 않는다. 네트워크·GUI 도 필요 없다.
"""

import os
import subprocess
import sys
import tempfile

# 리포지토리 루트와 스크립트 디렉터리(이 파일 기준).
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MON_BIN = os.path.join(REPO, "claude", "monitor", "bin")
ENF_BIN = os.path.join(REPO, "claude", "enforce", "bin")


def script(bindir, name):
    return os.path.join(bindir, name)


def make_config_dir():
    """monitor/ · enforce/ 하위를 갖춘 임시 CLAUDE_CONFIG_DIR 를 만든다."""
    d = tempfile.mkdtemp(prefix="claudi-test-")
    os.makedirs(os.path.join(d, "monitor"), exist_ok=True)
    os.makedirs(os.path.join(d, "enforce"), exist_ok=True)
    os.makedirs(os.path.join(d, "run"), exist_ok=True)   # 빈 XDG_RUNTIME_DIR
    return d


def base_env(config_dir, **extra):
    """스크립트 subprocess 용 환경. 데스크톱 부작용을 끄고 경로를 임시 디렉터리로."""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = config_dir
    env["CLAUDI_NO_DESKTOP_NOTIFY"] = "1"       # 펫 TCP·macOS 알림 억제
    env["XDG_RUNTIME_DIR"] = os.path.join(config_dir, "run")  # 실제 펫 포트와 격리
    env["CLAUDE_CODE_ENTRYPOINT"] = "cli"       # 헤드리스 스킵 회피
    env.pop("CLAUDLET_", None)
    env.update(extra)
    return env


def run_script(path, args=None, env=None, input_text=None, timeout=60):
    argv = [sys.executable, path] + (args or [])
    return subprocess.run(argv, env=env, input=input_text,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=timeout)


def monitor_path(config_dir, *parts):
    return os.path.join(config_dir, "monitor", *parts)


def enforce_path(config_dir, *parts):
    return os.path.join(config_dir, "enforce", *parts)
