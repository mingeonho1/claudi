#!/usr/bin/env python3
# ~/.claude/enforce/bin/claudlet_paths.py
#
# Shared helper to discover where claudlet (the pipx-installed desktop pet) lives.
# Stdlib only. Shared by ensure-pet.py / patch-claudlet.py. It does not import the
# claudlet package; it only finds "that venv's Python interpreter" and the
# "claudlet-attach executable".
# claudlet(pipx 설치형 데스크톱 펫)의 설치 위치를 발견하는 공용 헬퍼. 표준 라이브러리만.
# claudlet 패키지를 직접 import 하지 않고 venv 파이썬 경로와 claudlet-attach 경로만 찾는다.
#
# venv Python search order (stops at first hit):
#   1) env CLAUDI_CLAUDLET_PYTHON (explicit).
#   2) the Python the `claudlet-attach` shebang (#!) on PATH or in ~/.local/bin points to.
#   3) ~/Library/Application Support/pipx/venvs/claudlet/bin/python  (macOS pipx default).
#   4) ~/.local/pipx/venvs/claudlet/bin/python                       (Linux pipx default).
# venv 파이썬 탐색 순서: 1) 환경변수, 2) claudlet-attach 셔뱅이 가리키는 파이썬, 3)/4) pipx 기본 위치.
#
# claudlet-attach path: PATH lookup -> else ~/.local/bin/claudlet-attach.
# claudlet-attach 경로: PATH 조회 → 없으면 ~/.local/bin/claudlet-attach.

from __future__ import annotations

import os
import shutil

# pipx venv default install locations (per platform); used by the 2nd/3rd fallbacks.
# pipx venv 의 기본 설치 위치(플랫폼별). 2·3순위 폴백에 쓴다.
_PIPX_VENV_CANDIDATES = (
    "~/Library/Application Support/pipx/venvs/claudlet/bin/python",
    "~/.local/pipx/venvs/claudlet/bin/python",
)


def claudlet_attach_path() -> str:
    """Path to the `claudlet-attach` executable. PATH first, else ~/.local/bin. '' if not found.
    `claudlet-attach` 실행 파일 경로. PATH 우선, 없으면 ~/.local/bin. 못 찾으면 ''."""
    found = shutil.which("claudlet-attach")
    if found:
        return found
    cand = os.path.expanduser("~/.local/bin/claudlet-attach")
    return cand if os.path.exists(cand) else ""


def _shebang_python(script: str) -> str:
    """The Python path a script's shebang (#!) points to. Handles `env python`. '' on failure.
    스크립트의 셔뱅(#!) 이 가리키는 파이썬 경로. `env python` 형태도 처리. 실패 시 ''."""
    try:
        with open(script, "rb") as f:
            first = f.readline()
    except OSError:
        return ""
    if not first.startswith(b"#!"):
        return ""
    parts = first[2:].strip().decode("utf-8", "replace").split()
    if not parts:
        return ""
    exe = parts[0]
    # "/usr/bin/env python3" -> find python3 on PATH. / "/usr/bin/env python3" → PATH 에서 찾는다.
    if os.path.basename(exe) == "env" and len(parts) > 1:
        return shutil.which(parts[1]) or ""
    return exe


def venv_python() -> str:
    """Path to claudlet venv's Python interpreter, searched in order; '' if not found.
    claudlet venv 의 파이썬 인터프리터 경로. 위 순서로 탐색하고 못 찾으면 ''."""
    env = os.environ.get("CLAUDI_CLAUDLET_PYTHON")
    if env:
        return env                         # trust an explicit setting as is / 명시 지정은 그대로 신뢰
    attach = claudlet_attach_path()
    if attach:
        py = _shebang_python(attach)
        if py and os.path.isfile(py):
            return py
    for cand in _PIPX_VENV_CANDIDATES:
        p = os.path.expanduser(cand)
        if os.path.isfile(p):
            return p
    return ""


def petpy_glob_candidates() -> list:
    """site-packages glob patterns for finding pet.py directly (fallback).
    pet.py 를 직접 찾을 때 쓸 site-packages 글롭 패턴 목록(폴백용)."""
    bases = (
        "~/Library/Application Support/pipx/venvs/claudlet/lib",
        "~/.local/pipx/venvs/claudlet/lib",
    )
    return [os.path.expanduser(os.path.join(b, "*", "site-packages",
                                            "claudlet", "pet.py"))
            for b in bases]


if __name__ == "__main__":
    print("claudlet-attach:", claudlet_attach_path() or "(not found)")
    print("venv python    :", venv_python() or "(not found)")
