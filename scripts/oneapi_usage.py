#!/usr/bin/env python3
"""Collect LLM usage data from One API gateway (oneapi-comate.baidu-int.com).

Requires chrome-use + saved UUAP session state to authenticate.
Collects all log pages in a single browser session via chrome-use script.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ONEAPI_BASE = "https://oneapi-comate.baidu-int.com"
PAGE_SIZE = 20
DEFAULT_TZ = "Asia/Shanghai"


def resolve_tz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.timezone.utc


def safe_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def chrome_use_path() -> str:
    explicit = os.environ.get("CHROME_USE_BIN", "").strip()
    if explicit:
        return explicit
    found = shutil.which("chrome-use")
    if found:
        return found
    return "chrome-use"


FETCH_JS_TEMPLATE = r"""
(async () => {
    const BASE = '%(base)s';
    const PAGE_SIZE = %(page_size)d;
    const START_TS = %(start_ts)d;
    const END_TS = %(end_ts)d;
    const MAX_PAGES = 2000;

    // Verify session
    const userResp = await fetch(BASE + '/api/user/self', {credentials:'include'});
    const userData = await userResp.json();
    if (!userData.success) return JSON.stringify({_error:'not_authenticated'});

    const allRecords = [];
    const seenRequestIds = new Set();

    for (let page = 0; page < MAX_PAGES; page++) {
        const url = BASE + '/api/log/self/?p=' + page + '&type=0&model_name='
                    + '&start_timestamp=' + START_TS + '&end_timestamp=' + END_TS;
        const resp = await fetch(url, {credentials:'include'});
        const data = await resp.json();
        const records = data.data || [];
        if (records.length === 0) break;

        for (const rec of records) {
            const rid = String(rec.request_id || '');
            if (rid && seenRequestIds.has(rid)) continue;
            if (rid) seenRequestIds.add(rid);
            allRecords.push(rec);
        }
        if (records.length < PAGE_SIZE) break;
    }

    return JSON.stringify({_records: allRecords, _count: allRecords.length});
})()
"""


def collect_oneapi(
    timezone: str = DEFAULT_TZ,
    state_path: str = "/tmp/oneapi-chrome-state.json",
    since: str = "",
    until: str = "",
) -> dict[str, Any]:
    """Collect One API usage data."""
    tz = resolve_tz(timezone)
    now = dt.datetime.now(tz=tz)

    if until:
        end = dt.date.fromisoformat(until)
        end_dt = dt.datetime.combine(end, dt.time.max, tzinfo=tz)
    else:
        end_dt = now
    end_ts = int(end_dt.timestamp())

    if since:
        start = dt.date.fromisoformat(since)
        start_dt = dt.datetime.combine(start, dt.time.min, tzinfo=tz)
        start_ts = int(start_dt.timestamp())
    else:
        start_dt = now - dt.timedelta(days=5)
        start_ts = int(start_dt.timestamp())

    if not Path(state_path).exists():
        raise FileNotFoundError(
            f"Chrome state not found at {state_path}. "
            "Run: chrome-use open https://oneapi-comate.baidu-int.com/log && "
            "chrome-use state save /tmp/oneapi-chrome-state.json"
        )

    cu = chrome_use_path()

    # Close existing daemon, start fresh with saved state
    subprocess.run([cu, "close"], text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)

    proc = subprocess.run(
        [cu, "--state", state_path, "open", ONEAPI_BASE + "/log"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=60, check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip()[:300]
        raise RuntimeError(f"chrome-use open failed: {err}")

    js_code = FETCH_JS_TEMPLATE % {
        "base": ONEAPI_BASE,
        "page_size": PAGE_SIZE,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }

    proc2 = subprocess.run(
        [cu, "eval", "--stdin", "--timeout", "180000"],
        input=js_code, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=240, check=False,
    )

    if proc2.returncode != 0:
        err = proc2.stderr.strip()[:500]
        raise RuntimeError(f"chrome-use eval failed: {err}")

    raw = proc2.stdout.strip()
    # chrome-use may prepend stderr-like lines; take the last JSON line
    lines = [l for l in raw.split("\n") if l.strip().startswith("{") or l.strip().startswith('"')]
    json_str = lines[-1] if lines else raw
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        raise RuntimeError(f"chrome-use returned invalid JSON: {raw[:500]}")

    if isinstance(parsed, dict) and parsed.get("_error") == "not_authenticated":
        raise RuntimeError(
            "One API session expired. Re-login in Chrome, then:\n"
            "  chrome-use open https://oneapi-comate.baidu-int.com/log\n"
            "  chrome-use state save " + state_path
        )

    records = parsed.get("_records") if isinstance(parsed, dict) else parsed
    if not isinstance(records, list):
        raise RuntimeError(f"unexpected response shape from chrome-use")

    by_date_model: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )

    for rec in records:
        created_ts = safe_int(rec.get("created_at"))
        if not created_ts:
            continue
        dt_local = dt.datetime.fromtimestamp(created_ts, tz=tz)
        date_key = dt_local.strftime("%Y-%m-%d")
        model = str(rec.get("model_name") or "unknown")

        prompt = safe_int(rec.get("prompt_tokens"))
        completion = safe_int(rec.get("completion_tokens"))
        cache_read = safe_int(rec.get("cache_read_tokens"))
        cache_write = safe_int(rec.get("cache_write_tokens"))
        quota = safe_int(rec.get("quota"))

        acc = by_date_model[date_key][model]
        acc["input_tokens"] += prompt
        acc["output_tokens"] += completion
        acc["cache_read_tokens"] += cache_read
        acc["cache_write_tokens"] += cache_write
        acc["total_tokens"] += prompt + completion + cache_read + cache_write
        acc["count"] += 1
        acc["quota_total"] += quota

    daily_timeline: list[dict[str, Any]] = []
    total_input = total_output = total_cache_read = total_cache_write = 0
    total_tokens = total_quota = total_requests = 0

    for date_key in sorted(by_date_model):
        models = by_date_model[date_key]
        d_input = sum(m["input_tokens"] for m in models.values())
        d_output = sum(m["output_tokens"] for m in models.values())
        d_cache_read = sum(m["cache_read_tokens"] for m in models.values())
        d_cache_write = sum(m["cache_write_tokens"] for m in models.values())
        d_tokens = sum(m["total_tokens"] for m in models.values())
        d_quota = sum(m["quota_total"] for m in models.values())
        d_requests = sum(m["count"] for m in models.values())

        model_breakdowns = [
            {
                "model": mn,
                "input_tokens": mv["input_tokens"],
                "output_tokens": mv["output_tokens"],
                "cache_read_tokens": mv["cache_read_tokens"],
                "cache_write_tokens": mv["cache_write_tokens"],
                "total_tokens": mv["total_tokens"],
                "count": mv["count"],
                "quota": mv["quota_total"],
            }
            for mn, mv in sorted(models.items())
        ]

        daily_timeline.append({
            "date": date_key,
            "tokens": d_tokens,
            "input": d_input,
            "output": d_output,
            "cache_read": d_cache_read,
            "cache_write": d_cache_write,
            "requests": d_requests,
            "quota": d_quota,
            "model_breakdowns": model_breakdowns,
        })

        total_input += d_input
        total_output += d_output
        total_cache_read += d_cache_read
        total_cache_write += d_cache_write
        total_tokens += d_tokens
        total_quota += d_quota
        total_requests += d_requests

    first = daily_timeline[0]["date"] if daily_timeline else ""
    last = daily_timeline[-1]["date"] if daily_timeline else ""

    return {
        "available": True,
        "timezone": timezone,
        "request_count": len(records),
        "history": {"first": first, "last": last},
        "totals": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "total_tokens": total_tokens,
            "quota": total_quota,
            "requests": total_requests,
        },
        "daily_timeline": daily_timeline,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect One API usage data")
    parser.add_argument("--timezone", default=DEFAULT_TZ)
    parser.add_argument("--state-path", default="/tmp/oneapi-chrome-state.json")
    parser.add_argument("--since", default="", help="Start date YYYY-MM-DD")
    parser.add_argument("--until", default="", help="End date YYYY-MM-DD")
    args = parser.parse_args()

    try:
        result = collect_oneapi(
            timezone=args.timezone,
            state_path=args.state_path,
            since=args.since,
            until=args.until,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
