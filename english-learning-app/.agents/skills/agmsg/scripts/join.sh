#!/usr/bin/env bash
set -euo pipefail

# Usage: join.sh <team> <agent_id> <type> <project_path>
#
# Adds an agent to a team. Creates the team if it doesn't exist.

TEAM="${1:?Usage: join.sh <team> <agent_id> <type> <project_path>}"
AGENT_ID="${2:?Missing agent_id}"
AGENT_TYPE="${3:?Missing type (claude-code | codex)}"
PROJECT_PATH="${4:?Missing project_path}"

# Reject unknown agent types — the rest of agmsg (delivery.sh,
# session-start.sh, identities.sh lookups) only supports the values listed
# here. Allowing arbitrary strings silently mis-registers an agent and
# makes monitor mode fail with a confusing "no joined teams" message.
case "$AGENT_TYPE" in
  claude-code|codex|gemini|antigravity|copilot|opencode) ;;
  *) echo "Unknown agent type: '$AGENT_TYPE' (supported: claude-code, codex, gemini, antigravity, copilot, opencode)" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEAMS_DIR="$SCRIPT_DIR/../teams"

# Reject team names that would escape teams/ as a path segment (#140).
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/validate.sh"
agmsg_validate_team_name "$TEAM" || exit 1
agmsg_validate_agent_name "$AGENT_ID" || exit 1
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/team-lock.sh"

# Resolve the session's real project root from the passed pwd (see #92), so an
# agent-driven join from a subdir/worktree registers under the project the
# session lives in instead of minting a phantom record for the subdir.
# Callers passing an explicit, deliberate path (e.g. spawn.sh's --project, which
# may not be registered yet) set AGMSG_RESOLVE_PROJECT=0 to keep their path.
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/resolve-project.sh"
PROJECT_PATH="$(agmsg_resolve_project "$PROJECT_PATH" "$AGENT_TYPE")"

TEAM_CONFIG="$TEAMS_DIR/$TEAM/config.json"
LOCK_DIR="$(agmsg_team_lock_acquire "$TEAM")"
cleanup_lock() {
  agmsg_team_lock_release "$LOCK_DIR"
}
trap cleanup_lock EXIT

# --- Ensure team config exists ---
mkdir -p "$TEAMS_DIR/$TEAM"
if [ ! -f "$TEAM_CONFIG" ]; then
  tmp_config="$(mktemp "$TEAM_CONFIG.tmp.XXXXXX")"
  cat > "$tmp_config" <<EOF
{
  "name": "$TEAM",
  "agents": {},
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  mv "$tmp_config" "$TEAM_CONFIG"
  echo "Created team: $TEAM"
fi

# --- Add or extend agent registrations ---
ADDED_REGISTRATION=false
CONFIG_PATH=$(printf '%s' "$TEAM_CONFIG" | sed "s/'/''/g")
AGENT_TYPE_ESCAPED=$(printf '%s' "$AGENT_TYPE" | sed "s/'/''/g")
PROJECT_ESCAPED=$(printf '%s' "$PROJECT_PATH" | sed "s/'/''/g")
REGISTRATION=$(sqlite3 :memory: "SELECT json_object('type', '$AGENT_TYPE_ESCAPED', 'project', '$PROJECT_ESCAPED');")
REGISTRATION_ESCAPED=$(printf '%s' "$REGISTRATION" | sed "s/'/''/g")
AGENT_JSON_PATH="$.agents.\"$AGENT_ID\""

EXISTING=$(sqlite3 :memory: "SELECT json_extract(readfile('$CONFIG_PATH'), '$AGENT_JSON_PATH');")

if [ -z "$EXISTING" ] || [ "$EXISTING" = "null" ]; then
  AGENT_OBJ="{\"registrations\":[${REGISTRATION}]}"
  ADDED_REGISTRATION=true
else
  EXISTING_ESCAPED=$(printf '%s' "$EXISTING" | sed "s/'/''/g")
  NORMALIZED=$(sqlite3 :memory: "
    WITH agent(a) AS (SELECT '$EXISTING_ESCAPED')
    SELECT CASE
      WHEN json_type(json_extract(a, '\$.registrations')) = 'array' THEN a
      ELSE json_object(
        'registrations',
        json_array(json_object(
          'type', json_extract(a, '\$.type'),
          'project', json_extract(a, '\$.project')
        ))
      )
    END
    FROM agent;
  ")
  NORMALIZED_ESCAPED=$(printf '%s' "$NORMALIZED" | sed "s/'/''/g")

  HAS_REGISTRATION=$(sqlite3 :memory: "
    SELECT EXISTS(
      SELECT 1
      FROM json_each(json_extract('$NORMALIZED_ESCAPED', '\$.registrations'))
      WHERE json_extract(value, '\$.type') = '$AGENT_TYPE'
        AND json_extract(value, '\$.project') = '$PROJECT_ESCAPED'
    );
  ")

  if [ "$HAS_REGISTRATION" = "1" ]; then
    AGENT_OBJ="$NORMALIZED"
  else
    ADDED_REGISTRATION=true
    AGENT_OBJ=$(sqlite3 :memory: "
      SELECT json_set(
        '$NORMALIZED_ESCAPED',
        '\$.registrations[' || json_array_length(json_extract('$NORMALIZED_ESCAPED', '\$.registrations')) || ']',
        json('$REGISTRATION_ESCAPED')
      );
    ")
  fi
fi

UPDATED=$(sqlite3 :memory: \
  "SELECT json_set(readfile('$CONFIG_PATH'), '$AGENT_JSON_PATH', json('$(printf '%s' "$AGENT_OBJ" | sed "s/'/''/g")'));")
tmp_config="$(mktemp "$TEAM_CONFIG.tmp.XXXXXX")"
printf '%s\n' "$UPDATED" > "$tmp_config"
mv "$tmp_config" "$TEAM_CONFIG"

if [ -n "${AGMSG_JOIN_RESULT_FILE:-}" ]; then
  {
    printf 'added_registration=%s\n' "$ADDED_REGISTRATION"
    printf 'team=%s\n' "$TEAM"
    printf 'agent=%s\n' "$AGENT_ID"
    printf 'type=%s\n' "$AGENT_TYPE"
    printf 'project=%s\n' "$PROJECT_PATH"
  } > "$AGMSG_JOIN_RESULT_FILE"
fi

echo "Joined team $TEAM as $AGENT_ID"
