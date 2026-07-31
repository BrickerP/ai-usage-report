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
import tempfile
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
AUTH_EXPIRY_WARNING_HOURS = 48
STATUS_VERSION = 1
OWNERSHIP_RULE = "exclude_gpt_codex_and_claude_model_families"
OWNERSHIP_RULE_VERSION = 1
ACCOUNT_SCOPE = {
    "kind": "account",
    "scope_id": "oneapi:self",
    "account_id": "self",
    "endpoint": "/api/log/self/",
    "merge_strategy": "latest_complete_snapshot",
}


class OneApiCollectorError(RuntimeError):
    error_code = "oneapi_refresh_failed"
    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.metadata = dict(metadata or {})


class OneApiReauthRequired(OneApiCollectorError):
    error_code = "oneapi_reauth_required"
    exit_code = 20


class OneApiBrowserUnavailable(OneApiCollectorError):
    error_code = "oneapi_browser_unavailable"
    exit_code = 21


class OneApiNetworkUnavailable(OneApiCollectorError):
    error_code = "oneapi_network_unavailable"
    exit_code = 22


class OneApiStateUnavailable(OneApiCollectorError):
    error_code = "oneapi_state_unavailable"
    exit_code = 23


class OneApiRefreshFailed(OneApiCollectorError):
    error_code = "oneapi_refresh_failed"
    exit_code = 1

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


def _auth_cookie_component(cookie: dict[str, Any]) -> str:
    name = str(cookie.get("name") or "")
    domain = str(cookie.get("domain") or "").lower().lstrip(".")
    if name == "session" and domain == "oneapi-comate.baidu-int.com":
        return "oneapi_session"
    if (
        name == "SECURE_ZT_GW_TOKEN"
        and domain == "oneapi-comate.baidu-int.com"
    ):
        return "oneapi_gateway"
    if name in {"SECURE_UUAP_P_TOKEN", "UUAP_P_TOKEN"} and domain in {
        "baidu-int.com",
        "baidu.com",
    }:
        return "uuap_primary"
    if name == "UUAP_TRACE_TOKEN" and domain in {
        "baidu-int.com",
        "baidu.com",
    }:
        return "uuap_trace"
    if name in {"UUAPTGC", "UUAP-SESS-ID"} and domain == "uuap.baidu.com":
        return "uuap_session"
    if name == "USER_BIND_TOKEN" and domain == "uuap.baidu.com":
        return "uuap_binding"
    if name == "X-MFA-AUTH" and domain == "baidu-int.com":
        return "uuap_mfa"
    if name in {"SECURE_ZT_EXTRA_INFO", "ZT_EXTRA_INFO"} and domain in {
        "baidu-int.com",
        "baidu.com",
    }:
        return "zt_context"
    return ""


def _scoped_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cookies = payload.get("cookies")
    origins = payload.get("origins")
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise OneApiRefreshFailed(
            "saved browser state is missing cookies or origins"
        )
    scoped = {
        "cookies": [
            cookie
            for cookie in cookies
            if isinstance(cookie, dict) and _auth_cookie_component(cookie)
        ],
        "origins": [
            origin
            for origin in origins
            if isinstance(origin, dict) and origin.get("origin") == ONEAPI_BASE
        ],
    }
    if not any(
        _auth_cookie_component(cookie) in {"oneapi_session", "oneapi_gateway"}
        and isinstance(cookie.get("value"), str)
        and bool(cookie.get("value"))
        for cookie in scoped["cookies"]
    ):
        raise OneApiRefreshFailed(
            "saved browser state is missing One API authentication cookies"
        )
    return scoped


