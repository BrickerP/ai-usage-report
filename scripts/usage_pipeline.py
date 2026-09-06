#!/usr/bin/env python3
"""Canonical collection, reconciliation, and usage.json pipeline."""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo


# Repo layout: scripts/usage_pipeline.py → SCRIPTS_DIR is this folder.
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
DEFAULT_TZ = "Asia/Shanghai"
PUBLIC_SCHEMA_VERSION = 4
MODEL_BREAKDOWN_VERSION = 5
DEFAULT_MACHINES_DIR = REPO_ROOT / "public" / "machines"
PINNED_MODEL_PRICES_PATH = SCRIPTS_DIR / "model_prices.v1.json"
CURSOR_START = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
CURSOR_PRICING_VERSION = "cursor-charged-cents-v1"
CURSOR_PRICING_PROVENANCE = "filtered-events-charged-cents"
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
cursor_api = load_module("cursor_usage_api", SCRIPTS_DIR / "cursor_usage_api.py")
comate_usage = load_module("comate_usage", SCRIPTS_DIR / "comate_usage.py")
machine_fragments = load_module("machine_fragments", SCRIPTS_DIR / "machine_fragments.py")
oneapi_usage = load_module("oneapi_usage", SCRIPTS_DIR / "oneapi_usage.py")
legacy_renderer = load_module(
    "legacy_report_renderer", SCRIPTS_DIR / "legacy_report_renderer.py"
)


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
            bool(row.get("scan_complete", True))
            and component_total == point["tokens"]
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


CLAUDE_ROW_FIELDS = (
    "claude_tokens",
    "claude_cost",
    "claude_input",
    "claude_cache_create",
    "claude_cache_read",
    "claude_output",
)


def copy_claude_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in CLAUDE_ROW_FIELDS:
        target[key] = (
            safe_float(source.get(key))
            if key == "claude_cost"
            else safe_int(source.get(key))
        )


def clear_claude_fields(row: dict[str, Any]) -> None:
    for key in CLAUDE_ROW_FIELDS:
        row[key] = 0.0 if key == "claude_cost" else 0


def apply_claude_point(row: dict[str, Any], point: dict[str, Any]) -> None:
    row["claude_tokens"] = safe_int(point.get("tokens"))
    row["claude_cost"] = safe_float(point.get("cost_usd"))
    row["claude_input"] = safe_int(point.get("input"))
    row["claude_output"] = safe_int(point.get("output"))
    row["claude_cache_read"] = safe_int(point.get("cache_read"))
    row["claude_cache_create"] = safe_int(point.get("cache_create"))
    row["claude_models"] = normalize_models(point.get("model_breakdowns"))
    row["claude_snapshot_complete"] = True
    row["claude_pricing_version"] = "oneapi"
    row["claude_pricing_complete"] = True
    row["claude_pricing_provenance"] = "oneapi"


def claude_data_from_oneapi(oneapi_data: dict[str, Any]) -> dict[str, Any]:
    claude = oneapi_data.get("claude")
    if isinstance(claude, dict):
        return claude
    return {}


def reconcile_claude_rows(
    current_rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
    claude_data: dict[str, Any],
    *,
    rebuild_from: str = "",
) -> list[dict[str, Any]]:
    """Preserve frozen local Claude history; replace only dates at/after ``rebuild_from``.

    ``rebuild_from`` is the first calendar day whose Claude series is rebuilt from
    the One API Claude model family. Dates before it keep their prior local
    collector values unchanged.
    """
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
            safe_int(prior.get("claude_tokens"))
            or safe_float(prior.get("claude_cost"))
        ):
            continue
        row = by_date.setdefault(date_key, empty_daily_row(date_key))
        copy_claude_fields(row, prior)

    if rebuild_from:
        for date_key, row in by_date.items():
            if date_key >= rebuild_from:
                clear_claude_fields(row)

    for point in claude_data.get("daily_timeline") or []:
        if not isinstance(point, dict) or not point.get("date"):
            continue
        date_key = str(point["date"])
        if rebuild_from and date_key < rebuild_from:
            continue
        row = by_date.setdefault(date_key, empty_daily_row(date_key))
        apply_claude_point(row, point)

    result: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        row = by_date[date_key]
        recompute_daily_total(row)
        if has_daily_activity(row):
            result.append(row)
    return result


def codex_data_from_oneapi(oneapi_data: dict[str, Any]) -> dict[str, Any]:
    codex = oneapi_data.get("codex")
    if isinstance(codex, dict):
        return codex
    return {}


