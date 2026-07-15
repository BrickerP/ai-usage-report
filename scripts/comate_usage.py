#!/usr/bin/env python3
"""Collect local Comate / Zulu chat usage from ~/.comate-engine/store."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def safe_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def resolve_tz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.timezone.utc


def ms_to_local_date(ms_val: Any, tz_name: str) -> str:
    ms = safe_int(ms_val)
    if not ms:
        return ""
    # Comate uses unix ms; tolerate accidental seconds.
    if ms < 1_000_000_000_000:
        ms *= 1000
    utc = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return utc.astimezone(resolve_tz(tz_name)).strftime("%Y-%m-%d")


def empty_day() -> dict[str, Any]:
    return {
        "tokens": 0,
        "cost": 0.0,
        "input": 0,
        "output": 0,
        "sessions": 0,
        "messages": 0,
    }


def load_session(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def session_paths(home: Path) -> list[Path]:
    store = home / ".comate-engine" / "store"
    if not store.is_dir():
        return []
    return sorted(store.glob("chat_session_*"))


def parse_comate(home: Path, timezone: str = "Asia/Shanghai") -> dict[str, Any]:
    """Aggregate Comate usage by local calendar day.

    Comate local records expose context-window snapshots (`tokenUsage.contextUsed`),
    not billable input/output. We approximate daily tokens as the sum of positive
    contextUsed deltas within each session.
    """
    by_date: dict[str, dict[str, Any]] = {}
    session_files = 0
    sessions_with_usage = 0
    message_count = 0

    for path in session_paths(home):
        data = load_session(path)
        if not data:
            continue
        session_files += 1
        messages = data.get("messages")
        if not isinstance(messages, list):
            continue

        session_day = ms_to_local_date(data.get("ctime") or data.get("utime"), timezone)
        prev_ctx: int | None = None
        day_tokens = 0
        day_messages = 0
        touched_days: set[str] = set()

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "")
            stamp = msg.get("completedAt") or msg.get("requestedAt") or data.get("utime") or data.get("ctime")
            day = ms_to_local_date(stamp, timezone) or session_day
            if not day:
                continue

            if role in ("user", "assistant", "system"):
                day_messages += 1
                message_count += 1
                row = by_date.setdefault(day, empty_day())
                row["messages"] += 1
                touched_days.add(day)

            usage = msg.get("tokenUsage")
            if not isinstance(usage, dict):
                continue
            ctx = safe_int(usage.get("contextUsed"))
            if ctx <= 0:
                continue
            delta = ctx if prev_ctx is None else max(0, ctx - prev_ctx)
            prev_ctx = ctx
            if delta <= 0:
                continue
            row = by_date.setdefault(day, empty_day())
            row["tokens"] += delta
            row["input"] += delta
            day_tokens += delta

        if day_tokens or day_messages:
            sessions_with_usage += 1
            # Attribute one session count to the session create day (or first touched day).
            anchor = session_day or (sorted(touched_days)[0] if touched_days else "")
            if anchor:
                by_date.setdefault(anchor, empty_day())["sessions"] += 1

    daily_points: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        row = by_date[date_key]
        if not (row["tokens"] or row["sessions"] or row["messages"]):
            continue
        daily_points.append({"date": date_key, **row})

    total_tokens = sum(safe_int(p.get("tokens")) for p in daily_points)
    first = daily_points[0]["date"] if daily_points else ""
    last = daily_points[-1]["date"] if daily_points else ""
    return {
        "available": bool(daily_points) or session_files > 0,
        "session_files": session_files,
        "sessions_with_usage": sessions_with_usage,
        "message_count": message_count,
        "total_tokens": total_tokens,
        "cost": 0.0,
        "history": {"first": first, "last": last},
        "daily_timeline": daily_points,
        "note": (
            "Comate tokens are positive contextUsed deltas from local chat sessions; "
            "not billable API tokens. Cost is always 0."
        ),
    }
