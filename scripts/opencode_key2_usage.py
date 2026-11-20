#!/usr/bin/env python3
"""Usage of the second One API key, metered from local OpenCode records.

The second key belongs to another One API account, so its gateway logs are not
visible to this account's login. OpenCode's ``oneapi-key-split`` plugin appends
one line per One API response to ``requests-YYYY-MM.jsonl`` in its ledger
directory (time, session, request kind, which key, HTTP status; never the key).
Joining those lines with the assistant and compaction rows of OpenCode's local
database gives the exact tokens of every call made with the second key.
``backfill-YYYY-MM.jsonl`` holds the same lines for calls made before the plugin
kept a ledger; they were derived once by matching local calls against this
account's own gateway logs (a call without a key-1 log record used key 2).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SECTION_VERSION = 1
# Re-read this many calendar days on every run; it must cover the One API window.
RECOMPUTE_DAYS = 40
ALIASES_PATH = Path(__file__).resolve().parent / "opencode_oneapi_models.v1.json"
# The plugin logs a call when its response headers arrive: after OpenCode
# creates the message and before it records the stream (measured 2.5-34 s after
# creation and up to 61 s before ``time.streamed``). The next call is created
# after this one completes, so consecutive windows never overlap.
# Compaction rows record only their start; the response can take minutes.
COMPACTION_WINDOW_MS = 15 * 60_000
TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write")

CALL_QUERY = """
select session_id, type, time_created,
  coalesce(json_extract(data, '$.time.streamed'), 0),
  coalesce(json_extract(data, '$.time.completed'), 0),
  coalesce(json_extract(data, '$.model.providerID'), ''),
  coalesce(json_extract(data, '$.model.id'), ''),
  coalesce(json_extract(data, '$.tokens.input'), 0),
  coalesce(json_extract(data, '$.tokens.output'), 0),
  coalesce(json_extract(data, '$.tokens.reasoning'), 0),
  coalesce(json_extract(data, '$.tokens.cache.read'), 0),
  coalesce(json_extract(data, '$.tokens.cache.write'), 0)
