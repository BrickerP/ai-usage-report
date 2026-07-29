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

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SKIP_COLLECT=0
SKIP_PUSH=0
BACKFILL_CODEX_CACHE=0
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

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
GH_PUBLISH_ACCOUNT="${GH_PUBLISH_ACCOUNT:-BrickerP}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
PUBLISH_BRANCH="${PUBLISH_BRANCH:-main}"
RETRY_ATTEMPTS="${AI_USAGE_RETRY_ATTEMPTS:-3}"
RETRY_DELAY_SECONDS="${AI_USAGE_RETRY_DELAY_SECONDS:-300}"
AI_USAGE_TIMEZONE="${AI_USAGE_TIMEZONE:-Asia/Shanghai}"
AI_USAGE_MACHINE_ID="${AI_USAGE_MACHINE_ID:-}"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

if (( BACKFILL_CODEX_CACHE == 1 && SKIP_COLLECT == 1 )); then
  die "--backfill-codex-cache cannot be combined with --skip-collect"
fi
if (( BACKFILL_CODEX_CACHE == 1 )) && [[ -z "$AI_USAGE_MACHINE_ID" ]]; then
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

abort_in_progress_git_ops() {
  if [ -d "$ROOT/.git/rebase-merge" ] || [ -d "$ROOT/.git/rebase-apply" ]; then
    log "aborting in-progress rebase"
    git rebase --abort || die "failed to abort in-progress rebase"
  fi
  if [ -f "$ROOT/.git/MERGE_HEAD" ]; then
    log "aborting in-progress merge"
    git merge --abort || die "failed to abort in-progress merge"
  fi
  if [ -d "$ROOT/.git/cherry-pick-head" ] || [ -f "$ROOT/.git/CHERRY_PICK_HEAD" ]; then
    log "aborting in-progress cherry-pick"
    git cherry-pick --abort || die "failed to abort in-progress cherry-pick"
  fi
}