def apply_codex_gateway_point(row: dict[str, Any], point: dict[str, Any]) -> None:
    """Additive Codex gateway point: oneapi codex series appends onto the local
    Codex series rather than replacing it.  The gateway's cache_write_tokens
    (gateway cache-write) maps onto the Codex cache_read component.
    """
    had_local = bool(
        safe_int(row.get("codex_tokens")) or safe_float(row.get("codex_cost"))
    )
    row["codex_tokens"] = safe_int(row.get("codex_tokens")) + safe_int(
        point.get("tokens")
    )
    row["codex_cost"] = safe_float(row.get("codex_cost")) + safe_float(
        point.get("cost_usd")
    )
    row["codex_input"] = safe_int(row.get("codex_input")) + safe_int(
        point.get("input")
    )
    row["codex_output"] = safe_int(row.get("codex_output")) + safe_int(
        point.get("output")
    )
    row["codex_cache_read"] = safe_int(row.get("codex_cache_read")) + safe_int(
        point.get("cache_read")
    )
    row["codex_models"] = normalize_models(
        [
            *(
                row.get("codex_models")
                if isinstance(row.get("codex_models"), list)
                else []
            ),
            *(
                point.get("model_breakdowns")
                if isinstance(point.get("model_breakdowns"), list)
                else []
            ),
        ]
    )
    if not had_local:
        row["codex_snapshot_complete"] = True
        row["codex_pricing_version"] = "oneapi"
        row["codex_pricing_complete"] = True
        row["codex_pricing_provenance"] = "oneapi"