from session_message
where type in ('assistant', 'compaction') and time_created >= ?
order by time_created
"""


def default_db_path(home: Path) -> Path:
    explicit = os.environ.get("OPENCODE_DB_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return home / ".local" / "share" / "opencode" / "opencode.db"


def default_ledger_dir(home: Path) -> Path:
    explicit = os.environ.get("ONEAPI_KEY_LEDGER_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return home / ".local" / "state" / "oneapi-key-split"


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def load_aliases(path: Path = ALIASES_PATH) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, dict):
        raise ValueError(f"model alias file has no aliases: {path}")
    return {str(key): str(value) for key, value in aliases.items()}


def gateway_model(model_id: Any, aliases: dict[str, str]) -> tuple[str, bool]:
    """Map an OpenCode model ID to the name the gateway logs, if known."""
    name = str(model_id or "").strip()
    if name in aliases:
        return aliases[name], True
    if name and not any(character.isspace() for character in name):
        return name.lower(), True
    return name or "unknown", False


def read_calls(db_path: Path, since_ms: int) -> list[dict[str, Any]]:
    uri = "file:" + urllib.parse.quote(str(db_path)) + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        records = connection.execute(CALL_QUERY, (since_ms,)).fetchall()
    finally:
        connection.close()
    calls = []
    for record in records:
        (session, kind, created, streamed, completed, provider, model,
         prompt, output, reasoning, cache_read, cache_write) = record
        calls.append(
            {
                "session": str(session),
                "kind": str(kind),
                "created": _count(created),
                "streamed": _count(streamed) or max(_count(created), _count(completed)),
                "provider": str(provider),
                "model": str(model),
                "input": _count(prompt),
                # The gateway's completion tokens include reasoning tokens.
                "output": _count(output) + _count(reasoning),
                "cache_read": _count(cache_read),
                "cache_write": _count(cache_write),
            }
        )
    return calls


def read_ledger(directory: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stats = {"lines": 0, "backfill_lines": 0, "first_line_at": 0}
    if directory.is_dir():
        paths = sorted(directory.glob("requests-*.jsonl")) + sorted(
            directory.glob("backfill-*.jsonl")
        )
        for path in paths:
            backfill = path.name.startswith("backfill-")
            for raw in path.read_text(encoding="utf-8").splitlines():
                try:
                    line = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(line, dict) or not 200 <= _count(line.get("st")) < 300:
                    continue
                key = _count(line.get("key"))
                if key not in (1, 2):
                    continue
                item = {"t": _count(line.get("t")), "kind": str(line.get("k") or ""), "key": key}
                by_session[str(line.get("s") or "")].append(item)
                if backfill:
                    stats["backfill_lines"] += 1
                else:
                    stats["lines"] += 1
                    if not stats["first_line_at"] or item["t"] < stats["first_line_at"]:
                        stats["first_line_at"] = item["t"]
    for lines in by_session.values():
        lines.sort(key=lambda item: item["t"])
    return by_session, stats


def ledger_line(call: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    low = call["created"]
    if call["kind"] == "compaction":
        high = call["created"] + COMPACTION_WINDOW_MS
        return next(
            (line for line in lines if line["kind"] == "compaction" and low <= line["t"] <= high),
            None,
        )
    found = None
    for line in lines:
        if line["kind"] == "primary" and low <= line["t"] <= call["streamed"]:
            found = line  # a retried call leaves several lines; the last one carried it
    return found


def call_tokens(call: dict[str, Any]) -> int:
    return sum(call[field] for field in TOKEN_FIELDS)


def collect_key2_daily(
    *,
    home: Path,
    timezone: str,
    since: str,
    until: str,
    aliases: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Daily second-key usage per gateway model for ``since``..``until``.

    Returns ``None`` when this Mac has no OpenCode database.
    """
    db = default_db_path(home)
    if not db.is_file():
        return None
    tz = ZoneInfo(timezone)
    since_ms = int(
        dt.datetime.combine(dt.date.fromisoformat(since), dt.time.min, tzinfo=tz).timestamp()
        * 1000
    )
    calls = read_calls(db, since_ms)
    by_session, ledger_stats = read_ledger(default_ledger_dir(home))
    model_aliases = aliases if aliases is not None else load_aliases()

    # A forked session copies its parent's messages with identical times and
    # tokens: one call, several rows. Count it once; any copy's line attributes it.
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        signature = (
            call["kind"], call["created"], call["streamed"], call["provider"], call["model"],
            *(call[field] for field in TOKEN_FIELDS),
        )
        groups[signature].append(call)

    stats = {
        "calls": 0,
        "key1_calls": 0,
        "key2_calls": 0,
        "unattributed_calls": 0,
        "unattributed_after_ledger": 0,
        "fork_copies": 0,
        "ledger_lines": ledger_stats["lines"],
        "backfill_lines": ledger_stats["backfill_lines"],
        "ledger_since": (
            dt.datetime.fromtimestamp(ledger_stats["first_line_at"] / 1000, tz=tz).isoformat(timespec="seconds")
            if ledger_stats["first_line_at"]
            else ""
        ),
        "unmapped_models": [],
    }
    by_day: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    unmapped: set[str] = set()
    for members in groups.values():
        call = members[0]
        stats["fork_copies"] += len(members) - 1
        if call_tokens(call) <= 0:
            continue
        day = dt.datetime.fromtimestamp(call["created"] / 1000, tz=tz).date().isoformat()
        if not (since <= day <= until):
            continue
        stats["calls"] += 1
        line = next(
            (
                found
                for member in members
                if (found := ledger_line(member, by_session.get(member["session"], [])))
            ),
            None,
        )
        if line is None:
            stats["unattributed_calls"] += 1
            if ledger_stats["first_line_at"] and call["created"] >= ledger_stats["first_line_at"]:
                stats["unattributed_after_ledger"] += 1
            continue
        if line["key"] != 2:
            stats["key1_calls"] += 1
            continue
        stats["key2_calls"] += 1
        model, mapped = gateway_model(call["model"], model_aliases)
        if not mapped:
            unmapped.add(model)
        entry = by_day[day].setdefault(
            model,
            {"model": model, "raw_models": [], "requests": 0, **{field: 0 for field in TOKEN_FIELDS}, "tokens": 0},
        )
        if call["model"] and call["model"] not in entry["raw_models"]:
            entry["raw_models"].append(call["model"])
        entry["requests"] += 1
        for field in TOKEN_FIELDS:
            entry[field] += call[field]
        entry["tokens"] += call_tokens(call)
    stats["unmapped_models"] = sorted(unmapped)
    daily = []
    for day in sorted(by_day):
        models = sorted(by_day[day].values(), key=lambda entry: (-entry["tokens"], entry["model"]))
        for entry in models:
            entry["raw_models"].sort()
        daily.append(
            {
                "date": day,
                "requests": sum(entry["requests"] for entry in models),
                "tokens": sum(entry["tokens"] for entry in models),
                "models": models,
            }
        )
    return {"daily": daily, "stats": stats}


def recompute_from(today: str) -> str:
    return (dt.date.fromisoformat(today) - dt.timedelta(days=RECOMPUTE_DAYS - 1)).isoformat()


def merge_section(
    existing: Any,
    fresh: dict[str, Any],
    *,
    since: str,
    collected_at: str,
) -> dict[str, Any]:
    """Replace re-read days, keep older ones, and never lower a stored day."""
    stored = {
        str(point.get("date")): point
        for point in (existing.get("daily") if isinstance(existing, dict) else None) or []
        if isinstance(point, dict) and point.get("date")
    }
    kept_higher = []
    for point in fresh.get("daily") or []:
        date_key = str(point["date"])
        previous = stored.get(date_key)
        if previous is not None and _count(previous.get("tokens")) > _count(point.get("tokens")):
            kept_higher.append(date_key)
            continue
        stored[date_key] = point
    return {
        "version": SECTION_VERSION,
        "collected_at": collected_at,
        "recomputed_from": since,
        "stats": {**fresh.get("stats", {}), "kept_higher_dates": kept_higher},
        "daily": [stored[key] for key in sorted(stored)],
    }


def section_daily(section: Any) -> list[dict[str, Any]]:
    """Validated daily points of a fragment's ``oneapi_key2`` section."""
    if not isinstance(section, dict) or section.get("version") != SECTION_VERSION:
        return []
    points = []
    for point in section.get("daily") or []:
        if not isinstance(point, dict) or not point.get("date"):
            continue
        models = []
        for entry in point.get("models") or []:
            if not isinstance(entry, dict) or not entry.get("model"):
                continue
            values = {field: _count(entry.get(field)) for field in TOKEN_FIELDS}
            if sum(values.values()) != _count(entry.get("tokens")):
                raise ValueError(f"key-2 token components disagree for {point['date']} {entry['model']}")
            models.append({"model": str(entry["model"]), "requests": _count(entry.get("requests")), **values})
        points.append({"date": str(point["date"]), "models": models})
    return points
