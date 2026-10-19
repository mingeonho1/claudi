#!/bin/sh
# claudi 설치 래퍼 — installer/install.py install 로 위임.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$here/installer/install.py" install "$@"
