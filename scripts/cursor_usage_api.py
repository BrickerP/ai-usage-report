#!/usr/bin/env python3
"""Production Cursor session, transport, redaction, and event helpers."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import re
import sqlite3
import ssl
from typing import Any
from urllib import error, request


BASE_URL = "https://api2.cursor.sh"
CURSOR_STATE_KEY = "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser"
CURSOR_AGGREGATE_BOUNDARIES = (
    dt.datetime(2025, 8, 1, tzinfo=dt.timezone.utc),
    dt.datetime(2026, 5, 14, tzinfo=dt.timezone.utc),
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


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def sqlite_connect_ro(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def cursor_product_version() -> str:
    product = Path("/Applications/Cursor.app/Contents/Resources/app/product.json")
    data = read_json(product)
    return str(data.get("version") or "desktop")


def read_cursor_state(home: Path) -> dict[str, Any]:
    db_path = home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    state = {
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "access_token": "",
        "email": "",
        "membership": "",
        "subscription_status": "",
        "dashboard_user_id": 0,
        "team_ids": [],
        "has_token_based_pricing": None,
        "use_openai_key": None,
    }
    if not db_path.exists():
        return state
    with sqlite_connect_ro(db_path) as conn:
        def get_value(key: str) -> str:
            row = conn.execute("select value from ItemTable where key=?", (key,)).fetchone()
            if not row:
                return ""
            value = row[0]
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)

        state["access_token"] = get_value("cursorAuth/accessToken")
        state["email"] = get_value("cursorAuth/cachedEmail")
        state["membership"] = get_value("cursorAuth/stripeMembershipType")
        state["subscription_status"] = get_value("cursorAuth/stripeSubscriptionStatus")
        raw_app = get_value(CURSOR_STATE_KEY)
    if raw_app:
        try:
            app_user = json.loads(raw_app)
        except json.JSONDecodeError:
            app_user = {}
        if isinstance(app_user, dict):
            ai_settings = app_user.get("aiSettings") if isinstance(app_user.get("aiSettings"), dict) else {}
            team_ids = ai_settings.get("teamIds")
            state["team_ids"] = team_ids if isinstance(team_ids, list) else []
            state["dashboard_user_id"] = safe_int(app_user.get("dashboardUserId"))
            state["has_token_based_pricing"] = app_user.get("hasTokenBasedPricing")
            state["use_openai_key"] = app_user.get("useOpenAIKey")
            state["membership"] = state["membership"] or str(app_user.get("membershipType") or "")
            state["subscription_status"] = state["subscription_status"] or str(app_user.get("subscriptionStatus") or "")
    return state


def redact(value: Any, token: str, email: str) -> Any:
    if isinstance(value, dict):
        output = {}
        for key, child in value.items():
            lower = key.lower()
            if lower in {"authorization", "accesstoken", "refreshtoken", "paymentid", "authid", "workosid"}:
                output[key] = "<redacted>"
            elif lower == "email":
                output[key] = "<email>"
            elif lower == "userid":
                output[key] = "<user-id>"
            else:
                output[key] = redact(child, token, email)
        return output
    if isinstance(value, list):
        return [redact(item, token, email) for item in value]
    if isinstance(value, str):
        text = value
        if token:
            text = text.replace(token, "<access-token>")
        if email:
            text = text.replace(email, "<email>")
        text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <token>", text)
        text = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "sk-<redacted>", text)
        return text
    return value


def redacted_error_summary(
    value: Any,
    token: str = "",
    email: str = "",
    *,
    limit: int = 800,
) -> dict[str, Any]:
    """Return a bounded diagnostic safe to persist or include in logs."""
    redacted = redact(value, token, email) if token or email else value
    error_code = str(redacted.get("code") or "") if isinstance(redacted, dict) else ""
    text = json.dumps(redacted, ensure_ascii=True, sort_keys=True)
    truncated = len(text) > limit
    if truncated:
        text = text[:limit] + "..."
    return {
        "error_code": error_code,
        "body": text,
        "truncated": truncated,
    }


def split_aggregate_windows(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    """Split a half-open range at Cursor's known usage-backend boundaries."""
    if end_ms <= start_ms:
        return []
    boundaries = [
        int(boundary.timestamp() * 1000)
        for boundary in CURSOR_AGGREGATE_BOUNDARIES
        if start_ms < int(boundary.timestamp() * 1000) < end_ms
    ]
    points = [start_ms, *boundaries, end_ms]
    return list(zip(points, points[1:]))


class CursorClient:
    def __init__(self, token: str, version: str, email: str) -> None:
        self.token = token
        self.version = version
        self.email = email
        self.context = ssl.create_default_context()

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connect-Protocol-Version": "1",
            "x-cursor-client-version": self.version,
            "x-cursor-client-type": "ide",
            "x-cursor-client-device-type": "desktop",
            "x-cursor-timezone": dt.datetime.now().astimezone().tzname() or "local",
            "User-Agent": f"Cursor/{self.version}",
        }

    def dashboard(self, method: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, str], bytes]:
        return self.post(f"{BASE_URL}/aiserver.v1.DashboardService/{method}", body or {})

    def post(self, url: str, body: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = request.Request(url, data=data, headers=self.headers(), method="POST")
        return self._open(req)

    def get(self, url: str) -> tuple[int, dict[str, str], bytes]:
        headers = self.headers()
        headers.pop("Content-Type", None)
        req = request.Request(url, headers=headers, method="GET")
        return self._open(req)

    def _open(self, req: request.Request) -> tuple[int, dict[str, str], bytes]:
        try:
            with request.urlopen(req, timeout=30, context=self.context) as response:
                return response.status, dict(response.headers.items()), response.read()
        except error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read()


def decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return raw.decode("utf-8", "replace")


def ms_to_iso(value: Any) -> str:
    number = safe_int(value)
    if not number:
        return ""
    return dt.datetime.fromtimestamp(number / 1000, tz=dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def cents_to_usd(value: Any) -> float:
    return safe_float(value) / 100


def normalize_event_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in response.get("usageEventsDisplay") or []:
        if not isinstance(item, dict):
            continue
        token_usage = item.get("tokenUsage") if isinstance(item.get("tokenUsage"), dict) else {}
        rows.append(
            {
                "timestamp_ms": item.get("timestamp", ""),
                "timestamp": ms_to_iso(item.get("timestamp")),
                "model": item.get("model", ""),
                "kind": item.get("kind", ""),
                "max_mode": item.get("maxMode", ""),
                "is_token_based_call": item.get("isTokenBasedCall", ""),
                "input_tokens": token_usage.get("inputTokens", ""),
                "output_tokens": token_usage.get("outputTokens", ""),
                "cache_write_tokens": token_usage.get("cacheWriteTokens", ""),
                "cache_read_tokens": token_usage.get("cacheReadTokens", ""),
                "estimated_raw_cents": token_usage.get("totalCents", ""),
                "estimated_raw_usd": cents_to_usd(token_usage.get("totalCents", "")),
                "request_costs": item.get("requestsCosts", ""),
                "usage_based_costs": item.get("usageBasedCosts", ""),
                "charged_cents": item.get("chargedCents", ""),
                "charged_usd": cents_to_usd(item.get("chargedCents", "")),
                "client_type": item.get("clientType", ""),
                "is_headless": item.get("isHeadless", ""),
                "is_chargeable": item.get("isChargeable", ""),
            }
        )
    return rows
