#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
import copy
import datetime as dt
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo


# Repo layout: scripts/ai_usage_comparison_image.py → SCRIPTS_DIR is this folder.
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
DEFAULT_TZ = "Asia/Shanghai"
PUBLIC_SCHEMA_VERSION = 3
MODEL_BREAKDOWN_VERSION = 3
DEFAULT_MACHINES_DIR = REPO_ROOT / "public" / "machines"
PINNED_MODEL_PRICES_PATH = SCRIPTS_DIR / "model_prices.v1.json"
CURSOR_START = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
LITELLM_PRICES_CACHE = (
    Path.home() / ".cache" / "ai-usage-report" / "litellm_model_prices.json"
)
DEFAULT_ONEAPI_STATE_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "ai-usage-report"
    / "oneapi-chrome-state.json"
)


def load_pinned_model_prices(path: Path = PINNED_MODEL_PRICES_PATH) -> dict[str, Any]:
    """Load the checked-in price ledger used for reproducible estimates."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid pinned model price ledger: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not str(payload.get("pricing_version") or ""):
        raise RuntimeError(f"invalid pinned model price ledger: {path}")
    if not isinstance(payload.get("models"), list):
        raise RuntimeError(f"pinned model price ledger has no models: {path}")
    return payload


PINNED_MODEL_PRICES = load_pinned_model_prices()
PRICING_VERSION = str(PINNED_MODEL_PRICES["pricing_version"])


def stable_snapshot_id(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


local_records = load_module("local_ai_usage_records", SCRIPTS_DIR / "local_ai_usage_records.py")
cursor_api = load_module("cursor_usage_api_probe", SCRIPTS_DIR / "cursor_usage_api_probe.py")
comate_usage = load_module("comate_usage", SCRIPTS_DIR / "comate_usage.py")
machine_fragments = load_module("machine_fragments", SCRIPTS_DIR / "machine_fragments.py")
oneapi_usage = load_module("oneapi_usage", SCRIPTS_DIR / "oneapi_usage.py")


def run_json(command: list[str], timeout: int = 240) -> Any:
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed: {proc.stderr.strip()[:500]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{' '.join(command)} returned invalid JSON") from exc


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


def normalize_models(models: Any) -> list[dict[str, Any]]:
    """Normalize model rows without inventing token or cost attribution."""
    totals: dict[str, dict[str, Any]] = {}
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(
            item.get("model")
            or item.get("modelName")
            or item.get("name")
            or "Unattributed"
        ).strip() or "Unattributed"
        if name == "Unattributed history":
            name = "Legacy unknown"
        tokens = safe_int(
            item.get("tokens")
            if item.get("tokens") is not None
            else item.get("total_tokens")
        )
        if not tokens:
            tokens = model_breakdown_tokens(item)
        cost = safe_float(
            item.get("cost_usd")
            if item.get("cost_usd") is not None
            else item.get("cost")
        )
        if not (tokens or cost):
            continue
        component_sources = {
            "input": ("input", "input_tokens", "inputTokens"),
            "output": ("output", "output_tokens", "outputTokens"),
            "cache_read": ("cache_read", "cache_read_tokens", "cacheReadTokens"),
            "cache_write": ("cache_write", "cache_write_tokens", "cacheWriteTokens"),
            "cache_create": (
                "cache_create",
                "cache_creation_tokens",
                "cacheCreationTokens",
            ),
        }
        components: dict[str, int] = {}
        for field, aliases in component_sources.items():
            present = next((alias for alias in aliases if item.get(alias) is not None), None)
            if present is not None:
                components[field] = safe_int(item.get(present))
        components_complete = bool(components) and sum(components.values()) == tokens
        row = totals.setdefault(
            name,
            {
                "model": name,
                "tokens": 0,
                "cost": 0.0,
                "components_complete": True,
                "raw_models": [],
                "pricing_versions": [],
                "pricing_provenance": [],
                "ownership_rule_versions": [],
            },
        )
        row["tokens"] += tokens
        row["cost"] += cost
        row["components_complete"] = bool(row["components_complete"] and components_complete)
        for field, value in components.items():
            row[field] = safe_int(row.get(field)) + value
        reasoning_source = next(
            (
                key
                for key in ("reasoning", "reasoning_tokens", "reasoningOutputTokens")
                if item.get(key) is not None
            ),
            None,
        )
        if reasoning_source is not None:
            row["reasoning"] = safe_int(row.get("reasoning")) + safe_int(
                item.get(reasoning_source)
            )
        raw_values = item.get("raw_models")
        if not isinstance(raw_values, list):
            raw_values = [item.get("raw_model")] if item.get("raw_model") else []
        for raw_name in raw_values:
            raw_name = str(raw_name or "").strip()
            if raw_name and raw_name not in row["raw_models"]:
                row["raw_models"].append(raw_name)
        pricing_version = str(item.get("pricing_version") or "").strip()
        if pricing_version and pricing_version not in row["pricing_versions"]:
            row["pricing_versions"].append(pricing_version)
        provenance = str(item.get("pricing_provenance") or "").strip()
        if provenance and provenance not in row["pricing_provenance"]:
            row["pricing_provenance"].append(provenance)
        canonical_model = str(item.get("canonical_model") or "").strip()
        if canonical_model:
            row["canonical_model"] = canonical_model
        ownership_version = str(item.get("ownership_rule_version") or "").strip()
        if ownership_version and ownership_version not in row["ownership_rule_versions"]:
            row["ownership_rule_versions"].append(ownership_version)
    for row in totals.values():
        if not row.get("raw_models"):
            row.pop("raw_models", None)
        if not row.get("pricing_versions"):
            row.pop("pricing_versions", None)
        elif len(row["pricing_versions"]) == 1:
            row["pricing_version"] = row.pop("pricing_versions")[0]
        if not row.get("pricing_provenance"):
            row.pop("pricing_provenance", None)
        elif len(row["pricing_provenance"]) == 1:
            row["pricing_provenance"] = row["pricing_provenance"][0]
        elif isinstance(row.get("pricing_provenance"), list):
            row["pricing_provenance"] = "mixed"
        if not row.get("ownership_rule_versions"):
            row.pop("ownership_rule_versions", None)
        elif len(row["ownership_rule_versions"]) == 1:
            row["ownership_rule_version"] = row.pop("ownership_rule_versions")[0]
        if not row.get("components_complete"):
            for field in component_sources:
                row.pop(field, None)
    return sorted(
        totals.values(),
        key=lambda row: (-safe_int(row.get("tokens")), str(row.get("model"))),
    )


def merge_models(*model_lists: Any) -> list[dict[str, Any]]:
    return normalize_models(
        [
            item
            for models in model_lists
            for item in (models if isinstance(models, list) else [])
        ]
    )


def models_with_remainder(
    models: Any,
    *,
    total_tokens: Any,
    total_cost: Any,
    label: str = "Legacy unknown",
) -> list[dict[str, Any]]:
    """Reconcile positive unattributed remainder; reject impossible over-attribution."""
    result = normalize_models(models)
    target_tokens = safe_int(total_tokens)
    target_cost = safe_float(total_cost)
    attributed_tokens = sum(safe_int(row.get("tokens")) for row in result)
    attributed_cost = sum(safe_float(row.get("cost")) for row in result)
    if attributed_tokens > target_tokens:
        raise ValueError(
            f"model tokens exceed tool total: models={attributed_tokens}, total={target_tokens}"
        )
    cost_tolerance = max(1e-9, abs(target_cost) * 1e-9)
    if attributed_cost - target_cost > cost_tolerance:
        raise ValueError(
            f"model cost exceeds tool total: models={attributed_cost}, total={target_cost}"
        )

    remainder_tokens = target_tokens - attributed_tokens
    remainder_cost = target_cost - attributed_cost
    if abs(remainder_cost) <= cost_tolerance:
        remainder_cost = 0.0
    if remainder_tokens or remainder_cost > 0:
        result = merge_models(
            result,
            [{"model": label, "tokens": remainder_tokens, "cost": remainder_cost}],
        )
    final_tokens = sum(safe_int(row.get("tokens")) for row in result)
    final_cost = sum(safe_float(row.get("cost")) for row in result)
    if final_tokens != target_tokens or abs(final_cost - target_cost) > cost_tolerance:
        raise ValueError(
            f"model reconciliation failed: tokens={final_tokens}/{target_tokens}, "
            f"cost={final_cost}/{target_cost}"
        )
    return result


def ccusage_day_models(row: dict[str, Any]) -> list[dict[str, Any]]:
    breakdowns = row.get("modelBreakdowns")
    if isinstance(breakdowns, list):
        return normalize_models(breakdowns)

    models = row.get("models")
    if not isinstance(models, dict):
        return []
    raw: list[dict[str, Any]] = []
    for name, values in models.items():
        values = values if isinstance(values, dict) else {}
        tokens = safe_int(values.get("totalTokens"))
        if not tokens:
            tokens = (
                safe_int(values.get("inputTokens"))
                + safe_int(values.get("cacheCreationTokens"))
                + safe_int(values.get("cacheReadTokens"))
                + safe_int(values.get("outputTokens"))
            )
        raw.append(
            {
                "model": str(name),
                "tokens": tokens,
                "cost": safe_float(
                    values.get("costUSD")
                    if values.get("costUSD") is not None
                    else values.get("cost")
                ),
                "input": safe_int(values.get("inputTokens")),
                "output": safe_int(values.get("outputTokens")),
                "cache_create": safe_int(values.get("cacheCreationTokens")),
                "cache_read": safe_int(values.get("cacheReadTokens")),
                "reasoning": safe_int(values.get("reasoningOutputTokens")),
                "pricing_version": str(values.get("pricingVersion") or "legacy"),
                "pricing_provenance": str(
                    values.get("pricingProvenance") or "legacy"
                ),
            }
        )
    return normalize_models(raw)


def fmt_compact(value: Any) -> str:
    number = float(safe_float(value))
    sign = "-" if number < 0 else ""
    number = abs(number)
    for suffix, factor in (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if number >= factor:
            return f"{sign}{number / factor:.2f}{suffix}"
    return f"{sign}{number:.0f}"


def fmt_usd(value: Any) -> str:
    return f"${safe_float(value):,.2f}"


def fmt_int(value: Any) -> str:
    return f"{safe_int(value):,}"


def parse_codex_date(value: str) -> dt.datetime | None:
    for fmt in ("%b %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_iso_date(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_range(first: str, last: str) -> str:
    return f"{first} to {last}" if first and last else first or last or "unknown"


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def daily_range(daily: list[dict[str, Any]], codex: bool = False) -> tuple[str, str]:
    dates: list[dt.datetime] = []
    for row in daily:
        raw = str(row.get("date") or "")
        parsed = parse_codex_date(raw) if codex else parse_iso_date(raw)
        if parsed:
            dates.append(parsed)
    if not dates:
        return "", ""
    return min(dates).strftime("%Y-%m-%d"), max(dates).strftime("%Y-%m-%d")


def resolve_tz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"invalid timezone: {name}") from exc


def local_day_window(timezone: str, target: dt.date | None = None) -> tuple[str, int, int]:
    tz = resolve_tz(timezone)
    day = target or dt.datetime.now(tz=tz).date()
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
    end = start + dt.timedelta(days=1)
    return day.strftime("%Y-%m-%d"), int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def ms_to_calendar_date(ms_val: Any, tz_name: str) -> str:
    ms = safe_int(ms_val)
    if not ms:
        return ""
    utc = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return utc.astimezone(resolve_tz(tz_name)).strftime("%Y-%m-%d")


def codex_daily_points(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in daily:
        raw = str(row.get("date") or "")
        parsed = parse_codex_date(raw)
        if not parsed:
            continue
        cache_read = row.get("cacheReadTokens")
        if cache_read is None:
            cache_read = row.get("cachedInputTokens")
        models = ccusage_day_models(row)
        point = {
                "date": parsed.strftime("%Y-%m-%d"),
                "tokens": safe_int(row.get("totalTokens")),
                "cost": safe_float(row.get("costUSD")),
                "input": safe_int(row.get("inputTokens")),
                "cache_read": safe_int(cache_read),
                "output": safe_int(row.get("outputTokens")),
                "reasoning": safe_int(row.get("reasoningOutputTokens")),
                "models": models,
                "pricing_version": str(row.get("pricingVersion") or "legacy"),
                "pricing_complete": bool(row.get("pricingComplete")),
                "pricing_provenance": str(row.get("pricingProvenance") or "legacy"),
            }
        component_total = point["input"] + point["cache_read"] + point["output"]
        model_tokens = sum(safe_int(model.get("tokens")) for model in models)
        model_cost = sum(safe_float(model.get("cost")) for model in models)
        point["snapshot_complete"] = (
            component_total == point["tokens"]
            and model_tokens == point["tokens"]
            and abs(model_cost - point["cost"]) <= max(1e-9, abs(point["cost"]) * 1e-9)
        )
        rows.append(point)
    rows.sort(key=lambda r: r["date"])
    return rows


def claude_daily_points(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in daily:
        raw = str(row.get("date") or "")
        parsed = parse_iso_date(raw)
        if not parsed:
            continue
        aware = parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        models = ccusage_day_models(row)
        point = {
                "date": aware.strftime("%Y-%m-%d"),
                "tokens": safe_int(row.get("totalTokens")),
                "cost": safe_float(row.get("totalCost")),
                "input": safe_int(row.get("inputTokens")),
                "cache_create": safe_int(row.get("cacheCreationTokens")),
                "cache_read": safe_int(row.get("cacheReadTokens")),
                "output": safe_int(row.get("outputTokens")),
                "models": models,
                "pricing_version": str(row.get("pricingVersion") or "legacy"),
                "pricing_complete": bool(row.get("pricingComplete")),
                "pricing_provenance": str(row.get("pricingProvenance") or "legacy"),
            }
        component_total = (
            point["input"]
            + point["cache_create"]
            + point["cache_read"]
            + point["output"]
        )
        model_tokens = sum(safe_int(model.get("tokens")) for model in models)
        model_cost = sum(safe_float(model.get("cost")) for model in models)
        point["snapshot_complete"] = (
            component_total == point["tokens"]
            and model_tokens == point["tokens"]
            and abs(model_cost - point["cost"]) <= max(1e-9, abs(point["cost"]) * 1e-9)
        )
        rows.append(point)
    rows.sort(key=lambda r: r["date"])
    return rows


TOOL_TOKEN_FIELDS: dict[str, list[str]] = {
    "codex": ["input", "cache_read", "output", "reasoning"],
    "claude": ["input", "cache_create", "cache_read", "output"],
    "cursor": ["input", "cache_write", "cache_read", "output"],
    "oneapi": ["input", "cache_read", "cache_write", "output"],
}


def empty_daily_row(date_key: str) -> dict[str, Any]:
    row: dict[str, Any] = {"date": date_key}
    for prefix in TOOL_TOKEN_FIELDS:
        row[f"{prefix}_tokens"] = 0
        row[f"{prefix}_cost"] = 0.0
        for field in TOOL_TOKEN_FIELDS[prefix]:
            row[f"{prefix}_{field}"] = 0
    row["oneapi_requests"] = 0
    for prefix in TOOL_TOKEN_FIELDS:
        row[f"{prefix}_models"] = []
    row["total_tokens"] = 0
    row["total_cost"] = 0.0
    return row


ONEAPI_ROW_FIELDS = (
    "oneapi_tokens",
    "oneapi_cost",
    "oneapi_input",
    "oneapi_output",
    "oneapi_cache_read",
    "oneapi_cache_write",
    "oneapi_requests",
)


def recompute_daily_total(row: dict[str, Any]) -> None:
    row["total_tokens"] = sum(
        safe_int(row.get(f"{prefix}_tokens")) for prefix in TOOL_TOKEN_FIELDS
    )
    row["total_cost"] = sum(
        safe_float(row.get(f"{prefix}_cost")) for prefix in TOOL_TOKEN_FIELDS
    )


def has_daily_activity(row: dict[str, Any]) -> bool:
    return bool(
        any(
            safe_int(row.get(f"{prefix}_tokens"))
            or safe_float(row.get(f"{prefix}_cost"))
            for prefix in TOOL_TOKEN_FIELDS
        )
        or safe_int(row.get("oneapi_requests"))
    )


def copy_oneapi_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ONEAPI_ROW_FIELDS:
        target[key] = (
            safe_float(source.get(key))
            if key == "oneapi_cost"
            else safe_int(source.get(key))
        )


def clear_oneapi_fields(row: dict[str, Any]) -> None:
    for key in ONEAPI_ROW_FIELDS:
        row[key] = 0.0 if key == "oneapi_cost" else 0


def apply_oneapi_point(row: dict[str, Any], point: dict[str, Any]) -> None:
    row["oneapi_tokens"] = safe_int(point.get("tokens"))
    row["oneapi_cost"] = safe_float(point.get("cost_usd"))
    row["oneapi_input"] = safe_int(point.get("input"))
    row["oneapi_output"] = safe_int(point.get("output"))
    row["oneapi_cache_read"] = safe_int(point.get("cache_read"))
    row["oneapi_cache_write"] = safe_int(point.get("cache_write"))
    row["oneapi_requests"] = safe_int(point.get("requests"))
    row["oneapi_models"] = normalize_models(point.get("model_breakdowns"))


def oneapi_point_from_comate(point: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": str(point.get("date") or ""),
        "tokens": safe_int(point.get("tokens")),
        "input": safe_int(point.get("input")),
        "output": safe_int(point.get("output")),
        "cache_read": 0,
        "cache_write": 0,
        "requests": 0,
        "quota": 0,
        "cost_cny": 0.0,
        "cost_usd": 0.0,
        "source": "comate-local",
        "model_breakdowns": [
            {
                "model": str(model.get("model") or "Comate (unattributed)"),
                "total_tokens": safe_int(
                    model.get("total_tokens")
                    if model.get("total_tokens") is not None
                    else model.get("tokens")
                ),
                "cost_usd": 0.0,
                "source": "comate-local",
            }
            for model in (
                point.get("model_breakdowns")
                if isinstance(point.get("model_breakdowns"), list)
                else []
            )
            if isinstance(model, dict)
        ],
    }


def recompute_oneapi_totals(payload: dict[str, Any]) -> dict[str, Any]:
    daily = [
        point
        for point in (payload.get("daily_timeline") or [])
        if isinstance(point, dict) and point.get("date")
    ]
    daily.sort(key=lambda point: str(point.get("date")))
    payload["daily_timeline"] = daily
    total_quota = sum(safe_int(point.get("quota")) for point in daily)
    payload["history"] = {
        "first": str(daily[0].get("date")) if daily else "",
        "last": str(daily[-1].get("date")) if daily else "",
    }
    payload["totals"] = {
        "input_tokens": sum(safe_int(point.get("input")) for point in daily),
        "output_tokens": sum(safe_int(point.get("output")) for point in daily),
        "cache_read_tokens": sum(
            safe_int(point.get("cache_read")) for point in daily
        ),
        "cache_write_tokens": sum(
            safe_int(point.get("cache_write")) for point in daily
        ),
        "total_tokens": sum(safe_int(point.get("tokens")) for point in daily),
        "quota": total_quota,
        "cost_cny": oneapi_usage.quota_to_cny(total_quota),
        "cost_usd": sum(safe_float(point.get("cost_usd")) for point in daily),
        "requests": sum(safe_int(point.get("requests")) for point in daily),
    }
    return payload


def validate_oneapi_snapshot(
    payload: Any,
    *,
    timezone: str,
    today: str,
    calendar_days: int,
) -> dict[str, Any]:
    """Validate a complete account snapshot before it may replace durable rows."""
    if not isinstance(payload, dict):
        raise ValueError("One API snapshot is not an object")
    if payload.get("available") is not True or payload.get("complete") is not True:
        raise ValueError("One API snapshot is not available and complete")
    if safe_int(payload.get("accounting_version")) != oneapi_usage.ACCOUNTING_VERSION:
        raise ValueError("One API snapshot accounting version mismatch")
    if safe_int(payload.get("ownership_rule_version")) != oneapi_usage.OWNERSHIP_RULE_VERSION:
        raise ValueError("One API snapshot ownership rule version mismatch")
    if str(payload.get("timezone") or "") != timezone:
        raise ValueError("One API snapshot timezone mismatch")

    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    if (
        scope.get("kind") != "account"
        or scope.get("scope_id") != "oneapi:self"
        or scope.get("merge_strategy") != "latest_complete_snapshot"
    ):
        raise ValueError("One API snapshot account scope mismatch")

    snapshot_id = str(payload.get("snapshot_id") or "")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_id) is None:
        raise ValueError("One API snapshot id is invalid")
    captured_at = str(payload.get("captured_at") or "")
    try:
        captured = dt.datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("One API snapshot capture time is invalid") from exc
    if captured.tzinfo is None or captured.astimezone(resolve_tz(timezone)).date().isoformat() != today:
        raise ValueError("One API snapshot was not captured today")

    window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
    try:
        start_date = dt.date.fromisoformat(str(window.get("start") or ""))
        end_date = dt.date.fromisoformat(str(window.get("end") or ""))
        today_date = dt.date.fromisoformat(today)
    except ValueError as exc:
        raise ValueError("One API snapshot window is invalid") from exc
    expected_start = today_date - dt.timedelta(days=calendar_days - 1)
    if (
        end_date != today_date
        or start_date != expected_start
        or window.get("complete") is not True
        or str(window.get("timezone") or "") != timezone
        or safe_int(window.get("calendar_days")) != calendar_days
    ):
        raise ValueError("One API snapshot does not cover the expected calendar window")

    pagination = (
        payload.get("pagination")
        if isinstance(payload.get("pagination"), dict)
        else {}
    )
    if pagination.get("complete") is not True:
        raise ValueError("One API snapshot pagination is incomplete")
    if safe_int(pagination.get("records_after_deduplication")) != safe_int(
        payload.get("request_count")
    ):
        raise ValueError("One API snapshot pagination count mismatch")

    daily = payload.get("daily_timeline")
    if not isinstance(daily, list):
        raise ValueError("One API snapshot has no daily timeline")
    seen_dates: set[str] = set()
    for point in daily:
        if not isinstance(point, dict):
            raise ValueError("One API snapshot contains an invalid daily point")
        date_key = str(point.get("date") or "")
        if date_key in seen_dates or not (window["start"] <= date_key <= window["end"]):
            raise ValueError("One API snapshot daily dates are invalid")
        seen_dates.add(date_key)
        component_total = sum(
            safe_int(point.get(field))
            for field in ("input", "output", "cache_read", "cache_write")
        )
        if component_total != safe_int(point.get("tokens")):
            raise ValueError(f"One API component mismatch for {date_key}")
        models = point.get("model_breakdowns")
        if not isinstance(models, list):
            raise ValueError(f"One API model breakdown missing for {date_key}")
        if sum(safe_int(model.get("total_tokens")) for model in models if isinstance(model, dict)) != safe_int(point.get("tokens")):
            raise ValueError(f"One API model token mismatch for {date_key}")
        model_cost = sum(
            safe_float(model.get("cost_usd"))
            for model in models
            if isinstance(model, dict)
        )
        point_cost = safe_float(point.get("cost_usd"))
        if abs(model_cost - point_cost) > max(1e-9, abs(point_cost) * 1e-9):
            raise ValueError(f"One API model cost mismatch for {date_key}")

    actual_totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
    computed_totals = recompute_oneapi_totals(copy.deepcopy(payload))["totals"]
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        "quota",
        "requests",
    ):
        if safe_int(actual_totals.get(field)) != safe_int(computed_totals.get(field)):
            raise ValueError(f"One API snapshot total mismatch: {field}")
    for field in ("cost_cny", "cost_usd"):
        actual = safe_float(actual_totals.get(field))
        computed = safe_float(computed_totals.get(field))
        if abs(actual - computed) > max(1e-9, abs(computed) * 1e-9):
            raise ValueError(f"One API snapshot total mismatch: {field}")
    return payload


def reconcile_oneapi_payload(
    prior: dict[str, Any],
    fetched: dict[str, Any],
    legacy_comate: dict[str, Any],
) -> dict[str, Any]:
    """Keep the complete model timeline while replacing only the fetched window."""
    compatible = (
        safe_int(prior.get("accounting_version"))
        == oneapi_usage.ACCOUNTING_VERSION
    )
    by_date: dict[str, dict[str, Any]] = {}
    if compatible:
        for point in prior.get("daily_timeline") or []:
            if isinstance(point, dict) and point.get("date"):
                by_date[str(point["date"])] = copy.deepcopy(point)

    result = copy.deepcopy(fetched if fetched else prior)
    if fetched.get("available") and fetched.get("complete"):
        window = fetched.get("window")
        window = window if isinstance(window, dict) else {}
        start = str(window.get("start") or "")
        end = str(window.get("end") or "")
        if not start or not end:
            raise ValueError("complete One API collection is missing its window")
        for date_key in list(by_date):
            if start <= date_key <= end:
                del by_date[date_key]
        for point in fetched.get("daily_timeline") or []:
            if not isinstance(point, dict) or not point.get("date"):
                continue
            date_key = str(point["date"])
            if not (start <= date_key <= end):
                raise ValueError(
                    f"One API point {date_key} falls outside {start}..{end}"
                )
            gateway_point = copy.deepcopy(point)
            gateway_point["source"] = "oneapi"
            by_date[date_key] = gateway_point

    for point in legacy_comate.get("daily_timeline") or []:
        if not isinstance(point, dict) or not point.get("date"):
            continue
        date_key = str(point["date"])
        existing = by_date.get(date_key)
        if existing and existing.get("source") != "comate-local":
            continue
        by_date[date_key] = oneapi_point_from_comate(point)

    result["accounting_version"] = oneapi_usage.ACCOUNTING_VERSION
    result["daily_timeline"] = [by_date[key] for key in sorted(by_date)]
    result["legacy_comate"] = {
        "included": True,
        "history": legacy_comate.get("history") or {},
        "total_tokens": safe_int(legacy_comate.get("total_tokens")),
        "note": "Local Comate context deltas are retained only for dates without One API gateway coverage.",
    }
    return recompute_oneapi_totals(result)


def reconcile_oneapi_rows(
    current_rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
    oneapi_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Preserve durable history and replace only a complete fetched window."""
    by_date = {
        str(row.get("date")): dict(row)
        for row in current_rows
        if isinstance(row, dict) and row.get("date")
    }

    for prior in prior_rows:
        if not isinstance(prior, dict) or not prior.get("date"):
            continue
        date_key = str(prior["date"])
        if not (
            safe_int(prior.get("oneapi_tokens"))
            or safe_float(prior.get("oneapi_cost"))
            or safe_int(prior.get("oneapi_requests"))
        ):
            continue
        row = by_date.setdefault(date_key, empty_daily_row(date_key))
        copy_oneapi_fields(row, prior)

    if oneapi_data.get("available") and oneapi_data.get("complete"):
        window = oneapi_data.get("window")
        window = window if isinstance(window, dict) else {}
        window_start = str(window.get("start") or "")
        window_end = str(window.get("end") or "")
        if not window_start or not window_end:
            raise ValueError("complete One API collection is missing its window")

        for date_key, row in by_date.items():
            if window_start <= date_key <= window_end:
                clear_oneapi_fields(row)

        for point in oneapi_data.get("daily_timeline") or []:
            if not isinstance(point, dict) or not point.get("date"):
                continue
            date_key = str(point["date"])
            row = by_date.setdefault(date_key, empty_daily_row(date_key))
            apply_oneapi_point(row, point)
    else:
        for point in oneapi_data.get("daily_timeline") or []:
            if not isinstance(point, dict) or not point.get("date"):
                continue
            date_key = str(point["date"])
            row = by_date.setdefault(date_key, empty_daily_row(date_key))
            apply_oneapi_point(row, point)

    result: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        row = by_date[date_key]
        recompute_daily_total(row)
        if has_daily_activity(row):
            result.append(row)
    return result


