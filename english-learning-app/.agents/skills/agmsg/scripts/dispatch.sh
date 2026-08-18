#!/usr/bin/env bash
set -euo pipefail

# User-facing agmsg command dispatcher.
#
# This script keeps command semantics on the Bash side. Native Windows
# launchers should do only platform setup, then delegate here.
#
# Usage:
#   dispatch.sh [--type <agent_type>] [--project <path>] [--team <team>] [--agent <agent>] [--argv-file <path>] [--] [command ...]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

AGENT_TYPE="${AGMSG_AGENT_TYPE:-codex}"
PROJECT="$PWD"
TEAM="${AGMSG_TEAM:-}"
AGENT="${AGMSG_AGENT:-}"
ARGV_FILE=""

usage() {
  cat >&2 <<'EOF'
usage: agmsg [--team <team>] [--agent <agent>] [command ...]

commands:
  inbox [--quiet] [--peek]
  send <to> <message>
  history [agent] [limit]
  team [team]
  config [show|set ...]
  mode [turn|off]
  join <team> <agent>
  reset [agent]
  actas <agent>
  drop <agent>
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --type)    AGENT_TYPE="${2:?--type needs a value}"; shift 2 ;;
    --project) PROJECT="${2:?--project needs a path}"; shift 2 ;;
    --team)    TEAM="${2:?--team needs a value}"; shift 2 ;;
    --agent)   AGENT="${2:?--agent needs a value}"; shift 2 ;;
    --argv-file) ARGV_FILE="${2:?--argv-file needs a path}"; shift 2 ;;
    --) shift; break ;;
    -h|--help) usage; exit 0 ;;
    *) break ;;
  esac
done

if [ -n "$ARGV_FILE" ]; then
  if ! command -v base64 >/dev/null 2>&1; then
    echo "base64 is required to decode PowerShell argv handoff." >&2
    exit 127
  fi
  base64_decode() {
    if printf '' | base64 --decode >/dev/null 2>&1; then
      base64 --decode
    elif printf '' | base64 -d >/dev/null 2>&1; then
      base64 -d
    elif printf '' | base64 -D >/dev/null 2>&1; then
      base64 -D
    else
      echo "base64 does not support --decode, -d, or -D on this system." >&2
      return 127
    fi
  }
  decoded_args=()
  while IFS= read -r encoded || [ -n "$encoded" ]; do
    encoded="${encoded%$'\r'}"
    decoded_args+=("$(printf '%s' "$encoded" | base64_decode)")
  done < "$ARGV_FILE"
  set -- "${decoded_args[@]}" "$@"
fi

COMMAND="${1:-inbox}"
if [ $# -gt 0 ]; then
  shift
fi

run_script() {
  local script="$1"
  shift
  bash "$SCRIPT_DIR/$script" "$@"
}

require_args() {
  local usage_text="$1"
  local min="$2"
  shift 2
  if [ "$#" -lt "$min" ]; then
    echo "usage: $usage_text" >&2
    exit 2
  fi
}

kv_get() {
  local line="$1"
  local key="$2"
  local part
  for part in $line; do
    case "$part" in
      "$key="*) printf '%s\n' "${part#*=}"; return 0 ;;
    esac
  done
  return 1
}

team_count() {
  local teams="$1"
  if [ -z "$teams" ]; then
    printf '0\n'
    return 0
  fi
  awk -F',' '{print NF}' <<< "$teams"
}

for_each_team() {
  local teams="$1"
  shift
  local old_ifs="$IFS"
  local team
  IFS=,
  for team in $teams; do
    IFS="$old_ifs"
    [ -n "$team" ] || continue
    "$@" "$team"
    IFS=,
  done
  IFS="$old_ifs"
}

single_resolved_team() {
  local command_name="$1"
  local count
  count="$(team_count "$RESOLVED_TEAM")"
  if [ "$count" -gt 1 ]; then
    echo "Multiple agmsg teams match this project; pass --team <team> for '$command_name'." >&2
    echo "Matched teams: $RESOLVED_TEAM" >&2
    exit 2
  fi
  printf '%s\n' "$RESOLVED_TEAM"
}

run_inbox_for_team() {
  run_script inbox.sh "$1" "$RESOLVED_AGENT" "${INBOX_ARGS[@]}"
}

run_history_for_team() {
  if [ "${#HISTORY_ARGS[@]}" -gt 0 ]; then
    run_script history.sh "$1" "${HISTORY_ARGS[@]}"
  elif [ -n "$RESOLVED_AGENT" ]; then
    run_script history.sh "$1" "$RESOLVED_AGENT"
  else
    run_script history.sh "$1"
  fi
}

run_team_for_team() {
  run_script team.sh "$1"
}

show_identity_guidance() {
  local whoami_output="$1"
  printf '%s\n\n' "$whoami_output" >&2
  cat >&2 <<'EOF'
Resolve the identity explicitly, for example:
  agmsg join <team> <agent>
  agmsg --team <team> --agent <agent> inbox
  $env:AGMSG_TEAM = "<team>"; $env:AGMSG_AGENT = "<agent>"; agmsg inbox
EOF
}

