#!/usr/bin/env bash
set -euo pipefail

# Usage: leave.sh <team> <agent_id>
#
# Removes an agent from a team. Removes the team if empty.

TEAM="${1:?Usage: leave.sh <team> <agent_id>}"
AGENT_ID="${2:?Missing agent_id}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEAMS_DIR="$SCRIPT_DIR/../teams"

# Reject team names that would escape teams/ as a path segment (#140).
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/validate.sh"
agmsg_validate_team_name "$TEAM" || exit 1
agmsg_validate_agent_name "$AGENT_ID" || exit 1

TEAM_CONFIG="$TEAMS_DIR/$TEAM/config.json"

if [ ! -f "$TEAM_CONFIG" ]; then
  echo "Team not found: $TEAM"
  exit 1
fi

CONFIG_PATH=$(printf '%s' "$TEAM_CONFIG" | sed "s/'/''/g")
AGENT_JSON_PATH="$.agents.\"$AGENT_ID\""

# Check if agent exists
EXISTS=$(sqlite3 :memory: "SELECT json_extract(readfile('$CONFIG_PATH'), '$AGENT_JSON_PATH');")
if [ -z "$EXISTS" ] || [ "$EXISTS" = "null" ]; then
  echo "Agent $AGENT_ID not in team $TEAM"
  exit 1
fi

# Remove agent
UPDATED=$(sqlite3 :memory: "SELECT json_remove(readfile('$CONFIG_PATH'), '$AGENT_JSON_PATH');")

# Check if agents is now empty
AGENT_COUNT=$(sqlite3 :memory: \
  "SELECT count(*) FROM json_each(json_extract('$(printf '%s' "$UPDATED" | sed "s/'/''/g")', '$.agents'));")

if [ "$AGENT_COUNT" -eq 0 ]; then
  rm -f "$TEAM_CONFIG"
  rmdir "$TEAMS_DIR/$TEAM" 2>/dev/null || true
  echo "Left team $TEAM (team removed — no members left)"
else
  echo "$UPDATED" > "$TEAM_CONFIG"
  echo "Left team $TEAM"
fi
