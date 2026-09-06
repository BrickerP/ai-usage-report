#!/usr/bin/env bash
#
# Collect local AI usage → public/machines/<id>.json → merge public/usage.json
# → build Astryx site → publish to GitHub Pages (docs/).
#
# Usage (from repo root):
#   export AI_USAGE_MACHINE_ID=mac-home   # unique per Mac
#   bash scripts/publish.sh
#   bash scripts/publish.sh --skip-collect   # rebuild UI only
#   bash scripts/publish.sh --skip-push      # local build only
#   bash scripts/publish.sh --backfill-codex-cache  # one-time safe migration
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUBLISH_SOURCE_ONLY=0
if [[ "${AI_USAGE_PUBLISH_SOURCE_ONLY:-}" == "1" ]]; then
  PUBLISH_SOURCE_ONLY=1
else
  cd "$ROOT"
fi

SKIP_COLLECT=0
SKIP_PUSH=0
BACKFILL_CODEX_CACHE=0
if (( PUBLISH_SOURCE_ONLY == 0 )); then
for arg in "$@"; do
  case "$arg" in
    --skip-collect) SKIP_COLLECT=1 ;;
    --skip-push) SKIP_PUSH=1 ;;
    --backfill-codex-cache) BACKFILL_CODEX_CACHE=1 ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    *)
      printf 'ERROR: unknown option: %s\n' "$arg" >&2
      exit 2
      ;;
  esac
done
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
GH_PUBLISH_ACCOUNT="${GH_PUBLISH_ACCOUNT:-BrickerP}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
PUBLISH_BRANCH="${PUBLISH_BRANCH:-main}"
RETRY_ATTEMPTS="${AI_USAGE_RETRY_ATTEMPTS:-3}"
RETRY_DELAY_SECONDS="${AI_USAGE_RETRY_DELAY_SECONDS:-300}"
PULL_RETRY_ATTEMPTS="${AI_USAGE_PULL_RETRY_ATTEMPTS:-3}"
PULL_RETRY_DELAY_SECONDS="${AI_USAGE_PULL_RETRY_DELAY_SECONDS:-60}"
AI_USAGE_TIMEZONE="${AI_USAGE_TIMEZONE:-Asia/Shanghai}"
AI_USAGE_MACHINE_ID="${AI_USAGE_MACHINE_ID:-}"
ONEAPI_STATE_PATH="${ONEAPI_STATE_PATH:-${HOME}/Library/Application Support/ai-usage-report/oneapi-chrome-state.json}"
ONEAPI_STATUS_PATH="${ONEAPI_STATUS_PATH:-${HOME}/Library/Application Support/ai-usage-report/oneapi-status.json}"
export ONEAPI_STATE_PATH
export ONEAPI_STATUS_PATH

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

require_publish_auth() {
  local remote_url
  remote_url="$(git remote get-url --push "$REMOTE_NAME")" || \
    die "could not resolve Git remote $REMOTE_NAME"

  if ! printf 'url=%s\n\n' "$remote_url" |
    GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/false GCM_INTERACTIVE=Never \
      git credential fill >/dev/null 2>&1; then
    die "could not read a stored Git credential for $REMOTE_NAME"
  fi

  local attempt=1
  while (( attempt <= PULL_RETRY_ATTEMPTS )); do
    local probe_ref
    probe_ref="refs/heads/ai-usage-auth-probe/$(date -u '+%Y%m%dT%H%M%SZ')-$$-${attempt}"
    log "verifying Git push access with an isolated dry-run probe (attempt ${attempt})"
    if GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/false GCM_INTERACTIVE=Never \
      git push --dry-run --no-verify "$REMOTE_NAME" "HEAD:${probe_ref}" \
        >/dev/null 2>&1; then
      return 0
    fi
    if (( attempt < PULL_RETRY_ATTEMPTS )); then
      log "WARN: dry-run push probe failed; retrying in ${PULL_RETRY_DELAY_SECONDS}s"
      sleep "$PULL_RETRY_DELAY_SECONDS"
    fi
    ((attempt += 1))
  done
  die "Git credential dry-run push probe failed after ${PULL_RETRY_ATTEMPTS} attempts for $REMOTE_NAME"
}

if (( PUBLISH_SOURCE_ONLY == 0 && BACKFILL_CODEX_CACHE == 1 && SKIP_COLLECT == 1 )); then
  die "--backfill-codex-cache cannot be combined with --skip-collect"
fi
if (( PUBLISH_SOURCE_ONLY == 0 && BACKFILL_CODEX_CACHE == 1 )) && [[ -z "$AI_USAGE_MACHINE_ID" ]]; then
  die "AI_USAGE_MACHINE_ID is required for --backfill-codex-cache"
fi

require_clean_backfill_worktree() {
  if (( BACKFILL_CODEX_CACHE == 1 )) && [[ -n "$(git status --porcelain)" ]]; then
    die "--backfill-codex-cache requires a clean worktree; preserve or commit existing changes first"
  fi
}

