# AI usage report

Local collector + Astryx web UI for **Codex / Claude Code / Cursor / Comate** token spend.
Live site: https://brickerp.github.io/ai-usage-report/

Supports **multiple Macs**: each machine writes `public/machines/<id>.json`; publish merges them (SUM for local tools; Cursor from account API).

## Quick start (one Mac)

```bash
git clone https://github.com/BrickerP/ai-usage-report.git
cd ai-usage-report
npm install

# Dev UI against public/usage.json
npm run dev

# Refresh data from this machine (needs ccusage + Cursor session)
export AI_USAGE_MACHINE_ID=mac-home          # unique per Mac
export AI_USAGE_TIMEZONE=Asia/Shanghai
npm run collect

# Build static site into docs/ (GitHub Pages)
npm run build
npm run preview
```

## Two Macs (recommended)

On **each** Mac, set a different machine id (launchd / shell profile):

```bash
export AI_USAGE_MACHINE_ID=mac-home     # other Mac: mac-office
export AI_USAGE_TIMEZONE=Asia/Shanghai
```

Then either:

```bash
bash scripts/publish.sh
# or
npm run publish:pages
```

What happens:

1. `git pull --rebase` — pick up the other Mac’s `public/machines/*.json`
2. Collect this Mac into the durable ledger `public/machines/<your-id>.json`
   - **First run** on a machine: seed full local history once
   - **Later runs**: re-read `mutable_from` through today, then keep yesterday + today open
   - If the Mac was off for days, the old boundary remains and every missed day is recovered
   - A lower source snapshot is never allowed to overwrite the stored high-water value
3. Fetch Cursor Dashboard API (account-level; it has its own `cursor_mutable_from` boundary)
4. SUM local tools across all fragments → write `public/usage.json`
5. Build + commit + push
6. If push races the other Mac: pull → **re-merge** → rebuild → push again

Same launchd schedule on both Macs is fine; a push collision just triggers re-merge.

### Merge rules

| Tool | Rule |
|------|------|
| Codex / Claude Code / Comate | Per-machine durable fragment; dates before `mutable_from` are frozen, the open window is re-collected; report = SUM across `machines/*.json` |
| Cursor | Account-level; dates before `cursor_mutable_from` are frozen, the open window is refreshed; do not SUM across Macs |
| Ducc | Claude wrapper → counted under Claude Code |

One-time migration: `public/machines/legacy-other-mac.json` holds pre-multi-mac Codex/Claude history from the old single-Mac `usage.json`. After the other Mac publishes its own `machines/<id>.json`, delete the legacy file and re-publish once.

`--force-reseed` rebuilds a machine fragment from scratch (breaks freeze — only use if you intentionally want to replace that Mac’s ledger).

## Flags

`publish.sh`:

- `--skip-collect` — rebuild UI only
- `--skip-push` — local build only
- `--backfill-codex-cache` — one-time migration for this Mac's frozen Codex cache-read breakdown

Collector (`npm run collect` / Python):

- `--machine-id` / `AI_USAGE_MACHINE_ID`
- `--machines-dir` (default `public/machines`)
- `--no-merge` — write this Mac’s fragment only
- `--merge-only` — re-merge existing fragments + Cursor API (used after push conflict)
- `--collect-local-only` — atomically capture this Mac before any GitHub/build work
- `--today` — print today’s table
- `--backfill-codex-cache` — derive frozen cache read as `total - input - output`
- `--dry-run` — validate that migration without writing files

### One-time Codex cache backfill

Older fragments stored Codex totals correctly but wrote `codex_cache_read=0` because
the collector used the legacy `cachedInputTokens` field instead of ccusage's current
`cacheReadTokens`. The migration reconstructs the cache value from the same frozen
snapshot (`codex_tokens - codex_input - codex_output`). It changes no totals, costs,
dates, or Claude/Cursor/Comate fields and does not read mutable historical session logs.

Run it once on each Mac with that Mac's existing unique machine id. Publish Macs
strictly one at a time: do not start Mac B until Mac A reports a successful push.
The publish command also has a generated-file-only reconciliation path if two Macs
race accidentally; it preserves each machine fragment and rebuilds the shared aggregate.

```bash
export AI_USAGE_MACHINE_ID=mac-home
export AI_USAGE_TIMEZONE=Asia/Shanghai

# Validate first; does not write or publish.
python3 scripts/ai_usage_comparison_image.py \
  --json-out public/usage.json \
  --machines-dir public/machines \
  --machine-id "$AI_USAGE_MACHINE_ID" \
  --backfill-codex-cache \
  --dry-run

# Pull, migrate this machine fragment, rebuild, commit, and push.
bash scripts/publish.sh --backfill-codex-cache
```

The migration fails closed on missing components, duplicate dates, negative derived
cache, or an existing nonzero cache value that conflicts with the frozen snapshot.
Do not use `--force-reseed` for this migration.

## Layout

| Path | Role |
|------|------|
| `src/` | Astryx + React report UI |
| `public/usage.json` | Merged daily series for the UI |
| `public/machines/<id>.json` | Per-Mac Codex/Claude/Comate fragment |
| `scripts/ai_usage_comparison_image.py` | Collect + merge |
| `scripts/comate_usage.py` | Local Comate session parser |
| `scripts/machine_fragments.py` | Fragment I/O + SUM merge |
| `scripts/publish.sh` | Pull → collect → build → push (+ re-merge on conflict) |
| `docs/` | Built GitHub Pages output |

## Data sources

- Codex / Claude Code: `npx ccusage@latest … --json --offline`
- Cursor: authenticated Dashboard API via local Cursor session
- Comate: `~/.comate-engine/store/chat_session_*` (positive `contextUsed` deltas; cost always 0)
- Costs: ccusage LiteLLM pricing for Codex/Claude; Cursor API for Cursor

## launchd

Point your LaunchAgent at the bounded retry wrapper, not directly at
`publish.sh`:

```bash
export AI_USAGE_MACHINE_ID=mac-home
export AI_USAGE_TIMEZONE=Asia/Shanghai
bash /path/to/ai-usage-report/scripts/launchd-run.sh
```

Recommended LaunchAgent settings:

- `RunAtLoad = true` for login/reboot catch-up.
- `KeepAlive` absent/false; this is a finite batch job.
- Two `StartCalendarInterval` entries: shortly after midnight to close yesterday,
  and one daytime snapshot (for example `00:05` and `15:03`). Stagger the
  second Mac by several minutes to reduce avoidable push races.

`publish.sh` captures the machine-local fragment **before** Git fetch/pull, then
pulls, refreshes Cursor, merges, builds, and pushes. If GitHub or the build is
down, `launchd-run.sh` retries the complete pipeline without losing the local
snapshot. Every run refreshes at least today + yesterday, while an older persisted
boundary expands the recovery window across any missed days. Local source logs
must still be retained until at least one later successful reconciliation; no
collector can reconstruct records that were deleted before they were ever read.

Optional env:

- `AI_USAGE_MACHINE_ID` (required for multi-Mac; default = hostname)
- `AI_USAGE_TIMEZONE` (default `Asia/Shanghai`)
- `GH_PUBLISH_ACCOUNT` (default `BrickerP`)
- `PUBLISH_BRANCH` (default `main`)
- `AI_USAGE_RETRY_ATTEMPTS` / `AI_USAGE_RETRY_DELAY_SECONDS`
- `AI_USAGE_JOB_RETRY_ATTEMPTS` / `AI_USAGE_JOB_RETRY_DELAY_SECONDS`
