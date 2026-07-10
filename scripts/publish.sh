#!/usr/bin/env bash
#
# Collect local AI usage → public/usage.json → build Astryx site → publish to GitHub Pages (docs/).
#
# Usage (from repo root):
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
      sed -n '2,12p' "$0"
      exit 0
      ;;
  esac
done

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
GH_PUBLISH_ACCOUNT="${GH_PUBLISH_ACCOUNT:-BrickerP}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
RETRY_ATTEMPTS="${AI_USAGE_RETRY_ATTEMPTS:-3}"
RETRY_DELAY_SECONDS="${AI_USAGE_RETRY_DELAY_SECONDS:-300}"

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

command -v python3 >/dev/null || die "python3 not found"
command -v npm >/dev/null || die "npm not found"

if (( SKIP_COLLECT == 0 )); then
  log "collecting usage → public/usage.json"
  python3 "$ROOT/scripts/ai_usage_comparison_image.py" \
    --json-out "$ROOT/public/usage.json" \
    --timezone "${AI_USAGE_TIMEZONE:-Asia/Shanghai}"
else
  log "skip collect (--skip-collect)"
  [ -f "$ROOT/public/usage.json" ] || die "public/usage.json missing; run without --skip-collect"
fi

if [ ! -d "$ROOT/node_modules" ]; then
  log "npm install"
  npm install
fi

log "building site → docs/"
npm run build

if (( SKIP_PUSH == 1 )); then
  log "skip push (--skip-push); open docs/index.html via: npm run preview"
  log "=== publish OK (local only) ==="
  exit 0
fi

command -v gh >/dev/null || die "gh not found (needed to push)"
if ! gh auth status 2>&1 | grep -q "account $GH_PUBLISH_ACCOUNT"; then
  die "gh not logged in as $GH_PUBLISH_ACCOUNT"
fi

TOK=$(gh auth token -u "$GH_PUBLISH_ACCOUNT") || die "could not read token"
GIT_EXTRAHEADER="Authorization: Basic $(printf 'x-access-token:%s' "$TOK" | base64)"

git add public/usage.json docs package.json package-lock.json src scripts vite.config.ts index.html README.md .gitignore 2>/dev/null || true
git add -A

if git diff --staged --quiet; then
  log "nothing to commit"
else
  git -c user.email="${GH_PUBLISH_ACCOUNT}@users.noreply.github.com" \
    -c user.name="$GH_PUBLISH_ACCOUNT" \
    commit -m "Refresh usage report $(date -u '+%Y-%m-%dT%H:%MZ')"
  run_with_retry "git push" git -c "http.https://github.com/.extraheader=$GIT_EXTRAHEADER" push "$REMOTE_NAME" HEAD || \
    die "git push failed"
  log "pushed"
fi

log "=== publish OK ==="
log "Live: https://brickerp.github.io/ai-usage-report/"
