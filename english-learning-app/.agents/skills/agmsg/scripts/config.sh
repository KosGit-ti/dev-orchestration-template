#!/usr/bin/env bash
set -euo pipefail

# Manage agmsg configuration.
# Usage: config.sh get <key> [default]
#        config.sh set <key> <value>
#        config.sh show

ACTION="${1:?Usage: config.sh get|set|show ...}"
shift

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../db/config.yaml"

# --- Helpers ---

validate_yaml_key() {
  case "$1" in
    *[!A-Za-z0-9_.-]*|.*|*..*|*.) return 1 ;;
  esac
  case "$1" in
    *.*.*.*) return 1 ;;
  esac
  return 0
}

# Read a dotted key from the simple YAML shape agmsg writes.
# Supports top-level, section.field, and section.subsection.field.
yaml_get() {
  local key="$1"
  local default="${2:-}"

  validate_yaml_key "$key" || { echo "$default"; return; }

  if [ ! -f "$CONFIG_FILE" ]; then
    create_default_config
  fi

  IFS='.' read -r section subsection field extra <<< "$key"
  [ -z "${extra:-}" ] || { echo "$default"; return; }
  if [ -z "${subsection:-}" ]; then
    field="$section"
    section=""
  elif [ -z "${field:-}" ]; then
    field="$subsection"
    subsection=""
  fi

  local value=""
  if [ -n "${section:-}" ] && [ -n "${subsection:-}" ]; then
    value=$(awk -v section="$section" -v subsection="$subsection" -v field="$field" '
      /^[^ #]/ { in_section = ($0 == section ":"); in_subsection = 0 }
      in_section && $0 == "  " subsection ":" { in_subsection = 1; next }
      in_section && /^  [^ ]/ && $0 != "  " subsection ":" { in_subsection = 0 }
      in_section && in_subsection && index($0, "    " field ":") == 1 {
        sub(/^    [^ ]+:[ \t]*/, "")
        sub(/[ \t]+#.*$/, "")
        print
        exit
      }
    ' "$CONFIG_FILE")
  elif [ -n "${section:-}" ]; then
    value=$(awk -v section="$section" -v field="$field" '
      /^[^ #]/ { in_section = ($0 == section ":") }
      in_section && index($0, "  " field ":") == 1 {
        sub(/^  [^ ]+:[ \t]*/, "")
        sub(/[ \t]+#.*$/, "")
        print
        exit
      }
    ' "$CONFIG_FILE")
  else
    value=$(awk -v field="$field" '
      /^[^ #]/ && index($0, field ":") == 1 {
        sub(/^[^ ]+:[ \t]*/, "")
        sub(/[ \t]+#.*$/, "")
        print
        exit
      }
    ' "$CONFIG_FILE")
  fi

  if [ -n "$value" ]; then
    echo "$value"
  else
    echo "$default"
  fi
}

# Set a dotted key in YAML
yaml_set() {
  local key="$1"
  local value="$2"

  validate_yaml_key "$key" || { echo "Invalid config key: $key" >&2; exit 1; }
  mkdir -p "$(dirname "$CONFIG_FILE")"

  # Create config file with defaults if it doesn't exist
  if [ ! -f "$CONFIG_FILE" ]; then
    create_default_config
  fi

  IFS='.' read -r section subsection field extra <<< "$key"
  [ -z "${extra:-}" ] || { echo "Invalid config key depth: $key" >&2; exit 1; }
  if [ -z "${subsection:-}" ]; then
    field="$section"
    section=""
  elif [ -z "${field:-}" ]; then
    field="$subsection"
    subsection=""
  fi

  if [ -n "${section:-}" ] && [ -n "${subsection:-}" ]; then
    awk -v section="$section" -v subsection="$subsection" -v field="$field" -v value="$value" '
      /^[^ #]/ { in_section = ($0 == section ":"); in_subsection = 0 }
      in_section && $0 == "  " subsection ":" { in_subsection = 1; print; next }
      in_section && /^  [^ ]/ && $0 != "  " subsection ":" { in_subsection = 0 }
      in_section && in_subsection && index($0, "    " field ":") == 1 {
        print "    " field ": " value
        updated = 1
        next
      }
      { print }
      END { exit updated ? 0 : 2 }
    ' "$CONFIG_FILE" > "$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE" || {
      rm -f "$CONFIG_FILE.tmp"
      echo "Unsupported or missing nested config key: $key" >&2
      exit 1
    }
  elif [ -n "${section:-}" ]; then
    # Check if section exists
    if ! grep -q "^${section}:" "$CONFIG_FILE" 2>/dev/null; then
      printf '\n%s:\n  %s: %s\n' "$section" "$field" "$value" >> "$CONFIG_FILE"
    elif awk -v section="$section" -v field="$field" '
      /^[^ #]/ { in_section = ($0 == section ":") }
      in_section && index($0, "  " field ":") == 1 { found=1; exit }
      END { exit !found }
    ' "$CONFIG_FILE" 2>/dev/null; then
      # Update existing field under section
      awk -v section="$section" -v field="$field" -v value="$value" '
        /^[^ #]/ { in_section = ($0 == section ":") }
        in_section && index($0, "  " field ":") == 1 {
          print "  " field ": " value
          next
        }
        { print }
      ' "$CONFIG_FILE" > "$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
    else
      # Add field to existing section
      awk -v section="$section" -v field="$field" -v value="$value" '
        { print }
        /^[^ #]/ && $0 ~ "^" section ":" {
          print "  " field ": " value
        }
      ' "$CONFIG_FILE" > "$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
    fi
  else
    if grep -q "^${field}:" "$CONFIG_FILE" 2>/dev/null; then
      # Update existing top-level key
      awk -v field="$field" -v value="$value" '
        index($0, field ":") == 1 {
          print field ": " value
          next
        }
        { print }
      ' "$CONFIG_FILE" > "$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
    else
      printf '%s: %s\n' "$field" "$value" >> "$CONFIG_FILE"
    fi
  fi
}

create_default_config() {
  mkdir -p "$(dirname "$CONFIG_FILE")"
  cat > "$CONFIG_FILE" <<'YAML'
# agmsg configuration
# https://agmsg.cc/
#
# Mode (monitor | turn | both | off) is per-project — derived from each
# project's .claude/settings.local.json by `delivery.sh status`. There is
# no global "mode" key. Only machine-wide tuning lives here.

delivery:
  monitor:
    # watch.sh SQLite poll interval, seconds
    poll_interval: 5
  turn:
    # Stop hook cooldown, seconds. Legacy alias: hook.check_interval
    check_interval: 60
YAML
}

# --- Actions ---

case "$ACTION" in
  get)
    KEY="${1:?Usage: config.sh get <key> [default]}"
    DEFAULT="${2:-}"
    yaml_get "$KEY" "$DEFAULT"
    ;;
  set)
    KEY="${1:?Usage: config.sh set <key> <value>}"
    VALUE="${2:?Usage: config.sh set <key> <value>}"
    yaml_set "$KEY" "$VALUE"
    echo "Set $KEY = $VALUE"
    ;;
  show)
    if [ -f "$CONFIG_FILE" ]; then
      cat "$CONFIG_FILE"
    else
      echo "No config file. Using defaults."
      echo ""
      create_default_config
      cat "$CONFIG_FILE"
    fi
    ;;
  *)
    echo "Unknown action: $ACTION (use get|set|show)" >&2
    exit 1
    ;;
esac
