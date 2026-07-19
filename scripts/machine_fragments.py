#!/usr/bin/env python3
"""Per-machine usage fragments and multi-machine merge.

Machine fragments are append-only ledgers:
- First run (missing/empty fragment): seed full local history once.
- Later runs: append missing dates; refresh *today* only; never rewrite past days.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import socket
import tempfile
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


def write_bytes_atomic(path: Path, data: bytes, mode: int | None = None) -> None:
    """Write bytes beside the destination and atomically replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = mode if mode is not None else (path.stat().st_mode & 0o777 if path.exists() else 0o644)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, target_mode)
        os.replace(temp_path, path)
        fsync_directory(path.parent)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON beside the destination and atomically replace it."""
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    write_bytes_atomic(path, data)


def json_transaction_journal_path(paths: list[Path]) -> Path:
    if not paths:
        raise ValueError("transaction has no targets")
    common_parent = Path(os.path.commonpath([str(path.resolve().parent) for path in paths]))
    return common_parent / ".ai-usage-json-transaction.json"


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def recover_json_transaction(journal_path: Path, expected_paths: list[Path]) -> bool:
    """Restore a transaction interrupted between target replacements."""
    if not journal_path.is_file():
        return False
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read transaction journal {journal_path}: {exc}") from exc
    if not isinstance(journal, dict):
        raise RuntimeError(f"invalid transaction journal: {journal_path}")
    targets = journal.get("targets")
    if journal.get("version") != 1 or not isinstance(targets, list):
        raise RuntimeError(f"invalid transaction journal: {journal_path}")

    expected = [str(path.resolve()) for path in expected_paths]
    recorded = [str(item.get("path") or "") for item in targets if isinstance(item, dict)]
    if recorded != expected:
        raise RuntimeError(
            f"transaction journal target mismatch: expected={expected}, recorded={recorded}"
        )

    for item in targets:
        try:
            path = Path(str(item["path"]))
            mode = int(item["mode"])
            data = base64.b64decode(str(item["data_b64"]), validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid transaction journal entry: {item!r}") from exc
        if mode < 0 or mode > 0o777:
            raise RuntimeError(f"invalid transaction journal mode: {mode}")
        write_bytes_atomic(path, data, mode)
    journal_path.unlink()
    fsync_directory(journal_path.parent)
    return True


def write_json_transaction(updates: list[tuple[Path, dict[str, Any]]]) -> None:
    """Apply JSON updates with exact rollback and crash-recovery journaling."""
    normalized = [(path.resolve(), payload) for path, payload in updates]
    paths = [path for path, _payload in normalized]
    if len(paths) != len(set(paths)):
        raise ValueError("transaction contains duplicate paths")
    journal_path = json_transaction_journal_path(paths)
    recover_json_transaction(journal_path, paths)

    originals: dict[Path, tuple[bytes, int]] = {}
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"transaction target not found: {path}")
        originals[path] = (path.read_bytes(), path.stat().st_mode & 0o777)

    journal_payload = {
        "version": 1,
        "targets": [
            {
                "path": str(path),
                "mode": mode,
                "data_b64": base64.b64encode(data).decode("ascii"),
            }
            for path, (data, mode) in originals.items()
        ],
    }
    write_bytes_atomic(
        journal_path,
        (json.dumps(journal_payload, ensure_ascii=True, indent=2) + "\n").encode("utf-8"),
        0o600,
    )
    fsync_directory(journal_path.parent)

    try:
        for path, payload in normalized:
            write_json_atomic(path, payload)
        journal_path.unlink()
        fsync_directory(journal_path.parent)
    except Exception as exc:
        rollback_errors: list[str] = []
        for path in reversed(paths):
            data, mode = originals[path]
            try:
                write_bytes_atomic(path, data, mode)
            except Exception as rollback_exc:  # pragma: no cover - catastrophic I/O failure
                rollback_errors.append(f"{path}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                f"JSON transaction failed ({exc}); rollback also failed: "
                + "; ".join(rollback_errors)
            ) from exc
        journal_path.unlink(missing_ok=True)
        fsync_directory(journal_path.parent)
        raise


def recover_codex_cache_transaction(
    machines_dir: Path,
    machine_id: str,
    usage_json_path: Path,
) -> dict[str, Any]:
    """Recover an interrupted cache migration without loading live collectors."""
    if not machine_id.strip():
        raise ValueError("machine id is required for Codex cache transaction recovery")
    mid = sanitize_machine_id(machine_id)
    target_fragment = fragment_path(machines_dir, mid)
    transaction_paths = [target_fragment.resolve(), usage_json_path.resolve()]
    journal_path = json_transaction_journal_path(transaction_paths)
    recovered = recover_json_transaction(journal_path, transaction_paths)
    return {
        "machine_id": mid,
        "recovered": recovered,
        "journal_path": str(journal_path),
        "fragment_path": str(target_fragment),
        "usage_json_path": str(usage_json_path),
    }


def strict_nonnegative_int(value: Any, field: str, date_key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid integer {field} for {date_key or '(unknown date)'}: {value!r}")
    if value < 0:
        raise ValueError(f"negative {field} for {date_key or '(unknown date)'}: {value}")
    return value


def backfill_codex_cache_daily(
    existing_daily: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Derive frozen Codex cache-read tokens without changing any other field.

    ccusage defines Codex total tokens as input + cache read + output. Historical
    fragments already persisted total/input/output at collection time, so the
    missing cache field can be reconstructed exactly without rereading mutable
    local session history.
    """
    rows: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    updated_days = 0
    unchanged_days = 0
    active_days = 0
    cache_before = 0
    cache_after = 0

    for original in existing_daily:
        if not isinstance(original, dict):
            raise ValueError("machine fragment daily row is not an object")
        row = dict(original)
        date_key = str(row.get("date") or "")
        if not date_key:
            raise ValueError("fragment row is missing date")
        if date_key in seen_dates:
            raise ValueError(f"duplicate fragment date: {date_key}")
        seen_dates.add(date_key)

        codex_fields = ("codex_tokens", "codex_input", "codex_cache_read", "codex_output")
        present_codex_fields = [name for name in codex_fields if name in row]
        if not present_codex_fields:
            rows.append(row)
            continue
        parsed = {
            name: strict_nonnegative_int(row.get(name), name, date_key)
            for name in present_codex_fields
        }
        total = parsed.get("codex_tokens", 0)
        if total == 0:
            nonzero = {name: value for name, value in parsed.items() if value != 0}
            if nonzero:
                raise ValueError(
                    f"zero Codex total has nonzero components for {date_key}: {nonzero}"
                )
            rows.append(row)
            continue

        active_days += 1
        required = ("codex_tokens", "codex_input", "codex_output")
        missing = [name for name in required if name not in row]
        if missing:
            raise ValueError(
                f"missing Codex components for {date_key or '(unknown date)'}: "
                + ", ".join(missing)
            )

        input_tokens = parsed["codex_input"]
        output_tokens = parsed["codex_output"]
        derived = total - input_tokens - output_tokens
        if derived < 0:
            raise ValueError(
                f"negative derived Codex cache for {date_key or '(unknown date)'}: {derived}"
            )

        current = parsed.get("codex_cache_read", 0)
        if current not in (0, derived):
            raise ValueError(
                f"existing Codex cache conflicts for {date_key or '(unknown date)'}: "
                f"stored={current}, derived={derived}"
            )

        cache_before += current
        cache_after += derived
        if current == derived:
            unchanged_days += 1
        else:
            row["codex_cache_read"] = derived
            updated_days += 1
        rows.append(row)

    return rows, {
        "active_days": active_days,
        "updated_days": updated_days,
        "unchanged_days": unchanged_days,
        "cache_tokens_before": cache_before,
        "cache_tokens_after": cache_after,
        "cache_tokens_added": cache_after - cache_before,
    }


