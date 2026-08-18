#!/usr/bin/env bash
set -euo pipefail

# Usage: inbox.sh <team> <agent_id> [--quiet] [--peek] [--mark-until <created_at>] [--mark-ids <ids>] [--ids-file <path>]
# Shows unread messages and marks them as read unless --peek is set.
# --quiet: only output if there are unread messages (for hooks)
# --peek: show unread messages without marking them as read
# --mark-until: when marking read, only mark rows created at or before this timestamp
# --mark-ids: mark only the comma-separated message row ids and exit
# --ids-file: write the unread row ids displayed by this invocation

usage() {
  echo "Usage: inbox.sh <team> <agent_id> [--quiet] [--peek] [--mark-until <created_at>] [--mark-ids <ids>] [--ids-file <path>]" >&2
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

TEAM="${1:-}"
if [ -z "$TEAM" ]; then
  usage
  exit 2
fi
AGENT="${2:?Missing agent_id}"
QUIET=false
PEEK=false
MARK_UNTIL=""
MARK_IDS=""
IDS_FILE=""
args=("${@:3}")
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  arg="${args[$i]}"
  case "$arg" in
    --quiet) QUIET=true ;;
    --peek) PEEK=true ;;
    --mark-until)
      i=$((i + 1))
      [ "$i" -lt "${#args[@]}" ] || { echo "--mark-until needs a value" >&2; usage; exit 2; }
      MARK_UNTIL="${args[$i]}"
      ;;
    --mark-ids)
      i=$((i + 1))
      [ "$i" -lt "${#args[@]}" ] || { echo "--mark-ids needs a value" >&2; usage; exit 2; }
      MARK_IDS="${args[$i]}"
      ;;
    --ids-file)
      i=$((i + 1))
      [ "$i" -lt "${#args[@]}" ] || { echo "--ids-file needs a value" >&2; usage; exit 2; }
      IDS_FILE="${args[$i]}"
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $arg" >&2
      usage
      exit 2
      ;;
  esac
  i=$((i + 1))
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib/storage.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/validate.sh"
agmsg_validate_team_name "$TEAM" || exit 1
agmsg_validate_agent_name "$AGENT" || exit 1
if [ -n "$MARK_UNTIL" ] && ! printf '%s' "$MARK_UNTIL" \
    | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'; then
  echo "Invalid --mark-until timestamp: $MARK_UNTIL" >&2
  exit 2
fi
if [ -n "$MARK_IDS" ] && ! printf '%s' "$MARK_IDS" | grep -Eq '^[0-9]+(,[0-9]+)*$'; then
  echo "Invalid --mark-ids value: $MARK_IDS" >&2
  exit 2
fi
DB="$(agmsg_db_path)"

if [ ! -f "$DB" ]; then
  if [ "$QUIET" = true ]; then exit 0; fi
  echo "No messages (DB not initialized)"
  exit 0
fi

if [ -n "$MARK_IDS" ]; then
  agmsg_sqlite "$DB" "UPDATE messages SET read_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE team='$TEAM' AND to_agent='$AGENT' AND id IN ($MARK_IDS);" 2>/dev/null || true
  exit 0
fi

# Get unread messages — escape newlines/tabs in body to keep one record per line
UNREAD=$(agmsg_sqlite "$DB" "
  SELECT id || char(31) || from_agent || char(31) || replace(replace(body, char(10), '\n'), char(9), '\t') || char(31) || created_at
  FROM messages WHERE team='$TEAM' AND to_agent='$AGENT' AND read_at IS NULL
  ORDER BY created_at ASC, id ASC;
")

if [ -z "$UNREAD" ]; then
  if [ "$QUIET" = true ]; then exit 0; fi
  echo "No new messages."
  exit 0
fi

IDS=$(printf '%s\n' "$UNREAD" | cut -d "$(printf '\037')" -f 1 | paste -sd, -)
if [ -n "$IDS_FILE" ]; then
  tmp_ids="$(mktemp "${TMPDIR:-/tmp}/agmsg-inbox-ids.XXXXXX")"
  printf '%s\n' "$IDS" > "$tmp_ids"
  mv "$tmp_ids" "$IDS_FILE"
fi

# Display
COUNT=$(echo "$UNREAD" | wc -l | tr -d ' ')
echo "$COUNT new message(s):"
echo ""
while IFS=$'\x1f' read -r _id from body ts; do
  echo "  [$ts] $from: $body"
done <<< "$UNREAD"
echo ""

if [ "$PEEK" = false ]; then
  # Mark as read (non-fatal — may fail in sandboxed environments)
  mark_until_predicate=""
  if [ -n "$MARK_UNTIL" ]; then
    mark_until_predicate=" AND created_at <= '$MARK_UNTIL'"
  fi
  agmsg_sqlite "$DB" "UPDATE messages SET read_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE team='$TEAM' AND to_agent='$AGENT' AND read_at IS NULL AND id IN ($IDS)$mark_until_predicate;" 2>/dev/null || true
fi
