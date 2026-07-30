#!/usr/bin/env python3
"""Per-machine usage fragments and multi-machine merge.

Machine fragments are durable local ledgers.  ``mutable_from`` is the first
calendar day that is still provisional.  Every later collection re-reads that
day through today, so a machine can be offline for any number of days without
freezing the last partial snapshot forever.
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


LOCAL_TOOL_PREFIXES = ("codex", "claude")
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
    raise ValueError(
        "stable machine id is required; pass --machine-id or set AI_USAGE_MACHINE_ID"
    )


def fragment_path(machines_dir: Path, machine_id: str) -> Path:
    return machines_dir / f"{sanitize_machine_id(machine_id)}.json"


def tool_field_names(tool_token_fields: dict[str, list[str]], prefixes: tuple[str, ...]) -> list[str]:
    names: list[str] = []
    for prefix in prefixes:
        names.append(f"{prefix}_tokens")
        names.append(f"{prefix}_cost")
        for field in tool_token_fields.get(prefix, []):
            names.append(f"{prefix}_{field}")
    return names


def row_has_local_activity(row: dict[str, Any], safe_int: SafeInt, safe_float: SafeFloat) -> bool:
    for prefix in LOCAL_TOOL_PREFIXES:
        if safe_int(row.get(f"{prefix}_tokens")) or safe_float(row.get(f"{prefix}_cost")):
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
    for prefix in LOCAL_TOOL_PREFIXES:
        models = row.get(f"{prefix}_models")
        out[f"{prefix}_models"] = [
            dict(model) for model in models if isinstance(model, dict)
        ] if isinstance(models, list) else []
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


def previous_day(date_key: str) -> str:
    day = dt.date.fromisoformat(date_key)
    return (day - dt.timedelta(days=1)).isoformat()


def _valid_day(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        return dt.date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""


def fragment_mutable_from(fragment: dict[str, Any] | None, today: str) -> str:
    """Return the earliest day that must be re-collected.

    New fragments persist this value explicitly.  Legacy fragments did not, so
    their first seeded day is reopened once: the initial seed was a point-in-time
    snapshot and that day may contain usage after the snapshot.  Older days were
    already complete at seed time.
    """
    if not fragment or is_first_seed(fragment):
        return ""

    explicit = _valid_day(fragment.get("mutable_from"))
    if explicit:
        return explicit if explicit <= today else today

    seeded_at = str(fragment.get("seeded_at") or "").strip()
    if seeded_at:
        try:
            parsed = dt.datetime.fromisoformat(seeded_at.replace("Z", "+00:00"))
            seeded_day = parsed.date().isoformat()
            return seeded_day if seeded_day <= today else today
        except ValueError:
            pass

    dates = sorted(fragment_dates(fragment))
    if dates:
        return dates[0] if dates[0] <= today else today
    return ""


def append_range_start(fragment: dict[str, Any] | None, today: str) -> str:
    """Earliest local calendar day we still need from collectors.

    - First seed: empty string → caller collects full history.
    - Later: the persisted provisional boundary through today.
    - Legacy fragments reopen their original seed day once to repair snapshots
      that the previous today-only policy froze too early.
    """
    return fragment_mutable_from(fragment, today)


def _copy_tool_group(
    target: dict[str, Any],
    source: dict[str, Any],
    prefix: str,
    tool_token_fields: dict[str, list[str]],
) -> None:
    target[f"{prefix}_tokens"] = source[f"{prefix}_tokens"]
    target[f"{prefix}_cost"] = source[f"{prefix}_cost"]
    for field in tool_token_fields.get(prefix, []):
        target[f"{prefix}_{field}"] = source[f"{prefix}_{field}"]
    target[f"{prefix}_models"] = [
        dict(model)
        for model in source.get(f"{prefix}_models", [])
        if isinstance(model, dict)
    ]


def _tool_regressed(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    prefix: str,
    safe_int: SafeInt,
) -> bool:
    if safe_int(incoming.get(f"{prefix}_tokens")) < safe_int(
        existing.get(f"{prefix}_tokens")
    ):
        return True
    return False


def merge_append_daily(
    existing_daily: list[dict[str, Any]],
    incoming_daily: list[dict[str, Any]],
    today: str,
    tool_token_fields: dict[str, list[str]],
    safe_int: SafeInt,
    safe_float: SafeFloat,
    *,
    mutable_from: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge a complete collector snapshot into the provisional window.

    Existing dates before ``mutable_from`` are immutable.  Dates at or after it
    are refreshed tool-by-tool.  A lower replacement is treated as source
    regression (usually cleaned local logs): the older high-water snapshot is
    kept and that date remains provisional.
    """
    by_date: dict[str, dict[str, Any]] = {}
    for row in existing_daily:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        date_key = str(row["date"])
        by_date[date_key] = strip_row_to_local(row, tool_token_fields, safe_int, safe_float)

    incoming_by_date: dict[str, dict[str, Any]] = {}
    for row in incoming_daily:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        date_key = str(row["date"])
        if date_key > today:
            continue
        incoming_by_date[date_key] = strip_row_to_local(
            row, tool_token_fields, safe_int, safe_float
        )

    stats: dict[str, Any] = {
        "frozen_kept": 0,
        "appended": 0,
        "refreshed": 0,
        "today_updated": 0,
        "skipped": 0,
        "regression_kept": 0,
        "regression_dates": [],
    }
    regression_dates: set[str] = set()

    for date_key in sorted(set(by_date) | set(incoming_by_date)):
        existing = by_date.get(date_key)
        incoming = incoming_by_date.get(date_key)
        frozen = bool(mutable_from) and date_key < mutable_from

        if frozen and existing is not None:
            stats["frozen_kept"] += 1
            continue
        if incoming is None:
            if (
                existing is not None
                and date_key < today
                and (not mutable_from or date_key >= mutable_from)
                and row_has_local_activity(existing, safe_int, safe_float)
            ):
                regression_dates.add(date_key)
                stats["regression_kept"] += 1
            continue
        if not row_has_local_activity(incoming, safe_int, safe_float) and existing is None:
            stats["skipped"] += 1
            continue
        if existing is None:
            by_date[date_key] = incoming
            stats["appended"] += 1
            continue

        merged = dict(existing)
        for prefix in LOCAL_TOOL_PREFIXES:
            if _tool_regressed(existing, incoming, prefix, safe_int):
                regression_dates.add(date_key)
                stats["regression_kept"] += 1
                continue
            _copy_tool_group(merged, incoming, prefix, tool_token_fields)
        by_date[date_key] = merged
        if date_key == today:
            stats["today_updated"] += 1
        else:
            stats["refreshed"] += 1

    stats["regression_dates"] = sorted(regression_dates)

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
    """Atomically update machines/<id>.json and advance its open boundary."""
    machines_dir.mkdir(parents=True, exist_ok=True)
    mid = sanitize_machine_id(machine_id)
    path = fragment_path(machines_dir, mid)
    existing = None if force_reseed else load_machine_fragment(machines_dir, mid)
    first = force_reseed or is_first_seed(existing)

    existing_timezone = str((existing or {}).get("timezone") or "").strip()
    if existing_timezone and existing_timezone != timezone:
        raise ValueError(
            f"machine fragment timezone changed for {mid}: "
            f"{existing_timezone} != {timezone}"
        )

    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    mutable_from = "" if first else fragment_mutable_from(existing, today)
    if first:
        merged, stats = merge_append_daily(
            [],
            incoming_daily,
            today,
            tool_token_fields,
            safe_int,
            safe_float,
            mutable_from="",
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
            mutable_from=mutable_from,
        )
        mode = "append"
        seeded_at = str((existing or {}).get("seeded_at") or now)

    regression_dates = [
        str(value)
        for value in stats.get("regression_dates") or []
        if str(value) < today
    ]
    safety_window_start = previous_day(today)
    next_mutable_from = min([safety_window_start, *regression_dates])

    payload = {
        "machine_id": mid,
        "hostname": hostname or socket.gethostname(),
        "collected_at": now,
        "seeded_at": seeded_at,
        "seeded": True,
        "append_mode": True,
        "mutable_from": next_mutable_from,
        "last_mode": mode,
        "last_append_stats": stats,
        "timezone": timezone,
        "scope": "machine-local",
        "tools": list(LOCAL_TOOL_PREFIXES),
        "policy": (
            "Durable ledger: dates before mutable_from are finalized; every run "
            "re-collects mutable_from through today and always keeps yesterday + "
            "today open. Lower source snapshots remain at their previous high-water."
        ),
        "daily": merged,
    }
    write_json_atomic(path, payload)
    meta = {
        "path": str(path),
        "mode": mode,
        "stats": stats,
        "days": len(merged),
        "first_seed": first,
        "collect_from": mutable_from or "(full seed)",
        "mutable_from": next_mutable_from,
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


def validate_unique_fragment_hostnames(fragments: list[dict[str, Any]]) -> None:
    """Reject active fragments that identify the same physical host twice."""
    by_hostname: dict[str, tuple[str, str]] = {}
    for fragment in fragments:
        hostname = str(fragment.get("hostname") or "").strip()
        if not hostname:
            continue
        machine_id = str(
            fragment.get("machine_id")
            or Path(str(fragment.get("_path") or "unknown")).stem
        )
        hostname_key = hostname.casefold()
        previous = by_hostname.get(hostname_key)
        if previous is not None and previous[0] != machine_id:
            raise ValueError(
                f"duplicate machine hostname {previous[1]}: "
                f"{previous[0]}, {machine_id}"
            )
        by_hostname[hostname_key] = (machine_id, hostname)


def merge_local_fragments(
    fragments: list[dict[str, Any]],
    empty_daily_row: Callable[[str], dict[str, Any]],
    tool_token_fields: dict[str, list[str]],
    safe_int: SafeInt,
    safe_float: SafeFloat,
) -> tuple[list[dict[str, Any]], list[str]]:
    """SUM machine-local tool columns across fragments. Cursor left at 0."""
    validate_unique_fragment_hostnames(fragments)
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
            for prefix in LOCAL_TOOL_PREFIXES:
                by_model: dict[str, dict[str, Any]] = {}
                for model in [
                    *(
                        target.get(f"{prefix}_models")
                        if isinstance(target.get(f"{prefix}_models"), list)
                        else []
                    ),
                    *(
                        row.get(f"{prefix}_models")
                        if isinstance(row.get(f"{prefix}_models"), list)
                        else []
                    ),
                ]:
                    if not isinstance(model, dict):
                        continue
                    name = str(model.get("model") or "Legacy unknown").strip()
                    acc = by_model.setdefault(
                        name,
                        {"model": name, "tokens": 0, "cost": 0.0},
                    )
                    acc["tokens"] += safe_int(model.get("tokens"))
                    acc["cost"] += safe_float(model.get("cost"))
                target[f"{prefix}_models"] = sorted(
                    by_model.values(),
                    key=lambda item: (-safe_int(item.get("tokens")), str(item.get("model"))),
                )

    rows: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        row = by_date[date_key]
        for prefix in LOCAL_TOOL_PREFIXES:
            models = row.get(f"{prefix}_models")
            models = models if isinstance(models, list) else []
            attributed_tokens = sum(safe_int(model.get("tokens")) for model in models)
            attributed_cost = sum(safe_float(model.get("cost")) for model in models)
            remainder_tokens = max(
                0, safe_int(row.get(f"{prefix}_tokens")) - attributed_tokens
            )
            remainder_cost = max(
                0.0, safe_float(row.get(f"{prefix}_cost")) - attributed_cost
            )
            if remainder_tokens or remainder_cost > 1e-9:
                models = [
                    *models,
                    {
                        "model": "Legacy unknown",
                        "tokens": remainder_tokens,
                        "cost": remainder_cost,
                    },
                ]
            row[f"{prefix}_models"] = sorted(
                models,
                key=lambda item: (-safe_int(item.get("tokens")), str(item.get("model"))),
            )
        rows.append(row)
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
    cursor_mutable_from: str = "",
    freeze_cursor_history: bool = True,
    reconciliation_stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply Cursor points onto local rows.

    When freeze_cursor_history is True (default), dates before
    ``cursor_mutable_from`` that already have cursor_* values are left unchanged.
    The open window is refreshed through today.  An empty boundary intentionally
    reopens all Cursor history once when migrating a legacy usage.json.
    """
    by_date = {str(r["date"]): dict(r) for r in daily_rows if isinstance(r, dict) and r.get("date")}
    today_key = today or ""
    regression_dates: set[str] = set()

    for point in cursor_pts:
        if not isinstance(point, dict):
            continue
        date_key = str(point.get("date") or "")
        if not date_key:
            continue
        row = by_date.setdefault(date_key, empty_daily_row(date_key))
        if (
            freeze_cursor_history
            and cursor_mutable_from
            and date_key < cursor_mutable_from
            and (safe_int(row.get("cursor_tokens")) or safe_float(row.get("cursor_cost")))
        ):
            continue
        if today_key and date_key > today_key:
            continue
        if safe_int(point.get("tokens")) < safe_int(row.get("cursor_tokens")):
            # A partial Dashboard page must not replace a higher snapshot.  The
            # cursor boundary will remain open when the API reports incomplete.
            regression_dates.add(date_key)
            continue
        apply_tool_point(row, "cursor", point)

    if reconciliation_stats is not None:
        reconciliation_stats["regression_dates"] = sorted(regression_dates)
        reconciliation_stats["regression_kept"] = len(regression_dates)

    rows: list[dict[str, Any]] = []
    for date_key in sorted(by_date):
        row = by_date[date_key]
        ct = safe_int(row.get("codex_tokens"))
        lt = safe_int(row.get("claude_tokens"))
        ut = safe_int(row.get("cursor_tokens"))
        cc = safe_float(row.get("codex_cost"))
        lc = safe_float(row.get("claude_cost"))
        uc = safe_float(row.get("cursor_cost"))
        if ct or lt or ut or cc or lc or uc:
            row["total_tokens"] = ct + lt + ut
            row["total_cost"] = cc + lc + uc
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