def backfill_machine_codex_cache(
    machines_dir: Path,
    machine_id: str,
    *,
    dry_run: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Backfill one explicitly selected machine fragment."""
    mid = sanitize_machine_id(machine_id)
    path = fragment_path(machines_dir, mid)
    fragment = load_machine_fragment(machines_dir, mid)
    if fragment is None:
        raise FileNotFoundError(f"machine fragment not found: {path}")
    stored_mid = sanitize_machine_id(str(fragment.get("machine_id") or ""))
    if stored_mid != mid:
        raise ValueError(f"machine id mismatch: requested={mid}, stored={stored_mid}")

    rows, stats = backfill_codex_cache_daily(list(fragment.get("daily") or []))
    if not dry_run:
        payload = dict(fragment)
        payload["daily"] = rows
        write_json_atomic(path, payload)
    return path, stats


def backfill_merged_usage_codex_cache_daily(
    usage_daily: list[dict[str, Any]],
    fragments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Update only merged codex_cache_read values from machine fragments."""
    cache_by_date: dict[str, int] = {}
    active_dates: set[str] = set()
    for fragment in fragments:
        fragment_dates: set[str] = set()
        for row in fragment.get("daily") or []:
            if not isinstance(row, dict):
                raise ValueError("machine fragment daily row is not an object")
            date_key = str(row.get("date") or "")
            if not date_key:
                raise ValueError("machine fragment daily row is missing date")
            if date_key in fragment_dates:
                raise ValueError(f"duplicate fragment date: {date_key}")
            fragment_dates.add(date_key)
            total = strict_nonnegative_int(row.get("codex_tokens", 0), "codex_tokens", date_key)
            cache = strict_nonnegative_int(
                row.get("codex_cache_read", 0), "codex_cache_read", date_key
            )
            if total == 0 and cache != 0:
                raise ValueError(
                    f"zero Codex total has nonzero cache for {date_key}: {cache}"
                )
            if total > 0:
                active_dates.add(date_key)
            cache_by_date[date_key] = cache_by_date.get(date_key, 0) + cache

    usage_dates = {
        str(row.get("date") or "")
        for row in usage_daily
        if isinstance(row, dict) and row.get("date")
    }
    missing = sorted(active_dates - usage_dates)
    if missing:
        raise ValueError("merged usage is missing fragment dates: " + ", ".join(missing))

    rows: list[dict[str, Any]] = []
    updated_days = 0
    unchanged_days = 0
    cache_before = 0
    cache_after = 0
    seen_usage_dates: set[str] = set()
    for original in usage_daily:
        if not isinstance(original, dict):
            raise ValueError("merged usage daily row is not an object")
        row = dict(original)
        date_key = str(row.get("date") or "")
        if not date_key:
            raise ValueError("merged usage daily row is missing date")
        if date_key in seen_usage_dates:
            raise ValueError(f"duplicate merged usage date: {date_key}")
        seen_usage_dates.add(date_key)
        if date_key not in cache_by_date:
            rows.append(row)
            continue
        current = strict_nonnegative_int(
            row.get("codex_cache_read", 0), "codex_cache_read", date_key
        )
        merged = cache_by_date[date_key]
        cache_before += current
        cache_after += merged
        if current == merged:
            unchanged_days += 1
        else:
            row["codex_cache_read"] = merged
            updated_days += 1
        rows.append(row)

    return rows, {
        "updated_days": updated_days,
        "unchanged_days": unchanged_days,
        "cache_tokens_before": cache_before,
        "cache_tokens_after": cache_after,
        "cache_tokens_added": cache_after - cache_before,
    }


def load_machine_fragments_strict(machines_dir: Path) -> list[dict[str, Any]]:
    """Load every fragment for migration and reject any malformed sibling."""
    if not machines_dir.is_dir():
        raise FileNotFoundError(f"machines directory not found: {machines_dir}")
    fragments: list[dict[str, Any]] = []
    seen_machine_ids: set[str] = set()
    for path in sorted(machines_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read machine fragment {path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("daily"), list):
            raise ValueError(f"invalid machine fragment structure: {path}")
        raw_mid = str(data.get("machine_id") or "").strip()
        if not raw_mid:
            raise ValueError(f"machine fragment has no machine_id: {path}")
        mid = sanitize_machine_id(raw_mid)
        if mid != path.stem:
            raise ValueError(f"machine fragment id/path mismatch: {path.stem} != {mid}")
        if mid in seen_machine_ids:
            raise ValueError(f"duplicate machine_id: {mid}")
        seen_machine_ids.add(mid)
        backfill_codex_cache_daily(list(data.get("daily") or []))
        data["_path"] = str(path)
        fragments.append(data)
    if not fragments:
        raise ValueError(f"no machine fragments found: {machines_dir}")
    return fragments


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Machine fragment maintenance utilities.")
    parser.add_argument("--recover-codex-cache-transaction", action="store_true")
    parser.add_argument("--machines-dir", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args()
    if not args.recover_codex_cache_transaction:
        parser.error("--recover-codex-cache-transaction is required")
    try:
        result = recover_codex_cache_transaction(
            Path(args.machines_dir).expanduser(),
            args.machine_id,
            Path(args.json_out).expanduser(),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
