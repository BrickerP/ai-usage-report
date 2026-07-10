#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from typing import Any


USAGE_PATTERN = re.compile(
    r"(usage|token|tokens|cost|billing|credit|quota|premium|requestId|conversationId|composer|generation)",
    re.IGNORECASE,
)
CODEX_TOKEN_FIELDS = [
    "input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
]
CLAUDE_TOKEN_FIELDS = [
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "total_recorded_tokens",
    "ephemeral_5m_input_tokens",
    "ephemeral_1h_input_tokens",
    "web_search_requests",
    "web_fetch_requests",
]


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def local_iso_from_mtime(path: Path) -> str:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        return ""


def compact_json(value: Any, limit: int = 800) -> str:
    if value is None:
        return ""
    try:
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except TypeError:
        text = str(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            if isinstance(value.get(key), str):
                return value[key]
    return ""


def snippet(value: Any, limit: int = 240) -> str:
    text = " ".join(content_text(value).split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def iter_jsonl(path: Path):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield line_no, json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def safe_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def date_key(timestamp: str) -> str:
    if not timestamp:
        return ""
    raw = timestamp.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return timestamp[:10]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d")


def update_range(summary: dict[str, Any], timestamp: str) -> None:
    if not timestamp:
        return
    current_first = summary.get("first_timestamp") or ""
    current_last = summary.get("last_timestamp") or ""
    if not current_first or timestamp < current_first:
        summary["first_timestamp"] = timestamp
    if not current_last or timestamp > current_last:
        summary["last_timestamp"] = timestamp


def add_counts(target: dict[str, int], values: dict[str, Any], fields: list[str]) -> None:
    for field in fields:
        target[field] = target.get(field, 0) + safe_int(values.get(field))


def new_counter(fields: list[str]) -> dict[str, int]:
    return {field: 0 for field in fields}


def write_aggregate_csv(
    path: Path,
    group_field: str,
    grouped_counts: dict[str, dict[str, int]],
    fields: list[str],
) -> None:
    rows = []
    for group in sorted(grouped_counts):
        rows.append({group_field: group, **grouped_counts[group]})
    write_csv(path, [group_field, *fields], rows)


def record_row(
    *,
    tool: str,
    record_kind: str,
    timestamp: str,
    project: str,
    session_id: str,
    role: str,
    model: str,
    source_file: Path,
    line_no: int,
    has_usage: bool,
    usage: Any,
    include_snippets: bool,
    content: Any,
) -> dict[str, Any]:
    row = {
        "tool": tool,
        "record_kind": record_kind,
        "timestamp": timestamp,
        "project": project,
        "session_id": session_id,
        "role": role,
        "model": model,
        "source_file": str(source_file),
        "line_no": line_no,
        "has_usage": "yes" if has_usage else "no",
        "usage_json": compact_json(usage),
    }
    if include_snippets:
        row["snippet"] = snippet(content)
    return row


def parse_codex(home: Path, include_snippets: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    roots = [home / ".codex" / "sessions", home / ".codex" / "archived_sessions"]
    files = [path for root in roots if root.exists() for path in root.rglob("*.jsonl")]
    rows: list[dict[str, Any]] = []
    token_totals = new_counter(CODEX_TOKEN_FIELDS)
    by_model: dict[str, dict[str, int]] = defaultdict(lambda: new_counter(CODEX_TOKEN_FIELDS))
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: new_counter(CODEX_TOKEN_FIELDS))
    token_range: dict[str, Any] = {}
    max_context_window = 0
    token_event_fingerprints: set[tuple[str, str]] = set()
    for path in files:
        session_id = ""
        project = ""
        model = ""
        for line_no, obj in iter_jsonl(path):
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            typ = obj.get("type", "")
            timestamp = obj.get("timestamp") or payload.get("timestamp") or ""
            if typ == "session_meta":
                session_id = str(payload.get("id") or session_id)
                project = str(payload.get("cwd") or project)
                continue
            if typ == "turn_context":
                session_id = str(payload.get("turn_id") or session_id)
                project = str(payload.get("cwd") or project)
                model = str(payload.get("model") or model)
                continue
            if typ == "response_item" and payload.get("type") == "message":
                role = str(payload.get("role") or "")
                if not role:
                    continue
                rows.append(
                    record_row(
                        tool="codex",
                        record_kind="message",
                        timestamp=timestamp,
                        project=project,
                        session_id=session_id,
                        role=role,
                        model=model,
                        source_file=path,
                        line_no=line_no,
                        has_usage=False,
                        usage=None,
                        include_snippets=include_snippets,
                        content=payload.get("content"),
                    )
                )
            elif typ == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info")
                rows.append(
                    record_row(
                        tool="codex",
                        record_kind="token_count",
                        timestamp=timestamp,
                        project=project,
                        session_id=session_id,
                        role="",
                        model=model,
                        source_file=path,
                        line_no=line_no,
                        has_usage=info is not None,
                        usage=info,
                        include_snippets=include_snippets,
                        content=None,
                    )
                )
                if isinstance(info, dict):
                    max_context_window = max(max_context_window, safe_int(info.get("model_context_window")))
                    last_usage = info.get("last_token_usage")
                    total_usage = info.get("total_token_usage")
                    if isinstance(last_usage, dict):
                        fingerprint = (str(path), compact_json(total_usage or last_usage, limit=4000))
                        if fingerprint not in token_event_fingerprints:
                            token_event_fingerprints.add(fingerprint)
                            normalized = {
                                "input_tokens": safe_int(last_usage.get("input_tokens")),
                                "cached_input_tokens": safe_int(last_usage.get("cached_input_tokens")),
                                "uncached_input_tokens": max(
                                    safe_int(last_usage.get("input_tokens")) - safe_int(last_usage.get("cached_input_tokens")),
                                    0,
                                ),
                                "output_tokens": safe_int(last_usage.get("output_tokens")),
                                "reasoning_output_tokens": safe_int(last_usage.get("reasoning_output_tokens")),
                                "total_tokens": safe_int(last_usage.get("total_tokens")),
                            }
                            add_counts(token_totals, normalized, CODEX_TOKEN_FIELDS)
                            add_counts(by_model[model or "unknown"], normalized, CODEX_TOKEN_FIELDS)
                            day = date_key(timestamp)
                            if day:
                                add_counts(by_day[day], normalized, CODEX_TOKEN_FIELDS)
                            update_range(token_range, timestamp)
    summary = {
        "session_files": len(files),
        "records": len(rows),
        "user_messages": sum(1 for row in rows if row["record_kind"] == "message" and row["role"] == "user"),
        "token_count_events": sum(1 for row in rows if row["record_kind"] == "token_count"),
        "deduped_token_events": len(token_event_fingerprints),
        "token_totals": token_totals,
        "token_totals_by_model": dict(by_model),
        "token_totals_by_day": dict(by_day),
        "token_range": token_range,
        "max_context_window": max_context_window,
        "paths": [str(root) for root in roots],
    }
    return rows, summary


def parse_claude(home: Path, include_snippets: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    roots = [home / ".claude" / "projects", home / ".claude" / "sessions"]
    files = [path for root in roots if root.exists() for path in root.rglob("*.jsonl")]
    rows: list[dict[str, Any]] = []
    token_totals = new_counter(CLAUDE_TOKEN_FIELDS)
    by_model: dict[str, dict[str, int]] = defaultdict(lambda: new_counter(CLAUDE_TOKEN_FIELDS))
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: new_counter(CLAUDE_TOKEN_FIELDS))
    token_range: dict[str, Any] = {}
    service_tiers: Counter[str] = Counter()
    inference_geos: Counter[str] = Counter()
    unique_usage_records: set[tuple[str, str]] = set()
    for path in files:
        for line_no, obj in iter_jsonl(path):
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            top_type = str(obj.get("type") or "")
            role = str(msg.get("role") or (top_type if top_type in {"user", "assistant", "system"} else ""))
            usage = msg.get("usage") or obj.get("usage")
            if not role and usage is None:
                continue
            model = str(msg.get("model") or obj.get("model") or "")
            rows.append(
                record_row(
                    tool="claude-code",
                    record_kind="message" if role else "usage",
                    timestamp=str(obj.get("timestamp") or ""),
                    project=str(obj.get("cwd") or ""),
                    session_id=str(obj.get("sessionId") or ""),
                    role=role,
                    model=model,
                    source_file=path,
                    line_no=line_no,
                    has_usage=usage is not None,
                    usage=usage,
                    include_snippets=include_snippets,
                    content=msg.get("content"),
                )
            )
            if isinstance(usage, dict):
                unique_key = str(obj.get("requestId") or obj.get("uuid") or f"{path}:{line_no}")
                fingerprint = (str(path), unique_key)
                if fingerprint not in unique_usage_records:
                    unique_usage_records.add(fingerprint)
                    cache_creation = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
                    server_tool_use = usage.get("server_tool_use") if isinstance(usage.get("server_tool_use"), dict) else {}
                    normalized = {
                        "input_tokens": safe_int(usage.get("input_tokens")),
                        "cache_creation_input_tokens": safe_int(usage.get("cache_creation_input_tokens")),
                        "cache_read_input_tokens": safe_int(usage.get("cache_read_input_tokens")),
                        "output_tokens": safe_int(usage.get("output_tokens")),
                        "total_recorded_tokens": safe_int(usage.get("input_tokens"))
                        + safe_int(usage.get("cache_creation_input_tokens"))
                        + safe_int(usage.get("cache_read_input_tokens"))
                        + safe_int(usage.get("output_tokens")),
                        "ephemeral_5m_input_tokens": safe_int(cache_creation.get("ephemeral_5m_input_tokens")),
                        "ephemeral_1h_input_tokens": safe_int(cache_creation.get("ephemeral_1h_input_tokens")),
                        "web_search_requests": safe_int(server_tool_use.get("web_search_requests")),
                        "web_fetch_requests": safe_int(server_tool_use.get("web_fetch_requests")),
                    }
                    add_counts(token_totals, normalized, CLAUDE_TOKEN_FIELDS)
                    add_counts(by_model[model or "unknown"], normalized, CLAUDE_TOKEN_FIELDS)
                    day = date_key(str(obj.get("timestamp") or ""))
                    if day:
                        add_counts(by_day[day], normalized, CLAUDE_TOKEN_FIELDS)
                    update_range(token_range, str(obj.get("timestamp") or ""))
                    if usage.get("service_tier"):
                        service_tiers[str(usage.get("service_tier"))] += 1
                    if usage.get("inference_geo"):
                        inference_geos[str(usage.get("inference_geo"))] += 1
    usage_data = home / ".claude" / "usage-data"
    summary = {
        "session_files": len(files),
        "records": len(rows),
        "user_messages": sum(1 for row in rows if row["record_kind"] == "message" and row["role"] == "user"),
        "usage_records": sum(1 for row in rows if row["has_usage"] == "yes"),
        "deduped_usage_records": len(unique_usage_records),
        "token_totals": token_totals,
        "token_totals_by_model": dict(by_model),
        "token_totals_by_day": dict(by_day),
        "token_range": token_range,
        "service_tiers": dict(service_tiers),
        "inference_geos": dict(inference_geos),
        "paths": [str(root) for root in roots] + ([str(usage_data)] if usage_data.exists() else []),
    }
    return rows, summary


def cursor_project_from_path(path: Path, home: Path) -> tuple[str, str]:
    projects_root = home / ".cursor" / "projects"
    try:
        rel = path.relative_to(projects_root)
        parts = rel.parts
        project = parts[0] if parts else ""
        session_id = ""
        if "agent-transcripts" in parts:
            idx = parts.index("agent-transcripts")
            if idx + 1 < len(parts):
                session_id = parts[idx + 1]
        return project, session_id
    except ValueError:
        return "", ""


def find_usage_like(value: Any) -> Any:
    matches: dict[str, Any] = {}

    def walk(obj: Any, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for key, child in obj.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                if USAGE_PATTERN.search(str(key)):
                    matches[path] = child
                walk(child, path)
        elif isinstance(obj, list):
            for index, child in enumerate(obj[:50]):
                walk(child, f"{prefix}[{index}]")

    walk(value)
    return matches or None


def parse_cursor(home: Path, include_snippets: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = home / ".cursor" / "projects"
    files = list(root.rglob("agent-transcripts/**/*.jsonl")) if root.exists() else []
    rows: list[dict[str, Any]] = []
    for path in files:
        project, session_id = cursor_project_from_path(path, home)
        fallback_timestamp = local_iso_from_mtime(path)
        for line_no, obj in iter_jsonl(path):
            role = str(obj.get("role") or "")
            message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            usage = find_usage_like(obj)
            if not role and usage is None:
                continue
            rows.append(
                record_row(
                    tool="cursor",
                    record_kind="message" if role else "usage-like",
                    timestamp=str(obj.get("timestamp") or fallback_timestamp),
                    project=project,
                    session_id=session_id,
                    role=role,
                    model=str(obj.get("model") or message.get("model") or ""),
                    source_file=path,
                    line_no=line_no,
                    has_usage=usage is not None,
                    usage=usage,
                    include_snippets=include_snippets,
                    content=message.get("content"),
                )
            )
    summary = {
        "transcript_files": len(files),
        "records": len(rows),
        "user_messages": sum(1 for row in rows if row["record_kind"] == "message" and row["role"] == "user"),
        "usage_like_records": sum(1 for row in rows if row["has_usage"] == "yes"),
        "paths": [str(root)],
    }
    return rows, summary


def sqlite_connect_ro(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def inspect_cursor_ai_tracking(home: Path, out_dir: Path) -> dict[str, Any]:
    db_path = home / ".cursor" / "ai-tracking" / "ai-code-tracking.db"
    summary = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "tables": [],
        "rows": 0,
        "requests": 0,
        "conversations": 0,
        "models": 0,
    }
    if not db_path.exists():
        return summary
    schema_path = out_dir / "cursor-ai-tracking-schema.sql"
    csv_path = out_dir / "cursor-ai-tracking-summary.csv"
    try:
        with sqlite_connect_ro(db_path) as conn:
            schema_rows = conn.execute(
                "select sql from sqlite_master where sql is not null order by type, name"
            ).fetchall()
            schema_path.write_text("\n".join(row[0] for row in schema_rows) + "\n", encoding="utf-8")
            tables = [
                row[0]
                for row in conn.execute(
                    "select name from sqlite_master where type='table' order by name"
                ).fetchall()
            ]
            summary["tables"] = tables
            if "ai_code_hashes" in tables:
                rows = conn.execute(
                    """
                    select
                      source,
                      count(*) as records,
                      min(datetime(createdAt / 1000, 'unixepoch')) as first_created_at_utc,
                      max(datetime(createdAt / 1000, 'unixepoch')) as last_created_at_utc,
                      count(distinct conversationId) as conversations,
                      count(distinct requestId) as requests,
                      count(distinct model) as models
                    from ai_code_hashes
                    group by source
                    order by records desc
                    """
                ).fetchall()
                fieldnames = [
                    "source",
                    "records",
                    "first_created_at_utc",
                    "last_created_at_utc",
                    "conversations",
                    "requests",
                    "models",
                ]
                write_csv(csv_path, fieldnames, [dict(zip(fieldnames, row)) for row in rows])
                total = conn.execute("select count(*) from ai_code_hashes").fetchone()[0]
                summary["rows"] = total
                request_count, conversation_count, model_count = conn.execute(
                    """
                    select
                      count(distinct requestId),
                      count(distinct conversationId),
                      count(distinct model)
                    from ai_code_hashes
                    """
                ).fetchone()
                summary["requests"] = request_count
                summary["conversations"] = conversation_count
                summary["models"] = model_count
                summary["summary_csv"] = str(csv_path)
            summary["schema"] = str(schema_path)
    except sqlite3.Error as exc:
        summary["error"] = str(exc)
    return summary


def inspect_cursor_vscdb(home: Path, out_dir: Path) -> dict[str, Any]:
    cursor_user = home / "Library" / "Application Support" / "Cursor" / "User"
    dbs: list[Path] = []
    candidates = [cursor_user / "globalStorage" / "state.vscdb"]
    workspace_storage = cursor_user / "workspaceStorage"
    if workspace_storage.exists():
        candidates.extend(workspace_storage.glob("*/state.vscdb"))
    for candidate in candidates:
        if candidate.exists():
            dbs.append(candidate)
    rows: list[dict[str, Any]] = []
    for db_path in dbs:
        try:
            with sqlite_connect_ro(db_path) as conn:
                table_names = {
                    row[0]
                    for row in conn.execute(
                        "select name from sqlite_master where type='table'"
                    ).fetchall()
                }
                if "ItemTable" not in table_names:
                    continue
                for key, value_len in conn.execute(
                    """
                    select key, length(value)
                    from ItemTable
                    where lower(key) like '%usage%'
                       or lower(key) like '%token%'
                       or lower(key) like '%cost%'
                       or lower(key) like '%billing%'
                       or lower(key) like '%credit%'
                       or lower(key) like '%quota%'
                       or lower(key) like '%premium%'
                       or lower(key) like '%request%'
                       or lower(key) like '%conversation%'
                       or lower(key) like '%composer%'
                       or lower(key) like '%aiservice%'
                       or lower(key) like '%generation%'
                    order by key
                    """
                ).fetchall():
                    rows.append({"db_path": str(db_path), "key": key, "value_bytes": value_len})
        except sqlite3.Error:
            continue
    csv_path = out_dir / "cursor-vscdb-keys.csv"
    write_csv(csv_path, ["db_path", "key", "value_bytes"], rows)
    return {"state_dbs": len(dbs), "matching_keys": len(rows), "csv": str(csv_path)}


def inspect_cursor_daily_ai_stats(home: Path, out_dir: Path) -> dict[str, Any]:
    db_path = home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    fields = [
        "tabSuggestedLines",
        "tabAcceptedLines",
        "composerSuggestedLines",
        "composerAcceptedLines",
    ]
    rows: list[dict[str, Any]] = []
    totals = new_counter(fields)
    if not db_path.exists():
        return {"path": str(db_path), "exists": False, "days": 0, "totals": totals}
    try:
        with sqlite_connect_ro(db_path) as conn:
            for key, value in conn.execute(
                """
                select key, value
                from ItemTable
                where key like 'aiCodeTracking.dailyStats.%'
                order by key
                """
            ).fetchall():
                text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    continue
                row = {"key": key, "date": obj.get("date", "")}
                for field in fields:
                    row[field] = safe_int(obj.get(field))
                add_counts(totals, row, fields)
                rows.append(row)
    except sqlite3.Error as exc:
        return {"path": str(db_path), "exists": True, "error": str(exc), "days": 0, "totals": totals}
    csv_path = out_dir / "cursor-ai-code-daily-stats.csv"
    write_csv(csv_path, ["date", "key", *fields], rows)
    return {
        "path": str(db_path),
        "exists": True,
        "days": len(rows),
        "totals": totals,
        "csv": str(csv_path),
    }


def discover_cursor_candidate_files(home: Path, out_dir: Path) -> dict[str, Any]:
    targets = [
        home / ".cursor",
        home / "Library" / "Application Support" / "Cursor" / "User",
        home / "Library" / "Application Support" / "Cursor" / "process-monitor",
    ]
    existing = [str(path) for path in targets if path.exists()]
    output_path = out_dir / "cursor-candidate-files.txt"
    files: list[str] = []
    if existing and shutil.which("rg"):
        try:
            proc = subprocess.run(
                [
                    "rg",
                    "-a",
                    "-l",
                    "-i",
                    "usage|token|billing|credit|quota|premium|requestId|conversationId|composer|generation",
                    *existing,
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            files = [line for line in proc.stdout.splitlines() if line.strip()]
        except (subprocess.SubprocessError, OSError):
            files = []
    output_path.write_text("\n".join(files) + ("\n" if files else ""), encoding="utf-8")
    return {"targets": existing, "files": len(files), "path": str(output_path)}


def write_summary_md(out_dir: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Local AI Usage Records",
        "",
        f"Created: {summary['created_at']}",
        f"Output: {summary['output_dir']}",
        "",
        "## Evidence Notes",
        "",
    ]
    for note in summary["evidence_notes"]:
        lines.append(f"- {note}")
    lines.extend(["", "## Counts", ""])
    for tool in ("codex", "claude_code", "cursor"):
        lines.append(f"### {tool}")
        for key, value in summary[tool].items():
            if key in {"paths", "token_totals_by_model", "token_totals_by_day"}:
                continue
            lines.append(f"- {key}: {value}")
        lines.append("")
    lines.extend(["## Token Totals", ""])
    lines.append(f"- codex: {summary['codex'].get('token_totals', {})}")
    lines.append(f"- claude_code: {summary['claude_code'].get('token_totals', {})}")
    lines.append("- cursor: no explicit local token fields found in transcript index unless `usage_like_records` is greater than 0")
    lines.append("")
    lines.extend(["## Outputs", ""])
    for key, value in summary["outputs"].items():
        lines.append(f"- {key}: {value}")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Index local AI coding usage/session records.")
    parser.add_argument("--out", default="", help="Output directory. Defaults to /tmp/ai-usage-records/<timestamp>.")
    parser.add_argument("--home", default=str(Path.home()), help="Home directory to inspect.")
    parser.add_argument("--include-snippets", action="store_true", help="Include short prompt/message snippets in CSV indexes.")
    args = parser.parse_args()

    home = Path(args.home).expanduser()
    out_dir = Path(args.out).expanduser() if args.out else Path("/tmp/ai-usage-records") / now_stamp()
    out_dir.mkdir(parents=True, exist_ok=True)

    codex_rows, codex_summary = parse_codex(home, args.include_snippets)
    claude_rows, claude_summary = parse_claude(home, args.include_snippets)
    cursor_rows, cursor_summary = parse_cursor(home, args.include_snippets)

    base_fields = [
        "tool",
        "record_kind",
        "timestamp",
        "project",
        "session_id",
        "role",
        "model",
        "source_file",
        "line_no",
        "has_usage",
        "usage_json",
    ]
    fields = base_fields + (["snippet"] if args.include_snippets else [])

    codex_csv = out_dir / "codex-record-index.csv"
    claude_csv = out_dir / "claude-record-index.csv"
    cursor_csv = out_dir / "cursor-record-index.csv"
    write_csv(codex_csv, fields, codex_rows)
    write_csv(claude_csv, fields, claude_rows)
    write_csv(cursor_csv, fields, cursor_rows)

    codex_model_csv = out_dir / "codex-token-totals-by-model.csv"
    codex_day_csv = out_dir / "codex-token-totals-by-day.csv"
    claude_model_csv = out_dir / "claude-token-totals-by-model.csv"
    claude_day_csv = out_dir / "claude-token-totals-by-day.csv"
    write_aggregate_csv(codex_model_csv, "model", codex_summary["token_totals_by_model"], CODEX_TOKEN_FIELDS)
    write_aggregate_csv(codex_day_csv, "date", codex_summary["token_totals_by_day"], CODEX_TOKEN_FIELDS)
    write_aggregate_csv(claude_model_csv, "model", claude_summary["token_totals_by_model"], CLAUDE_TOKEN_FIELDS)
    write_aggregate_csv(claude_day_csv, "date", claude_summary["token_totals_by_day"], CLAUDE_TOKEN_FIELDS)

    cursor_ai_tracking = inspect_cursor_ai_tracking(home, out_dir)
    cursor_vscdb = inspect_cursor_vscdb(home, out_dir)
    cursor_daily_ai_stats = inspect_cursor_daily_ai_stats(home, out_dir)
    cursor_candidates = discover_cursor_candidate_files(home, out_dir)

    summary = {
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "output_dir": str(out_dir),
        "codex": codex_summary,
        "claude_code": claude_summary,
        "cursor": {
            **cursor_summary,
            "vscdb_state_dbs": cursor_vscdb["state_dbs"],
            "vscdb_matching_keys": cursor_vscdb["matching_keys"],
            "candidate_files": cursor_candidates["files"],
            "ai_tracking_rows": cursor_ai_tracking.get("rows", 0),
            "ai_tracking_requests": cursor_ai_tracking.get("requests", 0),
            "ai_tracking_conversations": cursor_ai_tracking.get("conversations", 0),
            "ai_code_stats_days": cursor_daily_ai_stats.get("days", 0),
            "ai_code_stats_totals": cursor_daily_ai_stats.get("totals", {}),
        },
        "cursor_ai_tracking": cursor_ai_tracking,
        "cursor_vscdb": cursor_vscdb,
        "cursor_daily_ai_stats": cursor_daily_ai_stats,
        "cursor_candidates": cursor_candidates,
        "outputs": {
            "codex_record_index": str(codex_csv),
            "claude_record_index": str(claude_csv),
            "cursor_record_index": str(cursor_csv),
            "codex_token_totals_by_model": str(codex_model_csv),
            "codex_token_totals_by_day": str(codex_day_csv),
            "claude_token_totals_by_model": str(claude_model_csv),
            "claude_token_totals_by_day": str(claude_day_csv),
            "cursor_vscdb_keys": cursor_vscdb["csv"],
            "cursor_candidate_files": cursor_candidates["path"],
            "cursor_ai_tracking_schema": cursor_ai_tracking.get("schema", ""),
            "cursor_ai_tracking_summary": cursor_ai_tracking.get("summary_csv", ""),
            "cursor_ai_code_daily_stats": cursor_daily_ai_stats.get("csv", ""),
        },
        "evidence_notes": [
            "Codex and Claude Code aggregate token/cost totals should come from ccusage; these CSVs index local records.",
            "Codex token totals sum deduped last_token_usage events from local token_count records.",
            "Claude Code token totals sum deduped message usage records keyed by requestId/uuid.",
            "Cursor transcripts and SQLite keys are local record evidence; generated-code tracking is not billing.",
            "Cursor billing/tokens are exact only when explicit usage/token/cost fields are found in local state or a local authenticated response.",
            "Transcript snippets are omitted by default; rerun with --include-snippets for searchable prompt previews.",
        ],
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    write_summary_md(out_dir, summary)

    print(f"Output directory: {out_dir}")
    print(f"Codex: {codex_summary['session_files']} files, {codex_summary['user_messages']} user messages, {codex_summary['token_count_events']} token-count events")
    print(f"Codex tokens: {codex_summary['token_totals']}")
    print(f"Claude Code: {claude_summary['session_files']} files, {claude_summary['user_messages']} user messages, {claude_summary['usage_records']} usage-bearing records")
    print(f"Claude Code tokens: {claude_summary['token_totals']}")
    print(f"Cursor: {cursor_summary['transcript_files']} transcript files, {cursor_summary['user_messages']} user messages, {cursor_vscdb['matching_keys']} matching SQLite keys, {cursor_ai_tracking.get('rows', 0)} AI-tracking rows")
    print(f"Cursor AI-code stats: {cursor_daily_ai_stats.get('totals', {})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
