#!/usr/bin/env bash
set -euo pipefail

# agmsg — Agent Messaging uninstaller
# Removes messaging skill, commands, hooks, and optionally DB/teams.
#
# Usage:
#   ./uninstall.sh                    # Interactive (confirms each step)
#   ./uninstall.sh --yes              # Remove all without confirmation
#   ./uninstall.sh --keep-data        # Remove skill but keep DB and teams

AGENTS_DIR="$HOME/.agents"

AUTO_YES=false
KEEP_DATA=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)       AUTO_YES=true;  shift ;;
    --keep-data)    KEEP_DATA=true; shift ;;
    -h|--help)
      echo "Usage: ./uninstall.sh [options]"
      echo ""
      echo "Options:"
      echo "  --yes, -y       Remove all without confirmation"
      echo "  --keep-data     Remove skill but keep DB and team configs"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo ""
echo "  agmsg — Uninstall"
echo "  ──────────────────"
echo ""

confirm() {
  if [ "$AUTO_YES" = true ]; then return 0; fi
  printf "  %s (y/n) [n]: " "$1"
  read -r input
  [ "${input:-n}" = "y" ] || [ "${input:-n}" = "Y" ]
}

sql_readfile_path() {
  local path="$1"
  if command -v cygpath >/dev/null 2>&1; then
    path=$(cygpath -w "$path" 2>/dev/null || printf '%s' "$path")
  fi
  printf '%s' "$path" | sed "s/'/''/g"
}