def reconcile_codex_rows(
    current_rows: list[dict[str, Any]],
    codex_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Append the One API Codex model family onto the local Codex series.

    Local Codex is collected per-machine from ``~/.codex`` jsonl and is already
    present in ``current_rows``.  The gateway's Codex family represents
    additional gateway-routed Codex traffic, so the two are summed (additive,
    no deduplication).
    """
    by_date = {
        str(row.get("date")): dict(row)
        for row in current_rows
        if isinstance(row, dict) and row.get("date")
    }
    for point in codex_data.get("daily_timeline") or []:
        if not isinstance(point, dict) or not point.get("date"):
            continue
        date_key = str(point["date"])
        row = by_date.setdefault(date_key, empty_daily_row(date_key))
        apply_codex_gateway_point(row, point)

    result: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        row = by_date[date_key]
        recompute_daily_total(row)
        if has_daily_activity(row):
            result.append(row)
    return result


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

    # Merge the gateway Codex family series the same way: prior days outside the
    # fresh window are retained so the appended Codex series stays complete.
    codex_by_date: dict[str, dict[str, Any]] = {}
    if compatible:
        for point in (prior.get("codex") or {}).get("daily_timeline") or []:
            if isinstance(point, dict) and point.get("date"):
                codex_by_date[str(point["date"])] = copy.deepcopy(point)

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

        fetched_codex = fetched.get("codex") if isinstance(fetched.get("codex"), dict) else {}
        if fetched_codex.get("daily_timeline"):
            for date_key in list(codex_by_date):
                if start <= date_key <= end:
                    del codex_by_date[date_key]
            for point in fetched_codex.get("daily_timeline") or []:
                if not isinstance(point, dict) or not point.get("date"):
                    continue
                date_key = str(point["date"])
                if not (start <= date_key <= end):
                    raise ValueError(
                        f"One API Codex point {date_key} falls outside {start}..{end}"
                    )
                codex_by_date[date_key] = copy.deepcopy(point)

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
    if codex_by_date:
        codex_series = (
            result.get("codex")
            if isinstance(result.get("codex"), dict)
            else {}
        )
        result["codex"] = {
            **codex_series,
            "daily_timeline": [codex_by_date[key] for key in sorted(codex_by_date)],
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
    cursor_rows, cursor = local_records.parse_cursor(home, False)
    cursor_ai_tracking = local_records.inspect_cursor_ai_tracking(home, tmp_dir)
    cursor_vscdb = local_records.inspect_cursor_vscdb(home, tmp_dir)
    return {
        "codex": codex,
        "cursor": {
            **cursor,
            "vscdb_matching_keys": cursor_vscdb.get("matching_keys", 0),
            "ai_tracking_rows": cursor_ai_tracking.get("rows", 0),
            "ai_tracking_requests": cursor_ai_tracking.get("requests", 0),
        },
        "row_counts": {
            "codex": len(codex_rows),
            "cursor": len(cursor_rows),
        },
    }


def call_cursor(client: Any, method: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        status, _headers, raw = client.dashboard(method, body or {})
    except Exception as exc:
        return {"_status": "exception", "_error": f"{type(exc).__name__}: {exc}"}
    if not (200 <= status < 300):
        decoded = cursor_api.decode_json(raw)
        summary = cursor_api.redacted_error_summary(
            decoded,
            str(getattr(client, "token", "") or ""),
            str(getattr(client, "email", "") or ""),
        )
        error_code = str(summary.get("error_code") or "")
        error_body = str(summary.get("body") or "")
        label = f"HTTP {status}"
        if error_code:
            label += f" {error_code}"
        if error_body:
            label += f": {error_body}"
        return {
            "_status": status,
            "_error_code": error_code,
            "_error_body": error_body,
            "_error": label,
        }
    decoded = cursor_api.decode_json(raw)
    return decoded if isinstance(decoded, dict) else {}


def fetch_cursor_aggregate_audit(
    client: Any,
    *,
    team_id: int,
    user_id: int,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    """Fetch advisory aggregate totals without gating the event ledger."""
    fields = (
        "totalInputTokens",
        "totalOutputTokens",
        "totalCacheWriteTokens",
        "totalCacheReadTokens",
        "totalCostCents",
    )
    totals: dict[str, float] = {field: 0.0 for field in fields}
    windows: list[dict[str, Any]] = []
    errors: list[str] = []
    for window_start, window_end in cursor_api.split_aggregate_windows(start_ms, end_ms):
        response = call_cursor(
            client,
            "GetAggregatedUsageEvents",
            {
                "teamId": team_id,
                "userId": user_id,
                "startDate": window_start,
                "endDate": window_end,
            },
        )
        window = {
            "start_ms": window_start,
            "end_ms": window_end,
            "status": response.get("_status", 200),
        }
        if response.get("_status"):
            error = str(response.get("_error") or response.get("_status"))
            errors.append(error)
            window["error_code"] = str(response.get("_error_code") or "")
            window["error_body"] = str(response.get("_error_body") or "")
        else:
            for field in fields:
                totals[field] += safe_float(response.get(field))
        windows.append(window)
    return {
        "available": bool(windows) and not errors,
        "windows": windows,
        "totals": totals,
        "errors": errors,
    }


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

    aggregate_audit = fetch_cursor_aggregate_audit(
        client,
        team_id=team_id,
        user_id=user_id,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    cursor_errors: list[str] = []

    expected_event_count: int | None = None
    first_event = ""
    last_event = ""
    cursor_day_tokens: dict[str, int] = defaultdict(int)
    cursor_day_input: dict[str, int] = defaultdict(int)
    cursor_day_output: dict[str, int] = defaultdict(int)
    cursor_day_cache_write: dict[str, int] = defaultdict(int)
    cursor_day_cache_read: dict[str, int] = defaultdict(int)
    cursor_day_cost = defaultdict(float)
    cursor_day_estimated_raw_cost = defaultdict(float)
    cursor_day_models: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    )
    page = 1
    filtered_complete = True
    processed_events = 0
    invalid_charged_cost_events = 0
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
            if not day_key:
                filtered_complete = False
                continue
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
            raw_charged_cents = event.get("charged_cents")
            try:
                if (
                    raw_charged_cents is None
                    or isinstance(raw_charged_cents, bool)
                    or (
                        isinstance(raw_charged_cents, str)
                        and not raw_charged_cents.strip()
                    )
                ):
                    raise ValueError("missing chargedCents")
                charged_cents = float(raw_charged_cents)
                if not math.isfinite(charged_cents):
                    raise ValueError("non-finite chargedCents")
            except (TypeError, ValueError):
                charged_cents = 0.0
                invalid_charged_cost_events += 1
                filtered_complete = False
            estimated_raw_cents = safe_float(event.get("estimated_raw_cents"))
            if not math.isfinite(estimated_raw_cents):
                estimated_raw_cents = 0.0
            cursor_day_cost[day_key] += charged_cents / 100
            cursor_day_estimated_raw_cost[day_key] += estimated_raw_cents / 100
            model_name = str(event.get("model") or "Unattributed").strip()
            model_tokens = (
                input_tokens + output_tokens + cache_write_tokens + cache_read_tokens
            )
            cursor_day_models[day_key][model_name]["tokens"] += model_tokens
            cursor_day_models[day_key][model_name]["cost"] += charged_cents / 100
        if len(events) < page_size or (
            expected_event_count is not None
            and page * page_size >= expected_event_count
        ):
            break
        page += 1

    total_input = sum(cursor_day_input.values())
    total_output = sum(cursor_day_output.values())
    total_cache_write = sum(cursor_day_cache_write.values())
    total_cache_read = sum(cursor_day_cache_read.values())
    filtered_tokens = sum(cursor_day_tokens.values())
    filtered_cost = sum(cursor_day_cost.values())
    filtered_estimated_raw_cost = sum(cursor_day_estimated_raw_cost.values())
    if invalid_charged_cost_events:
        cursor_errors.append(
            "GetFilteredUsageEvents returned missing or invalid chargedCents "
            f"for {invalid_charged_cost_events} event(s)"
        )
    collection_complete = (
        filtered_complete
        and expected_event_count is not None
        and processed_events == expected_event_count
    )
    aggregate_totals = (
        aggregate_audit.get("totals")
        if isinstance(aggregate_audit.get("totals"), dict)
        else {}
    )
    aggregate_tokens = (
        safe_int(aggregate_totals.get("totalInputTokens"))
        + safe_int(aggregate_totals.get("totalOutputTokens"))
        + safe_int(aggregate_totals.get("totalCacheWriteTokens"))
        + safe_int(aggregate_totals.get("totalCacheReadTokens"))
    )
    aggregate_cost = safe_float(aggregate_totals.get("totalCostCents")) / 100
    audit_warnings = list(aggregate_audit.get("errors") or [])
    if aggregate_audit.get("available"):
        token_delta = filtered_tokens - aggregate_tokens
        cost_delta = filtered_cost - aggregate_cost
        token_tolerance = max(1, round(abs(aggregate_tokens) * 0.01))
        cost_tolerance = max(0.01, abs(aggregate_cost) * 0.01)
        within_tolerance = (
            abs(token_delta) <= token_tolerance
            and abs(cost_delta) <= cost_tolerance
        )
        aggregate_audit["filtered_delta"] = {
            "tokens": token_delta,
            "billed_cost": cost_delta,
        }
        aggregate_audit["within_tolerance"] = within_tolerance
        aggregate_audit["relative_tolerance"] = 0.01
        if not within_tolerance:
            audit_warnings.append(
                "Cursor aggregate audit differs from filtered events by more than 1%: "
                f"tokens={token_delta}, billed_cost={cost_delta:.6f}"
            )
    else:
        aggregate_audit["filtered_delta"] = None
        aggregate_audit["within_tolerance"] = None
    aggregate_audit["warnings"] = audit_warnings
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
            "estimated_raw_cost": cursor_day_estimated_raw_cost[d],
            "snapshot_complete": collection_complete,
            "pricing_version": CURSOR_PRICING_VERSION,
            "pricing_complete": collection_complete,
            "pricing_provenance": CURSOR_PRICING_PROVENANCE,
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
            "input": total_input,
            "output": total_output,
            "cache_write": total_cache_write,
            "cache_read": total_cache_read,
            "total_tokens": filtered_tokens,
            "cost": filtered_cost,
            "estimated_raw_cost": filtered_estimated_raw_cost,
        },
        "aggregate_audit": aggregate_audit,
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
    codex_usage = codex_daily_from_jsonl(
        home, timezone, since=today_key, until=today_key
    )
    cursor_usage = fetch_cursor_usage(home, cursor_page_size, timezone, start_ms, end_ms)
    codex_pts = codex_daily_points(usage_daily_rows(codex_usage))
    claude_pts: list[dict[str, Any]] = []
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
        (
            f"{'One API':<12} {fmt_int(row.get('oneapi_tokens')):>12} "
            f"{fmt_int(row.get('oneapi_input')):>12} "
            f"{fmt_int(safe_int(row.get('oneapi_cache_read')) + safe_int(row.get('oneapi_cache_write'))):>12} "
            f"{fmt_int(row.get('oneapi_output')):>12} "
            f"{fmt_usd(row.get('oneapi_cost')):>12}"
        ),
        f"{'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 12}",
        f"{'Total':<12} {fmt_int(row.get('total_tokens')):>12} {'':>12} {'':>12} {'':>12} {fmt_usd(row.get('total_cost')):>12}",
        "",
        "Cache column = cache read for Codex; cache create + read for Claude; cache write + read for Cursor; cache read + write for One API.",
        "Historical local Comate context deltas are retained under One API.",
        "Codex reasoning tokens are included in total but omitted from this table.",
        "Claude Code is collected from One API's Claude model family.",
        "Codex totals include local ~/.codex rollout jsonl plus the One API gateway's Codex model family.",
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
) -> list[dict[str, Any]]:
    """Local-only daily rows for this machine fragment (no Cursor or Claude)."""
    return merge_daily_timeline(codex_pts, [], [])


def local_today(timezone: str) -> str:
    return dt.datetime.now(tz=resolve_tz(timezone)).strftime("%Y-%m-%d")


def model_breakdown_tokens(breakdown: dict[str, Any]) -> int:
    components = (
        safe_int(breakdown.get("inputTokens"))
        + safe_int(breakdown.get("outputTokens"))
        + safe_int(breakdown.get("cacheCreationTokens"))
        + safe_int(breakdown.get("cacheReadTokens"))
    )
    return components or safe_int(breakdown.get("totalTokens"))


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


def iter_codex_jsonl(home: Path):
    """Yield every Codex rollout JSONL under ~/.codex (sessions + archived)."""
    roots = [
        home / ".codex" / "sessions",
        home / ".codex" / "archived_sessions",
    ]
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            if path not in seen:
                seen.add(path)
                yield path


def codex_daily_from_jsonl(
    home: Path,
    timezone: str,
    since: str = "",
    until: str = "",
) -> dict[str, Any]:
    """Compute Codex daily usage by summing each request's real token increment.

    ccusage attributes usage by a session's ``lastActivity`` and only counts the
    trailing window of each session file, which silently drops the inherited
    context that subagent (spawned) threads copy from their parent. This method
    instead reads every ``token_count.last_token_usage`` event directly, which
    reflects the full context actually sent per API request (including the
    context a spawned thread inherits), deduplicates the same request that
    appears in both a parent and child rollout, and attributes each request to
    the report timezone's calendar day of its event timestamp.

    Returns a payload structurally identical to ``ccusage ... daily --json``
    (``{"daily": [...], "totals": {...}}``) so existing downstream helpers
    (``codex_daily_points``, pricing, machine fragments) work unchanged.
    """
    tz = resolve_tz(timezone)
    since_dt: dt.datetime | None = None
    until_dt: dt.datetime | None = None
    if since:
        since_dt = dt.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=tz)
    if until:
        until_dt = (
            dt.datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=tz)
            + dt.timedelta(days=1)
        )

    day_acc: dict[str, dict[str, Any]] = {}
    model_usage: dict[str, dict[str, dict[str, int]]] = {}
    scan_errors = {
        "unreadable_files": 0,
        "invalid_json_lines": 0,
        "invalid_timestamps": 0,
    }
    sessions: list[dict[str, Any]] = []

    # Rollout filenames carry a UTC creation timestamp (YYYY-MM-DDTHH-MM-SS).
    # A session created after the end of the requested window cannot contain
    # in-window events, so we can safely skip those files. Files created before
    # `since` are NOT skipped: a long-running or resumed session can carry
    # token events deep into the requested window.
    earliest_skip = ""
    if until_dt:
        earliest_skip = until_dt.strftime("%Y-%m-%dT")

    for path in iter_codex_jsonl(home):
        if earliest_skip:
            basename = path.name
            if basename.startswith("rollout-"):
                stamp = basename[len("rollout-"): len("rollout-") + 19]
                if stamp and stamp >= earliest_skip:
                    continue
        file_model = ""
        session_id = f"file:{path}"
        parent_thread_id = ""
        events: list[dict[str, Any]] = []
        file_scan_errors = defaultdict(int)
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for event_order, line in enumerate(handle):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        file_scan_errors["invalid_json_lines"] += 1
                        continue
                    if not isinstance(obj, dict):
                        continue
                    typ = obj.get("type") or ""
                    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                    if typ == "session_meta":
                        session_id = str(
                            payload.get("id")
                            or payload.get("session_id")
                            or session_id
                        ).strip()
                        parent_thread_id = str(
                            payload.get("parent_thread_id") or ""
                        ).strip()
                        continue
                    if typ == "turn_context":
                        new_model = str(payload.get("model") or "").strip()
                        if new_model:
                            file_model = new_model
                        continue
                    if typ == "event_msg" and payload.get("type") == "token_count":
                        raw_timestamp = obj.get("timestamp") or payload.get("timestamp")
                        if not isinstance(raw_timestamp, str):
                            file_scan_errors["invalid_timestamps"] += 1
                            continue
                        try:
                            event_dt = dt.datetime.fromisoformat(
                                raw_timestamp.strip().replace("Z", "+00:00")
                            )
                        except (TypeError, ValueError):
                            file_scan_errors["invalid_timestamps"] += 1
                            continue
                        if event_dt.tzinfo is None:
                            event_dt = event_dt.replace(tzinfo=dt.timezone.utc)
                        event_dt = event_dt.astimezone(dt.timezone.utc)
                        normalized_timestamp = event_dt.isoformat(
                            timespec="microseconds"
                        ).replace("+00:00", "Z")
                        local = event_dt.astimezone(tz)
                        if since_dt and local < since_dt:
                            continue
                        if until_dt and local >= until_dt:
                            continue
                        info = payload.get("info")
                        if not isinstance(info, dict):
                            continue
                        last = info.get("last_token_usage")
                        if not isinstance(last, dict):
                            continue
                        input_tokens = safe_int(last.get("input_tokens"))
                        cached_input = safe_int(last.get("cached_input_tokens"))
                        output_tokens = safe_int(last.get("output_tokens"))
                        reasoning = safe_int(last.get("reasoning_output_tokens"))
                        if not (input_tokens or cached_input or output_tokens or reasoning):
                            continue
                        events.append(
                            {
                                "timestamp": normalized_timestamp,
                                "event_dt": event_dt,
                                "order": event_order,
                                "input": input_tokens,
                                "cache_read": cached_input,
                                "output": output_tokens,
                                "reasoning": reasoning,
                                "model": file_model,
                            }
                        )
        except OSError:
            file_scan_errors["unreadable_files"] += 1
        for error_name, count in file_scan_errors.items():
            scan_errors[error_name] += count
        sessions.append(
            {
                "path": str(path),
                "session_id": session_id,
                "parent_thread_id": parent_thread_id,
                "events": events,
                "order": len(sessions),
            }
        )

    sessions_by_id = {
        session["session_id"]: session
        for session in sessions
        if session["session_id"]
    }

    def root_and_depth(session: dict[str, Any]) -> tuple[str, int]:
        current_id = session["session_id"]
        root_id = current_id
        depth = 0
        visited: set[str] = set()
        while current_id not in visited:
            visited.add(current_id)
            current = sessions_by_id.get(current_id)
            parent_id = str((current or {}).get("parent_thread_id") or "").strip()
            if not parent_id:
                break
            root_id = parent_id
            depth += 1
            current_id = parent_id
        return root_id, depth

    ordered_sessions = [
        (root_and_depth(session)[0], root_and_depth(session)[1], session)
        for session in sessions
    ]
    ordered_sessions.sort(key=lambda item: (item[0], item[1], item[2]["order"]))
    seen_candidates: dict[str, dict[tuple[Any, ...], set[str]]] = defaultdict(dict)
    for root_id, _depth, session in ordered_sessions:
        session_key = session["session_id"]
        for event in session["events"]:
            input_tokens = event["input"]
            cached_input = event["cache_read"]
            output_tokens = event["output"]
            reasoning = event["reasoning"]
            candidate_key = (
                event["timestamp"],
                input_tokens,
                cached_input,
                output_tokens,
                reasoning,
            )
            candidate_sessions = seen_candidates[root_id].setdefault(
                candidate_key, set()
            )
            if candidate_sessions and session_key not in candidate_sessions:
                continue
            candidate_sessions.add(session_key)

            local = event["event_dt"].astimezone(tz)
            day = local.strftime("%Y-%m-%d")
            acc = day_acc.setdefault(
                day,
                {
                    "input": 0,
                    "cache_read": 0,
                    "output": 0,
                    "reasoning": 0,
                },
            )
            acc["input"] += input_tokens
            acc["cache_read"] += cached_input
            acc["output"] += output_tokens
            acc["reasoning"] += reasoning

            model_key = event["model"] or "unknown"
            if model_key == "codex-auto-review":
                model_key = "gpt-5.5"
            macc = model_usage.setdefault(day, {}).setdefault(
                model_key, {"input": 0, "cache_read": 0, "output": 0, "reasoning": 0}
            )
            macc["input"] += input_tokens
            macc["cache_read"] += cached_input
            macc["output"] += output_tokens
            macc["reasoning"] += reasoning

    scan_complete = not any(scan_errors.values())

    daily: list[dict[str, Any]] = []
    for day in sorted(day_acc):
        acc = day_acc[day]
        full_input = acc["input"]
        cache_read = acc["cache_read"]
        output_tokens = acc["output"]
        reasoning = acc["reasoning"]
        uncached_input = full_input - cache_read
        total_tokens = full_input + output_tokens
        models: dict[str, dict[str, Any]] = {}
        for name, macc in (model_usage.get(day) or {}).items():
            models[name] = {
                "inputTokens": macc["input"] - macc["cache_read"],
                "cacheReadTokens": macc["cache_read"],
                "outputTokens": macc["output"],
                "reasoningOutputTokens": macc["reasoning"],
                "totalTokens": macc["input"] + macc["output"],
                "costUSD": 0.0,
            }
        daily.append(
            {
                "date": day,
                "inputTokens": uncached_input,
                "cacheReadTokens": cache_read,
                "outputTokens": output_tokens,
                "reasoningOutputTokens": reasoning,
                "totalTokens": total_tokens,
                "costUSD": 0.0,
                "models": models,
                "scan_complete": scan_complete,
            }
        )

    totals = {
        "inputTokens": sum(d["inputTokens"] for d in daily),
        "cacheReadTokens": sum(d["cacheReadTokens"] for d in daily),
        "outputTokens": sum(d["outputTokens"] for d in daily),
        "reasoningOutputTokens": sum(d["reasoningOutputTokens"] for d in daily),
        "totalTokens": sum(d["totalTokens"] for d in daily),
        "costUSD": 0.0,
    }
    return reprice_models_with_pinned_ledger(
        {
            "daily": daily,
            "totals": totals,
            "scan_complete": scan_complete,
            "scan_errors": scan_errors,
        }
    )


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
        "oneapi_incomplete",
        "oneapi_browser_unavailable",
        "oneapi_network_unavailable",
        "oneapi_reauth_required",
        "oneapi_refresh_failed",
        "oneapi_state_unavailable",
        "oneapi_unavailable",
    },
    "cursor": {"cursor_incomplete", "cursor_unavailable"},
    "oneapi": {
        "oneapi_incomplete",
        "oneapi_browser_unavailable",
        "oneapi_network_unavailable",
        "oneapi_reauth_required",
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
    "claude",
    "codex",
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
    active_fragments = [fragment for fragment in fragments if fragment.get("retired") is not True]
    if not active_fragments:
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
    for fragment in active_fragments:
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

    if missing or len(collected) != len(active_fragments):
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
    if str(payload.get("cursor_pricing_version") or "") != CURSOR_PRICING_VERSION:
        # Reopen the returned event window once when the authoritative cost
        # field changes. Dates absent from the API remain preserved.
        return ""
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
    }
    pricing_regressions: set[str] = set()
    for row in fragment.get("daily") or []:
        if not isinstance(row, dict):
            continue
        date_key = str(row.get("date") or "")
        for prefix in ("codex",):
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
    fragment["tools"] = ["codex"]
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
        codex_usage = codex_daily_from_jsonl(home, timezone)
    else:
        codex_usage = codex_daily_from_jsonl(
            home, timezone, since=since or today, until=until
        )

    model_codex_usage = codex_usage
    model_seed_complete = bool(codex_usage.get("scan_complete", True)) and (
        first_seed or not needs_model_seed
    )
    if needs_model_seed and not first_seed:
        model_seed_complete = False
        try:
            model_codex_usage = codex_daily_from_jsonl(home, timezone)
            model_seed_complete = bool(model_codex_usage.get("scan_complete", True))
        except Exception as exc:
            print(
                f"warning: full model backfill failed; keeping incremental model rows: {exc}",
                file=sys.stderr,
            )

    local_summary = local_record_summary(home, tmp_dir)
    comate = comate_usage.parse_comate(home, timezone)
    codex_rows = usage_daily_rows(codex_usage)
    codex_first, codex_last = daily_range(codex_rows, codex=True)
    codex_totals = usage_totals(codex_usage)

    codex_summary = {
        "tool": "Codex",
        "history": fmt_range(codex_first, codex_last),
        "cost": safe_float(codex_totals.get("costUSD")),
        "total_tokens": safe_int(codex_totals.get("totalTokens")),
    }
    codex_pts_all = codex_daily_points(usage_daily_rows(model_codex_usage))
    codex_pts = codex_daily_points(codex_rows)
    if since:
        codex_pts = filter_points_since(codex_pts, since)

    local_daily = build_local_machine_daily(codex_pts)
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
    skip_oneapi_live: bool = False,
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
    prior_cursor_pricing_version = str(
        prior_payload.get("cursor_pricing_version") or ""
    )
    next_cursor_pricing_version = prior_cursor_pricing_version
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
        claude_summary = {"tool": "Claude Code", "history": "from oneapi", "cost": 0, "total_tokens": 0}
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

    cursor_aggregate_audit = (
        cursor_usage.get("aggregate_audit")
        if isinstance(cursor_usage.get("aggregate_audit"), dict)
        else {}
    )
    for warning in cursor_aggregate_audit.get("warnings") or []:
        print(f"WARN: Cursor aggregate audit: {warning}", file=sys.stderr)

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

    if not oneapi_cache_loaded and skip_oneapi_live:
        status_path = Path(
            os.environ.get(
                "ONEAPI_STATUS_PATH",
                str(
                    Path(oneapi_state_path).with_name("oneapi-status.json")
                ),
            )
        ).expanduser()
        attempt_status = load_usage_payload(status_path)
        attempt_error = str(attempt_status.get("error_code") or "")
        oneapi_status_error = (
            attempt_error
            if attempt_error in SOURCE_STATUS_ERRORS["oneapi"]
            else "oneapi_refresh_failed"
        )
        note = (
            "One API was already attempted in this publish run; "
            "kept prior compatible series."
        )
        print(f"WARN: {note} ({oneapi_status_error})", file=sys.stderr)
        oneapi_refresh_error = note
        oneapi_data = {
            **prior_oneapi,
            "available": False,
            "complete": False,
            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
            "stale": True,
            "note": note,
            "state_path": oneapi_state_path,
        }
    elif not oneapi_cache_loaded and Path(oneapi_state_path).exists():
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
            oneapi_status_error = (
                str(exc.error_code)
                if isinstance(exc, oneapi_usage.OneApiCollectorError)
                else "oneapi_refresh_failed"
            )
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
            "claude": {
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
    if cursor_api_complete:
        next_cursor_pricing_version = CURSOR_PRICING_VERSION

    # Claude Code is rebuilt from One API's Claude model family. The local
    # Claude collector has been removed, so any date the One API Claude series
    # covers is authoritative; rebuild from its earliest covered date and keep
    # older prior values frozen. Fall back to the previous day when the series
    # is absent (e.g. a failed One API refresh).
    claude_data = claude_data_from_oneapi(oneapi_data)
    claude_first = str((claude_data.get("history") or {}).get("first") or "")
    claude_rebuild_from = claude_first or machine_fragments.previous_day(today)
    daily_rows = reconcile_claude_rows(
        daily_rows,
        prior_rows,
        claude_data,
        rebuild_from=claude_rebuild_from,
    )
    # The One API Codex model family appends onto the local Codex series
    # (gateway-routed Codex traffic that is not present in local ~/.codex jsonl).
    daily_rows = reconcile_codex_rows(
        daily_rows,
        codex_data_from_oneapi(oneapi_data),
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
        if summary.get("history") in ("from fragments", "from oneapi", "unknown", "") or merge:
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
            "cursor_pricing_version": next_cursor_pricing_version,
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
        "cursor_pricing_version": next_cursor_pricing_version,
        "cursor_reconciliation": cursor_reconciliation_stats,
        "source_status": source_status,
    }


def render_html(data: dict[str, Any]) -> str:
    return legacy_renderer.render_html(data, pricing_version=PRICING_VERSION)


CARD_BREAKDOWNS = legacy_renderer.CARD_BREAKDOWNS
fmt_compact = legacy_renderer.fmt_compact
esc = legacy_renderer.esc
chrome_path = legacy_renderer.chrome_path
render_png = legacy_renderer.render_png


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect Codex usage, write per-machine fragments under "
            "public/machines/, and merge into public/usage.json."
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
    parser.add_argument(
        "--skip-oneapi-live",
        action="store_true",
        help=(
            "Do not retry One API in merge after this publish run already "
            "attempted it; retain the prior durable series."
        ),
    )
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
            skip_oneapi_live=bool(args.skip_oneapi_live),
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
                "cursor_pricing_version": data.get("cursor_pricing_version"),
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
                        "Cursor cache = write + read. Claude Code is collected from the "
                        "One API gateway's Claude model family. "
                        "Codex totals are the local ~/.codex rollout jsonl summed across "
                        "public/machines/*.json plus the One API gateway's Codex model "
                        "family (gateway-routed Codex traffic). "
                        "One API includes only non-GPT/Codex and non-Claude model families "
                        "from the gateway, such as Grok and DeepSeek. Historical local "
                        "Comate context deltas are retained under One API only on dates "
                        "without gateway coverage."
                    ),
                    "cost": (
                        f"Codex estimates use the checked-in versioned price ledger "
                        f"{PRICING_VERSION}; known models are deterministically repriced from "
                        "persisted per-model token components. Models without a pinned rate retain "
                        "their collector value with explicit legacy provenance and cannot silently "
                        "replace a higher durable estimate; "
                        "Claude Code costs come from the One API gateway quota "
                        "(250,000 units/CNY, ~0.14 USD/CNY); "
                        "Cursor costs come from the authenticated Dashboard API; "
                        "One API quota uses 250,000 units/CNY and is estimated at "
                        "~0.14 USD/CNY. "
                        "Codex daily totals are SUMMED across public/machines/*.json "
                        "plus the One API gateway's Codex model family; "
                        "Claude Code and One API are account-level and rebuilt from the "
                        "One API gateway on each publish; "
                        "Cursor is account-level and replaced from the API on each publish "
                        "(if API unavailable, prior usage.json Cursor series is kept); "
                        "One API is account-level, excludes no owned model families, "
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