RESOLVED_TEAM=""
RESOLVED_AGENT=""

resolve_identity() {
  local need_team="$1"
  local need_agent="$2"

  RESOLVED_TEAM="$TEAM"
  RESOLVED_AGENT="$AGENT"

  if { [ "$need_team" != "1" ] || [ -n "$RESOLVED_TEAM" ]; } &&
     { [ "$need_agent" != "1" ] || [ -n "$RESOLVED_AGENT" ]; }; then
    return 0
  fi

  local whoami_output
  local whoami_code
  set +e
  whoami_output="$(run_script whoami.sh "$PROJECT" "$AGENT_TYPE" 2>&1)"
  whoami_code=$?
  set -e
  if [ "$whoami_code" -ne 0 ]; then
    printf '%s\n' "$whoami_output" >&2
    exit "$whoami_code"
  fi

  case "$whoami_output" in
    agent=*)
      if [ -z "$RESOLVED_TEAM" ]; then
        RESOLVED_TEAM="$(kv_get "$whoami_output" teams || true)"
      fi
      if [ -z "$RESOLVED_AGENT" ]; then
        RESOLVED_AGENT="$(kv_get "$whoami_output" agent || true)"
      fi
      ;;
    *)
      show_identity_guidance "$whoami_output"
      exit 2
      ;;
  esac

  if [ "$need_team" = "1" ] && [ -z "$RESOLVED_TEAM" ]; then
    echo "Could not resolve AGMSG_TEAM. Pass --team or set AGMSG_TEAM." >&2
    exit 2
  fi
  if [ "$need_agent" = "1" ] && [ -z "$RESOLVED_AGENT" ]; then
    echo "Could not resolve AGMSG_AGENT. Pass --agent or set AGMSG_AGENT." >&2
    exit 2
  fi
}

case "$COMMAND" in
  ""|inbox)
    resolve_identity 1 1
    INBOX_ARGS=("$@")
    for_each_team "$RESOLVED_TEAM" run_inbox_for_team
    ;;

  send)
    require_args "agmsg send <to> <message>" 2 "$@"
    resolve_identity 1 1
    to="$1"
    shift
    body="$*"
    run_script send.sh "$(single_resolved_team send)" "$RESOLVED_AGENT" "$to" "$body"
    ;;

  history)
    resolve_identity 1 0
    HISTORY_ARGS=("$@")
    for_each_team "$RESOLVED_TEAM" run_history_for_team
    ;;

  team)
    team_arg="${1:-${TEAM:-}}"
    if [ -z "$team_arg" ]; then
      resolve_identity 1 0
      team_arg="$RESOLVED_TEAM"
    fi
    for_each_team "$team_arg" run_team_for_team
    ;;

  config)
    if [ "$#" -eq 0 ]; then
      run_script config.sh show
    else
      run_script config.sh "$@"
    fi
    ;;

  mode)
    case "$#" in
      0)
        run_script delivery.sh status "$AGENT_TYPE" "$PROJECT"
        ;;
      1)
        if [ "$AGENT_TYPE" = "codex" ] && [ "$1" = "both" ]; then
          echo "Codex bridge beta supports 'monitor', 'turn', or 'off'; 'both' is not supported." >&2
          exit 2
        fi
        run_script delivery.sh set "$1" "$AGENT_TYPE" "$PROJECT"
        ;;
      *)
        echo "usage: agmsg mode [monitor|turn|both|off]" >&2
        exit 2
        ;;
    esac
    ;;

  join)
    require_args "agmsg join <team> <agent>" 2 "$@"
    run_script join.sh "$1" "$2" "$AGENT_TYPE" "$PROJECT"
    ;;

  reset)
    target_agent="${1:-${AGENT:-}}"
    if [ -n "$target_agent" ]; then
      run_script reset.sh "$PROJECT" "$AGENT_TYPE" "$target_agent"
    else
      run_script reset.sh "$PROJECT" "$AGENT_TYPE"
    fi
    ;;

  drop)
    require_args "agmsg drop <agent>" 1 "$@"
    run_script reset.sh "$PROJECT" "$AGENT_TYPE" "$1"
    ;;

  actas)
    require_args "agmsg actas <agent>" 1 "$@"
    name="$1"
    resolve_identity 1 0
    team_name="$(single_resolved_team actas)"

    identities="$(run_script identities.sh "$PROJECT" "$AGENT_TYPE")"
    found=0
    while IFS=$'\t' read -r identity_team identity_agent; do
      [ -n "${identity_team:-}" ] || continue
      if [ "$identity_team" = "$team_name" ] && [ "$identity_agent" = "$name" ]; then
        found=1
        break
      fi
    done <<< "$identities"

    if [ "$found" -eq 0 ]; then
      run_script join.sh "$team_name" "$name" "$AGENT_TYPE" "$PROJECT"
    fi
    echo "To act as '$name' in this PowerShell session, run:"
    echo "  \$env:AGMSG_TEAM = '$team_name'; \$env:AGMSG_AGENT = '$name'"
    ;;

  *)
    echo "Unknown agmsg command: $COMMAND" >&2
    usage
    exit 2
    ;;
esac
