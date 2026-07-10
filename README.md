# AI usage report

Local collector + Astryx web UI for Codex / Claude Code / Cursor token spend.
Live site: https://brickerp.github.io/ai-usage-report/

## Quick start

```bash
git clone https://github.com/BrickerP/ai-usage-report.git
cd ai-usage-report
npm install

# Dev UI against the checked-in public/usage.json
npm run dev

# Refresh data from this machine (needs ccusage + Cursor session)
npm run collect

# Build static site into docs/ (GitHub Pages)
npm run build
npm run preview
```

## One-shot publish (data + build + push)

```bash
bash scripts/publish.sh
# or
npm run publish:pages
```

Flags:

- `--skip-collect` — rebuild UI only
- `--skip-push` — local build only

## Layout

| Path | Role |
|------|------|
| `src/` | Astryx + React report UI |
| `public/usage.json` | Daily series consumed by the UI |
| `scripts/ai_usage_comparison_image.py` | Collects Codex/Claude/Cursor usage |
| `scripts/local_ai_usage_records.py` | Local file helpers |
| `scripts/cursor_usage_api_probe.py` | Cursor Dashboard API client |
| `scripts/publish.sh` | Collect → build → commit `docs/` + `usage.json` |
| `docs/` | Built GitHub Pages output |

## Data sources

- Codex / Claude Code: `npx ccusage@latest … --json --offline`
- Cursor: authenticated Dashboard API via local Cursor session
- Costs: ccusage LiteLLM pricing for Codex/Claude; Cursor API for Cursor

## launchd

Point your LaunchAgent at this repo:

```bash
bash /path/to/ai-usage-report/scripts/publish.sh
```

Optional env:

- `AI_USAGE_TIMEZONE` (default `Asia/Shanghai`)
- `GH_PUBLISH_ACCOUNT` (default `BrickerP`)