def apply_tool_point(row: dict[str, Any], prefix: str, point: dict[str, Any]) -> None:
    row[f"{prefix}_tokens"] = safe_int(point.get("tokens"))
    row[f"{prefix}_cost"] = safe_float(point.get("cost"))
    for field in TOOL_TOKEN_FIELDS[prefix]:
        row[f"{prefix}_{field}"] = safe_int(point.get(field))
    row[f"{prefix}_models"] = normalize_models(point.get("models"))
    row[f"{prefix}_snapshot_complete"] = bool(point.get("snapshot_complete", True))
    row[f"{prefix}_pricing_version"] = str(point.get("pricing_version") or "legacy")
    row[f"{prefix}_pricing_complete"] = bool(point.get("pricing_complete"))
    row[f"{prefix}_pricing_provenance"] = str(
        point.get("pricing_provenance") or "legacy"
    )


def usage_daily_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("daily") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def usage_totals(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("totals"), dict):
        return payload["totals"]
    return {}


def merge_daily_timeline(
    codex_pts: list[dict[str, Any]],
    claude_pts: list[dict[str, Any]],
    cursor_pts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for prefix, points in (
        ("codex", codex_pts),
        ("claude", claude_pts),
        ("cursor", cursor_pts),
    ):
        for point in points:
            date_key = str(point.get("date") or "")
            if not date_key:
                continue
            row = by_date.setdefault(date_key, empty_daily_row(date_key))
            apply_tool_point(row, prefix, point)

    rows: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        row = by_date[date_key]
        recompute_daily_total(row)
        if has_daily_activity(row):
            rows.append(row)
    return rows


def local_record_summary(home: Path, tmp_dir: Path) -> dict[str, Any]:
    codex_rows, codex = local_records.parse_codex(home, False)
    claude_rows, claude = local_records.parse_claude(home, False)
    cursor_rows, cursor = local_records.parse_cursor(home, False)
    cursor_ai_tracking = local_records.inspect_cursor_ai_tracking(home, tmp_dir)
    cursor_vscdb = local_records.inspect_cursor_vscdb(home, tmp_dir)
    return {
        "codex": codex,
        "claude_code": claude,
        "cursor": {
            **cursor,
            "vscdb_matching_keys": cursor_vscdb.get("matching_keys", 0),
            "ai_tracking_rows": cursor_ai_tracking.get("rows", 0),
            "ai_tracking_requests": cursor_ai_tracking.get("requests", 0),
        },
        "row_counts": {
            "codex": len(codex_rows),
            "claude_code": len(claude_rows),
            "cursor": len(cursor_rows),
        },
    }


def call_cursor(client: Any, method: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        status, _headers, raw = client.dashboard(method, body or {})
    except Exception as exc:
        return {"_status": "exception", "_error": f"{type(exc).__name__}: {exc}"}
    if not (200 <= status < 300):
        return {"_status": status}
    decoded = cursor_api.decode_json(raw)
    return decoded if isinstance(decoded, dict) else {}


def fetch_cursor_usage(
    home: Path,
    page_size: int,
    timezone: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, Any]:
    state = cursor_api.read_cursor_state(home)
    token = str(state.get("access_token") or "")
    if not token:
        return {"available": False, "error": "missing local Cursor access token"}

    version = cursor_api.cursor_product_version()
    client = cursor_api.CursorClient(token, version, str(state.get("email") or ""))
    user_id = safe_int(state.get("dashboard_user_id"))
    team_ids = state.get("team_ids") if isinstance(state.get("team_ids"), list) else []
    team_id = safe_int(team_ids[0]) if team_ids else 0
    account_scope = "team/account scope" if team_id else "individual signed-in account"
    query_scope = f"teamId={team_id}, userId=local dashboard user" if team_id else "teamId=0, userId=local dashboard user"
    end_ms = end_ms if end_ms is not None else int(dt.datetime.now(tz=dt.timezone.utc).timestamp() * 1000)
    start_ms = start_ms if start_ms is not None else int(CURSOR_START.timestamp() * 1000)

    full_usage = call_cursor(
        client,
        "GetAggregatedUsageEvents",
        {"teamId": team_id, "userId": user_id, "startDate": start_ms, "endDate": end_ms},
    )
    cursor_errors: list[str] = []
    aggregated_available = bool(full_usage) and not full_usage.get("_status")
    if full_usage.get("_status"):
        cursor_errors.append(f"GetAggregatedUsageEvents failed: {full_usage.get('_error') or full_usage.get('_status')}")
        full_usage = {}

    expected_event_count: int | None = None
    first_event = ""
    last_event = ""
    cursor_day_tokens: dict[str, int] = defaultdict(int)
    cursor_day_input: dict[str, int] = defaultdict(int)
    cursor_day_output: dict[str, int] = defaultdict(int)
    cursor_day_cache_write: dict[str, int] = defaultdict(int)
    cursor_day_cache_read: dict[str, int] = defaultdict(int)
    cursor_day_cost = defaultdict(float)
    cursor_day_models: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    )
    page = 1
    filtered_complete = True
    processed_events = 0
    while True:
        response = call_cursor(
            client,
            "GetFilteredUsageEvents",
            {
                "teamId": team_id,
                "userId": user_id,
                "startDate": start_ms,
                "endDate": end_ms,
                "page": page,
                "pageSize": page_size,
            },
        )
        if not response or response.get("_status"):
            if response and response.get("_status"):
                cursor_errors.append(f"GetFilteredUsageEvents failed: {response.get('_error') or response.get('_status')}")
            filtered_complete = False
            break
        raw_event_count = response.get("totalUsageEventsCount")
        page_event_count: int | None = None
        if isinstance(raw_event_count, int) and not isinstance(raw_event_count, bool):
            if raw_event_count >= 0:
                page_event_count = raw_event_count
        elif isinstance(raw_event_count, str) and raw_event_count.isdigit():
            page_event_count = int(raw_event_count)
        if page_event_count is None:
            filtered_complete = False
        elif expected_event_count is None:
            expected_event_count = page_event_count
        elif page_event_count != expected_event_count:
            filtered_complete = False
        events = cursor_api.normalize_event_rows(response)
        if not events:
            if (
                expected_event_count is None
                or expected_event_count > processed_events
            ):
                filtered_complete = False
            break
        processed_events += len(events)
        for event in events:
            timestamp = str(event.get("timestamp") or "")
            if timestamp:
                first_event = min(first_event, timestamp) if first_event else timestamp
                last_event = max(last_event, timestamp) if last_event else timestamp
            day_key = ms_to_calendar_date(event.get("timestamp_ms"), timezone)
            if day_key:
                input_tokens = safe_int(event.get("input_tokens"))
                output_tokens = safe_int(event.get("output_tokens"))
                cache_write_tokens = safe_int(event.get("cache_write_tokens"))
                cache_read_tokens = safe_int(event.get("cache_read_tokens"))
                cursor_day_input[day_key] += input_tokens
                cursor_day_output[day_key] += output_tokens
                cursor_day_cache_write[day_key] += cache_write_tokens
                cursor_day_cache_read[day_key] += cache_read_tokens
                cursor_day_tokens[day_key] += (
                    input_tokens + output_tokens + cache_write_tokens + cache_read_tokens
                )
                cents = safe_float(event.get("total_cents"))
                if cents <= 0:
                    cents = safe_float(event.get("charged_cents"))
                cursor_day_cost[day_key] += cents / 100
                model_name = str(event.get("model") or "Unattributed").strip()
                model_tokens = (
                    input_tokens + output_tokens + cache_write_tokens + cache_read_tokens
                )
                cursor_day_models[day_key][model_name]["tokens"] += model_tokens
                cursor_day_models[day_key][model_name]["cost"] += cents / 100
        if len(events) < page_size or (
            expected_event_count is not None
            and page * page_size >= expected_event_count
        ):
            break
        page += 1

    total_tokens = (
        safe_int(full_usage.get("totalInputTokens"))
        + safe_int(full_usage.get("totalOutputTokens"))
        + safe_int(full_usage.get("totalCacheWriteTokens"))
        + safe_int(full_usage.get("totalCacheReadTokens"))
    )
    aggregate_cost = safe_float(full_usage.get("totalCostCents")) / 100
    filtered_tokens = sum(cursor_day_tokens.values())
    filtered_cost = sum(cursor_day_cost.values())
    aggregate_matches_events = (
        aggregated_available
        and filtered_tokens == total_tokens
        and abs(filtered_cost - aggregate_cost)
        <= max(1e-9, abs(aggregate_cost) * 1e-9)
    )
    if aggregated_available and not aggregate_matches_events:
        cursor_errors.append(
            "Cursor aggregate/event totals mismatch: "
            f"tokens={filtered_tokens}/{total_tokens}, "
            f"cost={filtered_cost}/{aggregate_cost}"
        )
    collection_complete = (
        filtered_complete
        and expected_event_count is not None
        and processed_events == expected_event_count
        and aggregate_matches_events
    )
    daily_timeline = [
        {
            "date": d,
            "tokens": cursor_day_tokens[d],
            "cost": cursor_day_cost[d],
            "input": cursor_day_input[d],
            "output": cursor_day_output[d],
            "cache_write": cursor_day_cache_write[d],
            "cache_read": cursor_day_cache_read[d],
            "models": normalize_models(
                [
                    {"model": model_name, **values}
                    for model_name, values in cursor_day_models[d].items()
                ]
            ),
            "snapshot_complete": collection_complete,
            "pricing_version": "cursor-billed",
            "pricing_complete": collection_complete,
            "pricing_provenance": "billed-dashboard",
        }
        for d in sorted(cursor_day_tokens.keys())
    ]
    captured_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    snapshot_id = stable_snapshot_id(
        {
            "scope": {"team_id": team_id, "user_id": user_id},
            "start_ms": start_ms,
            "end_ms": end_ms,
            "events": expected_event_count,
            "daily_timeline": daily_timeline,
        }
    )
    return {
        "available": True,
        "complete": collection_complete,
        "captured_at": captured_at,
        "snapshot_id": snapshot_id,
        "scope": {
            "kind": "account",
            "merge_strategy": "latest-complete-snapshot",
            "endpoint": "Cursor Dashboard API",
        },
        "account": {
            "membership": state.get("membership", ""),
            "subscription_status": state.get("subscription_status", ""),
            "has_token_based_pricing": state.get("has_token_based_pricing"),
            "use_openai_key": state.get("use_openai_key"),
            "client_version": version,
            "scope": account_scope,
            "query_scope": query_scope,
            "team_ids_count": len(team_ids),
            "uses_dashboard_user_id": bool(user_id),
        },
        "history": {
            "first": first_event or cursor_api.ms_to_iso(start_ms),
            "last": last_event or cursor_api.ms_to_iso(end_ms),
            "events": expected_event_count or 0,
            "input": safe_int(full_usage.get("totalInputTokens")),
            "output": safe_int(full_usage.get("totalOutputTokens")),
            "cache_write": safe_int(full_usage.get("totalCacheWriteTokens")),
            "cache_read": safe_int(full_usage.get("totalCacheReadTokens")),
            "total_tokens": total_tokens,
            "cost": aggregate_cost,
        },
        "daily_timeline": daily_timeline,
        "error": "; ".join(cursor_errors),
    }


def daily_row_for_date(date_key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        if isinstance(row, dict) and row.get("date") == date_key:
            return row
    return empty_daily_row(date_key)


def collect_today_usage(home: Path, timezone: str, cursor_page_size: int) -> dict[str, Any]:
    today_key, start_ms, end_ms = local_day_window(timezone)
    codex_usage = ccusage_daily("codex", timezone, since=today_key, until=today_key)
    claude_usage = ccusage_daily("claude", timezone, since=today_key, until=today_key)
    cursor_usage = fetch_cursor_usage(home, cursor_page_size, timezone, start_ms, end_ms)
    codex_pts = codex_daily_points(usage_daily_rows(codex_usage))
    claude_pts = claude_daily_points(usage_daily_rows(claude_usage))
    cursor_pts: list[dict[str, Any]] = []
    if cursor_usage.get("available"):
        cursor_pts = cursor_usage.get("daily_timeline") or []
        if not any(isinstance(row, dict) and row.get("date") == today_key for row in cursor_pts):
            cursor_history = cursor_usage.get("history") if isinstance(cursor_usage.get("history"), dict) else {}
            tokens = safe_int(cursor_history.get("total_tokens"))
            cost = safe_float(cursor_history.get("cost"))
            if tokens or cost:
                cursor_pts = [*cursor_pts, {"date": today_key, "tokens": tokens, "cost": cost}]

    rows = merge_daily_timeline(codex_pts, claude_pts, cursor_pts)
    row = daily_row_for_date(today_key, rows)
    generated_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    report_snapshot_id = stable_snapshot_id(
        {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "pricing_version": PRICING_VERSION,
            "cursor_snapshot_id": cursor_usage.get("snapshot_id"),
            "date": today_key,
            "row": row,
        }
    )
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "pricing_version": PRICING_VERSION,
        "captured_at": generated_at,
        "snapshot_id": report_snapshot_id,
        "generated_at": generated_at,
        "timezone": timezone,
        "date": today_key,
        "row": row,
        "cursor": cursor_usage,
    }


def render_today_text(data: dict[str, Any]) -> str:
    row = data.get("row") if isinstance(data.get("row"), dict) else {}
    lines = [
        f"AI coding usage for {data.get('date')} ({data.get('timezone')})",
        f"Generated: {data.get('generated_at')}",
        "",
        f"{'Tool':<12} {'Total':>12} {'Input':>12} {'Cache':>12} {'Output':>12} {'Cost':>12}",
        f"{'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12}",
        (
            f"{'Codex':<12} {fmt_int(row.get('codex_tokens')):>12} "
            f"{fmt_int(row.get('codex_input')):>12} "
            f"{fmt_int(row.get('codex_cache_read')):>12} "
            f"{fmt_int(row.get('codex_output')):>12} "
            f"{fmt_usd(row.get('codex_cost')):>12}"
        ),
        (
            f"{'Claude Code':<12} {fmt_int(row.get('claude_tokens')):>12} "
            f"{fmt_int(row.get('claude_input')):>12} "
            f"{fmt_int(safe_int(row.get('claude_cache_create')) + safe_int(row.get('claude_cache_read'))):>12} "
            f"{fmt_int(row.get('claude_output')):>12} "
            f"{fmt_usd(row.get('claude_cost')):>12}"
        ),
        (
            f"{'Cursor':<12} {fmt_int(row.get('cursor_tokens')):>12} "
            f"{fmt_int(row.get('cursor_input')):>12} "
            f"{fmt_int(safe_int(row.get('cursor_cache_write')) + safe_int(row.get('cursor_cache_read'))):>12} "
            f"{fmt_int(row.get('cursor_output')):>12} "
            f"{fmt_usd(row.get('cursor_cost')):>12}"
        ),
        f"{'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12}",
        f"{'Total':<12} {fmt_int(row.get('total_tokens')):>12} {'':>12} {'':>12} {'':>12} {fmt_usd(row.get('total_cost')):>12}",
        "",
        "Cache column = cache read for Codex; cache create + read for Claude; cache write + read for Cursor.",
        "Historical local Comate context deltas are retained under One API.",
        "Codex reasoning tokens are included in total but omitted from this table.",
        "Ducc (Claude wrapper) is counted under Claude Code.",
    ]
    cursor = data.get("cursor") if isinstance(data.get("cursor"), dict) else {}
    if cursor.get("error"):
        lines.extend(["", f"Cursor note: {cursor.get('error')}"])
    elif not cursor.get("available"):
        lines.extend(["", f"Cursor note: {cursor.get('error') or 'Cursor API unavailable'}"])
    return "\n".join(lines)


def load_previous_cursor_points(usage_json: Path) -> list[dict[str, Any]]:
    if not usage_json.is_file():
        return []
    try:
        payload = json.loads(usage_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    points: list[dict[str, Any]] = []
    for row in payload.get("daily") or []:
        if not isinstance(row, dict):
            continue
        date_key = str(row.get("date") or "")
        tokens = safe_int(row.get("cursor_tokens"))
        cost = safe_float(row.get("cursor_cost"))
        if not date_key or not (tokens or cost):
            continue
        points.append(
            {
                "date": date_key,
                "tokens": tokens,
                "cost": cost,
                "input": safe_int(row.get("cursor_input")),
                "cache_write": safe_int(row.get("cursor_cache_write")),
                "cache_read": safe_int(row.get("cursor_cache_read")),
                "output": safe_int(row.get("cursor_output")),
                "models": normalize_models(row.get("cursor_models")),
                "snapshot_complete": bool(row.get("cursor_snapshot_complete", True)),
                "pricing_version": str(
                    row.get("cursor_pricing_version") or "cursor-billed"
                ),
                "pricing_complete": bool(row.get("cursor_pricing_complete", True)),
                "pricing_provenance": str(
                    row.get("cursor_pricing_provenance") or "billed-dashboard"
                ),
            }
        )
    return points


def overlay_prior_cursor_row(target: dict[str, Any], prior: dict[str, Any]) -> None:
    """Copy the durable account snapshot, including its model attribution."""
    for key in (
        "cursor_tokens",
        "cursor_cost",
        "cursor_input",
        "cursor_cache_write",
        "cursor_cache_read",
        "cursor_output",
    ):
        if not (safe_int(target.get(key)) or safe_float(target.get(key))):
            target[key] = (
                safe_float(prior.get(key))
                if key.endswith("cost")
                else safe_int(prior.get(key))
            )
    current_models = normalize_models(target.get("cursor_models"))
    if not current_models or all(
        str(model.get("model") or "") == "Legacy unknown"
        for model in current_models
    ):
        target["cursor_models"] = normalize_models(prior.get("cursor_models"))
    for key, default in (
        ("cursor_snapshot_complete", True),
        ("cursor_pricing_complete", True),
        ("cursor_pricing_version", "cursor-billed"),
        ("cursor_pricing_provenance", "billed-dashboard"),
    ):
        if key not in target:
            target[key] = prior.get(key, default)


def resolve_cursor_points(
    cursor_usage: dict[str, Any],
    usage_json: Path | None = None,
) -> list[dict[str, Any]]:
    api_points: list[dict[str, Any]] = []
    if cursor_usage.get("available"):
        api_points = [p for p in (cursor_usage.get("daily_timeline") or []) if isinstance(p, dict) and p.get("date")]

    prev_points = load_previous_cursor_points(usage_json) if usage_json is not None else []
    if not api_points:
        return prev_points
    if not prev_points:
        return api_points

    # API wins on overlapping dates; keep prior days the API did not return
    # (partial Dashboard responses must not wipe months of Cursor history).
    by_date = {str(p["date"]): p for p in prev_points}
    for point in api_points:
        by_date[str(point["date"])] = point
    return [by_date[key] for key in sorted(by_date)]


def build_local_machine_daily(
    codex_pts: list[dict[str, Any]],
    claude_pts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Local-only daily rows for this machine fragment (no Cursor)."""
    return merge_daily_timeline(codex_pts, claude_pts, [])


def local_today(timezone: str) -> str:
    return dt.datetime.now(tz=resolve_tz(timezone)).strftime("%Y-%m-%d")


def ccusage_command() -> list[str]:
    """Prefer an already installed/cached ccusage executable.

    ``npx ccusage@latest`` performs registry resolution even though ccusage's own
    ``--offline`` flag is present.  Reusing the cache keeps scheduled local
    capture working during registry or DNS outages after the first successful
    install.
    """
    explicit = os.environ.get("AI_USAGE_CCUSAGE_BIN", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"AI_USAGE_CCUSAGE_BIN is not executable: {path}")
        return [str(path)]

    installed = shutil.which("ccusage")
    if installed:
        return [installed]

    npx_root = Path.home() / ".npm" / "_npx"
    candidates = [
        path
        for path in npx_root.glob("*/node_modules/.bin/ccusage")
        if path.is_file() and os.access(path, os.X_OK)
    ]
    if candidates:
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        return [str(latest)]
    return ["npx", "ccusage@latest"]


def model_breakdown_tokens(breakdown: dict[str, Any]) -> int:
    components = (
        safe_int(breakdown.get("inputTokens"))
        + safe_int(breakdown.get("outputTokens"))
        + safe_int(breakdown.get("cacheCreationTokens"))
        + safe_int(breakdown.get("cacheReadTokens"))
    )
    return components or safe_int(breakdown.get("totalTokens"))


def unpriced_models(usage: Any) -> list[str]:
    """Model names with positive tokens but $0 cost (usually missing LiteLLM offline prices)."""
    if not isinstance(usage, dict):
        return []
    found: list[str] = []
    seen: set[str] = set()
    for day in usage.get("daily") or []:
        if not isinstance(day, dict):
            continue
        for breakdown in day.get("modelBreakdowns") or []:
            if not isinstance(breakdown, dict):
                continue
            name = str(breakdown.get("modelName") or "unknown").strip() or "unknown"
            if name in seen:
                continue
            if model_breakdown_tokens(breakdown) > 0 and safe_float(breakdown.get("cost")) == 0.0:
                seen.add(name)
                found.append(name)
    return found


def online_recovers_unpriced_models(unpriced: list[str], online_usage: Any) -> bool:
    """True when online pricing assigns a positive cost to at least one previously unpriced model."""
    if not unpriced or not isinstance(online_usage, dict):
        return False
    costs: dict[str, float] = defaultdict(float)
    for day in online_usage.get("daily") or []:
        if not isinstance(day, dict):
            continue
        for breakdown in day.get("modelBreakdowns") or []:
            if not isinstance(breakdown, dict):
                continue
            name = str(breakdown.get("modelName") or "unknown").strip() or "unknown"
            costs[name] += safe_float(breakdown.get("cost"))
    return any(costs.get(name, 0.0) > 0.0 for name in unpriced)


def litellm_price_cache_path() -> Path:
    override = os.environ.get("AI_USAGE_LITELLM_PRICES_CACHE", "").strip()
    return Path(override).expanduser() if override else LITELLM_PRICES_CACHE


def load_cached_litellm_prices(cache_path: Path | None = None) -> dict[str, Any]:
    path = cache_path or litellm_price_cache_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_litellm_prices_cache(prices: dict[str, Any], cache_path: Path | None = None) -> None:
    path = cache_path or litellm_price_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    machine_fragments.write_bytes_atomic(
        path, json.dumps(prices, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def fetch_litellm_prices(
    *,
    timeout: float = 45.0,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Load LiteLLM official model prices; fall back to local cache on network failure."""
    path = cache_path or litellm_price_cache_path()
    try:
        with urllib.request.urlopen(LITELLM_PRICES_URL, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or not payload:
            raise RuntimeError("LiteLLM price table was empty")
        try:
            save_litellm_prices_cache(payload, path)
        except OSError as exc:
            print(
                f"warning: could not cache LiteLLM prices at {path}: {exc}",
                file=sys.stderr,
            )
        return payload
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        cached = load_cached_litellm_prices(path)
        if cached:
            print(
                f"warning: LiteLLM price fetch failed ({exc}); using cache {path}",
                file=sys.stderr,
            )
            return cached
        raise RuntimeError(f"LiteLLM price fetch failed: {exc}") from exc


def resolve_litellm_model_price(
    prices: dict[str, Any], model_name: str
) -> dict[str, Any] | None:
    name = model_name.strip()
    if not name:
        return None
    candidates = [name, f"anthropic/{name}", f"anthropic.{name}"]
    for key in candidates:
        entry = prices.get(key)
        if isinstance(entry, dict) and entry.get("input_cost_per_token") is not None:
            return entry
    return None


def breakdown_cost_from_litellm(
    breakdown: dict[str, Any], price: dict[str, Any]
) -> float:
    return (
        safe_int(breakdown.get("inputTokens")) * safe_float(price.get("input_cost_per_token"))
        + safe_int(breakdown.get("outputTokens"))
        * safe_float(price.get("output_cost_per_token"))
        + safe_int(breakdown.get("cacheReadTokens"))
        * safe_float(price.get("cache_read_input_token_cost"))
        + safe_int(breakdown.get("cacheCreationTokens"))
        * safe_float(price.get("cache_creation_input_token_cost"))
    )


def pinned_price_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for raw in PINNED_MODEL_PRICES.get("models") or []:
        if not isinstance(raw, dict):
            continue
        canonical = str(raw.get("model") or "").strip()
        if not canonical:
            continue
        entry = dict(raw)
        entry["canonical_model"] = canonical
        for name in [canonical, *(raw.get("aliases") or [])]:
            normalized = str(name or "").strip()
            if normalized:
                index[normalized] = entry
    return index


PINNED_PRICE_INDEX = pinned_price_index()


def breakdown_cost_from_pinned(
    breakdown: dict[str, Any], price: dict[str, Any]
) -> float:
    return (
        safe_int(breakdown.get("inputTokens")) * safe_float(price.get("input"))
        + safe_int(breakdown.get("outputTokens")) * safe_float(price.get("output"))
        + safe_int(breakdown.get("cacheReadTokens"))
        * safe_float(price.get("cache_read"))
        + safe_int(breakdown.get("cacheCreationTokens"))
        * safe_float(price.get("cache_create"))
    )


def sync_usage_cost_fields(payload: dict[str, Any]) -> None:
    """Keep Codex costUSD and Claude totalCost mathematically identical."""
    daily = payload.get("daily") if isinstance(payload.get("daily"), list) else []
    total_cost = 0.0
    for day in daily:
        if not isinstance(day, dict):
            continue
        breakdowns = (
            day.get("modelBreakdowns")
            if isinstance(day.get("modelBreakdowns"), list)
            else []
        )
        if breakdowns:
            day_cost = sum(
                safe_float(item.get("cost"))
                for item in breakdowns
                if isinstance(item, dict)
            )
        else:
            day_cost = safe_float(
                day.get("costUSD")
                if day.get("costUSD") is not None
                else day.get("totalCost")
            )
        day["costUSD"] = day_cost
        day["totalCost"] = day_cost
        total_cost += day_cost
    totals = payload.get("totals")
    if not isinstance(totals, dict):
        totals = {}
        payload["totals"] = totals
    totals["costUSD"] = total_cost
    totals["totalCost"] = total_cost


def ensure_ccusage_model_breakdowns(payload: dict[str, Any]) -> set[int]:
    """Materialize Codex's models map and return the converted day indexes."""
    materialized: set[int] = set()
    for day_index, day in enumerate(payload.get("daily") or []):
        if not isinstance(day, dict) or isinstance(day.get("modelBreakdowns"), list):
            continue
        models = day.get("models")
        if not isinstance(models, dict):
            continue
        breakdowns: list[dict[str, Any]] = []
        for name, raw_values in models.items():
            values = raw_values if isinstance(raw_values, dict) else {}
            breakdowns.append(
                {
                    "modelName": str(name),
                    "inputTokens": safe_int(values.get("inputTokens")),
                    "outputTokens": safe_int(values.get("outputTokens")),
                    "cacheCreationTokens": safe_int(
                        values.get("cacheCreationTokens")
                    ),
                    "cacheReadTokens": safe_int(values.get("cacheReadTokens")),
                    "reasoningOutputTokens": safe_int(
                        values.get("reasoningOutputTokens")
                    ),
                    "totalTokens": safe_int(values.get("totalTokens")),
                    "cost": safe_float(
                        values.get("costUSD")
                        if values.get("costUSD") is not None
                        else values.get("cost")
                    ),
                }
            )
        day["modelBreakdowns"] = breakdowns
        materialized.add(day_index)
    return materialized


def reprice_models_with_pinned_ledger(usage: Any) -> Any:
    """Apply checked-in rates to known models and annotate unresolved legacy cost."""
    if not isinstance(usage, dict):
        return usage
    patched = copy.deepcopy(usage)
    materialized_days = ensure_ccusage_model_breakdowns(patched)
    for day_index, day in enumerate(patched.get("daily") or []):
        if not isinstance(day, dict):
            continue
        original_day_cost = safe_float(
            day.get("costUSD")
            if day.get("costUSD") is not None
            else day.get("totalCost")
        )
        collector_original_cost = safe_float(
            day.get("_collector_original_cost")
            if day.get("_collector_original_cost") is not None
            else original_day_cost
        )
        had_collector_residual = any(
            isinstance(item, dict)
            and str(item.get("modelName") or "") == "Legacy collector residual"
            for item in (day.get("modelBreakdowns") or [])
        )
        original_breakdowns = [
            item
            for item in (day.get("modelBreakdowns") or [])
            if not (
                isinstance(item, dict)
                and str(item.get("modelName") or "") == "Legacy collector residual"
            )
        ]
        day["modelBreakdowns"] = original_breakdowns
        if day_index in materialized_days:
            day["_collector_original_cost"] = original_day_cost
            collector_original_cost = original_day_cost
        active = 0
        pinned = 0
        unresolved = 0
        for breakdown in day.get("modelBreakdowns") or []:
            if not isinstance(breakdown, dict):
                continue
            tokens = model_breakdown_tokens(breakdown)
            if tokens <= 0:
                continue
            active += 1
            name = str(breakdown.get("modelName") or "unknown").strip() or "unknown"
            price = PINNED_PRICE_INDEX.get(name)
            priced_components = (
                safe_int(breakdown.get("inputTokens"))
                + safe_int(breakdown.get("outputTokens"))
                + safe_int(breakdown.get("cacheCreationTokens"))
                + safe_int(breakdown.get("cacheReadTokens"))
            )
            if price is not None and priced_components == tokens:
                breakdown["cost"] = breakdown_cost_from_pinned(breakdown, price)
                breakdown["pricing_version"] = PRICING_VERSION
                breakdown["pricing_provenance"] = "pinned-ledger"
                breakdown["canonical_model"] = price["canonical_model"]
                pinned += 1
            else:
                unresolved += 1
                breakdown["pricing_version"] = "legacy"
                breakdown["pricing_provenance"] = (
                    "collector-legacy"
                    if safe_float(breakdown.get("cost")) > 0
                    else "unpriced"
                )
        attributed_cost = sum(
            safe_float(item.get("cost"))
            for item in day.get("modelBreakdowns") or []
            if isinstance(item, dict)
        )
        # Codex's `models` map has components but no per-model legacy costs. In
        # that one case, keep the remaining collector estimate explicit. Rows
        # that already supplied modelBreakdowns have attributable model costs;
        # restoring their old day total would undo the pinned known-model price.
        legacy_unattributed_cost = (
            max(0.0, collector_original_cost - attributed_cost)
            if day_index in materialized_days or had_collector_residual
            else 0.0
        )
        if unresolved and legacy_unattributed_cost > 1e-9:
            day["modelBreakdowns"].append(
                {
                    "modelName": "Legacy collector residual",
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cacheCreationTokens": 0,
                    "cacheReadTokens": 0,
                    "cost": legacy_unattributed_cost,
                    "pricing_version": "legacy",
                    "pricing_provenance": "collector-residual",
                }
            )
        day["pricingVersion"] = PRICING_VERSION
        day["pricingComplete"] = active == pinned
        day["pricingProvenance"] = (
            "pinned-ledger"
            if active == pinned
            else "mixed-legacy"
            if pinned
            else "collector-legacy"
            if active and not unresolved_models_for_day(day)
            else "unpriced"
        )
    sync_usage_cost_fields(patched)
    totals = patched.get("totals")
    if isinstance(totals, dict):
        days = [day for day in patched.get("daily") or [] if isinstance(day, dict)]
        totals["pricingVersion"] = PRICING_VERSION
        totals["pricingComplete"] = all(bool(day.get("pricingComplete")) for day in days)
    patched["pricing_version"] = PRICING_VERSION
    return patched


def unresolved_models_for_day(day: dict[str, Any]) -> list[str]:
    return [
        str(item.get("modelName") or "unknown")
        for item in day.get("modelBreakdowns") or []
        if isinstance(item, dict)
        and model_breakdown_tokens(item) > 0
        and safe_float(item.get("cost")) <= 0
    ]


def reprice_unpriced_models_with_litellm(
    usage: Any,
    *,
    prices: dict[str, Any] | None = None,
) -> tuple[Any, list[str]]:
    """Fill $0 model breakdown costs from LiteLLM rates. Returns (payload, recovered models)."""
    if not isinstance(usage, dict):
        return usage, []
    missing = unpriced_models(usage)
    if not missing:
        return usage, []
    table = prices if prices is not None else fetch_litellm_prices()
    patched = copy.deepcopy(usage)
    recovered: list[str] = []
    seen_recovered: set[str] = set()
    for day in patched.get("daily") or []:
        if not isinstance(day, dict):
            continue
        day_cost = 0.0
        for breakdown in day.get("modelBreakdowns") or []:
            if not isinstance(breakdown, dict):
                continue
            name = str(breakdown.get("modelName") or "unknown").strip() or "unknown"
            if (
                name in missing
                and model_breakdown_tokens(breakdown) > 0
                and safe_float(breakdown.get("cost")) == 0.0
            ):
                price = resolve_litellm_model_price(table, name)
                if price is not None:
                    cost = breakdown_cost_from_litellm(breakdown, price)
                    if cost > 0.0:
                        breakdown["cost"] = cost
                        if name not in seen_recovered:
                            seen_recovered.add(name)
                            recovered.append(name)
            day_cost += safe_float(breakdown.get("cost"))
        if day.get("modelBreakdowns"):
            day["totalCost"] = day_cost
            day["costUSD"] = day_cost
    sync_usage_cost_fields(patched)
    return patched, recovered


def ccusage_daily_command(
    tool: str,
    timezone: str,
    since: str = "",
    until: str = "",
    *,
    offline: bool,
) -> list[str]:
    command = [
        *ccusage_command(),
        tool,
        "daily",
        "-z",
        timezone,
        "--json",
    ]
    if offline:
        command.append("--offline")
    if since:
        command.extend(["--since", since.replace("-", "")])
    if until:
        command.extend(["--until", until.replace("-", "")])
    return command


def run_ccusage_daily(
    tool: str,
    timezone: str,
    since: str = "",
    until: str = "",
    *,
    offline: bool,
) -> Any:
    return run_json(
        ccusage_daily_command(tool, timezone, since=since, until=until, offline=offline)
    )


def ccusage_daily(tool: str, timezone: str, since: str = "", until: str = "") -> Any:
    """Collect daily usage with resilient LiteLLM pricing.

    1. ``--offline`` first (launchd-safe cached prices)
    2. If any model has tokens but $0 cost, retry once without ``--offline``
    3. If still unpriced (stale ccusage cache / flaky price API), reprice those
       models from the LiteLLM JSON table (cached under ``~/.cache``)
    """
    offline_usage = reprice_models_with_pinned_ledger(
        run_ccusage_daily(tool, timezone, since=since, until=until, offline=True)
    )
    missing = unpriced_models(offline_usage)
    if not missing:
        return offline_usage

    missing_label = ", ".join(missing)
    range_label = f"{since or '...'}..{until or '...'}"
    candidate: Any = offline_usage
    try:
        online_usage = reprice_models_with_pinned_ledger(
            run_ccusage_daily(tool, timezone, since=since, until=until, offline=False)
        )
    except Exception as exc:
        print(
            f"warning: ccusage online pricing retry failed for {tool} "
            f"({missing_label}; {range_label}): {exc}",
            file=sys.stderr,
        )
        online_usage = None

    if online_usage is not None and online_recovers_unpriced_models(missing, online_usage):
        still = unpriced_models(online_usage)
        if not still:
            return online_usage
        print(
            f"warning: ccusage online pricing still missing costs for {tool} "
            f"({', '.join(still)}; {range_label}); trying LiteLLM reprice",
            file=sys.stderr,
        )
        candidate = online_usage
        missing = still
    elif online_usage is not None:
        # Prefer fresher online token totals even when costs are still zero.
        candidate = online_usage
        missing = unpriced_models(online_usage) or missing
        missing_label = ", ".join(missing)
        print(
            f"warning: ccusage offline pricing missing costs for {tool} "
            f"({missing_label}; {range_label}); online retry did not recover prices",
            file=sys.stderr,
        )
    else:
        print(
            f"warning: ccusage offline pricing missing costs for {tool} "
            f"({missing_label}; {range_label}); trying LiteLLM reprice",
            file=sys.stderr,
        )

    try:
        patched, recovered = reprice_unpriced_models_with_litellm(candidate)
    except Exception as exc:
        print(
            f"warning: LiteLLM reprice failed for {tool} "
            f"({missing_label}; {range_label}): {exc}",
            file=sys.stderr,
        )
        return candidate

    if recovered:
        still = unpriced_models(patched)
        if still:
            print(
                f"warning: LiteLLM reprice recovered {', '.join(recovered)} for {tool} "
                f"but still missing {', '.join(still)}; {range_label}",
                file=sys.stderr,
            )
        else:
            print(
                f"info: LiteLLM reprice recovered costs for {tool} "
                f"({', '.join(recovered)}; {range_label})",
                file=sys.stderr,
            )
        return reprice_models_with_pinned_ledger(patched)

    print(
        f"warning: no pricing source recovered costs for {tool} "
        f"({missing_label}; {range_label})",
        file=sys.stderr,
    )
    return reprice_models_with_pinned_ledger(candidate)


def filter_points_since(points: list[dict[str, Any]], since: str) -> list[dict[str, Any]]:
    if not since:
        return points
    return [p for p in points if isinstance(p, dict) and str(p.get("date") or "") >= since]


def load_usage_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


SOURCE_STATUS_NAMES = ("codex", "claude", "cursor", "oneapi")
SOURCE_STATUS_ERRORS = {
    "codex": {
        "codex_unavailable",
        "local_fragments_stale",
        "local_fragments_unavailable",
        "local_fragment_timestamp_invalid",
    },
    "claude": {
        "claude_unavailable",
        "local_fragments_stale",
        "local_fragments_unavailable",
        "local_fragment_timestamp_invalid",
    },
    "cursor": {"cursor_incomplete", "cursor_unavailable"},
    "oneapi": {
        "oneapi_incomplete",
        "oneapi_refresh_failed",
        "oneapi_state_unavailable",
        "oneapi_unavailable",
    },
}
SOURCE_STATUS_DEFAULT_ERROR = {
    "codex": "codex_unavailable",
    "claude": "claude_unavailable",
    "cursor": "cursor_unavailable",
    "oneapi": "oneapi_unavailable",
}
PUBLIC_ONEAPI_FIELDS = {
    "accounting_version",
    "available",
    "captured_at",
    "complete",
    "daily_timeline",
    "excluded",
    "history",
    "included_request_count",
    "legacy_comate",
    "legacy_history_discarded",
    "ownership_rule",
    "ownership_rule_version",
    "pages",
    "pagination",
    "rate_limit_retries",
    "raw_totals",
    "request_count",
    "stale",
    "scope",
    "snapshot_id",
    "timezone",
    "totals",
    "unclassified",
    "window",
}


def public_source_error(source: str, value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if raw in SOURCE_STATUS_ERRORS.get(source, set()):
        return raw
    return SOURCE_STATUS_DEFAULT_ERROR.get(source, "source_unavailable")


def public_iso_timestamp(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    try:
        dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return raw


def public_iso_date(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    try:
        dt.date.fromisoformat(raw)
    except ValueError:
        return ""
    return raw


def public_source_status(value: Any) -> dict[str, dict[str, Any]]:
    source_status = value if isinstance(value, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for source in SOURCE_STATUS_NAMES:
        raw = (
            source_status.get(source)
            if isinstance(source_status.get(source), dict)
            else {}
        )
        status = str(raw.get("status") or "")
        if status not in {"fresh", "stale", "failed"}:
            status = "failed"
        lag_days = raw.get("lag_days")
        if isinstance(lag_days, bool) or not isinstance(lag_days, int) or lag_days < 0:
            lag_days = None
        result[source] = {
            "status": status,
            "attempted_at": public_iso_timestamp(raw.get("attempted_at")),
            "last_success_at": public_iso_timestamp(raw.get("last_success_at")),
            "window_end": public_iso_date(raw.get("window_end")),
            "lag_days": lag_days,
            "error": public_source_error(source, raw.get("error")),
        }
    return result


def public_oneapi_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key in PUBLIC_ONEAPI_FIELDS
    }


def source_lag_days(today: str, window_end: str) -> int | None:
    try:
        today_date = dt.date.fromisoformat(today)
        window_date = dt.date.fromisoformat(window_end)
    except (TypeError, ValueError):
        return None
    return max(0, (today_date - window_date).days)


def local_fragment_source_attempt(
    fragments: list[dict[str, Any]],
    timezone: str,
    today: str,
    *,
    attempted: bool,
) -> dict[str, Any]:
    if not fragments:
        return {
            "attempted": attempted,
            "fresh": False,
            "has_data": False,
            "attempted_at": "",
            "last_success_at": "",
            "window_end": "",
            "error": "local_fragments_unavailable",
        }

    tz = resolve_tz(timezone)
    collected: list[tuple[dt.datetime, str, str]] = []
    missing: list[str] = []
    for fragment in fragments:
        machine_id = str(fragment.get("machine_id") or "unknown")
        raw = str(fragment.get("collected_at") or "").strip()
        if not raw:
            missing.append(machine_id)
            continue
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            missing.append(machine_id)
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        collected.append((parsed.astimezone(tz), raw, machine_id))

    if missing or len(collected) != len(fragments):
        return {
            "attempted": attempted,
            "fresh": False,
            "has_data": True,
            "attempted_at": "",
            "last_success_at": "",
            "window_end": "",
            "error": "local_fragment_timestamp_invalid",
        }

    oldest_time, oldest_raw, _oldest_machine = min(collected, key=lambda item: item[0])
    window_end = oldest_time.date().isoformat()
    fresh = all(item[0].date().isoformat() == today for item in collected)
    return {
        "attempted": attempted,
        "fresh": fresh,
        "has_data": True,
        "attempted_at": oldest_raw,
        "last_success_at": oldest_raw,
        "window_end": window_end,
        "error": "" if fresh else "local_fragments_stale",
    }


def reconcile_source_status(
    prior_payload: dict[str, Any],
    attempts: dict[str, dict[str, Any]],
    *,
    attempted_at: str,
    today: str,
) -> dict[str, dict[str, Any]]:
    prior_status = (
        prior_payload.get("source_status")
        if isinstance(prior_payload.get("source_status"), dict)
        else {}
    )
    result: dict[str, dict[str, Any]] = {}
    for source in SOURCE_STATUS_NAMES:
        prior = (
            prior_status.get(source)
            if isinstance(prior_status.get(source), dict)
            else {}
        )
        attempt = attempts.get(source) if isinstance(attempts.get(source), dict) else {}
        attempted = bool(attempt.get("attempted"))
        fresh = bool(attempt.get("fresh"))
        prior_last_success = str(prior.get("last_success_at") or "")
        observed_last_success = str(attempt.get("last_success_at") or "")

        if fresh:
            status = "fresh"
        elif attempted:
            status = (
                "stale"
                if bool(attempt.get("has_data")) or bool(prior_last_success)
                else "failed"
            )
        else:
            if observed_last_success or prior_last_success:
                status = "stale"
            else:
                prior_value = str(prior.get("status") or "")
                status = (
                    prior_value
                    if prior_value in {"fresh", "stale", "failed"}
                    else "failed"
                )

        effective_attempted_at = (
            attempted_at
            if attempted
            else str(
                attempt.get("attempted_at")
                or prior.get("attempted_at")
                or ""
            )
        )
        last_success_at = (
            observed_last_success or attempted_at
            if fresh
            else observed_last_success or prior_last_success
        )
        window_end = (
            str(attempt.get("window_end") or "")
            if fresh or observed_last_success
            else str(prior.get("window_end") or "")
        )
        error = (
            ""
            if fresh
            else public_source_error(
                source,
                attempt.get("error") or prior.get("error") or "",
            )
        )
        result[source] = {
            "status": status,
            "attempted_at": effective_attempted_at,
            "last_success_at": last_success_at,
            "window_end": window_end,
            "lag_days": source_lag_days(today, window_end),
            "error": error,
        }
    return result


def cursor_mutable_from(payload: dict[str, Any], today: str) -> str:
    raw = str(payload.get("cursor_mutable_from") or "").strip()
    if not raw:
        # Legacy reports froze each previous snapshot too early.  Reopen the
        # complete API history once; the next successful write persists today.
        return ""
    try:
        value = dt.date.fromisoformat(raw).isoformat()
    except ValueError:
        raise ValueError(f"invalid cursor_mutable_from: {raw}")
    return value if value <= today else today


def advance_cursor_mutable_from(
    prior: str,
    today: str,
    api_complete: bool,
    reconciliation_stats: dict[str, Any],
) -> str:
    if not api_complete:
        return prior
    regressions = sorted(
        str(value)
        for value in reconciliation_stats.get("regression_dates") or []
        if str(value) <= today
    )
    safety_window_start = machine_fragments.previous_day(today)
    return min([safety_window_start, *regressions])


def validate_fragment_timezones(
    fragments: list[dict[str, Any]], expected_timezone: str
) -> None:
    for fragment in fragments:
        machine_id = str(fragment.get("machine_id") or "unknown")
        timezone = str(fragment.get("timezone") or "").strip()
        if timezone and timezone != expected_timezone:
            raise ValueError(
                f"machine fragment timezone mismatch for {machine_id}: "
                f"{timezone} != {expected_timezone}"
            )


def persist_local_model_metadata(
    fragment_file: Path,
    codex_points: list[dict[str, Any]],
    claude_points: list[dict[str, Any]],
    legacy_comate: dict[str, Any],
    *,
    model_seed_complete: bool,
) -> None:
    """Backfill fact/model metadata and deterministically revalue complete snapshots."""
    fragment = machine_fragments.load_machine_fragment(
        fragment_file.parent, fragment_file.stem
    )
    if fragment is None:
        raise FileNotFoundError(f"machine fragment disappeared: {fragment_file}")

    points_by_tool = {
        "codex": {
            str(point.get("date")): point
            for point in codex_points
            if isinstance(point, dict) and point.get("date")
        },
        "claude": {
            str(point.get("date")): point
            for point in claude_points
            if isinstance(point, dict) and point.get("date")
        },
    }
    pricing_regressions: set[str] = set()
    for row in fragment.get("daily") or []:
        if not isinstance(row, dict):
            continue
        date_key = str(row.get("date") or "")
        for prefix in ("codex", "claude"):
            point = points_by_tool[prefix].get(date_key)
            if not isinstance(point, dict):
                row[f"{prefix}_models"] = models_with_remainder(
                    row.get(f"{prefix}_models"),
                    total_tokens=row.get(f"{prefix}_tokens"),
                    total_cost=row.get(f"{prefix}_cost"),
                    label="Legacy unknown",
                )
                continue

            same_fact_total = safe_int(point.get("tokens")) == safe_int(
                row.get(f"{prefix}_tokens")
            )
            snapshot_complete = bool(point.get("snapshot_complete")) and same_fact_total
            incoming_cost = safe_float(point.get("cost"))
            existing_cost = safe_float(row.get(f"{prefix}_cost"))
            pricing_complete = bool(point.get("pricing_complete"))
            unpriced_decrease = (
                incoming_cost + max(1e-9, abs(existing_cost) * 1e-9) < existing_cost
                and not pricing_complete
            )
            if not snapshot_complete or unpriced_decrease:
                row[f"{prefix}_models"] = models_with_remainder(
                    row.get(f"{prefix}_models"),
                    total_tokens=row.get(f"{prefix}_tokens"),
                    total_cost=row.get(f"{prefix}_cost"),
                    label="Legacy unknown",
                )
                row[f"{prefix}_snapshot_complete"] = False
                row[f"{prefix}_pricing_complete"] = False
                row[f"{prefix}_pricing_provenance"] = "legacy-preserved"
                if unpriced_decrease:
                    pricing_regressions.add(date_key)
                continue

            target_cost = incoming_cost
            try:
                reconciled_models = models_with_remainder(
                    point.get("models"),
                    total_tokens=row.get(f"{prefix}_tokens"),
                    total_cost=target_cost,
                    label="Legacy unknown",
                )
            except ValueError:
                row[f"{prefix}_snapshot_complete"] = False
                pricing_regressions.add(date_key)
                continue
            row[f"{prefix}_models"] = reconciled_models
            row[f"{prefix}_cost"] = target_cost
            for field in TOOL_TOKEN_FIELDS[prefix]:
                row[f"{prefix}_{field}"] = safe_int(point.get(field))
            row[f"{prefix}_snapshot_complete"] = True
            row[f"{prefix}_pricing_version"] = str(
                point.get("pricing_version") or "legacy"
            )
            row[f"{prefix}_pricing_complete"] = pricing_complete
            row[f"{prefix}_pricing_provenance"] = str(
                point.get("pricing_provenance") or "legacy"
            )

    if model_seed_complete:
        fragment["model_breakdown_version"] = MODEL_BREAKDOWN_VERSION
    fragment["schema_version"] = PUBLIC_SCHEMA_VERSION
    fragment["pricing_version"] = PRICING_VERSION
    if pricing_regressions:
        current_boundary = str(fragment.get("mutable_from") or "")
        fragment["mutable_from"] = min(
            [value for value in [current_boundary, *pricing_regressions] if value]
        )
        stats = (
            fragment.get("last_append_stats")
            if isinstance(fragment.get("last_append_stats"), dict)
            else {}
        )
        stats["pricing_regression_dates"] = sorted(pricing_regressions)
        fragment["last_append_stats"] = stats
    fragment["legacy_comate"] = legacy_comate
    fragment["tools"] = ["codex", "claude"]
    machine_fragments.write_json_atomic(fragment_file, fragment)


def collect_local_machine(
    home: Path,
    timezone: str,
    tmp_dir: Path,
    *,
    machine_id: str,
    machines_path: Path,
    force_reseed: bool = False,
) -> dict[str, Any]:
    """Collect and atomically persist only machine-local sources.

    This path intentionally has no GitHub, Cursor, npm, or build dependency, so
    a network outage cannot prevent the local high-water snapshot from landing.
    """
    today = local_today(timezone)
    existing_frag = (
        None
        if force_reseed
        else machine_fragments.load_machine_fragment(machines_path, machine_id)
    )
    first_seed = force_reseed or machine_fragments.is_first_seed(existing_frag)
    needs_model_seed = (
        safe_int((existing_frag or {}).get("model_breakdown_version"))
        < MODEL_BREAKDOWN_VERSION
    )
    since = "" if first_seed else machine_fragments.append_range_start(existing_frag, today)
    until = today

    if first_seed:
        codex_usage = ccusage_daily("codex", timezone)
        claude_usage = ccusage_daily("claude", timezone)
    else:
        codex_usage = ccusage_daily(
            "codex", timezone, since=since or today, until=until
        )
        claude_usage = ccusage_daily(
            "claude", timezone, since=since or today, until=until
        )

    model_codex_usage = codex_usage
    model_claude_usage = claude_usage
    model_seed_complete = first_seed or not needs_model_seed
    if needs_model_seed and not first_seed:
        model_seed_complete = False
        try:
            model_codex_usage = ccusage_daily("codex", timezone)
            model_claude_usage = ccusage_daily("claude", timezone)
            model_seed_complete = True
        except Exception as exc:
            print(
                f"warning: full model backfill failed; keeping incremental model rows: {exc}",
                file=sys.stderr,
            )

    local_summary = local_record_summary(home, tmp_dir)
    comate = comate_usage.parse_comate(home, timezone)
    codex_rows = usage_daily_rows(codex_usage)
    claude_rows = usage_daily_rows(claude_usage)
    codex_first, codex_last = daily_range(codex_rows, codex=True)
    claude_first, claude_last = daily_range(claude_rows)
    codex_totals = usage_totals(codex_usage)
    claude_totals = usage_totals(claude_usage)

    codex_summary = {
        "tool": "Codex",
        "history": fmt_range(codex_first, codex_last),
        "cost": safe_float(codex_totals.get("costUSD")),
        "total_tokens": safe_int(codex_totals.get("totalTokens")),
    }
    claude_summary = {
        "tool": "Claude Code",
        "history": fmt_range(claude_first, claude_last),
        "cost": safe_float(claude_totals.get("totalCost")),
        "total_tokens": safe_int(claude_totals.get("totalTokens")),
    }
    codex_pts_all = codex_daily_points(usage_daily_rows(model_codex_usage))
    claude_pts_all = claude_daily_points(usage_daily_rows(model_claude_usage))
    codex_pts = codex_daily_points(codex_rows)
    claude_pts = claude_daily_points(claude_rows)
    if since:
        codex_pts = filter_points_since(codex_pts, since)
        claude_pts = filter_points_since(claude_pts, since)

    local_daily = build_local_machine_daily(codex_pts, claude_pts)
    fragment_file, fragment_meta = machine_fragments.write_machine_fragment_append(
        machines_path,
        machine_id,
        timezone,
        local_daily,
        TOOL_TOKEN_FIELDS,
        safe_int,
        safe_float,
        today=today,
        hostname=socket.gethostname(),
        force_reseed=force_reseed,
    )
    persist_local_model_metadata(
        fragment_file,
        codex_pts_all,
        claude_pts_all,
        comate,
        model_seed_complete=model_seed_complete,
    )
    local_summary["machine_fragment"] = str(fragment_file)
    local_summary["fragment_mode"] = fragment_meta.get("mode")
    local_summary["fragment_stats"] = fragment_meta.get("stats")
    local_summary["fragment_first_seed"] = fragment_meta.get("first_seed")
    local_summary["collect_since"] = since or "(full seed)"
    local_summary["collect_until"] = until
    return {
        "today": today,
        "comate": comate,
        "local_summary": local_summary,
        "fragment_meta": fragment_meta,
        "codex_summary": codex_summary,
        "claude_summary": claude_summary,
    }


def recover_codex_cache_transaction(
    *,
    machines_dir: Path,
    machine_id: str,
    usage_json_path: Path,
) -> dict[str, Any]:
    """Recover an interrupted cache migration before any Git synchronization."""
    return machine_fragments.recover_codex_cache_transaction(
        machines_dir, machine_id, usage_json_path
    )


def backfill_codex_cache_report(
    *,
    machines_dir: Path,
    machine_id: str,
    usage_json_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backfill one machine's frozen Codex cache field and the merged report.

    This intentionally avoids all live collectors. Historical cache is derived
    from each frozen row's persisted total/input/output snapshot, so totals,
    costs, other tools, dates, and account-level Cursor data remain untouched.
    """
    if not machine_id.strip():
        raise ValueError("--machine-id is required for Codex cache backfill")

    mid = machine_fragments.sanitize_machine_id(machine_id)
    expected_fragment_path = machine_fragments.fragment_path(machines_dir, mid)
    transaction_paths = [expected_fragment_path.resolve(), usage_json_path.resolve()]
    journal_path = machine_fragments.json_transaction_journal_path(transaction_paths)
    if dry_run and journal_path.is_file():
        raise ValueError(
            "pending Codex cache transaction must be recovered before --dry-run: "
            f"{journal_path}"
        )
    if not dry_run:
        machine_fragments.recover_json_transaction(journal_path, transaction_paths)
    fragments = machine_fragments.load_machine_fragments_strict(machines_dir)
    matches = [
        candidate
        for candidate in fragments
        if machine_fragments.sanitize_machine_id(str(candidate.get("machine_id") or "")) == mid
    ]
    if not matches:
        raise FileNotFoundError(f"machine fragment not found: {expected_fragment_path}")
    if len(matches) != 1:
        raise ValueError(f"expected one fragment for {mid}, found {len(matches)}")

    fragment = dict(matches[0])
    fragment_path = Path(str(fragment.pop("_path")))
    if fragment_path != expected_fragment_path:
        raise ValueError(
            f"machine fragment path mismatch: expected={expected_fragment_path}, found={fragment_path}"
        )

    updated_fragment_daily, fragment_stats = machine_fragments.backfill_codex_cache_daily(
        list(fragment.get("daily") or [])
    )

    matched = 0
    for candidate in fragments:
        candidate_mid = machine_fragments.sanitize_machine_id(
            str(candidate.get("machine_id") or Path(str(candidate.get("_path") or "")).stem)
        )
        if candidate_mid == mid:
            candidate["daily"] = updated_fragment_daily
            matched += 1
    if matched != 1:
        raise ValueError(f"expected one fragment for {mid}, found {matched}")

    if not usage_json_path.is_file():
        raise FileNotFoundError(f"merged usage JSON not found: {usage_json_path}")
    try:
        usage_payload = json.loads(usage_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read merged usage JSON: {usage_json_path}: {exc}") from exc
    if not isinstance(usage_payload, dict) or not isinstance(usage_payload.get("daily"), list):
        raise ValueError(f"merged usage JSON has no daily list: {usage_json_path}")

    updated_usage_daily, usage_stats = machine_fragments.backfill_merged_usage_codex_cache_daily(
        list(usage_payload.get("daily") or []), fragments
    )

    if not dry_run:
        fragment_payload = dict(fragment)
        fragment_payload["daily"] = updated_fragment_daily
        updated_usage_payload = dict(usage_payload)
        updated_usage_payload["daily"] = updated_usage_daily
        machine_fragments.write_json_transaction(
            [
                (fragment_path, fragment_payload),
                (usage_json_path, updated_usage_payload),
            ]
        )

    return {
        "machine_id": mid,
        "dry_run": dry_run,
        "fragment_path": str(fragment_path),
        "usage_json_path": str(usage_json_path),
        "fragment": fragment_stats,
        "usage": usage_stats,
    }


def collect_usage(
    home: Path,
    timezone: str,
    tmp_dir: Path,
    cursor_page_size: int,
    *,
    machine_id: str | None = None,
    machines_dir: Path | None = None,
    merge: bool = True,
    merge_only: bool = False,
    usage_json_path: Path | None = None,
    force_reseed: bool = False,
    oneapi_days: int = 5,
    oneapi_cache_path: Path | None = None,
) -> dict[str, Any]:
    mid = machine_fragments.resolve_machine_id(machine_id)
    machines_path = Path(machines_dir) if machines_dir else DEFAULT_MACHINES_DIR
    prior_usage = usage_json_path or (REPO_ROOT / "public" / "usage.json")
    prior_payload = load_usage_payload(prior_usage)
    cursor_fallback_note = ""
    today = local_today(timezone)
    attempted_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    prior_cursor_mutable_from = cursor_mutable_from(prior_payload, today)
    next_cursor_mutable_from = prior_cursor_mutable_from
    cursor_reconciliation_stats: dict[str, Any] = {}
    fragment_meta: dict[str, Any] = {}
    cursor_refresh_error = ""
    cursor_status_error = ""
    status_fragments: list[dict[str, Any]] = []

    if merge_only:
        cursor_usage = fetch_cursor_usage(home, cursor_page_size, timezone)
        cursor_api_complete = bool(
            cursor_usage.get("available") and cursor_usage.get("complete")
        )
        cursor_refresh_error = str(cursor_usage.get("error") or "")
        if not cursor_api_complete and not cursor_refresh_error:
            cursor_refresh_error = "Cursor API response was incomplete"
        if not cursor_api_complete:
            if cursor_refresh_error:
                print(f"WARN: Cursor refresh failed: {cursor_refresh_error}", file=sys.stderr)
            cursor_status_error = (
                "cursor_unavailable"
                if not cursor_usage.get("available")
                else "cursor_incomplete"
            )
        cursor_pts = resolve_cursor_points(cursor_usage, prior_usage)
        if not cursor_usage.get("available") and cursor_pts:
            cursor_fallback_note = "Cursor API unavailable; kept prior usage.json Cursor series."
            cursor_usage = {
                **cursor_usage,
                "available": True,
                "history": {
                    "first": cursor_pts[0]["date"],
                    "last": cursor_pts[-1]["date"],
                    "cost": sum(safe_float(p.get("cost")) for p in cursor_pts),
                    "total_tokens": sum(safe_int(p.get("tokens")) for p in cursor_pts),
                },
                "daily_timeline": cursor_pts,
                "error": cursor_fallback_note,
            }
        fragments = machine_fragments.load_machine_fragments_strict(machines_path)
        status_fragments = fragments
        validate_fragment_timezones(fragments, timezone)
        local_merged, machine_ids = machine_fragments.merge_local_fragments(
            fragments,
            empty_daily_row,
            TOOL_TOKEN_FIELDS,
            safe_int,
            safe_float,
        )
        # Load prior usage rows so cursor history freeze can see existing cursor_* values
        prior_rows = [
            row for row in (prior_payload.get("daily") or []) if isinstance(row, dict)
        ]
        # Overlay prior cursor onto local_merged dates before applying API points
        prior_by = {str(r["date"]): r for r in prior_rows if r.get("date")}
        for row in local_merged:
            prev = prior_by.get(str(row.get("date")))
            if not prev:
                continue
            overlay_prior_cursor_row(row, prev)
        for date_key, prev in prior_by.items():
            if date_key in {str(r.get("date")) for r in local_merged}:
                continue
            if safe_int(prev.get("cursor_tokens")) or safe_float(prev.get("cursor_cost")):
                row = empty_daily_row(date_key)
                overlay_prior_cursor_row(row, prev)
                local_merged.append(row)
        local_merged.sort(key=lambda r: str(r.get("date") or ""))

        daily_rows = machine_fragments.apply_cursor_points(
            local_merged,
            cursor_pts,
            empty_daily_row,
            apply_tool_point,
            safe_int,
            safe_float,
            today=today,
            cursor_mutable_from=prior_cursor_mutable_from,
            freeze_cursor_history=True,
            reconciliation_stats=cursor_reconciliation_stats,
        )
        next_cursor_mutable_from = advance_cursor_mutable_from(
            prior_cursor_mutable_from,
            today,
            cursor_api_complete,
            cursor_reconciliation_stats,
        )
        current_fragment = next(
            (
                fragment
                for fragment in fragments
                if machine_fragments.sanitize_machine_id(
                    str(fragment.get("machine_id") or "")
                )
                == mid
            ),
            {},
        )
        comate = (
            current_fragment.get("legacy_comate")
            if isinstance(current_fragment.get("legacy_comate"), dict)
            else {}
        )
        local_summary = {"merge_only": True}
        codex_summary = {"tool": "Codex", "history": "from fragments", "cost": 0, "total_tokens": 0}
        claude_summary = {"tool": "Claude Code", "history": "from fragments", "cost": 0, "total_tokens": 0}
    else:
        local = collect_local_machine(
            home,
            timezone,
            tmp_dir,
            machine_id=mid,
            machines_path=machines_path,
            force_reseed=force_reseed,
        )
        today = str(local["today"])
        local_summary = local["local_summary"]
        fragment_meta = local["fragment_meta"]
        codex_summary = local["codex_summary"]
        claude_summary = local["claude_summary"]
        comate = local["comate"]
        cursor_usage = fetch_cursor_usage(home, cursor_page_size, timezone)
        cursor_api_complete = bool(
            cursor_usage.get("available") and cursor_usage.get("complete")
        )
        cursor_refresh_error = str(cursor_usage.get("error") or "")
        if not cursor_api_complete and not cursor_refresh_error:
            cursor_refresh_error = "Cursor API response was incomplete"
        if not cursor_api_complete:
            if cursor_refresh_error:
                print(f"WARN: Cursor refresh failed: {cursor_refresh_error}", file=sys.stderr)
            cursor_status_error = (
                "cursor_unavailable"
                if not cursor_usage.get("available")
                else "cursor_incomplete"
            )
        cursor_pts = resolve_cursor_points(cursor_usage, prior_usage)
        if not cursor_usage.get("available") and cursor_pts:
            cursor_fallback_note = "Cursor API unavailable; kept prior usage.json Cursor series."
            cursor_usage = {
                **cursor_usage,
                "available": True,
                "history": {
                    "first": cursor_pts[0]["date"],
                    "last": cursor_pts[-1]["date"],
                    "cost": sum(safe_float(p.get("cost")) for p in cursor_pts),
                    "total_tokens": sum(safe_int(p.get("tokens")) for p in cursor_pts),
                },
                "daily_timeline": cursor_pts,
                "error": cursor_fallback_note,
            }

        # Base rows for cursor freeze: merged fragments + prior cursor columns
        if merge:
            fragments = machine_fragments.load_machine_fragments_strict(machines_path)
            status_fragments = fragments
            validate_fragment_timezones(fragments, timezone)
            local_merged, machine_ids = machine_fragments.merge_local_fragments(
                fragments,
                empty_daily_row,
                TOOL_TOKEN_FIELDS,
                safe_int,
                safe_float,
            )
        else:
            machine_ids = [mid]
            seeded = machine_fragments.load_machine_fragment(machines_path, mid) or {}
            status_fragments = [seeded] if seeded else []
            local_merged = [r for r in (seeded.get("daily") or []) if isinstance(r, dict)]

        prior_by: dict[str, dict[str, Any]] = {
            str(row["date"]): row
            for row in (prior_payload.get("daily") or [])
            if isinstance(row, dict) and row.get("date")
        }
        merged_dates = {str(r.get("date")) for r in local_merged}
        for row in local_merged:
            prev = prior_by.get(str(row.get("date")))
            if not prev:
                continue
            overlay_prior_cursor_row(row, prev)
        for date_key, prev in prior_by.items():
            if date_key in merged_dates:
                continue
            if safe_int(prev.get("cursor_tokens")) or safe_float(prev.get("cursor_cost")):
                row = empty_daily_row(date_key)
                overlay_prior_cursor_row(row, prev)
                local_merged.append(row)
        local_merged.sort(key=lambda r: str(r.get("date") or ""))

        daily_rows = machine_fragments.apply_cursor_points(
            local_merged,
            cursor_pts,
            empty_daily_row,
            apply_tool_point,
            safe_int,
            safe_float,
            today=today,
            cursor_mutable_from=prior_cursor_mutable_from,
            freeze_cursor_history=True,
            reconciliation_stats=cursor_reconciliation_stats,
        )
        next_cursor_mutable_from = advance_cursor_mutable_from(
            prior_cursor_mutable_from,
            today,
            cursor_api_complete,
            cursor_reconciliation_stats,
        )

    # One API is the residual gateway source. Historical local Comate records are
    # retained under it only when no gateway record exists for the same date.
    oneapi_state_path = os.environ.get(
        "ONEAPI_STATE_PATH",
        str(DEFAULT_ONEAPI_STATE_PATH),
    )
    prior_rows = [
        row for row in (prior_payload.get("daily") or []) if isinstance(row, dict)
    ]
    prior_oneapi = (
        prior_payload.get("oneapi")
        if isinstance(prior_payload.get("oneapi"), dict)
        else {}
    )
    prior_oneapi_compatible = (
        safe_int(prior_oneapi.get("accounting_version"))
        == oneapi_usage.ACCOUNTING_VERSION
    )
    legacy_oneapi_present = any(
        safe_int(row.get("oneapi_tokens"))
        or safe_float(row.get("oneapi_cost"))
        or safe_int(row.get("oneapi_requests"))
        for row in prior_rows
    )
    durable_oneapi_rows = prior_rows if prior_oneapi_compatible else []
    oneapi_data: dict[str, Any] = {}
    oneapi_cache_loaded = False
    oneapi_refresh_complete = False
    oneapi_refresh_error = ""
    oneapi_status_error = ""

    # If a pre-collected One API snapshot was provided, load it directly
    if oneapi_cache_path and oneapi_cache_path.exists():
        try:
            cached = json.loads(oneapi_cache_path.read_text())
            oneapi_data = validate_oneapi_snapshot(
                cached,
                timezone=timezone,
                today=today,
                calendar_days=oneapi_days,
            )
            print(f"INFO: loaded pre-cached One API snapshot from {oneapi_cache_path}", file=sys.stderr)
            oneapi_refresh_complete = True
            oneapi_data["stale"] = False
            oneapi_data["state_path"] = oneapi_state_path
            oneapi_data["legacy_history_discarded"] = bool(
                legacy_oneapi_present and not prior_oneapi_compatible
            )
            oneapi_cache_loaded = True
        except Exception as exc:
            print(f"WARN: failed to load cached One API snapshot; falling back to live fetch: {exc}", file=sys.stderr)
            oneapi_cache_path = None

    if not oneapi_cache_loaded and Path(oneapi_state_path).exists():
        try:
            oneapi_data = oneapi_usage.collect_oneapi(
                timezone=timezone,
                state_path=oneapi_state_path,
                days=oneapi_days,
            )
            oneapi_data = validate_oneapi_snapshot(
                oneapi_data,
                timezone=timezone,
                today=today,
                calendar_days=oneapi_days,
            )
            oneapi_refresh_complete = bool(
                oneapi_data.get("available") and oneapi_data.get("complete")
            )
            if not oneapi_refresh_complete:
                oneapi_status_error = "oneapi_incomplete"
            oneapi_data["stale"] = False
            oneapi_data["state_path"] = oneapi_state_path
            oneapi_data["legacy_history_discarded"] = bool(
                legacy_oneapi_present and not prior_oneapi_compatible
            )
        except Exception as exc:
            note = f"One API refresh failed; kept prior compatible series: {exc}"
            if legacy_oneapi_present and not prior_oneapi_compatible:
                note += " Legacy pre-v2 One API values were not retained."
            print(f"WARN: {note}", file=sys.stderr)
            oneapi_refresh_error = note
            oneapi_status_error = "oneapi_refresh_failed"
            oneapi_data = {
                **prior_oneapi,
                "available": False,
                "complete": False,
                "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
                "stale": True,
                "note": note,
                "state_path": oneapi_state_path,
            }
    elif not oneapi_cache_loaded:
        note = (
            f"One API state not found at {oneapi_state_path}; "
            f"kept prior compatible series. "
            f"Run chrome-use state save {oneapi_state_path} after UUAP login."
        )
        if legacy_oneapi_present and not prior_oneapi_compatible:
            note += " Legacy pre-v2 One API values were not retained."
        print(f"WARN: {note}", file=sys.stderr)
        oneapi_refresh_error = note
        oneapi_status_error = "oneapi_state_unavailable"
        oneapi_data = {
            **prior_oneapi,
            "available": False,
            "complete": False,
            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
            "stale": True,
            "note": note,
            "state_path": oneapi_state_path,
        }

    if not comate and isinstance(prior_oneapi.get("legacy_comate"), dict):
        comate = {
            "history": prior_oneapi["legacy_comate"].get("history") or {},
            "total_tokens": safe_int(
                prior_oneapi["legacy_comate"].get("total_tokens")
            ),
            "daily_timeline": [
                point
                for point in (prior_oneapi.get("daily_timeline") or [])
                if isinstance(point, dict) and point.get("source") == "comate-local"
            ],
        }
    oneapi_data = reconcile_oneapi_payload(prior_oneapi, oneapi_data, comate)

    prior_status_map = (
        prior_payload.get("source_status")
        if isinstance(prior_payload.get("source_status"), dict)
        else {}
    )
    prior_oneapi_status = (
        prior_status_map.get("oneapi")
        if isinstance(prior_status_map.get("oneapi"), dict)
        else {}
    )
    prior_oneapi_last_success = str(
        prior_oneapi_status.get("last_success_at") or ""
    )
    prior_oneapi_has_durable_data = prior_oneapi_compatible and (
        bool(prior_oneapi.get("daily_timeline"))
        or any(
            safe_int(row.get("oneapi_tokens"))
            or safe_float(row.get("oneapi_cost"))
            or safe_int(row.get("oneapi_requests"))
            for row in durable_oneapi_rows
        )
    )
    local_source_attempt = local_fragment_source_attempt(
        status_fragments,
        timezone,
        today,
        attempted=not merge_only,
    )
    source_status = reconcile_source_status(
        prior_payload,
        {
            "codex": dict(local_source_attempt),
            "claude": dict(local_source_attempt),
            "cursor": {
                "attempted": True,
                "fresh": cursor_api_complete,
                "has_data": bool(cursor_pts),
                "window_end": today if cursor_api_complete else "",
                "error": cursor_status_error,
            },
            "oneapi": {
                "attempted": True,
                "fresh": oneapi_refresh_complete,
                "has_data": prior_oneapi_has_durable_data
                or bool(prior_oneapi_last_success),
                "window_end": str(
                    (
                        oneapi_data.get("window")
                        if isinstance(oneapi_data.get("window"), dict)
                        else {}
                    ).get("end")
                    or ""
                )
                if oneapi_refresh_complete
                else "",
                "error": oneapi_status_error,
            },
        },
        attempted_at=attempted_at,
        today=today,
    )

    daily_rows = reconcile_oneapi_rows(
        daily_rows,
        durable_oneapi_rows,
        oneapi_data,
    )
    for row in daily_rows:
        for prefix in ("codex", "claude", "cursor", "oneapi"):
            row[f"{prefix}_models"] = models_with_remainder(
                row.get(f"{prefix}_models"),
                total_tokens=row.get(f"{prefix}_tokens"),
                total_cost=row.get(f"{prefix}_cost"),
                label="Legacy unknown",
            )

    # Recompute per-tool summary totals from merged daily for UI cards
    def sum_prefix(prefix: str) -> tuple[int, float, str]:
        tokens = sum(safe_int(r.get(f"{prefix}_tokens")) for r in daily_rows)
        cost = sum(safe_float(r.get(f"{prefix}_cost")) for r in daily_rows)
        dates = [str(r.get("date")) for r in daily_rows if safe_int(r.get(f"{prefix}_tokens")) or safe_float(r.get(f"{prefix}_cost"))]
        hist = fmt_range(dates[0], dates[-1]) if dates else "unknown"
        return tokens, cost, hist

    for summary, prefix in (
        (codex_summary, "codex"),
        (claude_summary, "claude"),
    ):
        tokens, cost, hist = sum_prefix(prefix)
        summary["total_tokens"] = tokens
        summary["cost"] = cost
        if summary.get("history") in ("from fragments", "unknown", "") or merge:
            summary["history"] = hist

    tokens_c, cost_c, hist_c = sum_prefix("cursor")
    if cursor_usage.get("available") and tokens_c:
        cursor_history = cursor_usage["history"] if isinstance(cursor_usage.get("history"), dict) else {}
        api_hist = fmt_range(str(cursor_history.get("first", ""))[:10], str(cursor_history.get("last", ""))[:10])
        cursor_summary = {
            "tool": "Cursor",
            "history": hist_c or api_hist,
            "cost": cost_c,
            "total_tokens": tokens_c,
        }
    else:
        cursor_summary = {
            "tool": "Cursor",
            "history": hist_c,
            "cost": cost_c,
            "total_tokens": tokens_c,
        }

    tokens_o, cost_o, hist_o = sum_prefix("oneapi")
    oneapi_summary = {
        "tool": "One API",
        "history": hist_o,
        "cost": cost_o,
        "total_tokens": tokens_o,
        "requests": sum(safe_int(row.get("oneapi_requests")) for row in daily_rows),
    }

    for summary, prefix in (
        (codex_summary, "codex"),
        (claude_summary, "claude"),
        (cursor_summary, "cursor"),
        (oneapi_summary, "oneapi"),
    ):
        summary["models"] = merge_models(
            *(row.get(f"{prefix}_models") for row in daily_rows)
        )

    span_first, span_last = "", ""
    if daily_rows:
        span_first, span_last = daily_rows[0]["date"], daily_rows[-1]["date"]

    generated_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    report_snapshot_id = stable_snapshot_id(
        {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "pricing_version": PRICING_VERSION,
            "machines": machine_ids,
            "cursor_snapshot_id": cursor_usage.get("snapshot_id"),
            "oneapi_snapshot_id": oneapi_data.get("snapshot_id"),
            "daily": daily_rows,
        }
    )
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "pricing_version": PRICING_VERSION,
        "captured_at": generated_at,
        "snapshot_id": report_snapshot_id,
        "generated_at": generated_at,
        "timezone": timezone,
        "machine_id": mid,
        "machines": machine_ids,
        "machines_dir": str(machines_path),
        "tools": [codex_summary, claude_summary, cursor_summary, oneapi_summary],
        "local_summary": local_summary,
        "fragment_meta": fragment_meta,
        "cursor": cursor_usage,
        "oneapi": oneapi_data,
        "daily_timeline_rows": daily_rows,
        "timeline_meta": {
            "span": fmt_range(span_first, span_last),
        },
        "cursor_fallback_note": cursor_fallback_note,
        "cursor_mutable_from": next_cursor_mutable_from,
        "cursor_reconciliation": cursor_reconciliation_stats,
        "source_status": source_status,
    }


CARD_BREAKDOWNS: dict[str, list[tuple[str, str]]] = {
    "Codex": [
        ("input", "Input"),
        ("cache_read", "Cache read"),
        ("output", "Output (incl. reasoning)"),
        ("reasoning", "↳ Reasoning subset"),
    ],
    "Claude Code": [
        ("input", "Input"),
        ("cache_create", "Cache create"),
        ("cache_read", "Cache read"),
        ("output", "Output"),
    ],
    "Cursor": [
        ("input", "Input"),
        ("cache_write", "Cache write"),
        ("cache_read", "Cache read"),
        ("output", "Output"),
    ],
    "One API": [
        ("input", "Input"),
        ("cache_read", "Cache read"),
        ("cache_write", "Cache write"),
        ("output", "Output"),
    ],
}


def render_html(data: dict[str, Any]) -> str:
    tools = data["tools"]
    colors = {
        "Codex": "#2563eb",
        "Claude Code": "#c2410c",
        "Cursor": "#0d9488",
        "One API": "#7c3aed",
    }
    daily_rows = data.get("daily_timeline_rows") if isinstance(data.get("daily_timeline_rows"), list) else []
    meta = data.get("timeline_meta") if isinstance(data.get("timeline_meta"), dict) else {}
    span = str(meta.get("span") or "unknown")
    payload_b64 = base64.b64encode(json.dumps(daily_rows, ensure_ascii=False).encode("utf-8")).decode("ascii")

    vendor_path = SCRIPTS_DIR / "vendor" / "echarts.min.js"
    if vendor_path.is_file():
        echarts_tag = "<script>\n" + vendor_path.read_text(encoding="utf-8") + "\n</script>"
    else:
        echarts_tag = (
            '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js" '
            'crossorigin="anonymous"></script>'
        )

    series_map = {
        "Codex": ("codex", "codex_tokens", "codex_cost"),
        "Claude Code": ("claude", "claude_tokens", "claude_cost"),
        "Cursor": ("cursor", "cursor_tokens", "cursor_cost"),
        "One API": ("oneapi", "oneapi_tokens", "oneapi_cost"),
    }
    cards_parts: list[str] = []
    for tool in tools:
        name = str(tool.get("tool") or "")
        slug, kt, kc = series_map.get(name, ("", "", ""))
        color = colors.get(name, "#334155")
        toks_sum = sum(safe_int(r.get(kt)) for r in daily_rows if isinstance(r, dict))
        cost_sum = sum(safe_float(r.get(kc)) for r in daily_rows if isinstance(r, dict))
        breakdown_lines: list[str] = []
        for field_key, field_label in CARD_BREAKDOWNS.get(name, []):
            data_key = f"{slug}_{field_key}"
            field_sum = sum(safe_int(r.get(data_key)) for r in daily_rows if isinstance(r, dict))
            breakdown_lines.append(
                f'<div class="breakdown-row"><span>{esc(field_label)}</span>'
                f'<strong id="kpi-{esc(slug)}-{esc(field_key)}">{esc(fmt_compact(field_sum))}</strong></div>'
            )
        breakdown_html = "".join(breakdown_lines)
        cards_parts.append(
            f'<section class="card" style="--accent:{color}">'
            f"<h2>{esc(name)}</h2>"
            f'<div class="big" id="kpi-{esc(slug)}-cost">{esc(fmt_usd(cost_sum))}</div>'
            f'<div class="sub"><span id="kpi-{esc(slug)}-tokens">{esc(fmt_compact(toks_sum))}</span> tokens total</div>'
            f'<div class="token-breakdown">{breakdown_html}</div>'
            "</section>"
        )
    cards_html = "".join(cards_parts)
    total_cache_all = sum(
        safe_int(r.get("codex_cache_read"))
        + safe_int(r.get("claude_cache_create"))
        + safe_int(r.get("claude_cache_read"))
        + safe_int(r.get("cursor_cache_write"))
        + safe_int(r.get("cursor_cache_read"))
        + safe_int(r.get("oneapi_cache_write"))
        + safe_int(r.get("oneapi_cache_read"))
        for r in daily_rows
        if isinstance(r, dict)
    )
    total_toks_all = sum(
        safe_int(r.get("codex_tokens"))
        + safe_int(r.get("claude_tokens"))
        + safe_int(r.get("cursor_tokens"))
        + safe_int(r.get("oneapi_tokens"))
        for r in daily_rows
        if isinstance(r, dict)
    )
    total_cost_all = sum(
        safe_float(r.get("codex_cost"))
        + safe_float(r.get("claude_cost"))
        + safe_float(r.get("cursor_cost"))
        + safe_float(r.get("oneapi_cost"))
        for r in daily_rows
        if isinstance(r, dict)
    )
    if daily_rows:
        nwin = len(daily_rows)
        w0, w1 = daily_rows[0]["date"], daily_rows[-1]["date"]
        initial_kpi_window = esc(f"Totals for visible range: {w0} — {w1} · {nwin} day(s)")
    else:
        initial_kpi_window = esc("Totals for visible range: —")
    grand_initial_tok = esc(fmt_compact(total_toks_all))
    grand_initial_cost = esc(fmt_usd(total_cost_all))
    grand_initial_cache = esc(fmt_compact(total_cache_all))
    date_min = esc(str(daily_rows[0]["date"])) if daily_rows else ""
    date_max = esc(str(daily_rows[-1]["date"])) if daily_rows else ""
    app_js = r"""
(function () {
  const el = document.getElementById('main');
  const b64El = document.getElementById('usage-b64');
  const b64 = b64El ? b64El.textContent.trim() : '';
  let RAW = [];
  try {
    RAW = JSON.parse(atob(b64));
  } catch (e) {
    el.innerHTML = '<div id="empty-msg">Could not parse embedded data</div>';
    return;
  }
  if (!RAW.length) {
    el.innerHTML = '<div id="empty-msg">No daily rows (check ccusage + Cursor API)</div>';
    return;
  }
  const dates = RAW.map(r => r.date);
  const cT = RAW.map(r => r.codex_tokens || 0);
  const clT = RAW.map(r => r.claude_tokens || 0);
  const cuT = RAW.map(r => r.cursor_tokens || 0);
  const oT = RAW.map(r => r.oneapi_tokens || 0);
  const cC = RAW.map(r => Number(r.codex_cost) || 0);
  const clC = RAW.map(r => Number(r.claude_cost) || 0);
  const cuC = RAW.map(r => Number(r.cursor_cost) || 0);
  const oC = RAW.map(r => Number(r.oneapi_cost) || 0);
  const cCache = RAW.map(r => Number(r.codex_cache_read) || 0);
  const clCache = RAW.map(r => (Number(r.claude_cache_create) || 0) + (Number(r.claude_cache_read) || 0));
  const cuCache = RAW.map(r => (Number(r.cursor_cache_write) || 0) + (Number(r.cursor_cache_read) || 0));
  const oCache = RAW.map(r => (Number(r.oneapi_cache_write) || 0) + (Number(r.oneapi_cache_read) || 0));
  const breakdownFields = {
    codex: [
      ['input', 'Input', r => Number(r.codex_input) || 0],
      ['cache_read', 'Cache read', r => Number(r.codex_cache_read) || 0],
      ['output', 'Output (incl. reasoning)', r => Number(r.codex_output) || 0],
      ['reasoning', '↳ Reasoning subset', r => Number(r.codex_reasoning) || 0],
    ],
    claude: [
      ['input', 'Input', r => Number(r.claude_input) || 0],
      ['cache_create', 'Cache create', r => Number(r.claude_cache_create) || 0],
      ['cache_read', 'Cache read', r => Number(r.claude_cache_read) || 0],
      ['output', 'Output', r => Number(r.claude_output) || 0],
    ],
    cursor: [
      ['input', 'Input', r => Number(r.cursor_input) || 0],
      ['cache_write', 'Cache write', r => Number(r.cursor_cache_write) || 0],
      ['cache_read', 'Cache read', r => Number(r.cursor_cache_read) || 0],
      ['output', 'Output', r => Number(r.cursor_output) || 0],
    ],
    oneapi: [
      ['input', 'Input', r => Number(r.oneapi_input) || 0],
      ['cache_read', 'Cache read', r => Number(r.oneapi_cache_read) || 0],
      ['cache_write', 'Cache write', r => Number(r.oneapi_cache_write) || 0],
      ['output', 'Output', r => Number(r.oneapi_output) || 0],
    ],
  };

  const totalDaySpend = cC.map((v, i) => (Number(v) || 0) + (Number(clC[i]) || 0) + (Number(cuC[i]) || 0) + (Number(oC[i]) || 0));

  const COL = { codex: '#2563eb', claude: '#c2410c', cursor: '#0d9488', oneapi: '#7c3aed' };
  const chart = echarts.init(el, null, { renderer: 'canvas' });

  function mixChannel(c, t) {
    return Math.round(c + (255 - c) * t);
  }

  function stackSegStyle(hex, cap) {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    const topRgb = mixChannel(r, 0.15) + ',' + mixChannel(g, 0.15) + ',' + mixChannel(b, 0.15);
    const rad = 6;
    let br;
    if (cap === 'top') br = [rad, rad, 0, 0];
    else if (cap === 'bot') br = [0, 0, rad, rad];
    else br = [0, 0, 0, 0];
    return {
      color: {
        type: 'linear',
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: 'rgb(' + topRgb + ')' },
          { offset: 1, color: hex },
        ],
      },
      borderColor: 'rgba(15,23,42,0.06)',
      borderWidth: 1,
      borderRadius: br,
    };
  }

  const stackEmphasis = {
    focus: 'series',
    blurScope: 'coordinateSystem',
    itemStyle: { shadowBlur: 10, shadowColor: 'rgba(15,23,42,0.08)', shadowOffsetY: 1 },
  };

  const stackBar = {
    type: 'bar',
    stack: 'tokens',
    barCategoryGap: '42%',
    barMaxWidth: 34,
    emphasis: stackEmphasis,
  };

  const spendLine = {
    type: 'line',
    smooth: 0.35,
    symbol: 'circle',
    symbolSize: dates.length > 72 ? 0 : 5,
    showSymbol: dates.length <= 72,
    lineStyle: { width: 2.4, color: '#334155' },
    itemStyle: { color: '#334155', borderWidth: 0 },
    areaStyle: {
      color: {
        type: 'linear',
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: 'rgba(51,65,85,0.2)' },
          { offset: 1, color: 'rgba(51,65,85,0.02)' },
        ],
      },
    },
    emphasis: { focus: 'series', lineStyle: { width: 3 } },
  };

  function sliceRange(startPct, endPct) {
    const n = dates.length;
    if (n === 0) return { i0: 0, i1: -1 };
    // Round so (pct -> index) matches ECharts and our (index -> pct) math under JS floats.
    let i0 = Math.round((startPct / 100) * (n - 1));
    let i1 = Math.round((endPct / 100) * (n - 1));
    i0 = Math.max(0, Math.min(n - 1, i0));
    i1 = Math.max(0, Math.min(n - 1, i1));
    if (i1 < i0) { const t = i0; i0 = i1; i1 = t; }
    return { i0, i1 };
  }

  function dispatchZoom(start, end) {
    chart.dispatchAction({ type: 'dataZoom', start, end, xAxisIndex: [0, 1, 2] });
  }

  /** One category: start===end percent collapses the window and breaks bar layout; use a hair-wide span. */
  function zoomSingleCategoryIndex(k) {
    const n = dates.length;
    if (!n) return;
    if (n === 1) {
      dispatchZoom(0, 100);
      return;
    }
    const kk = Math.max(0, Math.min(n - 1, Math.round(k)));
    const den = n - 1;
    const c = (kk / den) * 100;
    const eps = 0.05;
    let start;
    let end;
    if (kk === 0) {
      start = 0;
      end = Math.min(100, eps);
    } else if (kk === n - 1) {
      end = 100;
      start = Math.max(0, 100 - eps);
    } else {
      start = c;
      end = Math.min(100, c + eps);
    }
    dispatchZoom(start, end);
  }

  function setWindowByIndex(i0, i1) {
    const n = dates.length;
    if (!n) return;
    let a = Math.max(0, Math.min(n - 1, i0));
    let b = Math.max(0, Math.min(n - 1, i1));
    if (a > b) { const t = a; a = b; b = t; }
    if (a === b) {
      zoomSingleCategoryIndex(a);
      return;
    }
    chart.dispatchAction({
      type: 'dataZoom',
      startValue: dates[a],
      endValue: dates[b],
      xAxisIndex: [0, 1, 2],
    });
  }

  function presetDays(d) {
    const n = dates.length;
    if (!n) return;
    const i1 = n - 1;
    const i0 = Math.max(0, n - Math.min(d, n));
    setWindowByIndex(i0, i1);
  }

  function applyDateInputs() {
    const s = document.getElementById('range-start');
    const e = document.getElementById('range-end');
    if (!s || !e || !s.value || !e.value) return;
    const dMin = dates[0];
    const dMax = dates[dates.length - 1];
    function clampIso(v) {
      if (v < dMin) return dMin;
      if (v > dMax) return dMax;
      return v;
    }
    let v0 = clampIso(s.value);
    let v1 = clampIso(e.value);
    if (v0 > v1) { const t = v0; v0 = v1; v1 = t; }
    if (v0 === v1) {
      const k = dates.indexOf(v0);
      if (k >= 0) zoomSingleCategoryIndex(k);
      return;
    }
    chart.dispatchAction({ type: 'dataZoom', startValue: v0, endValue: v1, xAxisIndex: [0, 1, 2] });
  }

  function pan(deltaPct) {
    const { start, end } = getDataZoomRange();
    const w = end - start;
    let ns = start + deltaPct * w;
    let ne = end + deltaPct * w;
    if (ns < 0) { ne -= ns; ns = 0; }
    if (ne > 100) { ns -= ne - 100; ne = 100; }
    dispatchZoom(ns, ne);
  }

  function sumSlice(arr, i0, i1) {
    let s = 0;
    for (let i = i0; i <= i1; i++) s += arr[i] || 0;
    return s;
  }

  function fmtTok(x) {
    if (x >= 1e9) return (x / 1e9).toFixed(2) + 'B';
    if (x >= 1e6) return (x / 1e6).toFixed(2) + 'M';
    if (x >= 1e3) return (x / 1e3).toFixed(2) + 'K';
    return String(Math.round(x));
  }

  function fmtUsd(x) {
    return '$' + x.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function getDataZoomRange() {
    const opt = chart.getOption();
    const dzList = opt.dataZoom || [];
    function pick(pred) {
      for (let i = 0; i < dzList.length; i++) {
        const z = dzList[i];
        if (z && pred(z) && z.start != null) {
          return { start: z.start, end: z.end != null ? z.end : 100 };
        }
      }
      return null;
    }
    return pick((z) => z.type === 'slider') || pick(() => true) || { start: 0, end: 100 };
  }

  /** Visible category indices from slider (startValue/endValue are indices for category axis). */
  function sliderIndexRange() {
    const dzList = chart.getOption().dataZoom || [];
    for (let i = 0; i < dzList.length; i++) {
      const z = dzList[i];
      if (!z || z.type !== 'slider' || z.start == null) continue;
      const a = z.startValue;
      const b = z.endValue;
      if (a != null && b != null && typeof a === 'number' && typeof b === 'number') {
        let i0 = Math.round(Math.min(a, b));
        let i1 = Math.round(Math.max(a, b));
        i0 = Math.max(0, Math.min(dates.length - 1, i0));
        i1 = Math.max(0, Math.min(dates.length - 1, i1));
        return { i0, i1 };
      }
      if (typeof a === 'string' && typeof b === 'string' && dates.indexOf(a) >= 0 && dates.indexOf(b) >= 0) {
        let i0 = dates.indexOf(a);
        let i1 = dates.indexOf(b);
        if (i0 > i1) { const t = i0; i0 = i1; i1 = t; }
        return { i0, i1 };
      }
    }
    const { start, end } = getDataZoomRange();
    return sliceRange(start, end);
  }

  function indicesFromZoomPart(p) {
    if (!p) return null;
    const a = p.startValue;
    const b = p.endValue;
    if (a != null && b != null) {
      if (typeof a === 'number' && typeof b === 'number') {
        let i0 = Math.round(Math.min(a, b));
        let i1 = Math.round(Math.max(a, b));
        i0 = Math.max(0, Math.min(dates.length - 1, i0));
        i1 = Math.max(0, Math.min(dates.length - 1, i1));
        return { i0, i1 };
      }
      if (typeof a === 'string' && typeof b === 'string') {
        let i0 = dates.indexOf(a);
        let i1 = dates.indexOf(b);
        if (i0 < 0 || i1 < 0) return null;
        if (i0 > i1) { const t = i0; i0 = i1; i1 = t; }
        return { i0, i1 };
      }
    }
    if (p.start != null && p.end != null) {
      return sliceRange(p.start, p.end);
    }
    return null;
  }

  /** same tick as dataZoom, getOption() may still be the *previous* window */
  function indicesFromDataZoomEvent(evt) {
    if (!evt) return null;
    if (evt.batch && evt.batch.length) {
      for (let i = evt.batch.length - 1; i >= 0; i--) {
        const r = indicesFromZoomPart(evt.batch[i]);
        if (r) return r;
      }
      return null;
    }
    return indicesFromZoomPart(evt);
  }

  function syncPickersToIndices(i0, i1) {
    const s = document.getElementById('range-start');
    const e = document.getElementById('range-end');
    if (!s || !e) return;
    if (document.activeElement === s || document.activeElement === e) return;
    const ds = dates[i0];
    const de = dates[i1];
    if (ds != null) s.value = ds;
    if (de != null) e.value = de;
  }

  function renderTotals(i0, i1) {
    const d0 = dates[i0];
    const d1 = dates[i1];
    const winEl = document.getElementById('kpi-window-label');
    if (winEl) {
      winEl.textContent =
        d0 && d1
          ? 'Totals for visible range: ' + d0 + ' — ' + d1 + ' · ' + (i1 - i0 + 1) + ' day(s)'
          : 'Totals for visible range: —';
    }
    function setKpi(slug, tokArr, costArr) {
      const costNode = document.getElementById('kpi-' + slug + '-cost');
      const tokNode = document.getElementById('kpi-' + slug + '-tokens');
      if (costNode) costNode.textContent = fmtUsd(sumSlice(costArr, i0, i1));
      if (tokNode) tokNode.textContent = fmtTok(sumSlice(tokArr, i0, i1));
    }
    function setBreakdown(slug) {
      const fields = breakdownFields[slug] || [];
      for (const [key, , getter] of fields) {
        const node = document.getElementById('kpi-' + slug + '-' + key);
        if (!node) continue;
        let total = 0;
        for (let i = i0; i <= i1; i++) total += getter(RAW[i]);
        node.textContent = fmtTok(total);
      }
    }
    setKpi('codex', cT, cC);
    setKpi('claude', clT, clC);
    setKpi('cursor', cuT, cuC);
    setKpi('oneapi', oT, oC);
    setBreakdown('codex');
    setBreakdown('claude');
    setBreakdown('cursor');
    setBreakdown('oneapi');
    const allTok = sumSlice(cT, i0, i1) + sumSlice(clT, i0, i1) + sumSlice(cuT, i0, i1) + sumSlice(oT, i0, i1);
    const allCost = sumSlice(cC, i0, i1) + sumSlice(clC, i0, i1) + sumSlice(cuC, i0, i1) + sumSlice(oC, i0, i1);
    const allCache = sumSlice(cCache, i0, i1) + sumSlice(clCache, i0, i1) + sumSlice(cuCache, i0, i1) + sumSlice(oCache, i0, i1);
    const allTokEl = document.getElementById('kpi-all-tokens');
    const allCostEl = document.getElementById('kpi-all-cost');
    const allCacheEl = document.getElementById('kpi-all-cache');
    if (allTokEl) allTokEl.textContent = fmtTok(allTok);
    if (allCostEl) allCostEl.textContent = fmtUsd(allCost);
    if (allCacheEl) allCacheEl.textContent = fmtTok(allCache);
  }

  function updateWindowTotals(evt) {
    const fromEvt = indicesFromDataZoomEvent(evt);
    if (fromEvt) {
      renderTotals(fromEvt.i0, fromEvt.i1);
      syncPickersToIndices(fromEvt.i0, fromEvt.i1);
      return;
    }
    const r = sliderIndexRange();
    renderTotals(r.i0, r.i1);
    if (evt) {
      requestAnimationFrame(() => {
        if (!chart || chart.isDisposed()) return;
        const r2 = sliderIndexRange();
        renderTotals(r2.i0, r2.i1);
        syncPickersToIndices(r2.i0, r2.i1);
      });
    } else {
      syncPickersToIndices(r.i0, r.i1);
    }
  }

  const rotate = dates.length > 36 ? 32 : 0;
  const xAxisCommon = {
    type: 'category',
    boundaryGap: true,
    data: dates,
    axisLabel: { rotate: rotate, fontSize: 11, color: '#64748b' },
    axisLine: { lineStyle: { color: '#e2e8f0' } },
    axisTick: { alignWithLabel: true, lineStyle: { color: '#e2e8f0' } },
  };

  const option = {
    animation: true,
    textStyle: { fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif' },
    axisPointer: { link: [{ xAxisIndex: [0, 1, 2] }], snap: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(15, 23, 42, 0.05)' } },
      borderRadius: 10,
      padding: [12, 14],
      backgroundColor: 'rgba(255,255,255,0.98)',
      borderColor: '#e2e8f0',
      borderWidth: 1,
      textStyle: { color: '#0f172a', fontSize: 12 },
      extraCssText: 'box-shadow:0 12px 40px rgba(15,23,42,0.08);',
      formatter: function (params) {
        if (!params || !params.length) return '';
        const idx = params[0].dataIndex;
        const row = RAW[idx] || {};
        const date = dates[idx] || '';
        const lines = ['<strong>' + date + '</strong>'];
        function toolSection(title, slug, total, cost, fields) {
          lines.push('<div style="margin-top:8px"><span style="color:#64748b">' + title + '</span></div>');
          lines.push('Total ' + fmtTok(total) + ' · ' + fmtUsd(cost));
          for (const [, label, getter] of fields) {
            const value = getter(row);
            if (value) lines.push(label + ': ' + fmtTok(value));
          }
        }
        toolSection('Codex', 'codex', row.codex_tokens || 0, Number(row.codex_cost) || 0, breakdownFields.codex);
        toolSection('Claude Code', 'claude', row.claude_tokens || 0, Number(row.claude_cost) || 0, breakdownFields.claude);
        toolSection('Cursor', 'cursor', row.cursor_tokens || 0, Number(row.cursor_cost) || 0, breakdownFields.cursor);
        toolSection('One API', 'oneapi', row.oneapi_tokens || 0, Number(row.oneapi_cost) || 0, breakdownFields.oneapi);
        lines.push('<div style="margin-top:8px">Daily spend (all tools): <strong>' + fmtUsd(totalDaySpend[idx] || 0) + '</strong></div>');
        return lines.join('<br/>');
      },
    },
    legend: {
      data: ['Codex', 'Claude', 'Cursor', 'One API', 'Codex cache', 'Claude cache', 'Cursor cache', 'One API cache', 'Daily spend (all tools)'],
      type: 'scroll',
      top: 6,
      left: 'center',
      itemGap: 14,
      itemWidth: 10,
      itemHeight: 10,
      icon: 'circle',
      textStyle: { color: '#64748b', fontSize: 11 },
    },
    grid: [
      { left: 56, right: 48, top: 88, height: '22%' },
      { left: 56, right: 48, top: '40%', height: '22%' },
      { left: 56, right: 48, top: '68%', height: '18%' },
    ],
    xAxis: [
      { ...xAxisCommon, gridIndex: 0, axisLabel: { ...xAxisCommon.axisLabel, margin: 10 } },
      { ...xAxisCommon, gridIndex: 1, axisLabel: { show: false } },
      { ...xAxisCommon, gridIndex: 2, axisLabel: { ...xAxisCommon.axisLabel, margin: 10 } },
    ],
    yAxis: [
      {
        type: 'value',
        gridIndex: 0,
        name: 'Total tokens / day',
        nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
        axisLabel: { formatter: (v) => fmtTok(v), color: '#64748b' },
        min: 0,
        splitLine: { show: true, lineStyle: { color: 'rgba(148,163,184,0.2)', type: [4, 4] } },
      },
      {
        type: 'value',
        gridIndex: 1,
        name: 'Cache tokens / day',
        nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
        axisLabel: { formatter: (v) => fmtTok(v), color: '#64748b' },
        min: 0,
        splitLine: { show: true, lineStyle: { color: 'rgba(148,163,184,0.16)', type: [4, 4] } },
      },
      {
        type: 'value',
        gridIndex: 2,
        name: 'Total spend / day',
        nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
        axisLabel: { formatter: (v) => '$' + v, color: '#64748b' },
        min: 0,
        splitLine: { show: true, lineStyle: { color: 'rgba(148,163,184,0.14)', type: [4, 4] } },
      },
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1, 2],
        filterMode: 'none',
        minSpan: 0,
        minValueSpan: 1,
        maxValueSpan: dates.length,
        zoomOnMouseWheel: false,
        moveOnMouseMove: false,
        moveOnMouseWheel: false,
      },
      {
        type: 'slider',
        xAxisIndex: [0, 1, 2],
        filterMode: 'none',
        minSpan: 0,
        minValueSpan: 1,
        maxValueSpan: dates.length,
        height: 36,
        bottom: 20,
        showDetail: true,
        textStyle: { fontSize: 12, color: '#475569' },
        borderColor: '#e2e8f0',
        backgroundColor: '#f8fafc',
        fillerColor: 'rgba(13, 148, 136, 0.14)',
        handleStyle: { color: '#fff', borderColor: '#0f766e', borderWidth: 2 },
        dataBackground: {
          lineStyle: { color: '#cbd5e1', width: 0.5 },
          areaStyle: { color: 'rgba(148, 163, 184, 0.1)' },
        },
        selectedDataBackground: {
          lineStyle: { color: '#0d9488', width: 0.8 },
          areaStyle: { color: 'rgba(13, 148, 136, 0.07)' },
        },
      },
    ],
    series: [
      {
        name: 'Codex',
        ...stackBar,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: stackSegStyle(COL.codex, 'bot'),
        data: cT,
      },
      {
        name: 'Claude',
        ...stackBar,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: stackSegStyle(COL.claude, 'mid'),
        data: clT,
      },
      {
        name: 'Cursor',
        ...stackBar,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: stackSegStyle(COL.cursor, 'mid'),
        data: cuT,
      },
      {
        name: 'One API',
        ...stackBar,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: stackSegStyle(COL.oneapi, 'top'),
        data: oT,
      },
      {
        name: 'Codex cache',
        ...stackBar,
        stack: 'cache',
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: stackSegStyle(COL.codex, 'bot'),
        data: cCache,
      },
      {
        name: 'Claude cache',
        ...stackBar,
        stack: 'cache',
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: stackSegStyle(COL.claude, 'mid'),
        data: clCache,
      },
      {
        name: 'Cursor cache',
        ...stackBar,
        stack: 'cache',
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: stackSegStyle(COL.cursor, 'mid'),
        data: cuCache,
      },
      {
        name: 'One API cache',
        ...stackBar,
        stack: 'cache',
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: stackSegStyle(COL.oneapi, 'top'),
        data: oCache,
      },
      {
        name: 'Daily spend (all tools)',
        ...spendLine,
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: totalDaySpend,
      },
    ],
  };

  chart.setOption(option);

  document.querySelectorAll('[data-preset]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const d = btn.getAttribute('data-preset');
      if (d === 'all') dispatchZoom(0, 100);
      else presetDays(parseInt(d, 10));
    });
  });
  document.getElementById('apply-dates')?.addEventListener('click', applyDateInputs);
  document.getElementById('pan-left')?.addEventListener('click', () => pan(-0.2));
  document.getElementById('pan-right')?.addEventListener('click', () => pan(0.2));

  chart.on('dataZoom', (evt) => updateWindowTotals(evt));
  updateWindowTotals();
  window.addEventListener('resize', () => chart.resize());
})();
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI coding usage</title>
<style>
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  margin: 0;
  background: linear-gradient(165deg, #f8fafc 0%, #f1f5f9 45%, #eef2f6 100%);
  color: #0f172a;
  font-family: "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}}
.page {{ max-width: 1200px; margin: 0 auto; padding: 40px 28px 56px; }}
.masthead {{ margin-bottom: 28px; }}
.kicker {{
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: #64748b;
  margin: 0 0 10px;
}}
h1 {{
  margin: 0;
  font-size: clamp(28px, 3.6vw, 34px);
  font-weight: 650;
  letter-spacing: -0.03em;
  line-height: 1.12;
  color: #0f172a;
}}
.meta {{
  margin-top: 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 20px 28px;
  font-size: 12px;
  color: #94a3b8;
}}
.meta span {{ white-space: nowrap; }}
.meta b {{ color: #475569; font-weight: 600; }}
.masthead .cards-context {{
  margin: 16px 0 12px;
}}
.cards {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}}
@media (max-width: 820px) {{ .cards {{ grid-template-columns: 1fr; }} }}
.card {{
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 20px 22px;
  box-shadow: 0 4px 24px rgba(15,23,42,0.045);
  border-left: 4px solid var(--accent, #334155);
}}
.card h2 {{ font-size: 15px; font-weight: 650; margin: 0 0 6px; color: #0f172a; }}
.big {{ font-size: 26px; font-weight: 680; letter-spacing: -0.02em; margin-bottom: 6px; color: #0f172a; }}
.sub {{ font-size: 13px; color: #475569; line-height: 1.4; }}
.token-breakdown {{
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #eef2f6;
  display: grid;
  gap: 6px;
}}
.breakdown-row {{
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #64748b;
}}
.breakdown-row strong {{
  color: #334155;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}}
.methodology {{
  margin: 0 0 18px;
  padding: 14px 18px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  font-size: 13px;
  color: #475569;
  line-height: 1.55;
}}
.methodology strong {{ color: #334155; }}
.controls {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px 20px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 14px 18px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(15,23,42,0.04);
}}
.presets, .daterange, .pan-btns {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
.ctl-label {{
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #94a3b8;
  margin-right: 4px;
}}
.btn {{
  appearance: none;
  border: 1px solid #e2e8f0;
  background: #fff;
  color: #334155;
  font-size: 13px;
  font-weight: 550;
  padding: 8px 14px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, transform 0.1s;
}}
.btn:hover {{ background: #f8fafc; border-color: #cbd5e1; }}
.btn:active {{ transform: scale(0.98); }}
.btn.primary {{
  background: #0f172a;
  color: #fff;
  border-color: #0f172a;
}}
.btn.primary:hover {{ background: #1e293b; border-color: #1e293b; }}
input[type="date"] {{
  font-family: inherit;
  font-size: 13px;
  padding: 7px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  color: #334155;
  background: #fff;
}}
.chart-wrap {{
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 8px 4px 6px;
  margin-bottom: 16px;
  box-shadow: 0 8px 30px rgba(15,23,42,0.06);
}}
#main {{ width: 100%; height: min(92vh, 980px); min-height: 620px; }}
.cards-context {{
  margin: 0 0 14px;
  font-size: 14px;
  font-weight: 600;
  color: #334155;
  letter-spacing: -0.01em;
}}
.kpi-grand {{
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 18px;
  margin: 0 0 18px;
  padding: 14px 18px;
  background: linear-gradient(135deg, rgba(255,255,255,0.95) 0%, rgba(248,250,252,0.92) 100%);
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 2px 14px rgba(15,23,42,0.05);
}}
.kpi-grand-title {{
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #94a3b8;
  width: 100%;
}}
.kpi-grand-metrics {{
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 14px;
  font-size: 15px;
  color: #475569;
}}
.kpi-grand-metrics b {{
  font-weight: 680;
  font-size: 20px;
  letter-spacing: -0.02em;
  color: #0f172a;
}}
.kpi-grand-dot {{ color: #cbd5e1; font-weight: 400; }}
#empty-msg {{ padding: 48px; text-align: center; color: #94a3b8; }}
</style>
</head>
<body>
<script type="text/plain" id="usage-b64">{payload_b64}</script>
{echarts_tag}
<main class="page">
<header class="masthead">
  <p class="kicker">Usage report</p>
  <h1>AI coding spend &amp; tokens</h1>
  <div class="meta">
    <span><b>Generated</b> {esc(data["generated_at"])}</span>
    <span><b>Timezone</b> {esc(data["timezone"])}</span>
    <span><b>Span</b> {esc(span)}</span>
  </div>
  <p class="cards-context" id="kpi-window-label">{initial_kpi_window}</p>
</header>
<div class="kpi-grand" role="group" aria-label="All tools combined for visible range">
  <span class="kpi-grand-title">All tools combined</span>
  <span class="kpi-grand-metrics">
    <span><b id="kpi-all-tokens">{grand_initial_tok}</b> tokens total</span>
    <span class="kpi-grand-dot">·</span>
    <span><b id="kpi-all-cache">{grand_initial_cache}</b> cache tokens</span>
    <span class="kpi-grand-dot">·</span>
    <span><b id="kpi-all-cost">{grand_initial_cost}</b> spend total</span>
  </span>
</div>
<p class="methodology">
  <strong>Token breakdown:</strong> cards and tooltips show input, cache, and output tokens per tool.
  Codex cache = cache read; Claude cache = create + read; Cursor cache = write + read.
  <strong>Cost estimate:</strong> Codex/Claude use the checked-in <code>{esc(PRICING_VERSION)}</code> price ledger;
  unresolved models retain an explicit legacy collector value. Cursor costs come from the authenticated Dashboard API.
</p>
<section class="cards">{cards_html}</section>
<section class="controls" aria-label="Time range controls">
  <div class="presets">
    <span class="ctl-label">Range</span>
    <button type="button" class="btn" data-preset="7">7 days</button>
    <button type="button" class="btn" data-preset="30">30 days</button>
    <button type="button" class="btn" data-preset="90">90 days</button>
    <button type="button" class="btn" data-preset="all">All</button>
  </div>
  <div class="daterange">
    <span class="ctl-label">Dates</span>
    <input type="date" id="range-start" min="{date_min}" max="{date_max}" />
    <span style="color:#cbd5e1">→</span>
    <input type="date" id="range-end" min="{date_min}" max="{date_max}" />
    <button type="button" class="btn primary" id="apply-dates">Apply</button>
  </div>
  <div class="pan-btns">
    <span class="ctl-label">Nudge</span>
    <button type="button" class="btn" id="pan-left" title="Show earlier dates">◀</button>
    <button type="button" class="btn" id="pan-right" title="Show later dates">▶</button>
</div>
</section>
<div class="chart-wrap">
  <div id="main"></div>
</div>
</main>
<script>
{app_js}
</script>
</body>
</html>
"""

def chrome_path() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")
    if found:
        return found
    raise RuntimeError("No Chrome/Chromium executable found for image rendering")


def render_png(html_path: Path, output_path: Path, width: int, height: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome_path(),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--virtual-time-budget=20000",
        "--run-all-compositor-stages-before-draw",
        f"--window-size={width},{height}",
        f"--screenshot={output_path}",
        html_path.resolve().as_uri(),
    ]
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"Chrome screenshot failed: {proc.stderr.strip()[:800]}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("Chrome did not create a PNG output")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect Codex / Claude Code usage, write per-machine "
            "fragments under public/machines/, and merge into public/usage.json."
        )
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output HTML path (legacy single-file report). Default: /tmp/ai-usage-comparison-<timestamp>.html",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Write usage payload JSON for the Astryx web app (public/usage.json).",
    )
    parser.add_argument(
        "--png",
        default="",
        help="Optional: also write a static PNG via headless Chrome (works when ECharts is embedded or network OK).",
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="Print only today's token usage and cost by tool; does not create an HTML file.",
    )
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument(
        "--timezone",
        default=os.environ.get("AI_USAGE_TIMEZONE", DEFAULT_TZ),
    )
    parser.add_argument(
        "--machine-id",
        default=os.environ.get("AI_USAGE_MACHINE_ID", ""),
        help="Stable id for this Mac (required; also AI_USAGE_MACHINE_ID).",
    )
    parser.add_argument(
        "--machines-dir",
        default=str(DEFAULT_MACHINES_DIR),
        help="Directory for per-machine fragment JSON files.",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Write this machine fragment only; do not SUM other machines/*.json.",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Re-merge existing machines/*.json + Cursor API into usage.json (no local re-collect).",
    )
    parser.add_argument(
        "--collect-local-only",
        action="store_true",
        help=(
            "Atomically update only this Mac's fragment; do not call Cursor, GitHub, "
            "npm, or write the shared usage.json."
        ),
    )
    parser.add_argument(
        "--force-reseed",
        action="store_true",
        help="Ignore existing machine fragment and re-seed full local history (dangerous; breaks append freeze).",
    )
    parser.add_argument(
        "--backfill-codex-cache",
        action="store_true",
        help=(
            "One-time migration: derive historical Codex cache-read tokens from each "
            "frozen total/input/output row for this machine, then update only cache fields."
        ),
    )
    parser.add_argument(
        "--recover-codex-cache-transaction",
        action="store_true",
        help="Recover an interrupted Codex cache migration before Git pull or validation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print Codex cache backfill stats without writing files.",
    )
    parser.add_argument("--cursor-page-size", type=int, default=500)
    parser.add_argument("--oneapi-days", type=int, default=5,
                        help="One API lookback days from today (default 5).")
    parser.add_argument("--oneapi-cache-path", type=str, default="",
                        help="Path to a pre-collected One API JSON snapshot to skip live fetch.")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()

    if args.dry_run and not args.backfill_codex_cache:
        parser.error("--dry-run is only supported with --backfill-codex-cache")
    if args.backfill_codex_cache and args.recover_codex_cache_transaction:
        parser.error(
            "--backfill-codex-cache cannot be combined with "
            "--recover-codex-cache-transaction"
        )
    if (args.backfill_codex_cache or args.recover_codex_cache_transaction) and (
        args.today
        or args.merge_only
        or args.collect_local_only
        or args.force_reseed
        or args.no_merge
        or bool(args.out)
        or bool(args.png)
    ):
        parser.error(
            "Codex cache migration modes cannot be combined with --today, --merge-only, "
            "--collect-local-only, --force-reseed, --no-merge, --out, or --png"
        )
    if args.collect_local_only and (
        args.today
        or args.merge_only
        or args.no_merge
        or bool(args.out)
        or bool(args.png)
        or bool(args.json_out)
    ):
        parser.error(
            "--collect-local-only cannot be combined with --today, --merge-only, "
            "--no-merge, --json-out, --out, or --png"
        )

    home = Path(args.home).expanduser()
    if args.today:
        data = collect_today_usage(home, args.timezone, max(1, args.cursor_page_size))
        print(render_today_text(data))
        return 0

    if args.collect_local_only:
        tmp_dir = Path(tempfile.mkdtemp(prefix="ai-usage-local-"))
        try:
            result = collect_local_machine(
                home,
                args.timezone,
                tmp_dir,
                machine_id=machine_fragments.resolve_machine_id(
                    args.machine_id or None
                ),
                machines_path=Path(args.machines_dir).expanduser(),
                force_reseed=bool(args.force_reseed),
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        meta = result.get("fragment_meta") or {}
        print(meta.get("path"))
        print(
            f"fragment_mode={meta.get('mode')} mutable_from={meta.get('mutable_from')} "
            f"stats={meta.get('stats')}"
        )
        return 0

    if not args.json_out and not args.out:
        args.json_out = str(
            Path(__file__).resolve().parents[1] / "public" / "usage.json"
        )

    if args.recover_codex_cache_transaction:
        try:
            result = recover_codex_cache_transaction(
                machines_dir=Path(args.machines_dir).expanduser(),
                machine_id=args.machine_id,
                usage_json_path=Path(args.json_out).expanduser(),
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.backfill_codex_cache:
        try:
            result = backfill_codex_cache_report(
                machines_dir=Path(args.machines_dir).expanduser(),
                machine_id=args.machine_id,
                usage_json_path=Path(args.json_out).expanduser(),
                dry_run=bool(args.dry_run),
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    tmp_dir = Path(tempfile.mkdtemp(prefix="ai-usage-image-"))
    try:
        data = collect_usage(
            home,
            args.timezone,
            tmp_dir,
            max(1, args.cursor_page_size),
            machine_id=args.machine_id or None,
            machines_dir=Path(args.machines_dir).expanduser(),
            merge=not args.no_merge,
            merge_only=bool(args.merge_only),
            usage_json_path=(
                Path(args.json_out).expanduser() if args.json_out else None
            ),
            force_reseed=bool(args.force_reseed),
            oneapi_days=max(1, args.oneapi_days),
            oneapi_cache_path=(
                Path(args.oneapi_cache_path).expanduser()
                if args.oneapi_cache_path
                else None
            ),
        )
        if args.json_out:
            json_path = Path(args.json_out).expanduser()
            machines = data.get("machines") if isinstance(data.get("machines"), list) else []
            payload = {
                "schema_version": data.get("schema_version", PUBLIC_SCHEMA_VERSION),
                "pricing_version": data.get("pricing_version", PRICING_VERSION),
                "captured_at": data.get("captured_at"),
                "snapshot_id": data.get("snapshot_id"),
                "generated_at": data.get("generated_at"),
                "timezone": data.get("timezone"),
                "cursor_mutable_from": data.get("cursor_mutable_from"),
                "cursor_reconciliation": data.get("cursor_reconciliation"),
                "machine_id": data.get("machine_id"),
                "machines": machines,
                "tools": data.get("tools"),
                "timeline_meta": data.get("timeline_meta"),
                "source_status": public_source_status(data.get("source_status")),
                "oneapi": public_oneapi_payload(data.get("oneapi")),
                "daily": data.get("daily_timeline_rows") or [],
                "notes": {
                    "token_breakdown": (
                        "Cards and tooltips show input, cache, and output tokens per tool. "
                        "Codex cache = cache read; Claude cache = create + read; "
                        "Cursor cache = write + read. Ducc is counted under Claude Code. "
                        "One API includes only non-GPT/Codex and non-Claude model families "
                        "from the gateway, such as Grok and DeepSeek. Historical local "
                        "Comate context deltas are retained under One API only on dates "
                        "without gateway coverage."
                    ),
                    "cost": (
                        f"Codex/Claude estimates use the checked-in versioned price ledger "
                        f"{PRICING_VERSION}; known models are deterministically repriced from "
                        "persisted per-model token components. Models without a pinned rate retain "
                        "their collector value with explicit legacy provenance and cannot silently "
                        "replace a higher durable estimate; "
                        "Cursor costs come from the authenticated Dashboard API; "
                        "One API quota uses 250,000 units/CNY and is estimated at "
                        "~0.14 USD/CNY. "
                        "Codex/Claude daily totals are SUMMED across public/machines/*.json; "
                        "Cursor is account-level and replaced from the API on each publish "
                        "(if API unavailable, prior usage.json Cursor series is kept); "
                        "One API is account-level, excludes GPT/Codex and Claude traffic, "
                        "and keeps its prior series when authentication or pagination fails."
                    ),
                    "merge": (
                        f"Merged machine fragments: {', '.join(machines) if machines else '(none)'}. "
                        "Each Mac atomically updates machines/<id>.json. The first run "
                        "seeds history; later runs re-collect mutable_from through today, "
                        "while older dates and higher prior snapshots remain protected."
                        + (
                            f" Fragment mode={((data.get('fragment_meta') or {}).get('mode'))}."
                            if data.get("fragment_meta")
                            else ""
                        )
                        + (
                            f" {data.get('cursor_fallback_note')}"
                            if data.get("cursor_fallback_note")
                            else ""
                        )
                    ),
                },
            }
            machine_fragments.write_json_atomic(json_path, payload)
            print(json_path)
            print(f"machine_id={data.get('machine_id')} machines={machines}")
            meta = data.get("fragment_meta") if isinstance(data.get("fragment_meta"), dict) else {}
            if meta:
                print(f"fragment_mode={meta.get('mode')} stats={meta.get('stats')}")

        if args.out or args.png:
            output_html = (
                Path(args.out).expanduser()
                if args.out
                else Path("/tmp")
                / f"ai-usage-comparison-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
            )
            html_path = tmp_dir / "comparison.html"
            html_path.write_text(render_html(data), encoding="utf-8")
            output_html.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(html_path, output_html)
            print(output_html)
            if args.png:
                png_path = Path(args.png).expanduser()
                render_png(
                    output_html, png_path, max(800, args.width), max(700, args.height)
                )
                print(png_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
