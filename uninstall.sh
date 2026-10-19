#!/bin/sh
# claudi 제거 래퍼 — installer/install.py uninstall 로 위임.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$here/installer/install.py" uninstall "$@"