ensure_on_publish_branch() {
  abort_in_progress_git_ops

  local current
  current="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ "$current" == "HEAD" || -z "$current" ]]; then
    log "detached HEAD detected; checking out ${PUBLISH_BRANCH}"
    git checkout "$PUBLISH_BRANCH" || die "could not checkout ${PUBLISH_BRANCH} from detached HEAD"
    current="$(git rev-parse --abbrev-ref HEAD)"
  fi

  if [[ "$current" != "$PUBLISH_BRANCH" ]]; then
    log "on branch ${current}; switching to ${PUBLISH_BRANCH}"
    git checkout "$PUBLISH_BRANCH" || die "could not checkout ${PUBLISH_BRANCH}"
  fi

  if ! git symbolic-ref -q HEAD >/dev/null; then
    die "still in detached HEAD after checkout ${PUBLISH_BRANCH}"
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
        case "$path" in
          public/usage.json|docs/*) ;;
          *)
            log "ERROR: refusing to auto-resolve unexpected conflict: $path"
            unexpected=1
            ;;
        esac
      done <<< "$conflicts"
      if (( unexpected == 1 )); then
        return 1
      fi

      while IFS= read -r path; do
        if git cat-file -e "${remote_tip}:${path}" 2>/dev/null; then
          git checkout "$remote_tip" -- "$path" || return 1
        else
          git rm -f -- "$path" || return 1
        fi
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
  ensure_on_publish_branch
  log "git fetch ${REMOTE_NAME} ${PUBLISH_BRANCH}"
  git fetch "$REMOTE_NAME" "$PUBLISH_BRANCH" || die "git fetch failed"

  local remote_tip
  remote_tip="$(git rev-parse "${REMOTE_NAME}/${PUBLISH_BRANCH}")" || \
    die "could not resolve ${REMOTE_NAME}/${PUBLISH_BRANCH}"

  local stashed=0
  if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    log "stashing local changes before pull --rebase"
    git stash push -u -m "publish.sh auto-stash $(date -u '+%Y-%m-%dT%H:%MZ')" || die "git stash failed"
    stashed=1
  fi

  log "git pull --rebase ${REMOTE_NAME} ${PUBLISH_BRANCH}"
  if ! git pull --rebase "$REMOTE_NAME" "$PUBLISH_BRANCH"; then
    if [ -d "$ROOT/.git/rebase-merge" ] || [ -d "$ROOT/.git/rebase-apply" ]; then
      log "rebase stopped; resolving generated usage/docs conflicts"
      if ! resolve_generated_rebase_conflicts "$remote_tip"; then
        log "ERROR: pull --rebase has a non-generated or unresolvable conflict"
        abort_in_progress_git_ops
        if (( stashed )); then
          git stash pop || log "WARN: stash pop failed after pull error"
        fi
        die "git pull --rebase ${REMOTE_NAME}/${PUBLISH_BRANCH} failed"
      fi
    else
      log "ERROR: pull --rebase failed before a resolvable rebase began"
      if (( stashed )); then
        git stash pop || log "WARN: stash pop failed after pull error"
      fi
      die "git pull --rebase ${REMOTE_NAME}/${PUBLISH_BRANCH} failed"
    fi
  fi

  if (( stashed )); then
    log "restoring stashed local changes"
    git stash pop || die "git stash pop conflict after pull --rebase; resolve manually"
  fi

  ensure_on_publish_branch
}

collect_local_usage() {
  local extra_args=()
  if [[ -n "$AI_USAGE_MACHINE_ID" ]]; then
    extra_args+=(--machine-id "$AI_USAGE_MACHINE_ID")
  fi
  python3 "$ROOT/scripts/ai_usage_comparison_image.py" \
    --machines-dir "$ROOT/public/machines" \
    --timezone "$AI_USAGE_TIMEZONE" \
    --collect-local-only \
    "${extra_args[@]}"
}

backfill_codex_cache() {
  log "backfilling frozen Codex cache fields for ${AI_USAGE_MACHINE_ID}"
  python3 "$ROOT/scripts/ai_usage_comparison_image.py" \
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
  if [[ -f "$HOME/Projects/ai-usage-report/scripts/oneapi_usage.py" ]]; then
    log "One API usage script found"
  fi
  if [[ ! -f "/tmp/oneapi-chrome-state.json" ]]; then
    log "WARN: One API chrome state not found at /tmp/oneapi-chrome-state.json"
    log "WARN: One API usage data will be skipped. To enable:"
    log "  chrome-use open https://oneapi-comate.baidu-int.com/log"
    log "  chrome-use state save /tmp/oneapi-chrome-state.json"
  fi
}

remerge_usage() {
  local extra_args=()
  if [[ -n "$AI_USAGE_MACHINE_ID" ]]; then
    extra_args+=(--machine-id "$AI_USAGE_MACHINE_ID")
  fi
  log "re-merging machines/*.json + Cursor API → usage.json"
  python3 "$ROOT/scripts/ai_usage_comparison_image.py" \
    --json-out "$ROOT/public/usage.json" \
    --machines-dir "$ROOT/public/machines" \
    --timezone "$AI_USAGE_TIMEZONE" \
    --merge-only \
    "${extra_args[@]}"
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
  git add public/usage.json public/machines docs package.json package-lock.json \
    src scripts vite.config.ts index.html README.md .gitignore 2>/dev/null || true
  git add -A
  if git diff --staged --quiet; then
    log "nothing to commit"
    return 1
  fi
  git -c user.email="${GH_PUBLISH_ACCOUNT}@users.noreply.github.com" \
    -c user.name="$GH_PUBLISH_ACCOUNT" \
    commit -m "$msg"
  return 0
}

push_branch() {
  local tok="$1"
  local git_extraheader="Authorization: Basic $(printf 'x-access-token:%s' "$tok" | base64)"
  # Always push the current commit to refs/heads/<branch> — never bare HEAD.
  git -c "http.https://github.com/.extraheader=$git_extraheader" \
    push "$REMOTE_NAME" "HEAD:refs/heads/${PUBLISH_BRANCH}"
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
      abort_in_progress_git_ops
      die "cache migration rebase failed without resolvable generated-file conflicts"
    fi

    while IFS= read -r path; do
      case "$path" in
        public/usage.json|docs/*) ;;
        *)
          log "ERROR: refusing to auto-resolve unexpected conflict: $path"
          unexpected=1
          ;;
      esac
    done <<< "$conflicts"
    if (( unexpected == 1 )); then
      abort_in_progress_git_ops
      die "cache migration touched a non-generated conflict; local commit remains recoverable via reflog"
    fi

    while IFS= read -r path; do
      if git cat-file -e "${remote_tip}:${path}" 2>/dev/null; then
        git checkout "$remote_tip" -- "$path" || {
          abort_in_progress_git_ops
          die "could not restore remote generated file during reconciliation: $path"
        }
      else
        git rm -f -- "$path" || {
          abort_in_progress_git_ops
          die "could not remove remote-absent generated file during reconciliation: $path"
        }
      fi
    done <<< "$conflicts"

    GIT_EDITOR=true git rebase --continue || {
      abort_in_progress_git_ops
      die "could not finish cache migration rebase after generated-file resolution"
    }
  fi

  # Discard every stale aggregate/build artifact from the local migration commit.
  # The machine-specific fragment remains, then the aggregate and site are regenerated
  # from the newly fetched set of fragments.
  git restore --source="$remote_tip" --staged --worktree -- public/usage.json docs || \
    die "could not restore remote aggregate/build artifacts"
  backfill_codex_cache
  build_site
  git add public/usage.json public/machines docs
  if ! git diff --staged --quiet; then
    git -c user.email="${GH_PUBLISH_ACCOUNT}@users.noreply.github.com" \
      -c user.name="$GH_PUBLISH_ACCOUNT" \
      commit --amend --no-edit
  fi
}

push_with_remmerge() {
  command -v gh >/dev/null || die "gh not found (needed to push)"
  if ! gh auth status 2>&1 | grep -q "account $GH_PUBLISH_ACCOUNT"; then
    die "gh not logged in as $GH_PUBLISH_ACCOUNT"
  fi

  local tok
  tok=$(gh auth token -u "$GH_PUBLISH_ACCOUNT") || die "could not read token"

  local attempt=1
  while (( attempt <= RETRY_ATTEMPTS )); do
    ensure_on_publish_branch
    log "attempt ${attempt}/${RETRY_ATTEMPTS}: git push ${REMOTE_NAME} HEAD:refs/heads/${PUBLISH_BRANCH}"
    if push_branch "$tok"; then
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
        remerge_usage
      fi
    fi
    build_site
    stage_and_commit "Refresh usage report merge-retry $(date -u '+%Y-%m-%dT%H:%MZ')" || true
    ((attempt += 1))
  done
  die "git push failed after re-merge retries"
}

command -v python3 >/dev/null || die "python3 not found"
command -v npm >/dev/null || die "npm not found"

# Capture local sources before touching the network.  A GitHub outage must not
# prevent this Mac from advancing its durable local high-water snapshot.
if (( BACKFILL_CODEX_CACHE == 1 )); then
  recover_codex_cache_transaction
fi
require_clean_backfill_worktree
ensure_on_publish_branch

if (( SKIP_COLLECT == 0 && BACKFILL_CODEX_CACHE == 0 )); then
  log "capturing local usage → public/machines/ (network-independent)"
  collect_local_usage

  log "refreshing One API chrome session state"
  require_oneapi_state
fi

# Pull after local capture; pull_latest safely stashes and restores the fragment.
pull_latest
require_backfill_at_remote_tip

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
  push_with_remmerge
else
  log "no local changes to push"
fi

ensure_on_publish_branch
log "=== publish OK ==="
log "Live: https://brickerp.github.io/ai-usage-report/"
