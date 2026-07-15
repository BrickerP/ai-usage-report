#!/usr/bin/env python3
"""Per-machine usage fragments and multi-machine merge.

Machine fragments are append-only ledgers:
- First run (missing/empty fragment): seed full local history once.
- Later runs: append missing dates; refresh *today* only; never rewrite past days.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import socket
from pathlib import Path
from typing import Any, Callable


LOCAL_TOOL_PREFIXES = ("codex", "claude", "comate")
ACCOUNT_TOOL_PREFIXES = ("cursor",)

SafeInt = Callable[[Any], int]
SafeFloat = Callable[[Any], float]


def sanitize_machine_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned[:80] or "machine"


def resolve_machine_id(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return sanitize_machine_id(explicit)
    env = os.environ.get("AI_USAGE_MACHINE_ID", "").strip()
    if env:
        return sanitize_machine_id(env)
    return sanitize_machine_id(socket.gethostname() or "machine")


def fragment_path(machines_dir: Path, machine_id: str) -> Path:
    return machines_dir / f"{sanitize_machine_id(machine_id)}.json"


def tool_field_names(tool_token_fields: dict[str, list[str]], prefixes: tuple[str, ...]) -> list[str]:
    names: list[str] = []
    for prefix in prefixes:
        names.append(f"{prefix}_tokens")
        names.append(f"{prefix}_cost")
        for field in tool_token_fields.get(prefix, []):
            names.append(f"{prefix}_{field}")
        if prefix == "comate":
            names.extend(["comate_sessions", "comate_messages"])
    return names


def row_has_local_activity(row: dict[str, Any], safe_int: SafeInt, safe_float: SafeFloat) -> bool:
    for prefix in LOCAL_TOOL_PREFIXES:
        if safe_int(row.get(f"{prefix}_tokens")) or safe_float(row.get(f"{prefix}_cost")):
            return True
        if prefix == "comate" and (
            safe_int(row.get("comate_sessions")) or safe_int(row.get("comate_messages"))
        ):
            return True
    return False


def strip_row_to_local(
    row: dict[str, Any],
    tool_token_fields: dict[str, list[str]],
    safe_int: SafeInt,
    safe_float: SafeFloat,
) -> dict[str, Any]:
    out: dict[str, Any] = {"date": str(row.get("date") or "")}
    for name in tool_field_names(tool_token_fields, LOCAL_TOOL_PREFIXES):
        if name.endswith("_cost"):
            out[name] = safe_float(row.get(name))
        else:
            out[name] = safe_int(row.get(name))
    return out


def load_machine_fragment(machines_dir: Path, machine_id: str) -> dict[str, Any] | None:
    path = fragment_path(machines_dir, machine_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("daily"), list):
        data["daily"] = []
    return data


def fragment_dates(fragment: dict[str, Any] | None) -> set[str]:
    if not fragment:
        return set()
    dates: set[str] = set()
    for row in fragment.get("daily") or []:
        if isinstance(row, dict) and row.get("date"):
            dates.add(str(row["date"]))
    return dates


def is_first_seed(fragment: dict[str, Any] | None) -> bool:
    """True when this machine has never been seeded with local history."""
    if fragment is None:
        return True
    daily = fragment.get("daily") or []
    if not daily:
        return True
    return False


def next_day(date_key: str) -> str:
    day = dt.date.fromisoformat(date_key)
    return (day + dt.timedelta(days=1)).isoformat()


def append_range_start(fragment: dict[str, Any] | None, today: str) -> str:
    """Earliest local calendar day we still need from collectors.

    - First seed: empty string → caller collects full history.
    - Later: day after latest frozen date, but never after today.
      Today is always re-collected even if present.
    """
    dates = fragment_dates(fragment)
    if not dates:
        return ""
    frozen = {d for d in dates if d < today}
    if not frozen:
        return today
    latest_frozen = max(frozen)
    start = next_day(latest_frozen)
    return start if start <= today else today


def merge_append_daily(
    existing_daily: list[dict[str, Any]],
    incoming_daily: list[dict[str, Any]],
    today: str,
    tool_token_fields: dict[str, list[str]],
    safe_int: SafeInt,
    safe_float: SafeFloat,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Append-only merge.

    - date < today and already present: keep existing (frozen)
    - date < today and missing: append incoming
    - date == today: replace with incoming (current day may still grow)
    - date > today: ignore
    """
    by_date: dict[str, dict[str, Any]] = {}
    for row in existing_daily:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        date_key = str(row["date"])
        by_date[date_key] = strip_row_to_local(row, tool_token_fields, safe_int, safe_float)

    stats = {"frozen_kept": 0, "appended": 0, "today_updated": 0, "skipped": 0}
    for row in incoming_daily:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        date_key = str(row["date"])
        if date_key > today:
            stats["skipped"] += 1
            continue
        local_row = strip_row_to_local(row, tool_token_fields, safe_int, safe_float)
        if not row_has_local_activity(local_row, safe_int, safe_float):
            if date_key != today:
                stats["skipped"] += 1
                continue

        if date_key < today and date_key in by_date:
            stats["frozen_kept"] += 1
            continue
        if date_key == today:
            by_date[date_key] = local_row
            stats["today_updated"] += 1
            continue
        by_date[date_key] = local_row
        stats["appended"] += 1

    rows = [
        by_date[key]
        for key in sorted(by_date)
        if row_has_local_activity(by_date[key], safe_int, safe_float)
    ]
    return rows, stats


