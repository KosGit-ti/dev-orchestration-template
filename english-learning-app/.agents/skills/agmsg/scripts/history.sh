#!/usr/bin/env bash
set -euo pipefail

# Usage: history.sh <team> [agent_id] [limit]
# Shows message history. If agent_id given, shows only that agent's messages.

TEAM="${1:?Usage: history.sh <team> [agent_id] [limit]}"
AGENT="${2:-}"
LIMIT="${3:-20}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib/storage.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/validate.sh"
agmsg_validate_team_name "$TEAM" || exit 1
if [ -n "$AGENT" ]; then
  agmsg_validate_agent_name "$AGENT" || exit 1
fi
case "$LIMIT" in
  ''|*[!0-9]*)
    echo "agmsg: invalid history limit '$LIMIT': must be a non-negative integer" >&2
    exit 1 ;;
esac
DB="$(agmsg_db_path)"

if [ ! -f "$DB" ]; then
  echo "No messages (DB not initialized)"
  exit 0
fi

if [ -n "$AGENT" ]; then
  WHERE="WHERE team='$TEAM' AND (from_agent='$AGENT' OR to_agent='$AGENT')"
else
  WHERE="WHERE team='$TEAM'"
fi

# Escape newlines/tabs in body, use unit separator between fields
RESULT=$(agmsg_sqlite "$DB" "
  SELECT from_agent || char(31) || to_agent || char(31) || replace(replace(body, char(10), '\n'), char(9), '\t') || char(31) || created_at || char(31) || CASE WHEN read_at IS NULL THEN '●' ELSE '○' END
  FROM messages $WHERE ORDER BY created_at DESC LIMIT $LIMIT;
")

if [ -z "$RESULT" ]; then
  echo "No message history."
  exit 0
fi

# Reverse order (oldest first) and display
REVERSED=$(echo "$RESULT" | tail -r 2>/dev/null || echo "$RESULT" | tac 2>/dev/null || echo "$RESULT" | awk '{a[NR]=$0} END{for(i=NR;i>=1;i--)print a[i]}')
while IFS=$'\x1f' read -r from to body ts status; do
  echo "  $status [$ts] $from → $to: $body"
done <<< "$REVERSED"
