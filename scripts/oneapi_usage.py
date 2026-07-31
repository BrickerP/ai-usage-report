#!/usr/bin/env python3
"""Collect LLM usage data from One API gateway.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ONEAPI_BASE = "https://oneapi-comate.baidu-int.com"
PAGE_SIZE = 20
DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_STATE_PATH = str(
    Path.home()
    / "Library"
    / "Application Support"
    / "ai-usage-report"
    / "oneapi-chrome-state.json"
)
QUOTA_PER_CNY = 250_000
USD_PER_CNY = 0.14
ACCOUNTING_VERSION = 2
DEFAULT_DAYS = 5
OWNERSHIP_RULE = "exclude_gpt_codex_and_claude_model_families"
OWNERSHIP_RULE_VERSION = 1
ACCOUNT_SCOPE = {
    "kind": "account",
    "scope_id": "oneapi:self",
    "account_id": "self",
    "endpoint": "/api/log/self/",
    "merge_strategy": "latest_complete_snapshot",
}

CLAUDE_MODEL_RE = re.compile(
    r"(?:^|[/,.:\\])claude(?=[-_.]|$)",
    re.IGNORECASE,
)
CODEX_MODEL_RE = re.compile(
    r"(?:^|[/,.:\\])(?:gpt|chatgpt|codex)(?=[-_.]|$)|"
    r"(?:^|[/,.:\\])o\d+(?=[-_.]|$)",
    re.IGNORECASE,
)
OWNED_MODEL_TOKEN_RE = re.compile(
    r"(?:^|[/,.:\\])(?P<model>(?:claude|gpt|chatgpt|codex|o\d+)(?:[-_.].*)?)$",
    re.IGNORECASE,
)


def resolve_tz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.timezone.utc


def safe_int(v: Any) -> int:
    if isinstance(v, bool) or v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def quota_to_cny(quota: Any) -> float:
    return safe_int(quota) / QUOTA_PER_CNY


def quota_to_usd(quota: Any) -> float:
    return quota_to_cny(quota) * USD_PER_CNY


def canonical_model_name(model_name: Any) -> str:
    """Return a stable model label while removing ownership-provider prefixes."""
    raw = str(model_name or "").strip()
    if not raw:
        return ""
    normalized = re.sub(r"\s+", " ", raw.replace("\\", "/")).casefold()
    owned = OWNED_MODEL_TOKEN_RE.search(normalized)
    if owned:
        return owned.group("model")
    return normalized


def classify_model(model_name: Any) -> str:
    model = str(model_name or "").strip()
    if not model:
        return "unclassified"
    if CLAUDE_MODEL_RE.search(model):
        return "claude"
    if CODEX_MODEL_RE.search(model):
        return "codex"
    return "oneapi"


def window_calendar_days(start: str, end: str) -> int:
    try:
        first = dt.date.fromisoformat(start)
        last = dt.date.fromisoformat(end)
    except (TypeError, ValueError):
        return 0
    return max(0, (last - first).days + 1)


def snapshot_id_for_records(
    records: list[dict[str, Any]],
    *,
    timezone: str,
    window_start: str,
    window_end: str,
) -> str:
    """Hash stable accounting inputs; browser/session details never enter it."""
    normalized_records = []
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_model = str(record.get("model_name") or "").strip()
        normalized_records.append(
            {
                "request_id": str(
                    record.get("request_id") or record.get("id") or ""
                ),
                "created_at": safe_int(record.get("created_at")),
                "raw_model": raw_model,
                "canonical_model": canonical_model_name(raw_model),
                "owner": classify_model(raw_model),
                "input_tokens": safe_int(record.get("prompt_tokens")),
                "output_tokens": safe_int(record.get("completion_tokens")),
                "cache_read_tokens": safe_int(record.get("cache_read_tokens")),
                "cache_write_tokens": safe_int(
                    record.get("cache_write_tokens")
                ),
                "quota": safe_int(record.get("quota")),
            }
        )
    normalized_records.sort(
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    content = {
        "snapshot_schema": 1,
        "accounting_version": ACCOUNTING_VERSION,
        "ownership_rule_version": OWNERSHIP_RULE_VERSION,
        "scope": ACCOUNT_SCOPE,
        "timezone": timezone,
        "window": {"start": window_start, "end": window_end},
        "records": normalized_records,
    }
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def record_tokens(record: dict[str, Any]) -> int:
    return (
        safe_int(record.get("prompt_tokens"))
        + safe_int(record.get("completion_tokens"))
        + safe_int(record.get("cache_read_tokens"))
        + safe_int(record.get("cache_write_tokens"))
    )


def aggregate_records(
    records: list[dict[str, Any]],
    *,
    timezone: str = DEFAULT_TZ,
    window_start: str = "",
    window_end: str = "",
) -> dict[str, Any]:
    """Aggregate only One API traffic not owned by Codex or Claude Code."""
    tz = resolve_tz(timezone)
    by_date_model: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "total_tokens": 0,
                "count": 0,
                "quota_total": 0,
                "raw_models": set(),
            }
        )
    )
    excluded: dict[str, dict[str, int]] = {
        "codex": {"requests": 0, "tokens": 0, "quota": 0},
        "claude": {"requests": 0, "tokens": 0, "quota": 0},
    }
    unclassified = {"requests": 0, "tokens": 0, "quota": 0}
    raw_tokens = 0
    raw_quota = 0

    for record in records:
        if not isinstance(record, dict):
            continue
        tokens = record_tokens(record)
        quota = safe_int(record.get("quota"))
        raw_tokens += tokens
        raw_quota += quota
        owner = classify_model(record.get("model_name"))
        if owner in excluded:
            excluded[owner]["requests"] += 1
            excluded[owner]["tokens"] += tokens
            excluded[owner]["quota"] += quota
            continue
        if owner == "unclassified":
            unclassified["requests"] += 1
            unclassified["tokens"] += tokens
            unclassified["quota"] += quota
            continue

        created_at = safe_int(record.get("created_at"))
        if not created_at:
            unclassified["requests"] += 1
            unclassified["tokens"] += tokens
            unclassified["quota"] += quota
            continue
        date_key = dt.datetime.fromtimestamp(created_at, tz=tz).strftime("%Y-%m-%d")
        raw_model = str(record.get("model_name") or "").strip()
        canonical_model = canonical_model_name(raw_model)
        acc = by_date_model[date_key][canonical_model]
        acc["raw_models"].add(raw_model)
        acc["input_tokens"] += safe_int(record.get("prompt_tokens"))
        acc["output_tokens"] += safe_int(record.get("completion_tokens"))
        acc["cache_read_tokens"] += safe_int(record.get("cache_read_tokens"))
        acc["cache_write_tokens"] += safe_int(record.get("cache_write_tokens"))
        acc["total_tokens"] += tokens
        acc["count"] += 1
        acc["quota_total"] += quota

    daily: list[dict[str, Any]] = []
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    total_tokens = 0
    total_quota = 0
    total_requests = 0
    for date_key in sorted(by_date_model):
        models = by_date_model[date_key]
        day_input = sum(model["input_tokens"] for model in models.values())
        day_output = sum(model["output_tokens"] for model in models.values())
        day_cache_read = sum(
            model["cache_read_tokens"] for model in models.values()
        )
        day_cache_write = sum(
            model["cache_write_tokens"] for model in models.values()
        )
        day_tokens = sum(model["total_tokens"] for model in models.values())
        day_quota = sum(model["quota_total"] for model in models.values())
        day_requests = sum(model["count"] for model in models.values())
        daily.append(
            {
                "date": date_key,
                "tokens": day_tokens,
                "input": day_input,
                "output": day_output,
                "cache_read": day_cache_read,
                "cache_write": day_cache_write,
                "requests": day_requests,
                "quota": day_quota,
                "cost_cny": quota_to_cny(day_quota),
                "cost_usd": quota_to_usd(day_quota),
                "model_breakdowns": [
                    {
                        "model": model_name,
                        "canonical_model": model_name,
                        "raw_model": sorted(model_totals["raw_models"])[0],
                        "raw_models": sorted(model_totals["raw_models"]),
                        "ownership": "oneapi",
                        "ownership_rule_version": OWNERSHIP_RULE_VERSION,
                        "input_tokens": model_totals["input_tokens"],
                        "output_tokens": model_totals["output_tokens"],
                        "cache_read_tokens": model_totals[
                            "cache_read_tokens"
                        ],
                        "cache_write_tokens": model_totals[
                            "cache_write_tokens"
                        ],
                        "total_tokens": model_totals["total_tokens"],
                        "count": model_totals["count"],
                        "quota_total": model_totals["quota_total"],
                        "cost_usd": quota_to_usd(
                            model_totals["quota_total"]
                        ),
                    }
                    for model_name, model_totals in sorted(
                        models.items(),
                        key=lambda item: (
                            -item[1]["total_tokens"],
                            item[0],
                        ),
                    )
                ],
            }
        )
        total_input += day_input
        total_output += day_output
        total_cache_read += day_cache_read
        total_cache_write += day_cache_write
        total_tokens += day_tokens
        total_quota += day_quota
        total_requests += day_requests

    first = daily[0]["date"] if daily else ""
    last = daily[-1]["date"] if daily else ""
    calendar_days = window_calendar_days(window_start, window_end)
    window_complete = bool(window_start and window_end and calendar_days)
    return {
        "available": True,
        "complete": window_complete,
        "accounting_version": ACCOUNTING_VERSION,
        "captured_at": dt.datetime.now(tz=tz).isoformat(timespec="microseconds"),
        "snapshot_id": snapshot_id_for_records(
            records,
            timezone=timezone,
            window_start=window_start,
            window_end=window_end,
        ),
        "scope": dict(ACCOUNT_SCOPE),
        "ownership_rule": OWNERSHIP_RULE,
        "ownership_rule_version": OWNERSHIP_RULE_VERSION,
        "timezone": timezone,
        "request_count": len(records),
        "included_request_count": total_requests,
        "window": {
            "start": window_start,
            "end": window_end,
            "timezone": timezone,
            "calendar_days": calendar_days,
            "complete": window_complete,
        },
        "history": {"first": first, "last": last},
        "raw_totals": {
            "total_tokens": raw_tokens,
            "quota": raw_quota,
            "requests": len(records),
        },
        "excluded": excluded,
        "unclassified": unclassified,
        "totals": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "total_tokens": total_tokens,
            "quota": total_quota,
            "cost_cny": quota_to_cny(total_quota),
            "cost_usd": quota_to_usd(total_quota),
            "requests": total_requests,
        },
        "daily_timeline": daily,
    }


def chrome_use_path() -> str:
    explicit = os.environ.get("CHROME_USE_BIN", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"CHROME_USE_BIN is not executable: {path}")
        return str(path)

    path_candidate = shutil.which("chrome-use")
    if path_candidate:
        path = Path(path_candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    fallback = Path.home() / ".local" / "bin" / "chrome-use"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)

    raise RuntimeError(
        "chrome-use executable not found or not executable; set CHROME_USE_BIN, "
        "add chrome-use to PATH, or install it at "
        f"{fallback}"
    )


FETCH_BATCH_JS = r"""
(async () => {
  const BASE = '%(base)s';
  const START_TS = %(start_ts)d;
  const END_TS = %(end_ts)d;
  const PAGE_SIZE = %(page_size)d;
  const START_PAGE = %(start_page)d;
  const BATCH_PAGES = %(batch_pages)d;
  const CHECK_AUTH = %(check_auth)s;
  const MAX_ATTEMPTS = 3;
  const PAGE_DELAY_MS = 600;
  const records = [];
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function fetchPage(url, label) {
    let lastError = '';
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      try {
        const response = await fetch(url, {credentials: 'include'});
        if (response.status === 429) {
          return {_rate_limited: true};
        } else if (response.status >= 500) {
          lastError = 'http_' + response.status + '_' + label;
        } else if (!response.ok) {
          throw new Error('http_' + response.status + '_' + label);
        } else {
          const payload = await response.json();
          if (!payload.success) throw new Error('api_' + label);
          return payload;
        }
      } catch (error) {
        lastError = String(error && error.message ? error.message : error);
      }
      if (attempt + 1 < MAX_ATTEMPTS) {
        await delay(500 * Math.pow(2, attempt));
      }
    }
    throw new Error(lastError || 'fetch_failed_' + label);
  }

  if (CHECK_AUTH) {
    try {
      const response = await fetch(BASE + '/api/user/self', {
        credentials: 'include',
      });
      const payload = response.ok ? await response.json() : {};
      if (!response.ok || !payload.success) {
        throw new Error('http_' + response.status);
      }
    } catch (error) {
      return JSON.stringify({
        _complete: false,
        _e: 'not_authenticated:' + String(error.message || error),
        _next_page: START_PAGE,
        _pages: 0,
        _records: [],
      });
    }
  }

  let pages = 0;
  for (
    let page = START_PAGE;
    page < START_PAGE + BATCH_PAGES;
    page++
  ) {
    let payload;
    try {
      const url = BASE + '/api/log/self/?p=' + page
        + '&type=0&model_name=&start_timestamp=' + START_TS
        + '&end_timestamp=' + END_TS;
      payload = await fetchPage(url, 'page_' + page);
    } catch (error) {
      return JSON.stringify({
        _complete: false,
        _e: String(error.message || error),
        _next_page: page,
        _pages: pages,
        _records: records,
      });
    }
    if (payload._rate_limited) {
      return JSON.stringify({
        _complete: false,
        _rate_limited: true,
        _next_page: page,
        _pages: pages,
        _records: records,
      });
    }

    const pageRecords = Array.isArray(payload.data) ? payload.data : [];
    pages += 1;
    for (const record of pageRecords) {
      records.push(record);
    }
    if (pageRecords.length < PAGE_SIZE) {
      return JSON.stringify({
        _complete: true,
        _pages: pages,
        _next_page: page + 1,
        _records: records,
      });
    }
    await delay(PAGE_DELAY_MS);
  }

  return JSON.stringify({
    _complete: false,
    _batch_complete: true,
    _next_page: START_PAGE + BATCH_PAGES,
    _pages: pages,
    _records: records,
  });
})()
"""


def collect_oneapi(
    timezone: str = DEFAULT_TZ,
    state_path: str = DEFAULT_STATE_PATH,
    since: str = "",
    until: str = "",
    days: int = DEFAULT_DAYS,
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")
    tz = resolve_tz(timezone)
    now = dt.datetime.now(tz=tz)
    end_date = dt.date.fromisoformat(until) if until else now.date()
    start_date = (
        dt.date.fromisoformat(since)
        if since
        else end_date - dt.timedelta(days=days - 1)
    )
    if start_date > end_date:
        raise ValueError("since must not be after until")
    end_time = dt.time.max if until else now.time()
    end_ts = int(
        dt.datetime.combine(end_date, end_time, tzinfo=tz).timestamp()
    )
    start_ts = int(
        dt.datetime.combine(start_date, dt.time.min, tzinfo=tz).timestamp()
    )

    if not Path(state_path).exists():
        raise FileNotFoundError(f"Chrome state not found: {state_path}")

    cu = chrome_use_path()
    session_name = f"oneapi-usage-{os.getpid()}"
    open_proc = subprocess.run(
        [
            cu,
            "--session",
            session_name,
            "--state",
            state_path,
            "open",
            ONEAPI_BASE + "/api/user/self",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if open_proc.returncode != 0:
        raise RuntimeError(
            f"chrome-use open: {open_proc.stderr.strip()[:500]}"
        )

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates_removed = 0
    page = 0
    pages = 0
    rate_limit_count = 0
    max_pages = 2000
    batch_pages = 10
    try:
        while page < max_pages:
            js = FETCH_BATCH_JS % {
                "base": ONEAPI_BASE,
                "page_size": PAGE_SIZE,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "start_page": page,
                "batch_pages": batch_pages,
                "check_auth": "true" if page == 0 else "false",
            }
            proc = subprocess.run(
                [
                    cu,
                    "--session",
                    session_name,
                    "eval",
                    "--stdin",
                    "--timeout",
                    "120000",
                ],
                input=js,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=150,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"chrome-use eval: {proc.stderr.strip()[:500]}"
                )

            raw = proc.stdout.strip()
            if not raw:
                raise RuntimeError("no output from chrome-use")
            try:
                browser_result = json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(f"invalid JSON: {raw[:300]}")
            if isinstance(browser_result, str):
                browser_result = json.loads(browser_result)
            if not isinstance(browser_result, dict):
                raise RuntimeError(
                    "unexpected response type: "
                    f"{type(browser_result).__name__}"
                )

            batch_records = browser_result.get("_records")
            if not isinstance(batch_records, list):
                raise RuntimeError("One API batch response missing records")
            for record in batch_records:
                if not isinstance(record, dict):
                    continue
                request_id = str(
                    record.get("request_id") or record.get("id") or ""
                )
                if request_id and request_id in seen:
                    duplicates_removed += 1
                    continue
                if request_id:
                    seen.add(request_id)
                records.append(record)
            pages += safe_int(browser_result.get("_pages"))

            if browser_result.get("_complete"):
                break
            next_page = safe_int(browser_result.get("_next_page"))
            if next_page < page:
                raise RuntimeError(
                    f"One API incomplete fetch returned invalid page {next_page}"
                )
            if browser_result.get("_rate_limited"):
                rate_limit_count += 1
                if rate_limit_count > 20:
                    raise RuntimeError(
                        f"One API incomplete fetch at page {next_page}: "
                        "rate limit did not recover"
                    )
                page = next_page
                time.sleep(60)
                continue
            if browser_result.get("_batch_complete"):
                if next_page <= page:
                    raise RuntimeError(
                        f"One API incomplete fetch stalled at page {page}"
                    )
                page = next_page
                continue

            error_text = str(browser_result.get("_e") or "unknown error")
            raise RuntimeError(
                f"One API incomplete fetch at page {next_page} "
                f"after {len(records)} records: {error_text}"
            )
        else:
            raise RuntimeError(
                f"One API incomplete fetch exceeded {max_pages} pages"
            )
    finally:
        subprocess.run(
            [cu, "--session", session_name, "close"],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )

    result = aggregate_records(
        records,
        timezone=timezone,
        window_start=start_date.isoformat(),
        window_end=end_date.isoformat(),
    )
    result["pages"] = pages
    result["rate_limit_retries"] = rate_limit_count
    result["pagination"] = {
        "complete": True,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "records_after_deduplication": len(records),
        "duplicates_removed": duplicates_removed,
    }
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--timezone", default=DEFAULT_TZ)
    p.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    p.add_argument("--since", default="")
    p.add_argument("--until", default="")
    p.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=(
            "Lookback calendar days from today (default 5). "
            "Ignored when --since is set."
        ),
    )
    a = p.parse_args()
    try:
        r = collect_oneapi(a.timezone, a.state_path, a.since, a.until, days=a.days)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