def write_machine_fragment_append(
    machines_dir: Path,
    machine_id: str,
    timezone: str,
    incoming_daily: list[dict[str, Any]],
    tool_token_fields: dict[str, list[str]],
    safe_int: SafeInt,
    safe_float: SafeFloat,
    *,
    today: str,
    hostname: str | None = None,
    force_reseed: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Seed once or append into machines/<id>.json. Past days are immutable."""
    machines_dir.mkdir(parents=True, exist_ok=True)
    mid = sanitize_machine_id(machine_id)
    path = fragment_path(machines_dir, mid)
    existing = None if force_reseed else load_machine_fragment(machines_dir, mid)
    first = force_reseed or is_first_seed(existing)

    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if first:
        merged, stats = merge_append_daily(
            [],
            incoming_daily,
            today,
            tool_token_fields,
            safe_int,
            safe_float,
        )
        mode = "seed"
        seeded_at = now
    else:
        merged, stats = merge_append_daily(
            list(existing.get("daily") or []) if existing else [],
            incoming_daily,
            today,
            tool_token_fields,
            safe_int,
            safe_float,
        )
        mode = "append"
        seeded_at = str((existing or {}).get("seeded_at") or now)

    payload = {
        "machine_id": mid,
        "hostname": hostname or socket.gethostname(),
        "collected_at": now,
        "seeded_at": seeded_at,
        "seeded": True,
        "append_mode": True,
        "last_mode": mode,
        "last_append_stats": stats,
        "timezone": timezone,
        "scope": "machine-local",
        "tools": list(LOCAL_TOOL_PREFIXES),
        "policy": (
            "Append-only ledger: first run seeds full local history; later runs append "
            "missing dates and refresh today only; past dates are never rewritten "
            "(survives local session cleanup / data drift)."
        ),
        "daily": merged,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = {
        "path": str(path),
        "mode": mode,
        "stats": stats,
        "days": len(merged),
        "first_seed": first,
    }
    return path, meta


def write_machine_fragment(
    machines_dir: Path,
    machine_id: str,
    timezone: str,
    daily_rows: list[dict[str, Any]],
    tool_token_fields: dict[str, list[str]],
    safe_int: SafeInt,
    safe_float: SafeFloat,
    hostname: str | None = None,
) -> Path:
    today = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    path, _meta = write_machine_fragment_append(
        machines_dir,
        machine_id,
        timezone,
        daily_rows,
        tool_token_fields,
        safe_int,
        safe_float,
        today=today,
        hostname=hostname,
    )
    return path


def load_machine_fragments(machines_dir: Path) -> list[dict[str, Any]]:
    if not machines_dir.is_dir():
        return []
    fragments: list[dict[str, Any]] = []
    for path in sorted(machines_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("daily"), list):
            data["_path"] = str(path)
            fragments.append(data)
    return fragments


def merge_local_fragments(
    fragments: list[dict[str, Any]],
    empty_daily_row: Callable[[str], dict[str, Any]],
    tool_token_fields: dict[str, list[str]],
    safe_int: SafeInt,
    safe_float: SafeFloat,
) -> tuple[list[dict[str, Any]], list[str]]:
    """SUM machine-local tool columns across fragments. Cursor left at 0."""
    by_date: dict[str, dict[str, Any]] = {}
    machine_ids: list[str] = []

    for frag in fragments:
        mid = str(frag.get("machine_id") or Path(str(frag.get("_path") or "unknown")).stem)
        machine_ids.append(mid)
        for row in frag.get("daily") or []:
            if not isinstance(row, dict):
                continue
            date_key = str(row.get("date") or "")
            if not date_key:
                continue
            target = by_date.setdefault(date_key, empty_daily_row(date_key))
            for name in tool_field_names(tool_token_fields, LOCAL_TOOL_PREFIXES):
                if name.endswith("_cost"):
                    target[name] = safe_float(target.get(name)) + safe_float(row.get(name))
                else:
                    target[name] = safe_int(target.get(name)) + safe_int(row.get(name))

    rows: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        rows.append(by_date[date_key])
    seen: set[str] = set()
    ordered: list[str] = []
    for mid in machine_ids:
        if mid not in seen:
            seen.add(mid)
            ordered.append(mid)
    return rows, ordered


def apply_cursor_points(
    daily_rows: list[dict[str, Any]],
    cursor_pts: list[dict[str, Any]],
    empty_daily_row: Callable[[str], dict[str, Any]],
    apply_tool_point: Callable[[dict[str, Any], str, dict[str, Any]], None],
    safe_int: SafeInt,
    safe_float: SafeFloat,
    *,
    today: str | None = None,
    freeze_cursor_history: bool = True,
) -> list[dict[str, Any]]:
    """Apply Cursor points onto local rows.

    When freeze_cursor_history is True (default), dates before today that already
    have cursor_* values are left unchanged; only missing historical days and
    today are updated.
    """
    by_date = {str(r["date"]): dict(r) for r in daily_rows if isinstance(r, dict) and r.get("date")}
    today_key = today or ""

    for point in cursor_pts:
        if not isinstance(point, dict):
            continue
        date_key = str(point.get("date") or "")
        if not date_key:
            continue
        row = by_date.setdefault(date_key, empty_daily_row(date_key))
        if (
            freeze_cursor_history
            and today_key
            and date_key < today_key
            and (safe_int(row.get("cursor_tokens")) or safe_float(row.get("cursor_cost")))
        ):
            continue
        apply_tool_point(row, "cursor", point)

    rows: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        row = by_date[date_key]
        ct = safe_int(row.get("codex_tokens"))
        lt = safe_int(row.get("claude_tokens"))
        ut = safe_int(row.get("cursor_tokens"))
        mt = safe_int(row.get("comate_tokens"))
        cc = safe_float(row.get("codex_cost"))
        lc = safe_float(row.get("claude_cost"))
        uc = safe_float(row.get("cursor_cost"))
        mc = safe_float(row.get("comate_cost"))
        if ct or lt or ut or mt or cc or lc or uc or mc:
            row["total_tokens"] = ct + lt + ut + mt
            row["total_cost"] = cc + lc + uc + mc
            rows.append(row)
    return rows