require_backfill_at_remote_tip() {
  if (( BACKFILL_CODEX_CACHE != 1 )); then
    return 0
  fi
  local local_head remote_head
  local_head="$(git rev-parse HEAD)" || die "could not resolve local HEAD"
  remote_head="$(git rev-parse "${REMOTE_NAME}/${PUBLISH_BRANCH}")" || \
    die "could not resolve ${REMOTE_NAME}/${PUBLISH_BRANCH}"
  if [[ "$local_head" != "$remote_head" ]]; then
    die "--backfill-codex-cache requires HEAD to exactly match ${REMOTE_NAME}/${PUBLISH_BRANCH}; push or preserve unrelated local commits first"
  fi
}

is_generated_report_path() {
  case "$1" in
    public/usage.json|public/ai-usage-card-light.svg|public/ai-usage-card-dark.svg|docs/*|public/machines/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

list_unmerged_paths() {
  git diff --name-only --diff-filter=U
}

machine_fragment_path() {
  if [[ -z "${AI_USAGE_MACHINE_ID:-}" ]]; then
    return 1
  fi
  printf '%s\n' "$ROOT/public/machines/${AI_USAGE_MACHINE_ID}.json"
}

json_file_is_valid() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  python3 -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$path" >/dev/null 2>&1
}

unmerged_paths_are_generated_only() {
  local path unexpected=0
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    if ! is_generated_report_path "$path"; then
      log "ERROR: refusing to auto-resolve unexpected conflict: $path"
      unexpected=1
    fi
  done <<< "$1"
  return "$unexpected"
}

restore_unmerged_machine_json() {
  local path="$1" tmp
  if json_file_is_valid "$path"; then
    git reset -q HEAD -- "$path" || return 1
    return 0
  fi
  tmp="$(mktemp "${TMPDIR:-/tmp}/ai-usage-unmerged.XXXXXX")"
  if git show ":3:$path" >"$tmp" 2>/dev/null && json_file_is_valid "$tmp"; then
    mv -f -- "$tmp" "$path" || return 1
    git reset -q HEAD -- "$path" || return 1
    return 0
  fi
  if git show ":2:$path" >"$tmp" 2>/dev/null && json_file_is_valid "$tmp"; then
    mv -f -- "$tmp" "$path" || return 1
    git reset -q HEAD -- "$path" || return 1
    return 0
  fi
  rm -f -- "$tmp"
  return 1
}

clear_unmerged_generated_paths() {
  local path
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in
      public/machines/*.json)
        restore_unmerged_machine_json "$path" || return 1
        ;;
      *)
        git reset -q HEAD -- "$path" || return 1
        git checkout -q HEAD -- "$path" 2>/dev/null || rm -f -- "$path"
        ;;
    esac
  done <<< "$1"
}

drop_stale_generated_autostashes() {
  local i line entry files path keep
  local -a stash_refs=()
  while IFS= read -r line; do
    [[ "$line" == *'publish.sh auto-stash'* ]] || continue
    entry="${line%%:*}"
    [[ -n "$entry" ]] || continue
    stash_refs+=("$entry")
  done <<EOF
$(git stash list)
EOF
  (( ${#stash_refs[@]} > 0 )) || return 0

  for (( i=${#stash_refs[@]}-1; i>=0; i-- )); do
    entry="${stash_refs[$i]}"
    files="$(git stash show --name-only "$entry" 2>/dev/null || true)"
    [[ -n "$files" ]] || continue
    keep=0
    while IFS= read -r path; do
      [[ -n "$path" ]] || continue
      if ! is_generated_report_path "$path"; then
        keep=1
        break
      fi
    done <<< "$files"
    if (( keep == 0 )); then
      log "dropping leftover generated auto-stash ${entry}"
      git stash drop -q "$entry" || log "WARN: could not drop leftover auto-stash ${entry}"
    fi
  done
}

reset_generated_paths_to_head() {
  local path
  for path in public/usage.json public/ai-usage-card-light.svg public/ai-usage-card-dark.svg; do
    if git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
      git restore --source=HEAD --staged --worktree -- "$path" || return 1
    fi
  done
  if git ls-files -- "public/machines/" | grep -q .; then
    git restore --source=HEAD --staged --worktree -- public/machines || return 1
  fi
  if git ls-files -- "docs/" | grep -q .; then
    git restore --source=HEAD --staged --worktree -- docs || return 1
  fi
}

backup_local_machine_fragment() {
  local src dest
  src="$(machine_fragment_path)" || return 0
  [[ -f "$src" ]] || return 0
  dest="$(mktemp "${TMPDIR:-/tmp}/ai-usage-fragment.${AI_USAGE_MACHINE_ID}.XXXXXX")"
  cp "$src" "$dest" || die "could not backup local machine fragment"
  printf '%s\n' "$dest"
}

restore_local_machine_fragment() {
  local backup="$1" dest
  [[ -n "$backup" && -f "$backup" ]] || return 0
  dest="$(machine_fragment_path)" || {
    rm -f -- "$backup"
    return 0
  }
  if [[ -f "$dest" ]]; then
    # Pull may have brought a newer copy of this machine fragment from another
    # publisher. Restore only when the sidecar is valid and demonstrably newer.
    # A valid remote object without a timestamp is retained because its recency
    # cannot be proven; a malformed remote file is repaired from a valid sidecar.
    if python3 - "$backup" "$dest" <<'PY'
import datetime as dt
import json
import sys

def load(path):
    try:
        value = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return "invalid"
    return value if isinstance(value, dict) else None

def collected_at(value):
    raw = value.get("collected_at") if isinstance(value, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(dt.timezone.utc)

backup = load(sys.argv[1])
current = load(sys.argv[2])
backup_time = collected_at(backup)
current_time = collected_at(current)
should_restore = (
    isinstance(backup, dict)
    and (
        current == "invalid"
        or (
            backup_time is not None
            and isinstance(current, dict)
            and current_time is not None
            and backup_time > current_time
        )
    )
)
raise SystemExit(0 if should_restore else 1)
PY
    then
      mkdir -p "$(dirname "$dest")"
      cp "$backup" "$dest" || die "could not restore local machine fragment"
    fi
    rm -f -- "$backup"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  cp "$backup" "$dest" || die "could not restore local machine fragment"
  rm -f -- "$backup"
}

resolve_generated_stash_conflicts() {
  local conflicts path
  conflicts="$(list_unmerged_paths)"
  [[ -n "$conflicts" ]] || return 1
  if ! unmerged_paths_are_generated_only "$conflicts"; then
    return 1
  fi

  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in
      public/machines/*)
        git checkout --theirs -- "$path" || git rm -f -- "$path" || return 1
        ;;
      *)
        git checkout --ours -- "$path" || git rm -f -- "$path" || return 1
        ;;
    esac
    git add -- "$path" || return 1
  done <<< "$conflicts"

  if [[ -n "$(list_unmerged_paths)" ]]; then
    return 1
  fi
  if git stash list | head -1 | grep -q 'publish.sh auto-stash'; then
    git stash drop -q || log "WARN: resolved stash conflicts but could not drop auto-stash"
  fi
  return 0
}

snapshot_valid_machine_fragments() {
  local dest="$1" path rel
  mkdir -p "$dest"
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in
      public/machines/*.json)
        if json_file_is_valid "$ROOT/$path"; then
          rel="${path#public/machines/}"
          mkdir -p "$(dirname "$dest/$rel")"
          cp "$ROOT/$path" "$dest/$rel" || return 1
        fi
        ;;
    esac
  done <<EOF
$( { git diff --name-only --diff-filter=U; git ls-files -- "public/machines/"; } | sort -u )
EOF
}

restore_snapshotted_machine_fragments() {
  local src="$1" file rel dest
  [[ -d "$src" ]] || return 0
  while IFS= read -r file; do
    rel="${file#"$src"/}"
    dest="$ROOT/public/machines/$rel"
    mkdir -p "$(dirname "$dest")"
    cp "$file" "$dest" || return 1
    git reset -q HEAD -- "public/machines/$rel" >/dev/null 2>&1 || true
  done < <(find "$src" -type f -name '*.json' 2>/dev/null)
}

recover_leftover_generated_git_state() {
  local conflicts remote_tip snapshot

  snapshot="$(mktemp -d "${TMPDIR:-/tmp}/ai-usage-fragment-snap.XXXXXX")"
  snapshot_valid_machine_fragments "$snapshot" || true

  if [ -d "$ROOT/.git/rebase-merge" ] || [ -d "$ROOT/.git/rebase-apply" ]; then
    log "WARN: leftover rebase detected; attempting generated-file recovery"
    remote_tip="$(git rev-parse "${REMOTE_NAME}/${PUBLISH_BRANCH}" 2>/dev/null || git rev-parse HEAD)"
    if ! resolve_generated_rebase_conflicts "$remote_tip"; then
      log "WARN: aborting leftover rebase so publish can continue"
      abort_publish_rebase
    fi
  fi

  if [ -f "$ROOT/.git/MERGE_HEAD" ]; then
    conflicts="$(list_unmerged_paths)"
    if [[ -n "$conflicts" ]] && ! unmerged_paths_are_generated_only "$conflicts"; then
      rm -rf -- "$snapshot"
      die "refusing to auto-abort a merge with non-generated conflicts"
    fi
    log "WARN: aborting leftover merge of generated files"
    git merge --abort || {
      rm -rf -- "$snapshot"
      die "failed to abort leftover merge"
    }
  fi

  conflicts="$(list_unmerged_paths)"
  if [[ -n "$conflicts" ]]; then
    if ! unmerged_paths_are_generated_only "$conflicts"; then
      rm -rf -- "$snapshot"
      die "refusing to auto-reset non-generated unmerged paths"
    fi
    log "WARN: clearing leftover unmerged generated files"
    clear_unmerged_generated_paths "$conflicts" || {
      rm -rf -- "$snapshot"
      die "failed to clear leftover unmerged generated files"
    }
  fi

  restore_snapshotted_machine_fragments "$snapshot" || {
    rm -rf -- "$snapshot"
    die "failed to restore snapshotted machine fragments"
  }
  rm -rf -- "$snapshot"

  drop_stale_generated_autostashes
}

restore_autostash_after_pull() {
  if git stash pop; then
    return 0
  fi
  log "WARN: git stash pop conflicted; auto-resolving generated files"
  if resolve_generated_stash_conflicts; then
    return 0
  fi
  return 1
}


reject_in_progress_git_ops() {
  if [ -d "$ROOT/.git/rebase-merge" ] || [ -d "$ROOT/.git/rebase-apply" ]; then
    die "refusing to publish during an in-progress rebase"
  fi
  if [ -f "$ROOT/.git/MERGE_HEAD" ]; then
    die "refusing to publish during an in-progress merge"
  fi
  if [ -d "$ROOT/.git/cherry-pick-head" ] || [ -f "$ROOT/.git/CHERRY_PICK_HEAD" ]; then
    die "refusing to publish during an in-progress cherry-pick"
  fi
  if [[ -n "$(list_unmerged_paths)" ]]; then
    die "refusing to publish with unmerged paths"
  fi
}

abort_publish_rebase() {
  if [ -d "$ROOT/.git/rebase-merge" ] || [ -d "$ROOT/.git/rebase-apply" ]; then
    git rebase --abort || die "failed to abort publish-owned rebase"
  fi
}

ensure_on_publish_branch() {
  reject_in_progress_git_ops

  local current
  current="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ "$current" == "HEAD" || -z "$current" ]]; then
    die "refusing to publish from detached HEAD"
  fi

  if [[ "$current" != "$PUBLISH_BRANCH" ]]; then
    die "refusing to publish from branch ${current}; expected ${PUBLISH_BRANCH}"
  fi

  if ! git symbolic-ref -q HEAD >/dev/null; then
    die "refusing to publish without a symbolic branch"
  fi
}

resolve_generated_rebase_conflicts() {
  local remote_tip="$1"
  local rounds=0 conflicts path unexpected

  while [ -d "$ROOT/.git/rebase-merge" ] || [ -d "$ROOT/.git/rebase-apply" ]; do
    ((rounds += 1))
    if (( rounds > 20 )); then
      log "ERROR: exceeded generated-file rebase resolution limit"
      return 1
    fi

    conflicts="$(git diff --name-only --diff-filter=U)"
    if [[ -n "$conflicts" ]]; then
      unexpected=0
      while IFS= read -r path; do
        if ! is_generated_report_path "$path"; then
          log "ERROR: refusing to auto-resolve unexpected conflict: $path"
          unexpected=1
        fi
      done <<< "$conflicts"
      if (( unexpected == 1 )); then
        return 1
      fi

      while IFS= read -r path; do
        case "$path" in
          public/machines/*)
            # Machine fragments are authoritative per-machine source data
            # (appended from ~/.codex, monotonic, never shrunk). During a rebase
            # the replayed local commit is the fresher snapshot, so keep it.
            git checkout --theirs -- "$path" || git rm -f -- "$path" || return 1
            git add -- "$path" || return 1
            ;;
          *)
            if git cat-file -e "${remote_tip}:${path}" 2>/dev/null; then
              git checkout "$remote_tip" -- "$path" || return 1
            else
              git rm -f -- "$path" || return 1
            fi
            ;;
        esac
      done <<< "$conflicts"
    fi

    if git diff --staged --quiet; then
      git rebase --skip || return 1
    elif ! GIT_EDITOR=true git rebase --continue; then
      # A later replayed commit may expose another generated-only conflict.
      if [[ -z "$(git diff --name-only --diff-filter=U)" ]]; then
        return 1
      fi
    fi
  done
  return 0
}

pull_latest() {
  local attempt=1
  while (( attempt <= PULL_RETRY_ATTEMPTS )); do
    ensure_on_publish_branch
    log "git fetch ${REMOTE_NAME} ${PUBLISH_BRANCH} (attempt ${attempt})"
    if git fetch "$REMOTE_NAME" "$PUBLISH_BRANCH"; then
      break
    fi
    if (( attempt < PULL_RETRY_ATTEMPTS )); then
      log "WARN: git fetch failed; retrying in ${PULL_RETRY_DELAY_SECONDS}s"
      sleep "$PULL_RETRY_DELAY_SECONDS"
    else
      die "git fetch failed after ${PULL_RETRY_ATTEMPTS} attempts"
    fi
    ((attempt += 1))
  done

  local remote_tip
  remote_tip="$(git rev-parse "${REMOTE_NAME}/${PUBLISH_BRANCH}")" || \
    die "could not resolve ${REMOTE_NAME}/${PUBLISH_BRANCH}"

  local fragment_backup=""
  fragment_backup="$(backup_local_machine_fragment || true)"
  trap '[[ -n "${fragment_backup:-}" ]] && rm -f -- "$fragment_backup"' RETURN

  # Generated report artifacts are regenerated after pull. Reset them so a
  # local machine fragment cannot stall stash/rebase the way it did on 8/23.
  log "resetting generated report artifacts to HEAD before pull --rebase"
  reset_generated_paths_to_head || die "could not reset generated report artifacts before pull"

  local stashed=0
  if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    log "stashing remaining non-generated local changes before pull --rebase"
    git stash push -u -m "publish.sh auto-stash $(date -u '+%Y-%m-%dT%H:%MZ')" || die "git stash failed"
    stashed=1
  fi

  attempt=1
  while (( attempt <= PULL_RETRY_ATTEMPTS )); do
    log "git pull --rebase ${REMOTE_NAME} ${PUBLISH_BRANCH} (attempt ${attempt})"
    if git pull --rebase "$REMOTE_NAME" "$PUBLISH_BRANCH"; then
      break
    fi
    if [ -d "$ROOT/.git/rebase-merge" ] || [ -d "$ROOT/.git/rebase-apply" ]; then
      log "rebase stopped; resolving generated usage/docs conflicts"
      if resolve_generated_rebase_conflicts "$remote_tip"; then
        break
      fi
      log "ERROR: pull --rebase has a non-generated or unresolvable conflict"
      abort_publish_rebase
      if (( stashed )); then
        restore_autostash_after_pull || log "WARN: stash pop failed after pull error"
      fi
      restore_local_machine_fragment "$fragment_backup"
      fragment_backup=""
      die "git pull --rebase ${REMOTE_NAME}/${PUBLISH_BRANCH} failed"
    fi
    if (( attempt < PULL_RETRY_ATTEMPTS )); then
      log "WARN: git pull failed; retrying in ${PULL_RETRY_DELAY_SECONDS}s"
      sleep "$PULL_RETRY_DELAY_SECONDS"
    else
      log "ERROR: pull --rebase failed after ${PULL_RETRY_ATTEMPTS} attempts"
      if (( stashed )); then
        restore_autostash_after_pull || log "WARN: stash pop failed after pull error"
      fi
      restore_local_machine_fragment "$fragment_backup"
      fragment_backup=""
      die "git pull --rebase ${REMOTE_NAME}/${PUBLISH_BRANCH} failed"
    fi
    ((attempt += 1))
  done

  if (( stashed )); then
    log "restoring stashed local changes"
    restore_autostash_after_pull || die "git stash pop conflict after pull --rebase; resolve manually"
  fi

  restore_local_machine_fragment "$fragment_backup"
  fragment_backup=""
  ensure_on_publish_branch
}

collect_local_usage() {
  local extra_args=()
  if [[ -n "${AI_USAGE_MACHINE_ID:-}" ]]; then
    extra_args+=(--machine-id "$AI_USAGE_MACHINE_ID")
  fi
  python3 "$ROOT/scripts/usage_pipeline.py" \
    --machines-dir "$ROOT/public/machines" \
    --timezone "$AI_USAGE_TIMEZONE" \
    --collect-local-only \
    "${extra_args[@]:+${extra_args[@]}}"
}

backfill_codex_cache() {
  log "backfilling frozen Codex cache fields for ${AI_USAGE_MACHINE_ID}"
  python3 "$ROOT/scripts/usage_pipeline.py" \
    --json-out "$ROOT/public/usage.json" \
    --machines-dir "$ROOT/public/machines" \
    --timezone "$AI_USAGE_TIMEZONE" \
    --machine-id "$AI_USAGE_MACHINE_ID" \
    --backfill-codex-cache
}

recover_codex_cache_transaction() {
  log "checking for an interrupted Codex cache transaction before Git synchronization"
  python3 "$ROOT/scripts/machine_fragments.py" \
    --json-out "$ROOT/public/usage.json" \
    --machines-dir "$ROOT/public/machines" \
    --machine-id "$AI_USAGE_MACHINE_ID" \
    --recover-codex-cache-transaction
}

require_oneapi_state() {
  local state_path="$ONEAPI_STATE_PATH"
  if [[ -f "$HOME/Projects/ai-usage-report/scripts/oneapi_usage.py" ]]; then
    log "One API usage script found"
  fi
  if [[ ! -f "$state_path" ]]; then
    log "WARN: One API chrome state not found at $state_path"
    log "WARN: the prior One API series will be kept. To refresh the session:"
    log "  chrome-use open https://oneapi-comate.baidu-int.com/log"
    log "  chrome-use state save $state_path"
  fi
}

notify_oneapi_status() {
  [[ -s "$ONEAPI_STATUS_PATH" ]] || return 0

  local notification dedupe_key kind marker_path current_key title message temp_path
  if ! notification="$(python3 -c '
import json, sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
notice = payload.get("notification") if isinstance(payload, dict) else {}
session = payload.get("session") if isinstance(payload, dict) else {}
if not isinstance(notice, dict) or not notice.get("required"):
    raise SystemExit(0)
key = str(notice.get("dedupe_key") or "")
warning = str(session.get("warning") or "") if isinstance(session, dict) else ""
code = str(payload.get("error_code") or "") if isinstance(payload, dict) else ""
if not key:
    raise SystemExit(0)
if code:
    print(key + "\t" + code)
elif warning == "oneapi_auth_expiring":
    print(key + "\texpiry")
else:
    print(key + "\treauth")
' "$ONEAPI_STATUS_PATH")"; then
    log "WARN: could not read One API notification status"
    return 0
  fi
  [[ -n "$notification" ]] || return 0
  IFS=$'\t' read -r dedupe_key kind <<< "$notification"
  [[ -n "$dedupe_key" ]] || return 0

  marker_path="${ONEAPI_STATUS_PATH}.last-notification"
  current_key=""
  if [[ -r "$marker_path" ]]; then
    IFS= read -r current_key < "$marker_path" || current_key=""
  fi
  [[ "$current_key" != "$dedupe_key" ]] || return 0

  title="AI Usage · One API"
  case "$kind" in
    expiry)
      message="One API 应用会话将在 48 小时内到期；脚本会尝试静默续期，只有 UUAP 要求确认时才需介入。"
      ;;
    oneapi_browser_unavailable)
      message="One API 采集失败（浏览器不可用）；本次发布沿用旧快照，今天的用量可能不完整，请检查后手动重跑。"
      ;;
    oneapi_network_unavailable)
      message="One API 采集失败（网络不可用）；本次发布沿用旧快照，今天的用量可能不完整。"
      ;;
    oneapi_refresh_failed)
      message="One API 采集失败（数据刷新异常）；本次发布沿用旧快照，今天的用量可能不完整。"
      ;;
    *)
      message="One API 需要重新登录；历史用量已保留，本次发布不会清零。"
      ;;
  esac
  if /usr/bin/osascript \
      -e 'on run argv' \
      -e 'display notification (item 2 of argv) with title (item 1 of argv)' \
      -e 'end run' \
      "$title" "$message" >/dev/null 2>&1; then
    temp_path="${marker_path}.tmp.$$"
    if printf '%s\n' "$dedupe_key" > "$temp_path" \
        && chmod 600 "$temp_path" \
        && mv -f -- "$temp_path" "$marker_path"; then
      log "One API authentication notification sent"
    else
      rm -f -- "$temp_path"
      log "WARN: One API notification sent but dedupe marker could not be saved"
    fi
  else
    log "WARN: could not send One API macOS notification"
  fi
}

ONEAPI_CACHE_READY_PATH=""
ONEAPI_LIVE_ATTEMPTED=0
collect_oneapi_cache() {
  local state_path="$ONEAPI_STATE_PATH"
  local cache_path="${ONEAPI_CACHE_PATH:-/tmp/oneapi-cache.json}"
  local cache_dir temp_path
  ONEAPI_CACHE_READY_PATH=""
  if [[ ! -f "$state_path" ]]; then
    log "One API state not found; skipping One API account collection"
    ONEAPI_LIVE_ATTEMPTED=1
    python3 "$ROOT/scripts/oneapi_usage.py" \
      --state-path "$state_path" \
      --status-out "$ONEAPI_STATUS_PATH" \
      --days 5 >/dev/null 2>/dev/null || true
    notify_oneapi_status
    return 0
  fi
  cache_dir="$(dirname "$cache_path")"
  if ! mkdir -p "$cache_dir"; then
    log "WARN: could not create One API cache directory ${cache_dir}; merge will keep prior series"
    return 0
  fi
  if ! temp_path="$(mktemp "${cache_path}.tmp.XXXXXX")"; then
    log "WARN: could not create One API cache temp file; merge will keep prior series"
    return 0
  fi
  if ! chmod 600 "$temp_path"; then
    rm -f -- "$temp_path"
    log "WARN: could not secure One API cache temp file; merge will keep prior series"
    return 0
  fi
  log "collecting complete five-day One API account snapshot → ${cache_path}"
  ONEAPI_LIVE_ATTEMPTED=1
  if python3 "$ROOT/scripts/oneapi_usage.py" \
      --state-path "$state_path" \
      --status-out "$ONEAPI_STATUS_PATH" \
      --days 5 \
      > "$temp_path" \
      2> >(while IFS= read -r line; do log "oneapi: $line"; done >&2); then
    if ! mv -f -- "$temp_path" "$cache_path"; then
      rm -f -- "$temp_path"
      log "WARN: could not atomically install One API cache; merge will keep prior series"
      return 0
    fi
    ONEAPI_CACHE_READY_PATH="$cache_path"
    log "One API snapshot saved (${cache_path})"
  else
    local rc=$?
    rm -f -- "$temp_path"
    log "WARN: One API collection failed (exit ${rc}); merge will keep the prior published series"
  fi
  notify_oneapi_status
}

remerge_usage() {
  local extra_args=()
  if [[ -n "${AI_USAGE_MACHINE_ID:-}" ]]; then
    extra_args+=(--machine-id "$AI_USAGE_MACHINE_ID")
  fi
  if [[ -n "$ONEAPI_CACHE_READY_PATH" ]]; then
    extra_args+=(--oneapi-cache-path "$ONEAPI_CACHE_READY_PATH")
  fi
  if (( ONEAPI_LIVE_ATTEMPTED )); then
    extra_args+=(--skip-oneapi-live)
  fi
  log "re-merging machines/*.json + Cursor API → usage.json"
  python3 "$ROOT/scripts/usage_pipeline.py" \
    --json-out "$ROOT/public/usage.json" \
    --machines-dir "$ROOT/public/machines" \
    --timezone "$AI_USAGE_TIMEZONE" \
    --merge-only \
    "${extra_args[@]:+${extra_args[@]}}"
}

build_site() {
  if [ ! -d "$ROOT/node_modules" ]; then
    log "npm install"
    npm install
  fi
  log "building site → docs/"
  npm run build
}

stage_and_commit() {
  local msg="$1"
  ensure_on_publish_branch
  git add -A -- public/usage.json public/machines docs || \
    die "could not stage required report artifacts"
  local card
  for card in public/ai-usage-card-light.svg public/ai-usage-card-dark.svg; do
    if [[ -e "$card" ]] || git ls-files --error-unmatch -- "$card" >/dev/null 2>&1; then
      git add -A -- "$card" || die "could not stage README card: $card"
    fi
  done
  if git diff --staged --quiet; then
    log "nothing to commit"
    return 1
  fi
  git -c user.email="${GH_PUBLISH_ACCOUNT}@users.noreply.github.com" \
    -c user.name="$GH_PUBLISH_ACCOUNT" \
    commit -m "$msg" || die "could not commit report artifacts"
  return 0
}

has_unpublished_commits() {
  local ahead
  ahead="$(git rev-list --count "${REMOTE_NAME}/${PUBLISH_BRANCH}..HEAD")" || \
    die "could not compare HEAD with ${REMOTE_NAME}/${PUBLISH_BRANCH}"
  (( ahead > 0 ))
}

validate_unpublished_paths() {
  local upstream="${REMOTE_NAME}/${PUBLISH_BRANCH}"
  local commits commit path invalid=0

  commits="$(git rev-list "$upstream..HEAD")" || \
    die "could not inspect unpublished commits against $upstream"
  while IFS= read -r commit; do
    [[ -n "$commit" ]] || continue
    git diff-tree --root -m --no-commit-id --name-only --no-renames -r \
      "$commit" >/dev/null || \
      die "could not inspect unpublished commit $commit"
    while IFS= read -r -d '' path; do
      case "$path" in
        public/usage.json|public/machines/*|public/ai-usage-card-light.svg|public/ai-usage-card-dark.svg|docs/*) ;;
        *)
          log "ERROR: refusing to push unpublished non-report path: $path"
          invalid=1
          ;;
      esac
    done < <(
      git diff-tree --root -m --no-commit-id --name-only --no-renames -r -z \
        "$commit"
    )
  done <<< "$commits"

  if (( invalid )); then
    die "unpublished commits contain paths outside the report artifact allowlist"
  fi
}

push_branch() {
  validate_unpublished_paths
  # Always push the current commit to refs/heads/<branch> — never bare HEAD.
  GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/false GCM_INTERACTIVE=Never \
    git push "$REMOTE_NAME" "HEAD:refs/heads/${PUBLISH_BRANCH}"
}

reconcile_backfill_push() {
  log "fetching remote tip for safe cache-migration reconciliation"
  git fetch "$REMOTE_NAME" "$PUBLISH_BRANCH" || \
    die "push failed and remote fetch failed; leave the local migration commit untouched"

  local remote_tip
  remote_tip="$(git rev-parse "${REMOTE_NAME}/${PUBLISH_BRANCH}")" || \
    die "could not resolve ${REMOTE_NAME}/${PUBLISH_BRANCH}"

  if ! git rebase "$remote_tip"; then
    local conflicts path unexpected=0
    conflicts="$(git diff --name-only --diff-filter=U)"
    if [[ -z "$conflicts" ]]; then
      abort_publish_rebase
      die "cache migration rebase failed without resolvable generated-file conflicts"
    fi

    while IFS= read -r path; do
      case "$path" in
        public/usage.json|public/ai-usage-card-light.svg|public/ai-usage-card-dark.svg|docs/*) ;;
        *)
          log "ERROR: refusing to auto-resolve unexpected conflict: $path"
          unexpected=1
          ;;
      esac
    done <<< "$conflicts"
    if (( unexpected == 1 )); then
      abort_publish_rebase
      die "cache migration touched a non-generated conflict; local commit remains recoverable via reflog"
    fi

    while IFS= read -r path; do
      if git cat-file -e "${remote_tip}:${path}" 2>/dev/null; then
        git checkout "$remote_tip" -- "$path" || {
          abort_publish_rebase
          die "could not restore remote generated file during reconciliation: $path"
        }
      else
        git rm -f -- "$path" || {
          abort_publish_rebase
          die "could not remove remote-absent generated file during reconciliation: $path"
        }
      fi
    done <<< "$conflicts"

    GIT_EDITOR=true git rebase --continue || {
      abort_publish_rebase
      die "could not finish cache migration rebase after generated-file resolution"
    }
  fi

  # Discard every stale aggregate/build artifact from the local migration commit.
  # The machine-specific fragment remains, then the aggregate and site are regenerated
  # from the newly fetched set of fragments.
  git restore --source="$remote_tip" --staged --worktree -- \
    public/usage.json docs || \
    die "could not restore remote aggregate/build artifacts"
  local card
  for card in public/ai-usage-card-light.svg public/ai-usage-card-dark.svg; do
    if git cat-file -e "${remote_tip}:${card}" 2>/dev/null; then
      git restore --source="$remote_tip" --staged --worktree -- "$card" || \
        die "could not restore remote README card: $card"
    else
      git rm -f --ignore-unmatch -- "$card" >/dev/null || \
        die "could not clear remote-absent README card: $card"
      rm -f -- "$card"
    fi
  done
  backfill_codex_cache
  build_site
  git add public/usage.json public/machines docs || \
    die "could not stage reconciled report artifacts"
  for card in public/ai-usage-card-light.svg public/ai-usage-card-dark.svg; do
    if [[ -e "$card" ]] || git ls-files --error-unmatch -- "$card" >/dev/null 2>&1; then
      git add -A -- "$card" || die "could not stage reconciled README card: $card"
    fi
  done
  if ! git diff --staged --quiet; then
    git -c user.email="${GH_PUBLISH_ACCOUNT}@users.noreply.github.com" \
      -c user.name="$GH_PUBLISH_ACCOUNT" \
      commit --amend --no-edit
  fi
}

push_with_remmerge() {
  local attempt=1
  while (( attempt <= RETRY_ATTEMPTS )); do
    ensure_on_publish_branch
    log "attempt ${attempt}/${RETRY_ATTEMPTS}: git push ${REMOTE_NAME} HEAD:refs/heads/${PUBLISH_BRANCH}"
    if push_branch; then
      log "pushed"
      return 0
    fi
    if (( attempt < RETRY_ATTEMPTS )); then
      log "waiting ${RETRY_DELAY_SECONDS}s before push reconciliation retry"
      sleep "$RETRY_DELAY_SECONDS"
    fi
    if (( BACKFILL_CODEX_CACHE == 1 )); then
      log "WARN: cache migration push failed; preserving this Mac's fragment and regenerating shared artifacts"
      reconcile_backfill_push
      ((attempt += 1))
      continue
    fi
    log "WARN: push rejected or failed; pull + reconcile + rebuild"
    pull_latest
    if (( SKIP_COLLECT == 0 )); then
      if (( BACKFILL_CODEX_CACHE == 1 )); then
        backfill_codex_cache
      else
        # The retry pull may contain a newer account snapshot, so collect again.
        ONEAPI_CACHE_READY_PATH=""
        collect_oneapi_cache
        remerge_usage
      fi
    fi
    build_site
    stage_and_commit "Refresh usage report merge-retry $(date -u '+%Y-%m-%dT%H:%MZ')" || true
    ((attempt += 1))
  done
  die "git push failed after re-merge retries"
}

if (( PUBLISH_SOURCE_ONLY == 1 )); then
  return 0
fi

command -v python3 >/dev/null || die "python3 not found"
command -v npm >/dev/null || die "npm not found"

# Capture local sources before touching the network.  A GitHub outage must not
# prevent this Mac from advancing its durable local high-water snapshot.
recover_leftover_generated_git_state
if (( BACKFILL_CODEX_CACHE == 1 )); then
  recover_codex_cache_transaction
fi
require_clean_backfill_worktree
ensure_on_publish_branch
require_backfill_at_remote_tip

if (( SKIP_COLLECT == 0 && BACKFILL_CODEX_CACHE == 0 )); then
  log "capturing local usage → public/machines/ (network-independent)"
  collect_local_usage

  log "checking One API chrome session state"
  require_oneapi_state
fi

if (( SKIP_PUSH == 0 )); then
  # Preserve the network-independent local snapshot, then fail before any Git
  # synchronization, build, or commit if publication credentials are invalid.
  require_publish_auth
fi

# Pull after local capture. The local machine fragment is restored from a sidecar backup so generated-file stash/rebase conflicts cannot stall publish.
pull_latest
require_backfill_at_remote_tip

# Account-level snapshots are collected only after pull. This guarantees that
# an older pre-pull cache from one Mac cannot replace a newer remote snapshot.
if (( SKIP_COLLECT == 0 && BACKFILL_CODEX_CACHE == 0 )); then
  collect_oneapi_cache
fi

if (( SKIP_COLLECT == 0 )); then
  if (( BACKFILL_CODEX_CACHE == 1 )); then
    backfill_codex_cache
  else
    remerge_usage
  fi
else
  log "skip collect (--skip-collect)"
  [ -f "$ROOT/public/usage.json" ] || die "public/usage.json missing; run without --skip-collect"
fi

build_site

if (( SKIP_PUSH == 1 )); then
  log "skip push (--skip-push); open docs/index.html via: npm run preview"
  log "=== publish OK (local only) ==="
  exit 0
fi

commit_message="Refresh usage report $(date -u '+%Y-%m-%dT%H:%MZ')"
if (( BACKFILL_CODEX_CACHE == 1 )); then
  commit_message="Backfill Codex cache history $(date -u '+%Y-%m-%dT%H:%MZ')"
fi
if stage_and_commit "$commit_message"; then
  log "created a new report commit"
fi
if has_unpublished_commits; then
  push_with_remmerge
else
  log "no unpublished commits to push"
fi

ensure_on_publish_branch
log "=== publish OK ==="
log "Live: https://brickerp.github.io/ai-usage-report/"
