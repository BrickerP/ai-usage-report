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
2. Collect this Mac into **append-only** `public/machines/<your-id>.json`
   - **First run** on a machine: seed full local history once
   - **Later runs**: append missing days + refresh **today** only; **never rewrite past days** (survives local session cleanup)
3. Fetch Cursor Dashboard API (account-level; historical Cursor days already in `usage.json` stay frozen)
4. SUM local tools across all fragments → write `public/usage.json`
5. Build + commit + push
6. If push races the other Mac: pull → **re-merge** → rebuild → push again

Same launchd schedule on both Macs is fine; a push collision just triggers re-merge.

### Merge rules

| Tool | Rule |
|------|------|
| Codex / Claude Code / Comate | Per-machine **append-only** fragment; report = SUM by date across `machines/*.json` |
| Cursor | Account-level; refresh today / fill missing days; do not rewrite frozen historical Cursor days; do not SUM across Macs |
| Ducc | Claude wrapper → counted under Claude Code |

One-time migration: `public/machines/legacy-other-mac.json` holds pre-multi-mac Codex/Claude history from the old single-Mac `usage.json`. After the other Mac publishes its own `machines/<id>.json`, delete the legacy file and re-publish once.

`--force-reseed` rebuilds a machine fragment from scratch (breaks freeze — only use if you intentionally want to replace that Mac’s ledger).

## Flags

`publish.sh`:

- `--skip-collect` — rebuild UI only
- `--skip-push` — local build only

Collector (`npm run collect` / Python):

- `--machine-id` / `AI_USAGE_MACHINE_ID`
- `--machines-dir` (default `public/machines`)
- `--no-merge` — write this Mac’s fragment only
- `--merge-only` — re-merge existing fragments + Cursor API (used after push conflict)
- `--today` — print today’s table

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

Point your LaunchAgent at this repo:

```bash
export AI_USAGE_MACHINE_ID=mac-home
export AI_USAGE_TIMEZONE=Asia/Shanghai
bash /path/to/ai-usage-report/scripts/publish.sh
```

`publish.sh` always checks out `main` (override with `PUBLISH_BRANCH`), aborts leftover rebase/merge, then `git pull --rebase origin main` and `git push origin HEAD:refs/heads/main`. It never pushes bare `HEAD` (that previously left the clone in detached HEAD and blocked later publishes).

Optional env:

- `AI_USAGE_MACHINE_ID` (required for multi-Mac; default = hostname)
- `AI_USAGE_TIMEZONE` (default `Asia/Shanghai`)
- `GH_PUBLISH_ACCOUNT` (default `BrickerP`)
- `PUBLISH_BRANCH` (default `main`)
- `AI_USAGE_RETRY_ATTEMPTS` / `AI_USAGE_RETRY_DELAY_SECONDS`