def auth_expiry_metadata(
    state_payload: dict[str, Any],
    *,
    now: dt.datetime | None = None,
    warning_hours: int = AUTH_EXPIRY_WARNING_HOURS,
) -> dict[str, Any]:
    observed_at = now or dt.datetime.now(tz=dt.timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=dt.timezone.utc)
    observed_at = observed_at.astimezone(dt.timezone.utc)
    threshold = observed_at + dt.timedelta(hours=warning_hours)
    expiries: dict[str, float] = {}
    cookies = state_payload.get("cookies")
    if not isinstance(cookies, list):
        cookies = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        component = _auth_cookie_component(cookie)
        if not component:
            continue
        raw_expiry = cookie.get("expires")
        if isinstance(raw_expiry, bool) or not isinstance(raw_expiry, (int, float)):
            continue
        expiry = float(raw_expiry)
        if expiry <= 0:
            continue
        current = expiries.get(component)
        if current is None or expiry < current:
            expiries[component] = expiry

    expiring_components = sorted(
        component
        for component, expiry in expiries.items()
        if dt.datetime.fromtimestamp(expiry, tz=dt.timezone.utc) <= threshold
    )
    earliest = ""
    if expiries:
        earliest = (
            dt.datetime.fromtimestamp(min(expiries.values()), tz=dt.timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    warning = "oneapi_auth_expiring" if expiring_components else ""
    return {
        "warning": warning,
        "warning_before_hours": warning_hours,
        "earliest_expiry_at": earliest,
        "expiring_components": expiring_components,
    }


def _read_scoped_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OneApiRefreshFailed(
            "saved browser state failed structural validation"
        ) from exc
    if not isinstance(payload, dict):
        raise OneApiRefreshFailed("saved browser state is not an object")
    return _scoped_state_payload(payload)


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def _scoped_launch_state(state_path: str) -> Path:
    source = Path(state_path).expanduser()
    scoped = _read_scoped_state(source)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{source.name}.launch.",
        suffix=".json",
        dir=str(source.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        _write_private_json(temporary, scoped)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _chrome_failure_type(stderr: str) -> type[OneApiCollectorError]:
    lowered = stderr.lower()
    browser_markers = (
        "failed to connect",
        "daemon",
        "socket",
        "no such file or directory",
        "connection refused",
    )
    network_markers = (
        "err_name_not_resolved",
        "err_connection_timed_out",
        "err_internet_disconnected",
        "dns",
        "network unreachable",
    )
    if any(marker in lowered for marker in browser_markers):
        return OneApiBrowserUnavailable
    if any(marker in lowered for marker in network_markers):
        return OneApiNetworkUnavailable
    return OneApiBrowserUnavailable


def _run_chrome_command(
    cu: str,
    session_name: str,
    args: list[str],
    *,
    phase: str,
    timeout: int,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            [cu, "--session", session_name, *args],
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OneApiBrowserUnavailable(
            f"chrome-use {phase} timed out"
        ) from exc
    except OSError as exc:
        raise OneApiBrowserUnavailable(
            f"chrome-use {phase} could not start"
        ) from exc
    if proc.returncode != 0:
        failure_type = _chrome_failure_type(proc.stderr or "")
        raise failure_type(f"chrome-use {phase} failed")
    return proc


def _decode_browser_object(raw: str, *, phase: str) -> dict[str, Any]:
    if not raw.strip():
        raise OneApiRefreshFailed(f"One API {phase} returned no result")
    try:
        value: Any = json.loads(raw)
        if isinstance(value, str):
            value = json.loads(value)
    except json.JSONDecodeError as exc:
        raise OneApiRefreshFailed(
            f"One API {phase} returned invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise OneApiRefreshFailed(
            f"One API {phase} returned an unexpected result"
        )
    return value


def _evaluate_browser_object(
    cu: str,
    session_name: str,
    js: str,
    *,
    phase: str,
    timeout_ms: int,
    process_timeout: int,
) -> dict[str, Any]:
    proc = _run_chrome_command(
        cu,
        session_name,
        ["eval", "--stdin", "--timeout", str(timeout_ms)],
        phase=phase,
        timeout=process_timeout,
        input_text=js,
    )
    return _decode_browser_object(proc.stdout, phase=phase)


def _raise_browser_error(error_code: str, message: str) -> None:
    error_types: dict[str, type[OneApiCollectorError]] = {
        OneApiReauthRequired.error_code: OneApiReauthRequired,
        OneApiBrowserUnavailable.error_code: OneApiBrowserUnavailable,
        OneApiNetworkUnavailable.error_code: OneApiNetworkUnavailable,
        OneApiRefreshFailed.error_code: OneApiRefreshFailed,
    }
    raise error_types.get(error_code, OneApiRefreshFailed)(message)


AUTH_CHECK_JS = r"""
(async () => {
  const BASE = '%(base)s';
  const expectedHost = new URL(BASE).hostname;
  if (window.location.hostname !== expectedHost) {
    return JSON.stringify({
      _authenticated: false,
      _error_code: 'oneapi_reauth_required',
    });
  }
  try {
    const response = await fetch(BASE + '/api/user/self', {
      credentials: 'include',
    });
    if (response.status === 401 || response.status === 403) {
      return JSON.stringify({
        _authenticated: false,
        _error_code: 'oneapi_reauth_required',
      });
    }
    if (response.status >= 500) {
      return JSON.stringify({
        _authenticated: false,
        _error_code: 'oneapi_network_unavailable',
      });
    }
    if (!response.ok) {
      return JSON.stringify({
        _authenticated: false,
        _error_code: 'oneapi_refresh_failed',
      });
    }
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      return JSON.stringify({
        _authenticated: false,
        _error_code: 'oneapi_reauth_required',
      });
    }
    if (!payload || payload.success !== true) {
      return JSON.stringify({
        _authenticated: false,
        _error_code: 'oneapi_reauth_required',
      });
    }
    return JSON.stringify({_authenticated: true, _error_code: ''});
  } catch (_error) {
    return JSON.stringify({
      _authenticated: false,
      _error_code: 'oneapi_network_unavailable',
    });
  }
})()
"""


def _check_authentication(cu: str, session_name: str) -> None:
    result = _evaluate_browser_object(
        cu,
        session_name,
        AUTH_CHECK_JS % {"base": ONEAPI_BASE},
        phase="authentication check",
        timeout_ms=30_000,
        process_timeout=45,
    )
    if result.get("_authenticated") is True:
        return
    _raise_browser_error(
        str(result.get("_error_code") or OneApiRefreshFailed.error_code),
        "One API authentication check failed",
    )


def save_session_state_atomic(
    cu: str,
    session_name: str,
    state_path: str,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    destination = Path(state_path).expanduser()
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    temporary = Path(temporary_name)
    os.fchmod(fd, 0o600)
    os.close(fd)
    try:
        _run_chrome_command(
            cu,
            session_name,
            ["state", "save", str(temporary)],
            phase="state save",
            timeout=60,
        )
        os.chmod(temporary, 0o600)
        saved_payload = _read_scoped_state(temporary)
        _write_private_json(temporary, saved_payload)
        saved_payload = _read_scoped_state(temporary)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        return {
            "state_refreshed": True,
            **auth_expiry_metadata(saved_payload, now=now),
        }
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


FETCH_BATCH_JS = r"""
(async () => {
  const BASE = '%(base)s';
  const START_TS = %(start_ts)d;
  const END_TS = %(end_ts)d;
  const PAGE_SIZE = %(page_size)d;
  const START_PAGE = %(start_page)d;
  const BATCH_PAGES = %(batch_pages)d;
  const MAX_ATTEMPTS = 3;
  const PAGE_DELAY_MS = 600;
  const records = [];
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function fetchPage(url, label) {
    let lastError = '';
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      try {
        const response = await fetch(url, {credentials: 'include'});
        if (response.status === 401 || response.status === 403) {
          return {_error_code: 'oneapi_reauth_required'};
        } else if (response.status === 429) {
          return {_rate_limited: true};
        } else if (response.status >= 500) {
          lastError = 'oneapi_network_unavailable';
        } else if (!response.ok) {
          return {_error_code: 'oneapi_refresh_failed'};
        } else {
          const payload = await response.json();
          if (!payload.success) {
            return {_error_code: 'oneapi_refresh_failed'};
          }
          return payload;
        }
      } catch (_error) {
        lastError = 'oneapi_network_unavailable';
      }
      if (attempt + 1 < MAX_ATTEMPTS) {
        await delay(500 * Math.pow(2, attempt));
      }
    }
    return {_error_code: lastError || 'oneapi_refresh_failed'};
  }

  let pages = 0;
  for (
    let page = START_PAGE;
    page < START_PAGE + BATCH_PAGES;
    page++
  ) {
    let payload;
    const url = BASE + '/api/log/self/?p=' + page
      + '&type=0&model_name=&start_timestamp=' + START_TS
      + '&end_timestamp=' + END_TS;
    payload = await fetchPage(url, 'page_' + page);
    if (payload._error_code) {
      return JSON.stringify({
        _complete: false,
        _error_code: payload._error_code,
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

    resolved_state_path = str(Path(state_path).expanduser())
    if not Path(resolved_state_path).exists():
        raise OneApiStateUnavailable("One API browser state is unavailable")
    try:
        cu = chrome_use_path()
    except RuntimeError as exc:
        raise OneApiBrowserUnavailable(
            "chrome-use executable is unavailable"
        ) from exc
    launch_state_path = _scoped_launch_state(resolved_state_path)
    session_name = f"oneapi-usage-{os.getpid()}"
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates_removed = 0
    page = 0
    pages = 0
    rate_limit_count = 0
    max_pages = 2000
    batch_pages = 10
    opened = False
    silent_sso_attempted = False
    session_health: dict[str, Any] = {}
    try:
        _run_chrome_command(
            cu,
            session_name,
            [
                "--launch",
                "--state",
                str(launch_state_path),
                "open",
                ONEAPI_BASE + "/api/user/self",
            ],
            phase="open",
            timeout=60,
        )
        opened = True
        try:
            _check_authentication(cu, session_name)
        except OneApiReauthRequired:
            silent_sso_attempted = True
            _run_chrome_command(
                cu,
                session_name,
                ["open", ONEAPI_BASE + "/log"],
                phase="silent SSO",
                timeout=90,
            )
            time.sleep(2)
            _run_chrome_command(
                cu,
                session_name,
                ["open", ONEAPI_BASE + "/api/user/self"],
                phase="silent SSO verification",
                timeout=60,
            )
            try:
                _check_authentication(cu, session_name)
            except OneApiReauthRequired as exc:
                exc.metadata["silent_sso_attempted"] = True
                raise

        session_health = save_session_state_atomic(
            cu,
            session_name,
            resolved_state_path,
            now=now,
        )
        session_health["silent_sso_attempted"] = silent_sso_attempted

        while page < max_pages:
            js = FETCH_BATCH_JS % {
                "base": ONEAPI_BASE,
                "page_size": PAGE_SIZE,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "start_page": page,
                "batch_pages": batch_pages,
            }
            browser_result = _evaluate_browser_object(
                cu,
                session_name,
                js,
                phase="usage fetch",
                timeout_ms=120_000,
                process_timeout=150,
            )
            error_code = str(browser_result.get("_error_code") or "")
            if error_code:
                _raise_browser_error(
                    error_code,
                    "One API usage fetch failed",
                )

            batch_records = browser_result.get("_records")
            if not isinstance(batch_records, list):
                raise OneApiRefreshFailed(
                    "One API batch response is missing records"
                )
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
                raise OneApiRefreshFailed(
                    f"One API incomplete fetch returned invalid page {next_page}"
                )
            if browser_result.get("_rate_limited"):
                rate_limit_count += 1
                if rate_limit_count > 20:
                    raise OneApiRefreshFailed(
                        f"One API incomplete fetch at page {next_page}: "
                        "rate limit did not recover"
                    )
                page = next_page
                time.sleep(60)
                continue
            if browser_result.get("_batch_complete"):
                if next_page <= page:
                    raise OneApiRefreshFailed(
                        f"One API incomplete fetch stalled at page {page}"
                    )
                page = next_page
                continue

            raise OneApiRefreshFailed(
                f"One API incomplete fetch at page {next_page} "
                f"after {len(records)} records"
            )
        else:
            raise OneApiRefreshFailed(
                f"One API incomplete fetch exceeded {max_pages} pages"
            )
    except OneApiCollectorError as exc:
        exc.metadata.setdefault("silent_sso_attempted", silent_sso_attempted)
        if session_health:
            exc.metadata.setdefault(
                "state_refreshed",
                bool(session_health.get("state_refreshed")),
            )
            if session_health.get("warning"):
                exc.metadata.setdefault("warning", session_health["warning"])
        raise
    finally:
        launch_state_path.unlink(missing_ok=True)
        if opened:
            try:
                subprocess.run(
                    [cu, "--session", session_name, "close"],
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                print(
                    "warning: chrome-use close did not complete",
                    file=sys.stderr,
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
    result["session_health"] = session_health
    return result


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _notification_metadata(
    code: str,
    *,
    today: str,
    required: bool,
) -> dict[str, Any]:
    return {
        "required": required,
        "dedupe_key": f"{code}:{today}" if required and code else "",
    }


def successful_status_metadata(
    result: dict[str, Any],
    *,
    timezone: str,
) -> dict[str, Any]:
    observed = dt.datetime.now(tz=resolve_tz(timezone))
    health = (
        result.get("session_health")
        if isinstance(result.get("session_health"), dict)
        else {}
    )
    warning = str(health.get("warning") or "")
    return {
        "version": STATUS_VERSION,
        "observed_at": observed.isoformat(timespec="seconds"),
        "status": "warning" if warning else "fresh",
        "error_code": "",
        "session": {
            "state_refreshed": bool(health.get("state_refreshed")),
            "silent_sso_attempted": bool(health.get("silent_sso_attempted")),
            "warning": warning,
            "warning_before_hours": safe_int(
                health.get("warning_before_hours")
            ),
            "earliest_expiry_at": str(
                health.get("earliest_expiry_at") or ""
            ),
            "expiring_components": list(
                health.get("expiring_components")
                if isinstance(health.get("expiring_components"), list)
                else []
            ),
        },
        "notification": _notification_metadata(
            warning,
            today=observed.date().isoformat(),
            required=bool(warning),
        ),
    }


def failed_status_metadata(
    error: OneApiCollectorError,
    *,
    timezone: str,
) -> dict[str, Any]:
    observed = dt.datetime.now(tz=resolve_tz(timezone))
    requires_reauth = error.error_code in {
        OneApiReauthRequired.error_code,
        OneApiStateUnavailable.error_code,
    }
    warning = str(error.metadata.get("warning") or "")
    return {
        "version": STATUS_VERSION,
        "observed_at": observed.isoformat(timespec="seconds"),
        "status": "reauth_required" if requires_reauth else "failed",
        "error_code": error.error_code,
        "session": {
            "state_refreshed": bool(error.metadata.get("state_refreshed")),
            "silent_sso_attempted": bool(
                error.metadata.get("silent_sso_attempted")
            ),
            "warning": warning,
        },
        "notification": _notification_metadata(
            error.error_code,
            today=observed.date().isoformat(),
            required=requires_reauth,
        ),
    }


def _write_status_best_effort(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    try:
        _atomic_write_json(path, payload)
    except OSError:
        print(
            "WARN: could not write One API status metadata",
            file=sys.stderr,
        )


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
    p.add_argument(
        "--status-out",
        default="",
        help="Optional safe status JSON path for a rate-limited outer notifier.",
    )
    a = p.parse_args()
    try:
        r = collect_oneapi(a.timezone, a.state_path, a.since, a.until, days=a.days)
        _write_status_best_effort(
            a.status_out,
            successful_status_metadata(r, timezone=a.timezone),
        )
        print(json.dumps(r, ensure_ascii=False, indent=2))
    except OneApiCollectorError as e:
        _write_status_best_effort(
            a.status_out,
            failed_status_metadata(e, timezone=a.timezone),
        )
        print(f"ERROR[{e.error_code}]: {e}", file=sys.stderr)
        sys.exit(e.exit_code)
    except ValueError:
        error = OneApiRefreshFailed("One API collector arguments are invalid")
        _write_status_best_effort(
            a.status_out,
            failed_status_metadata(error, timezone=a.timezone),
        )
        print(f"ERROR[{error.error_code}]: {error}", file=sys.stderr)
        sys.exit(error.exit_code)
    except Exception:
        error = OneApiRefreshFailed("One API collector failed unexpectedly")
        _write_status_best_effort(
            a.status_out,
            failed_status_metadata(error, timezone=a.timezone),
        )
        print(f"ERROR[{error.error_code}]: {error}", file=sys.stderr)
        sys.exit(error.exit_code)
