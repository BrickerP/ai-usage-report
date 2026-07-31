#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
import re
import sqlite3
import ssl
import time
from typing import Any
from urllib import error, request


BASE_URL = "https://api2.cursor.sh"
CURSOR_STATE_KEY = "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser"
CURSOR_USAGE_START = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
CURSOR_AGGREGATE_BOUNDARIES = (
    dt.datetime(2025, 8, 1, tzinfo=dt.timezone.utc),
    dt.datetime(2026, 5, 14, tzinfo=dt.timezone.utc),
)
USAGE_FIELDS = [
    "inputTokens",
    "outputTokens",
    "cacheWriteTokens",
    "cacheReadTokens",
    "totalCents",
    "requestCost",
    "tier",
]


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


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


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


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


def save_response(
    out_dir: Path,
    name: str,
    status: int,
    headers: dict[str, str],
    raw: bytes,
    token: str,
    email: str,
) -> Any:
    decoded = decode_json(raw)
    persisted = (
        redacted_error_summary(decoded, token, email)
        if not (200 <= status < 300)
        else redact(decoded, token, email)
    )
    (out_dir / f"{name}.json").write_text(
        json.dumps(persisted, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    metadata = {
        "status": status,
        "ok": 200 <= status < 300,
        "content_type": headers.get("Content-Type") or headers.get("content-type") or "",
        "bytes": len(raw),
        "top_keys": list(persisted.keys()) if isinstance(persisted, dict) else [],
    }
    (out_dir / f"{name}.meta.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return persisted


def ms_to_iso(value: Any) -> str:
    number = safe_int(value)
    if not number:
        return ""
    return dt.datetime.fromtimestamp(number / 1000, tz=dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def cents_to_usd(value: Any) -> float:
    return safe_float(value) / 100


def normalize_aggregation_rows(response: dict[str, Any], period_name: str) -> list[dict[str, Any]]:
    rows = []
    for item in response.get("aggregations") or []:
        if not isinstance(item, dict):
            continue
        row = {
            "period": period_name,
            "model_intent": item.get("modelIntent", ""),
        }
        for field in USAGE_FIELDS:
            row[field] = item.get(field, "")
        row["total_usd"] = cents_to_usd(item.get("totalCents"))
        rows.append(row)
    return rows


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


def fetch_event_pages(
    client: CursorClient,
    *,
    out_dir: Path,
    token: str,
    email: str,
    team_id: int,
    user_id: int,
    start_ms: int,
    end_ms: int,
    page_size: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], int]:
    all_rows: list[dict[str, Any]] = []
    total_count = 0
    for page in range(1, max_pages + 1):
        body = {
            "teamId": team_id,
            "userId": user_id,
            "startDate": start_ms,
            "endDate": end_ms,
            "page": page,
            "pageSize": page_size,
        }
        status, headers, raw = client.dashboard("GetFilteredUsageEvents", body)
        response = save_response(out_dir, f"cursor-filtered-usage-events-page-{page:04d}", status, headers, raw, token, email)
        if not (200 <= status < 300) or not isinstance(response, dict):
            break
        total_count = safe_int(response.get("totalUsageEventsCount"))
        rows = normalize_event_rows(response)
        all_rows.extend(rows)
        if not rows or len(all_rows) >= total_count:
            break
    return all_rows, total_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Cursor usage via this computer's logged-in Cursor account.")
    parser.add_argument("--out", default="", help="Output directory. Defaults to /tmp/cursor-usage-api/<timestamp>.")
    parser.add_argument("--home", default=str(Path.home()), help="Home directory to inspect.")
    parser.add_argument("--start-ms", type=int, default=int(CURSOR_USAGE_START.timestamp() * 1000))
    parser.add_argument("--end-ms", type=int, default=int(time.time() * 1000))
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=3, help="Max event pages to fetch. Use 0 to skip events.")
    parser.add_argument("--all-pages", action="store_true", help="Fetch all usage-event pages.")
    args = parser.parse_args()

    home = Path(args.home).expanduser()
    out_dir = Path(args.out).expanduser() if args.out else Path("/tmp/cursor-usage-api") / now_stamp()
    out_dir.mkdir(parents=True, exist_ok=True)

    state = read_cursor_state(home)
    token = str(state.get("access_token") or "")
    email = str(state.get("email") or "")
    if not token:
        print("Cursor access token not found in local Cursor globalStorage/state.vscdb")
        return 1

    version = cursor_product_version()
    client = CursorClient(token, version, email)
    team_id = safe_int((state.get("team_ids") or [0])[0])
    user_id = safe_int(state.get("dashboard_user_id"))

    account = {
        "email_present": bool(email),
        "membership": state.get("membership", ""),
        "subscription_status": state.get("subscription_status", ""),
        "dashboard_user_id_present": bool(user_id),
        "team_id": team_id,
        "team_ids_count": len(state.get("team_ids") or []),
        "has_token_based_pricing": state.get("has_token_based_pricing"),
        "use_openai_key": state.get("use_openai_key"),
        "client_version": version,
    }

    responses: dict[str, Any] = {}
    for method in [
        "GetCurrentPeriodUsage",
        "GetPlanInfo",
        "GetCurrentBillingCycle",
        "GetMonthlyBillingCycle",
        "GetUsageLimitStatusAndActiveGrants",
        "GetCreditGrantsBalance",
        "GetClientVisibleCreditGrants",
        "GetMe",
    ]:
        status, headers, raw = client.dashboard(method, {})
        responses[method] = save_response(out_dir, f"cursor-{method}", status, headers, raw, token, email)

    for name, path in {
        "stripe_profile": "/auth/stripe_profile",
        "full_stripe_profile": "/auth/full_stripe_profile",
        "has_valid_payment_method": "/auth/has_valid_payment_method",
    }.items():
        status, headers, raw = client.get(f"{BASE_URL}{path}")
        responses[name] = save_response(out_dir, f"cursor-auth-{name}", status, headers, raw, token, email)

    current_cycle = responses.get("GetCurrentBillingCycle") if isinstance(responses.get("GetCurrentBillingCycle"), dict) else {}
    cycle_start = safe_int(current_cycle.get("startDateEpochMillis"))
    cycle_end = safe_int(current_cycle.get("endDateEpochMillis"))

    aggregate_ranges = {
        "full_history": {
            "teamId": team_id,
            "userId": user_id,
            "startDate": args.start_ms,
            "endDate": args.end_ms,
        },
        "current_cycle": {
            "teamId": team_id,
            "userId": user_id,
            "startDate": cycle_start or args.start_ms,
            "endDate": cycle_end or args.end_ms,
        },
    }
    aggregate_rows: list[dict[str, Any]] = []
    aggregate_totals: dict[str, Any] = {}
    for label, request_range in aggregate_ranges.items():
        windows = split_aggregate_windows(
            safe_int(request_range["startDate"]),
            safe_int(request_range["endDate"]),
        )
        for index, (window_start, window_end) in enumerate(windows, start=1):
            window_label = f"{label}-{index:02d}"
            body = {
                "teamId": team_id,
                "userId": user_id,
                "startDate": window_start,
                "endDate": window_end,
            }
            status, headers, raw = client.dashboard("GetAggregatedUsageEvents", body)
            response = save_response(
                out_dir,
                f"cursor-aggregated-usage-{window_label}",
                status,
                headers,
                raw,
                token,
                email,
            )
            aggregate_totals[window_label] = {
                "status": status,
                "start_ms": window_start,
                "end_ms": window_end,
                "start": ms_to_iso(window_start),
                "end": ms_to_iso(window_end),
            }
            if not (200 <= status < 300) or not isinstance(response, dict):
                if isinstance(response, dict):
                    aggregate_totals[window_label].update(response)
                continue
            aggregate_totals[window_label].update(
                {
                    "total_input_tokens": safe_int(response.get("totalInputTokens")),
                    "total_output_tokens": safe_int(response.get("totalOutputTokens")),
                    "total_cache_write_tokens": safe_int(response.get("totalCacheWriteTokens")),
                    "total_cache_read_tokens": safe_int(response.get("totalCacheReadTokens")),
                    "total_cost_cents": safe_float(response.get("totalCostCents")),
                    "total_cost_usd": cents_to_usd(response.get("totalCostCents")),
                    "model_count": len(response.get("aggregations") or []),
                }
            )
            aggregate_rows.extend(normalize_aggregation_rows(response, window_label))

    aggregate_csv = out_dir / "cursor-aggregated-usage-by-model.csv"
    write_csv(
        aggregate_csv,
        ["period", "model_intent", *USAGE_FIELDS, "total_usd"],
        aggregate_rows,
    )

    max_pages = 0 if args.max_pages < 0 else args.max_pages
    if args.all_pages:
        max_pages = 1000000
    event_rows: list[dict[str, Any]] = []
    total_event_count = 0
    if max_pages > 0:
        event_rows, total_event_count = fetch_event_pages(
            client,
            out_dir=out_dir,
            token=token,
            email=email,
            team_id=team_id,
            user_id=user_id,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
            page_size=max(1, args.page_size),
            max_pages=max_pages,
        )
    events_csv = out_dir / "cursor-usage-events.csv"
    write_csv(
        events_csv,
        [
            "timestamp_ms",
            "timestamp",
            "model",
            "kind",
            "max_mode",
            "is_token_based_call",
            "input_tokens",
            "output_tokens",
            "cache_write_tokens",
            "cache_read_tokens",
            "estimated_raw_cents",
            "estimated_raw_usd",
            "request_costs",
            "usage_based_costs",
            "charged_cents",
            "charged_usd",
            "client_type",
            "is_headless",
            "is_chargeable",
        ],
        event_rows,
    )

    current_period = responses.get("GetCurrentPeriodUsage") if isinstance(responses.get("GetCurrentPeriodUsage"), dict) else {}
    plan_info = responses.get("GetPlanInfo") if isinstance(responses.get("GetPlanInfo"), dict) else {}
    full_profile = responses.get("full_stripe_profile") if isinstance(responses.get("full_stripe_profile"), dict) else {}

    summary = {
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "output_dir": str(out_dir),
        "account": account,
        "plan": plan_info.get("planInfo", {}),
        "stripe_profile": {
            "membershipType": full_profile.get("membershipType"),
            "subscriptionStatus": full_profile.get("subscriptionStatus"),
            "isOnBillableAuto": full_profile.get("isOnBillableAuto"),
            "hasValidPaymentMethod": (
                responses.get("has_valid_payment_method", {}).get("hasValidPaymentMethod")
                if isinstance(responses.get("has_valid_payment_method"), dict)
                else None
            ),
        },
        "current_period_usage": current_period,
        "aggregate_totals": aggregate_totals,
        "usage_events": {
            "total_usage_events_count": total_event_count,
            "exported_rows": len(event_rows),
            "page_size": args.page_size,
            "max_pages": max_pages,
            "csv": str(events_csv),
        },
        "outputs": {
            "summary_json": str(out_dir / "summary.json"),
            "aggregated_usage_by_model_csv": str(aggregate_csv),
            "usage_events_csv": str(events_csv),
        },
        "evidence_notes": [
            "Uses this computer's local Cursor access token from Cursor globalStorage/state.vscdb.",
            "Does not print tokens, API keys, raw auth IDs, payment IDs, or email addresses.",
            "GetAggregatedUsageEvents is split at known backend boundaries and is diagnostic only.",
            "GetFilteredUsageEvents is paginated; default export fetches only the first few pages unless --all-pages is used.",
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    def sum_aggregate_period(prefix: str) -> dict[str, Any]:
        rows = [
            row
            for label, row in aggregate_totals.items()
            if label.startswith(f"{prefix}-") and isinstance(row, dict)
        ]
        return {
            "windows": len(rows),
            "successful_windows": sum(
                1 for row in rows if 200 <= safe_int(row.get("status")) < 300
            ),
            "total_input_tokens": sum(safe_int(row.get("total_input_tokens")) for row in rows),
            "total_output_tokens": sum(safe_int(row.get("total_output_tokens")) for row in rows),
            "total_cache_write_tokens": sum(safe_int(row.get("total_cache_write_tokens")) for row in rows),
            "total_cache_read_tokens": sum(safe_int(row.get("total_cache_read_tokens")) for row in rows),
            "total_cost_usd": sum(safe_float(row.get("total_cost_usd")) for row in rows),
        }

    full = sum_aggregate_period("full_history")
    cycle = sum_aggregate_period("current_cycle")
    print(f"Output directory: {out_dir}")
    print(f"Cursor account: membership={account['membership']} subscription={account['subscription_status']} token_pricing={account['has_token_based_pricing']}")
    print(
        "Cursor full-history aggregate: "
        f"windows={full.get('successful_windows', 0)}/{full.get('windows', 0)} "
        f"input={full.get('total_input_tokens', 0)} output={full.get('total_output_tokens', 0)} "
        f"cache_write={full.get('total_cache_write_tokens', 0)} cache_read={full.get('total_cache_read_tokens', 0)} "
        f"cost=${full.get('total_cost_usd', 0):.2f}"
    )
    print(
        "Cursor current-cycle aggregate: "
        f"windows={cycle.get('successful_windows', 0)}/{cycle.get('windows', 0)} "
        f"input={cycle.get('total_input_tokens', 0)} output={cycle.get('total_output_tokens', 0)} "
        f"cache_write={cycle.get('total_cache_write_tokens', 0)} cache_read={cycle.get('total_cache_read_tokens', 0)} "
        f"cost=${cycle.get('total_cost_usd', 0):.2f}"
    )
    print(f"Cursor usage events: total={total_event_count} exported={len(event_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
