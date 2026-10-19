#!/bin/sh
# Open a deep link when a notification is clicked (with logging).
# 알림 클릭 시 딥링크를 연다(로그 남김).
# Log location: enforce/logs under the config dir (CLAUDE_CONFIG_DIR, default ~/.claude).
# 로그 위치는 설정 디렉터리(CLAUDE_CONFIG_DIR, 없으면 ~/.claude) 아래 enforce/logs.
logdir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/enforce/logs"
mkdir -p "$logdir" 2>/dev/null
log="$logdir/open-link.log"
echo "$(date '+%F %T') url=$1" >> "$log"
/usr/bin/open "$1" >> "$log" 2>&1
echo "  exit=$?" >> "$log"
