#!/usr/bin/env bash
# Verify leftover generated-file git conflicts cannot stall publish.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PUBLISH_SH="$REPO_ROOT/scripts/publish.sh"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'PASS: %s\n' "$*"; }

setup_git_identity() {
  git config user.name "ai-usage-test"
  git config user.email "ai-usage-test@example.com"
}

init_report_repo() {
  local dir="$1"
  mkdir -p "$dir/public/machines" "$dir/docs" "$dir/scripts"
  printf '{"v":1}\n' > "$dir/public/machines/mac.json"
  printf '{"generated_at":"2026-08-22T00:00:00+08:00"}\n' > "$dir/public/usage.json"
  printf '# docs\n' > "$dir/docs/index.html"
  printf 'base\n' > "$dir/scripts/keep.txt"
  git -C "$dir" init -q -b main
  git -C "$dir" config user.name "ai-usage-test"
  git -C "$dir" config user.email "ai-usage-test@example.com"
  git -C "$dir" add public docs scripts
  git -C "$dir" commit -qm "seed"
}

source_publish_lib() {
  export AI_USAGE_PUBLISH_SOURCE_ONLY=1
  # shellcheck source=../scripts/publish.sh
  source "$PUBLISH_SH"
  AI_USAGE_MACHINE_ID="mac"
  REMOTE_NAME="origin"
  PUBLISH_BRANCH="main"
}

TMP="$(mktemp -d "${TMPDIR:-/tmp}/ai-usage-git-recovery.XXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT

# --- leftover unmerged machine json keeps valid worktree ---
repo="$TMP/unmerged"
init_report_repo "$repo"
git -C "$repo" checkout -q -b other
printf '{"v":2}\n' > "$repo/public/machines/mac.json"
git -C "$repo" add public/machines/mac.json
git -C "$repo" commit -qm "other"
git -C "$repo" checkout -q main
printf '{"v":3}\n' > "$repo/public/machines/mac.json"
git -C "$repo" add public/machines/mac.json
git -C "$repo" commit -qm "main"
git -C "$repo" merge --no-commit other >/dev/null 2>&1 || true
printf '{"v":3,"kept":true}\n' > "$repo/public/machines/mac.json"
[[ -n "$(git -C "$repo" diff --name-only --diff-filter=U)" ]] || fail "expected unmerged machine json"

(
  cd "$repo"
  source_publish_lib
  ROOT="$repo"
  recover_leftover_generated_git_state
)
[[ -z "$(git -C "$repo" diff --name-only --diff-filter=U)" ]] || fail "unmerged machine json was not cleared"
python3 - "$repo/public/machines/mac.json" <<'PY' || fail "worktree machine json was not kept"
import json, sys
d=json.load(open(sys.argv[1]))
assert d=={"v":3,"kept":True}, d
PY
pass "leftover unmerged machine json is cleared and valid worktree is kept"

# --- leftover unmerged non-generated file is refused ---
repo="$TMP/unexpected"
init_report_repo "$repo"
git -C "$repo" checkout -q -b other
printf 'theirs\n' > "$repo/scripts/keep.txt"
git -C "$repo" add scripts/keep.txt
git -C "$repo" commit -qm "other"
git -C "$repo" checkout -q main
printf 'ours\n' > "$repo/scripts/keep.txt"
git -C "$repo" add scripts/keep.txt
git -C "$repo" commit -qm "main"
git -C "$repo" merge --no-commit other >/dev/null 2>&1 || true
[[ -n "$(git -C "$repo" diff --name-only --diff-filter=U)" ]] || fail "expected unmerged script"

if (
  cd "$repo"
  source_publish_lib
  ROOT="$repo"
  recover_leftover_generated_git_state
); then
  fail "recover should refuse non-generated unmerged paths"
fi
pass "leftover non-generated conflict is refused"

# --- leftover generated-only auto-stash is dropped ---
repo="$TMP/stash"
init_report_repo "$repo"
printf '{"v":9}\n' > "$repo/public/machines/mac.json"
git -C "$repo" stash push -u -m "publish.sh auto-stash 2026-08-22T16:06Z" -- public/machines/mac.json >/dev/null
[[ -n "$(git -C "$repo" stash list)" ]] || fail "expected auto-stash"
(
  cd "$repo"
  source_publish_lib
  ROOT="$repo"
  recover_leftover_generated_git_state
)
[[ -z "$(git -C "$repo" stash list)" ]] || fail "generated auto-stash was not dropped"
pass "leftover generated auto-stash is dropped"

# --- pull_latest restores local machine fragment across a remote update ---
origin="$TMP/origin.git"
git init -q --bare "$origin"
git -C "$origin" symbolic-ref HEAD refs/heads/main
repo="$TMP/pull"
init_report_repo "$repo"
git -C "$repo" remote add origin "$origin"
git -C "$repo" push -q origin main
# remote advances usage.json
work="$TMP/origin-work"
git clone -q -b main "$origin" "$work"
printf '{"generated_at":"2026-08-24T00:00:00+08:00","from":"remote"}\n' > "$work/public/usage.json"
git -C "$work" add public/usage.json
git -C "$work" -c user.name=ai-usage-test -c user.email=ai-usage-test@example.com commit -qm "remote usage"
git -C "$work" push -q origin main

printf '{"v":42,"local":true}\n' > "$repo/public/machines/mac.json"
git -C "$repo" fetch -q origin
(
  cd "$repo"
  source_publish_lib
  ROOT="$repo"
  AI_USAGE_MACHINE_ID="mac"
  PULL_RETRY_ATTEMPTS=1
  PULL_RETRY_DELAY_SECONDS=0
  pull_latest
)
python3 - "$repo/public/machines/mac.json" "$repo/public/usage.json" <<'PY' || fail "pull_latest did not restore fragment and usage"
import json, sys
machine=json.load(open(sys.argv[1]))
usage=json.load(open(sys.argv[2]))
assert machine=={"v":42,"local":True}, machine
assert usage["from"]=="remote", usage
PY
pass "pull_latest restores local machine fragment after remote pull"

printf 'All publish git recovery tests passed.\n'
