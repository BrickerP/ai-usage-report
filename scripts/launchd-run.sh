#!/usr/bin/env bash
# Bounded whole-pipeline retry wrapper for the LaunchAgent.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBLISH_SCRIPT="${AI_USAGE_PUBLISH_SCRIPT:-$ROOT/scripts/publish.sh}"
ATTEMPTS="${AI_USAGE_JOB_RETRY_ATTEMPTS:-3}"
DELAY_SECONDS="${AI_USAGE_JOB_RETRY_DELAY_SECONDS:-${AI_USAGE_RETRY_DELAY_SECONDS:-300}}"
LOCK_ROOT="${AI_USAGE_LOCK_ROOT:-${TMPDIR:-/tmp}}"

log() { printf '[%s] launchd-run: %s\n' "$(date '+%F %T')" "$*"; }

case "$ATTEMPTS" in
  ''|*[!0-9]*) log "ERROR: invalid retry attempts: $ATTEMPTS"; exit 2 ;;
esac
case "$DELAY_SECONDS" in
  ''|*[!0-9]*) log "ERROR: invalid retry delay: $DELAY_SECONDS"; exit 2 ;;
esac
if (( ATTEMPTS < 1 )); then
  log "ERROR: retry attempts must be at least 1"
  exit 2
fi

machine_key="$(printf '%s' "${AI_USAGE_MACHINE_ID:-default}" | tr -c 'A-Za-z0-9._-' '-')"
lock_path="${LOCK_ROOT%/}/ai-usage-report-${UID}-${machine_key}.lock"
if ! mkdir -p "$LOCK_ROOT"; then
  log "ERROR: could not create lock root: $LOCK_ROOT"
  exit 1
fi
if [[ ! -x /usr/bin/shlock ]]; then
  log "ERROR: /usr/bin/shlock is required on this macOS host"
  exit 2
fi

acquire_lock() {
  if /usr/bin/shlock -p "$$" -f "$lock_path"; then
    return 0
  fi
  local holder="unknown"
  if [[ -r "$lock_path" ]]; then
    holder="$(<"$lock_path")"
  fi
  if ! [[ "$holder" =~ ^[0-9]+$ ]] || ! kill -0 "$holder" 2>/dev/null; then
    # shlock deliberately protects a just-created lock for one timestamp tick.
    # Wait once, then let it atomically replace a confirmed dead/corrupt owner.
    sleep 1
    if /usr/bin/shlock -p "$$" -f "$lock_path"; then
      return 0
    fi
  fi
  holder="unknown"
  if [[ -r "$lock_path" ]]; then
    holder="$(<"$lock_path")"
  fi
  if [[ "$holder" =~ ^[0-9]+$ ]] && kill -0 "$holder" 2>/dev/null; then
    log "another publish is already running (pid=$holder); coalescing this trigger"
    return 1
  fi
  log "ERROR: could not acquire or validate publish lock: $lock_path"
  return 2
}

acquire_lock
lock_status=$?
if (( lock_status == 1 )); then
  exit 0
elif (( lock_status != 0 )); then
  exit 1
fi

cleanup() {
  if [[ -r "$lock_path" && "$(<"$lock_path")" == "$$" ]]; then
    rm -f "$lock_path"
  fi
}
child_pid=""
handle_signal() {
  local signal="$1" status="$2"
  if [[ "$child_pid" =~ ^[0-9]+$ ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -"$signal" "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'handle_signal INT 130' INT
trap 'handle_signal TERM 143' TERM
trap 'handle_signal HUP 129' HUP

attempt=1
last_status=1
while (( attempt <= ATTEMPTS )); do
  log "whole-job attempt ${attempt}/${ATTEMPTS}"
  bash "$PUBLISH_SCRIPT" &
  child_pid=$!
  if wait "$child_pid"; then
    child_pid=""
    log "publish completed"
    exit 0
  else
    last_status=$?
    child_pid=""
  fi
  if (( attempt < ATTEMPTS )); then
    log "publish failed (status=$last_status); retrying in ${DELAY_SECONDS}s"
    sleep "$DELAY_SECONDS"
  fi
  ((attempt += 1))
done

log "ERROR: publish failed after ${ATTEMPTS} attempts (status=$last_status)"
exit "$last_status"
