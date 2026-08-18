#!/usr/bin/env bash

: "${SKILL_DIR:?team-lock.sh requires SKILL_DIR}"

agmsg_hash_text() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    printf '%s' "$1" | openssl dgst -sha256 | awk '{print $NF}'
  else
    echo "sha256sum, shasum, or openssl is required to lock team configs." >&2
    return 127
  fi
}

agmsg_team_lock_acquire() {
  local team="$1"
  local lock_root="$SKILL_DIR/run/team-locks"
  local lock_id lock_dir locked
  lock_id="$(agmsg_hash_text "$team")" || return $?
  lock_dir="$lock_root/$lock_id.lock"

  mkdir -p "$lock_root"
  locked=false
  for _ in $(seq 1 100); do
    if mkdir "$lock_dir" 2>/dev/null; then
      locked=true
      break
    fi
    sleep 0.1
  done
  if [ "$locked" != true ]; then
    echo "Timed out waiting for team config lock: $team" >&2
    return 1
  fi
  printf '%s\n' "$lock_dir"
}

agmsg_team_lock_release() {
  local lock_dir="${1:-}"
  [ -n "$lock_dir" ] && rmdir "$lock_dir" 2>/dev/null || true
}