strip_agmsg_event_file() {
  local skill_dir="$1"
  local path="$2"
  local event="$3"
  local script_dir="$skill_dir/scripts"
  local sql_path tmp tmp_sql skill_dir_sql script_dir_sql wrote
  sql_path=$(sql_readfile_path "$path")
  tmp=$(mktemp "${TMPDIR:-/tmp}/agmsg-uninstall.XXXXXX")
  tmp_sql=$(sql_readfile_path "$tmp")
  skill_dir_sql=$(printf '%s/' "$skill_dir" | sed "s/'/''/g")
  script_dir_sql=$(printf '%s/' "$script_dir" | sed "s/'/''/g")
  wrote=$(sqlite3 :memory: "
    WITH src AS (SELECT CAST(readfile('$sql_path') AS TEXT) AS j),
    out AS (SELECT coalesce(CASE
      WHEN json_extract(src.j, '\$.hooks.$event') IS NULL THEN src.j
      WHEN (SELECT count(*) FROM json_each(json_extract(src.j, '\$.hooks.$event')) AS s
            WHERE NOT EXISTS (
              SELECT 1 FROM json_each(json_extract(s.value, '\$.hooks')) AS h
              WHERE instr(coalesce(json_extract(h.value, '\$.command'), ''), '$skill_dir_sql') > 0
                 OR instr(coalesce(json_extract(h.value, '\$.command'), ''), '$script_dir_sql') > 0
            )) = 0 THEN
        json_remove(src.j, '\$.hooks.$event')
      ELSE
        json_set(src.j, '\$.hooks.$event',
          (SELECT json_group_array(json(s.value))
           FROM json_each(json_extract(src.j, '\$.hooks.$event')) AS s
           WHERE NOT EXISTS (
             SELECT 1 FROM json_each(json_extract(s.value, '\$.hooks')) AS h
             WHERE instr(coalesce(json_extract(h.value, '\$.command'), ''), '$skill_dir_sql') > 0
                OR instr(coalesce(json_extract(h.value, '\$.command'), ''), '$script_dir_sql') > 0
           ))
        )
    END, '') AS blob FROM src)
    SELECT writefile('$tmp_sql', blob) = length(CAST(blob AS BLOB)) FROM out;
  ") || { rm -f "$tmp"; return 1; }
  [ "$wrote" = "1" ] || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$path"
}

prune_empty_hooks_file() {
  local path="$1"
  local sql_path tmp tmp_sql wrote
  sql_path=$(sql_readfile_path "$path")
  tmp=$(mktemp "${TMPDIR:-/tmp}/agmsg-uninstall.XXXXXX")
  tmp_sql=$(sql_readfile_path "$tmp")
  wrote=$(sqlite3 :memory: "
    WITH src AS (SELECT CAST(readfile('$sql_path') AS TEXT) AS j),
    out AS (SELECT coalesce(CASE
      WHEN json_extract(src.j, '\$.hooks') IS NULL THEN src.j
      WHEN (SELECT count(*) FROM json_each(json_extract(src.j, '\$.hooks'))) = 0 THEN
        json_remove(src.j, '\$.hooks')
      ELSE src.j
    END, '') AS blob FROM src)
    SELECT writefile('$tmp_sql', blob) = length(CAST(blob AS BLOB)) FROM out;
  ") || { rm -f "$tmp"; return 1; }
  [ "$wrote" = "1" ] || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$path"
}

# --- Find installed skill directories ---
SKILL_DIRS=()
for d in "$AGENTS_DIR"/skills/*/; do
  if [ -f "${d}.agmsg" ]; then
    SKILL_DIRS+=("${d%/}")
  fi
done

if [ ${#SKILL_DIRS[@]} -eq 0 ]; then
  echo "  Nothing to remove (not installed?)"
  echo ""
  exit 0
fi

echo "  Found installation(s):"
for sd in "${SKILL_DIRS[@]}"; do
  echo "    $(basename "$sd") → $sd"
done
echo ""

REMOVED=false

# --- 1. Remove slash commands and hooks from joined projects ---
for SKILL_DIR in "${SKILL_DIRS[@]}"; do
  TEAMS_DIR="$SKILL_DIR/teams"
  [ -d "$TEAMS_DIR" ] || continue

  echo "  Scanning joined projects for commands and hooks..."
  for config in "$TEAMS_DIR"/*/config.json; do
    [ -f "$config" ] || continue

    config_path=$(printf '%s' "$config" | sed "s/'/''/g")
    projects=$(sqlite3 -separator '	' :memory: \
      "WITH agents AS (
         SELECT CASE
           WHEN json_type(json_extract(value, '\$.registrations')) = 'array' THEN json_extract(value, '\$.registrations')
           ELSE json_array(json_object('type', json_extract(value, '\$.type'), 'project', json_extract(value, '\$.project')))
         END AS registrations
         FROM json_each(json_extract(readfile('$config_path'), '\$.agents'))
       )
       SELECT DISTINCT json_extract(r.value, '\$.project')
       FROM agents, json_each(agents.registrations) AS r
       WHERE json_extract(r.value, '\$.type') IN ('claude-code', 'codex', 'gemini', 'antigravity', 'copilot', 'opencode')
         AND json_extract(r.value, '\$.project') IS NOT NULL;" 2>/dev/null || true)

    while IFS= read -r project; do
      [ -n "$project" ] || continue

      # Remove command files that reference agmsg scripts
      if [ -d "$project/.claude/commands" ]; then
        for cmd_file in "$project/.claude/commands"/*.md; do
          [ -f "$cmd_file" ] || continue
          if grep -q "scripts/whoami.sh\|scripts/inbox.sh\|scripts/send.sh" "$cmd_file" 2>/dev/null; then
            cmd_name=$(basename "$cmd_file" .md)
            rm "$cmd_file"
            echo "  - removed /$cmd_name command from $project"
            REMOVED=true
          fi
        done
      fi

      # Remove only agmsg hook entries from settings files (preserve other hooks)
      for settings_file in "$project/.claude/settings.json" "$project/.claude/settings.local.json" "$project/.codex/hooks.json"; do
        if [ -f "$settings_file" ] && grep -F -q -e "$SKILL_DIR" -e "$SKILL_DIR/scripts" "$settings_file" 2>/dev/null; then
          before=$(mktemp "${TMPDIR:-/tmp}/agmsg-uninstall-before.XXXXXX")
          cp "$settings_file" "$before"
          for event in SessionStart SessionEnd Stop PostToolUse; do
            strip_agmsg_event_file "$SKILL_DIR" "$settings_file" "$event" || true
          done
          prune_empty_hooks_file "$settings_file" || true
          if ! cmp -s "$before" "$settings_file"; then
            echo "  - removed agmsg hook from $settings_file"
            REMOVED=true
          fi
          rm -f "$before"
        fi
      done

      # Remove project-scoped rule/hook files installed for other agents.
      for rule_file in "$project/.agent/rules/agmsg.md" "$project/.opencode/rules/agmsg.md" "$project/.github/hooks/agmsg.json"; do
        if [ -f "$rule_file" ] && grep -F -q -e "$SKILL_DIR" -e "$SKILL_DIR/scripts" -e "$(basename "$SKILL_DIR")" "$rule_file" 2>/dev/null; then
          rm "$rule_file"
          echo "  - removed agmsg project file $rule_file"
          REMOVED=true
        fi
      done
    done <<< "$projects"
  done
done

# --- 2. Remove Claude Code global command ---
for SKILL_DIR in "${SKILL_DIRS[@]}"; do
  SKILL_NAME="$(basename "$SKILL_DIR")"
  CC_CMD="$HOME/.claude/commands/$SKILL_NAME.md"
  if [ -f "$CC_CMD" ]; then
    rm "$CC_CMD"
    echo "  - removed /$SKILL_NAME from ~/.claude/commands/"
    REMOVED=true
  fi
done

# --- 2b. Remove Copilot CLI skill ---
for SKILL_DIR in "${SKILL_DIRS[@]}"; do
  SKILL_NAME="$(basename "$SKILL_DIR")"
  COPILOT_SKILL="$HOME/.copilot/skills/$SKILL_NAME"
  if [ -d "$COPILOT_SKILL" ]; then
    rm -rf "$COPILOT_SKILL"
    echo "  - removed /$SKILL_NAME skill from ~/.copilot/skills/"
    REMOVED=true
  fi
done

# --- 2c. Remove OpenCode skill ---
for SKILL_DIR in "${SKILL_DIRS[@]}"; do
  SKILL_NAME="$(basename "$SKILL_DIR")"
  OPENCODE_SKILL="$HOME/.config/opencode/skills/$SKILL_NAME"
  if [ -d "$OPENCODE_SKILL" ]; then
    rm -rf "$OPENCODE_SKILL"
    echo "  - removed /$SKILL_NAME skill from ~/.config/opencode/skills/"
    REMOVED=true
  fi
done

# --- 2d. Remove native Windows helpers ---
for SKILL_DIR in "${SKILL_DIRS[@]}"; do
  SKILL_NAME="$(basename "$SKILL_DIR")"
  for helper in "$AGENTS_DIR/$SKILL_NAME.ps1" "$AGENTS_DIR/$SKILL_NAME-run.sh"; do
    if [ -f "$helper" ]; then
      rm "$helper"
      echo "  - removed $helper"
      REMOVED=true
    fi
  done
done

CODEX_SHIM="$HOME/.agents/bin/codex"
if [ -f "$CODEX_SHIM" ] && grep -q "Optional Codex entrypoint shim for agmsg monitor mode" "$CODEX_SHIM" 2>/dev/null; then
  rm "$CODEX_SHIM"
  echo "  - removed agmsg Codex shim from $CODEX_SHIM"
  REMOVED=true
fi

SQLITE_SHIM="$AGENTS_DIR/bin/sqlite3"
REMOVED_SQLITE_SHIM=false
if [ -f "$SQLITE_SHIM" ] && grep -q "sqlite3 compatibility shim for agmsg" "$SQLITE_SHIM" 2>/dev/null; then
  rm "$SQLITE_SHIM"
  echo "  - removed $SQLITE_SHIM"
  REMOVED=true
  REMOVED_SQLITE_SHIM=true
fi

SQLITE_SHIM_CACHE="$AGENTS_DIR/run/sqlite3-shim.cache"
if [ "$REMOVED_SQLITE_SHIM" = true ] && [ -f "$SQLITE_SHIM_CACHE" ]; then
  rm "$SQLITE_SHIM_CACHE"
  echo "  - removed $SQLITE_SHIM_CACHE"
  REMOVED=true
fi

# --- 3. Remove skill directories ---
for SKILL_DIR in "${SKILL_DIRS[@]}"; do
  SKILL_NAME="$(basename "$SKILL_DIR")"
  if [ "$KEEP_DATA" = true ]; then
    echo ""
    echo "  Removing $SKILL_NAME skill (keeping DB and teams)..."
    rm -rf "$SKILL_DIR/scripts" "$SKILL_DIR/templates" "$SKILL_DIR/agents"
    rm -f "$SKILL_DIR/SKILL.md"
    echo "  - removed scripts, templates, SKILL.md"
    echo "  ~ preserved $SKILL_DIR/db/ and $SKILL_DIR/teams/"
    REMOVED=true
  else
    echo ""
    if confirm "Remove $SKILL_NAME (including DB and teams)?"; then
      rm -rf "$SKILL_DIR"
      echo "  - removed $SKILL_DIR"
      REMOVED=true
    fi
  fi
done

# --- 4. Clean up Codex writable_roots ---
CODEX_CONFIG="$HOME/.codex/config.toml"
if [ -f "$CODEX_CONFIG" ]; then
  needs_cleanup=false
  for SKILL_DIR in "${SKILL_DIRS[@]}"; do
    if grep -q "$SKILL_DIR" "$CODEX_CONFIG" 2>/dev/null; then
      needs_cleanup=true
      break
    fi
  done

  if [ "$needs_cleanup" = true ]; then
    cp "$CODEX_CONFIG" "$CODEX_CONFIG.bak"
    # Build pattern of skill dirs to remove
    skill_pattern=$(printf '|%s' "${SKILL_DIRS[@]}")
    skill_pattern="${skill_pattern:1}"  # remove leading |

    # Remove matching entries from writable_roots (handles multiline arrays)
    awk -v pattern="$skill_pattern" '
      /writable_roots/ { in_roots=1; buf="" }
      in_roots { buf = buf $0 "\n" }
      in_roots && /\]/ {
        # Remove entries matching skill dirs
        n = split(pattern, pats, "|")
        for (i = 1; i <= n; i++) {
          gsub("\"" pats[i] "[^\"]*\"[, ]*", "", buf)
        }
        # Clean up trailing/leading commas
        gsub(/,[ \t]*\]/, "]", buf)
        gsub(/\[[ \t]*,/, "[", buf)
        gsub(/,[ \t]*,/, ",", buf)
        # Check if empty
        if (buf ~ /writable_roots[^[]*\[\s*\]/) {
          in_roots=0; next
        }
        printf "%s", buf
        in_roots=0; next
      }
      !in_roots { print }
    ' "$CODEX_CONFIG" > "$CODEX_CONFIG.tmp" && mv "$CODEX_CONFIG.tmp" "$CODEX_CONFIG"
    # Remove empty [sandbox_workspace_write] section
    awk '
      /^\[sandbox_workspace_write\]/ {
        header=$0
        if (getline nextline <= 0) next
        if (nextline ~ /^\[/ || nextline == "") { print nextline; next }
        print header
        print nextline
        next
      }
      { print }
    ' "$CODEX_CONFIG" > "$CODEX_CONFIG.tmp" && mv "$CODEX_CONFIG.tmp" "$CODEX_CONFIG"
    echo "  - cleaned Codex writable_roots (backup: config.toml.bak)"
  fi
fi

# --- 5. Clean up empty ~/.agents/ ---
if [ -d "$AGENTS_DIR" ]; then
  rmdir "$AGENTS_DIR/bin" 2>/dev/null || true
  rmdir "$AGENTS_DIR/skills" 2>/dev/null || true
  rmdir "$AGENTS_DIR" 2>/dev/null || true
fi

# --- Done ---
echo ""
if [ "$REMOVED" = true ]; then
  echo "  ✓ Uninstall complete"
else
  echo "  Nothing removed."
fi
echo ""
