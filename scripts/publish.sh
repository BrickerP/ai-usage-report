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
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SKIP_COLLECT=0
SKIP_PUSH=0
for arg in "$@"; do
  case "$arg" in
    --skip-collect) SKIP_COLLECT=1 ;;
    --skip-push) SKIP_PUSH=1 ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
  esac
done

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
GH_PUBLISH_ACCOUNT="${GH_PUBLISH_ACCOUNT:-BrickerP}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
RETRY_ATTEMPTS="${AI_USAGE_RETRY_ATTEMPTS:-3}"
RETRY_DELAY_SECONDS="${AI_USAGE_RETRY_DELAY_SECONDS:-300}"
AI_USAGE_TIMEZONE="${AI_USAGE_TIMEZONE:-Asia/Shanghai}"
AI_USAGE_MACHINE_ID="${AI_USAGE_MACHINE_ID:-}"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

run_with_retry() {
  local label="$1"
  shift
  local attempt=1
  local status=0
  while (( attempt <= RETRY_ATTEMPTS )); do
    log "attempt ${attempt}/${RETRY_ATTEMPTS}: ${label}"
    if "$@"; then
      return 0
    fi
    status=$?
    log "WARN: ${label} failed (exit ${status})"
    if (( attempt < RETRY_ATTEMPTS )); then
      log "WARN: retrying ${label} in ${RETRY_DELAY_SECONDS}s"
      sleep "$RETRY_DELAY_SECONDS"
    fi
    ((attempt += 1))
  done
  return "$status"
}

pull_latest() {
  log "git pull --rebase (${REMOTE_NAME})"
  git pull --rebase "$REMOTE_NAME" HEAD || git pull --rebase "$REMOTE_NAME" || true
}

collect_usage() {
  local extra_args=()
  if [[ -n "$AI_USAGE_MACHINE_ID" ]]; then
    extra_args+=(--machine-id "$AI_USAGE_MACHINE_ID")
  fi
  python3 "$ROOT/scripts/ai_usage_comparison_image.py" \
    --json-out "$ROOT/public/usage.json" \
    --machines-dir "$ROOT/public/machines" \
    --timezone "$AI_USAGE_TIMEZONE" \
    "${extra_args[@]}"
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

push_with_remmerge() {
  command -v gh >/dev/null || die "gh not found (needed to push)"
  if ! gh auth status 2>&1 | grep -q "account $GH_PUBLISH_ACCOUNT"; then
    die "gh not logged in as $GH_PUBLISH_ACCOUNT"
  fi

  TOK=$(gh auth token -u "$GH_PUBLISH_ACCOUNT") || die "could not read token"
  GIT_EXTRAHEADER="Authorization: Basic $(printf 'x-access-token:%s' "$TOK" | base64)"

  local attempt=1
  while (( attempt <= RETRY_ATTEMPTS )); do
    log "attempt ${attempt}/${RETRY_ATTEMPTS}: git push"
    if git -c "http.https://github.com/.extraheader=$GIT_EXTRAHEADER" push "$REMOTE_NAME" HEAD; then
      log "pushed"
      return 0
    fi
    log "WARN: push rejected or failed; pull + re-merge + rebuild"
    pull_latest
    if (( SKIP_COLLECT == 0 )); then
      remerge_usage
    fi
    build_site
    stage_and_commit "Refresh usage report merge-retry $(date -u '+%Y-%m-%dT%H:%MZ')" || true
    ((attempt += 1))
  done
  die "git push failed after re-merge retries"
}

command -v python3 >/dev/null || die "python3 not found"
command -v npm >/dev/null || die "npm not found"

# Always pull first so other machines' fragments are present before merge.
pull_latest

if (( SKIP_COLLECT == 0 )); then
  log "collecting usage → public/machines/ + public/usage.json"
  collect_usage
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

if stage_and_commit "Refresh usage report $(date -u '+%Y-%m-%dT%H:%MZ')"; then
  push_with_remmerge
else
  log "no local changes to push"
fi

log "=== publish OK ==="
log "Live: https://brickerp.github.io/ai-usage-report/"
