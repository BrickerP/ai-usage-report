#!/usr/bin/env python3
"""Per-machine usage fragments and multi-machine merge."""
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
        # Optional Comate extras
        if prefix == "comate":
            names.extend(["comate_sessions", "comate_messages"])
    return names


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
    machines_dir.mkdir(parents=True, exist_ok=True)
    mid = sanitize_machine_id(machine_id)
    local_daily = [
        strip_row_to_local(row, tool_token_fields, safe_int, safe_float)
        for row in daily_rows
        if isinstance(row, dict) and row.get("date")
    ]
    # Keep days that have any local activity
    filtered: list[dict[str, Any]] = []
    for row in local_daily:
        has = False
        for prefix in LOCAL_TOOL_PREFIXES:
            if safe_int(row.get(f"{prefix}_tokens")) or safe_float(row.get(f"{prefix}_cost")):
                has = True
                break
            if prefix == "comate" and (
                safe_int(row.get("comate_sessions")) or safe_int(row.get("comate_messages"))
            ):
                has = True
                break
        if has:
            filtered.append(row)

    payload = {
        "machine_id": mid,
        "hostname": hostname or socket.gethostname(),
        "collected_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "timezone": timezone,
        "scope": "machine-local",
        "tools": list(LOCAL_TOOL_PREFIXES),
        "daily": filtered,
    }
    path = fragment_path(machines_dir, mid)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    # stable unique machine ids
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
) -> list[dict[str, Any]]:
    by_date = {str(r["date"]): dict(r) for r in daily_rows if isinstance(r, dict) and r.get("date")}
    for point in cursor_pts:
        if not isinstance(point, dict):
            continue
        date_key = str(point.get("date") or "")
        if not date_key:
            continue
        row = by_date.setdefault(date_key, empty_daily_row(date_key))
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
