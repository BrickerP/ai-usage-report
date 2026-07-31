#!/bin/bash
# Collect One API usage data via chrome-use + saved session.
# Usage: bash scripts/oneapi_usage.sh [--since YYYY-MM-DD] [--until YYYY-MM-DD]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_PATH="${ONEAPI_STATE_PATH:-${HOME}/Library/Application Support/ai-usage-report/oneapi-chrome-state.json}"
SINCE=""
UNTIL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) SINCE="$2"; shift 2 ;;
    --until) UNTIL="$2"; shift 2 ;;
    *) echo "unknown: $1" >&2; exit 2 ;;
  esac
done

command -v chrome-use >/dev/null || { echo "chrome-use not found" >&2; exit 1; }
[[ -f "$STATE_PATH" ]] || { echo "State file not found: $STATE_PATH" >&2; exit 1; }

# Compute timestamps
START_TS=0
END_TS=2147483647
if [[ -n "$SINCE" ]]; then
  START_TS=$(python3 -c "import datetime; d=datetime.date.fromisoformat('$SINCE'); print(int(datetime.datetime.combine(d, datetime.time.min).timestamp()))")
fi
if [[ -n "$UNTIL" ]]; then
  END_TS=$(python3 -c "import datetime; d=datetime.date.fromisoformat('$UNTIL'); print(int(datetime.datetime.combine(d, datetime.time.max).timestamp()))")
fi

# Navigate to One API with the saved session.
chrome-use --state "$STATE_PATH" open "https://oneapi-comate.baidu-int.com/log" >/dev/null 2>&1

# Fetch all pages via stdin to avoid shell escaping issues
cat << JSEOF | chrome-use eval --stdin --timeout 180000 2>/dev/null
(async()=>{const B='https://oneapi-comate.baidu-int.com';const S=${START_TS};const E=${END_TS};const R=[];const V=new Set();for(let p=0;p<2000;p++){const u=B+'/api/log/self/?p='+p+'&type=0&model_name=&start_timestamp='+S+'&end_timestamp='+E;const r=await fetch(u,{credentials:'include'});const d=await r.json();const a=d.data||[];if(a.length===0)break;for(const c of a){const i=String(c.request_id||'');if(i&&V.has(i))continue;if(i)V.add(i);R.push(c)}if(a.length<20)break}return JSON.stringify(R)})()
JSEOF
