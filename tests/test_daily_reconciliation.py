from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


usage_report = load_module(
    "usage_pipeline_reconciliation_test",
    ROOT / "scripts" / "usage_pipeline.py",
)
machine_fragments = usage_report.machine_fragments


def local_row(date: str, *, codex_tokens: int, claude_tokens: int = 0):
    row = usage_report.empty_daily_row(date)
    codex_input = codex_tokens // 5
    codex_output = codex_tokens // 10
    codex_cache = codex_tokens - codex_input - codex_output
    claude_input = claude_tokens // 5
    claude_create = claude_tokens // 10
    claude_output = claude_tokens // 10
    claude_read = claude_tokens - claude_input - claude_create - claude_output
    row.update(
        {
            "codex_tokens": codex_tokens,
            "codex_cost": codex_tokens / 100,
            "codex_input": codex_input,
            "codex_cache_read": codex_cache,
            "codex_output": codex_output,
            "codex_reasoning": codex_output // 2,
            "codex_models": ([{"model": "codex-test", "tokens": codex_tokens, "cost": codex_tokens / 100}] if codex_tokens else []),
            "codex_snapshot_complete": True,
            "codex_pricing_version": usage_report.PRICING_VERSION,
            "codex_pricing_complete": True,
            "codex_pricing_provenance": "pinned-ledger",
            "claude_tokens": claude_tokens,
            "claude_cost": claude_tokens / 100,
            "claude_input": claude_input,
            "claude_cache_create": claude_create,
            "claude_cache_read": claude_read,
            "claude_output": claude_output,
            "claude_models": ([{"model": "claude-test", "tokens": claude_tokens, "cost": claude_tokens / 100}] if claude_tokens else []),
            "claude_snapshot_complete": True,
            "claude_pricing_version": usage_report.PRICING_VERSION,
            "claude_pricing_complete": True,
            "claude_pricing_provenance": "pinned-ledger",
        }
    )
    return row


def merge_local(existing, incoming, *, today: str, mutable_from: str):
    return machine_fragments.merge_append_daily(
        existing,
        incoming,
        today,
        usage_report.TOOL_TOKEN_FIELDS,
        usage_report.safe_int,
        usage_report.safe_float,
        mutable_from=mutable_from,
    )


class LocalReconciliationTests(unittest.TestCase):
    def test_machine_id_does_not_fall_back_to_hostname(self):
        with mock.patch.dict(os.environ, {"AI_USAGE_MACHINE_ID": ""}):
            with mock.patch.object(
                machine_fragments.socket,
                "gethostname",
                return_value="unstable-hostname.local",
            ):
                with self.assertRaisesRegex(ValueError, "stable machine id is required"):
                    machine_fragments.resolve_machine_id()

    def test_merge_rejects_different_machine_ids_for_same_hostname(self):
        fragments = [
            {
                "machine_id": "mac-m4-local",
                "hostname": "same-mac.local",
                "daily": [local_row("2026-07-28", codex_tokens=10, claude_tokens=20)],
            },
            {
                "machine_id": "legacy-hostname",
                "hostname": "SAME-MAC.LOCAL",
                "daily": [local_row("2026-07-28", codex_tokens=10, claude_tokens=20)],
            },
        ]

        with self.assertRaisesRegex(
            ValueError,
            r"duplicate machine hostname.*same-mac\.local.*mac-m4-local.*legacy-hostname",
        ):
            machine_fragments.merge_local_fragments(
                fragments,
                usage_report.empty_daily_row,
                usage_report.TOOL_TOKEN_FIELDS,
                usage_report.safe_int,
                usage_report.safe_float,
            )

    def test_legacy_seeded_at_opens_that_date_for_next_day_reconciliation(self):
        fragment = {
            "seeded_at": "2026-07-19T15:03:00+08:00",
            "daily": [local_row("2026-07-19", codex_tokens=100)],
        }
        mutable_from = machine_fragments.fragment_mutable_from(
            fragment, "2026-07-20"
        )

        rows, _stats = merge_local(
            fragment["daily"],
            [
                local_row("2026-07-19", codex_tokens=160),
                local_row("2026-07-20", codex_tokens=5),
            ],
            today="2026-07-20",
            mutable_from=mutable_from,
        )

        self.assertEqual(mutable_from, "2026-07-19")
        self.assertEqual(rows[0]["codex_tokens"], 160)

    def test_offline_gap_keeps_collecting_from_persisted_mutable_from(self):
        fragment = {
            "seeded_at": "2026-07-19T15:03:00+08:00",
            "mutable_from": "2026-07-20",
            "daily": [local_row("2026-07-20", codex_tokens=20)],
        }

        self.assertEqual(
            machine_fragments.fragment_mutable_from(fragment, "2026-07-23"),
            "2026-07-20",
        )

    def test_open_window_keeps_only_regressing_tool_group(self):
        existing = local_row("2026-07-19", codex_tokens=100, claude_tokens=200)
        incoming = local_row("2026-07-19", codex_tokens=80, claude_tokens=250)
        original_codex = {
            key: value for key, value in existing.items() if key.startswith("codex_")
        }

        rows, stats = merge_local(
            [existing],
            [incoming],
            today="2026-07-20",
            mutable_from="2026-07-19",
        )

        reconciled = rows[0]
        self.assertEqual(
            {key: value for key, value in reconciled.items() if key.startswith("codex_")},
            original_codex,
        )
        self.assertEqual(reconciled["claude_tokens"], 250)
        self.assertEqual(stats["regression_dates"], ["2026-07-19"])
        self.assertEqual(stats["regression_kept"], 1)

    def test_finalized_history_is_not_rewritten(self):
        existing = local_row("2026-07-18", codex_tokens=100, claude_tokens=200)
        before = copy.deepcopy(existing)
        incoming = local_row("2026-07-18", codex_tokens=180, claude_tokens=280)

        rows, _stats = merge_local(
            [existing],
            [incoming],
            today="2026-07-20",
            mutable_from="2026-07-19",
        )

        self.assertEqual(rows[0]["codex_tokens"], before["codex_tokens"])
        self.assertEqual(rows[0]["claude_tokens"], before["claude_tokens"])

    def test_unpriced_cost_decrease_is_kept_open(self):
        existing = local_row("2026-07-19", codex_tokens=100)
        incoming = local_row("2026-07-19", codex_tokens=100)
        incoming["codex_cost"] = 0.5
        incoming["codex_models"] = [{"model": "unknown", "tokens": 100, "cost": 0.5}]
        incoming["codex_pricing_complete"] = False

        rows, stats = merge_local([existing], [incoming], today="2026-07-20", mutable_from="2026-07-19")

        self.assertEqual(rows[0]["codex_cost"], 1.0)
        self.assertEqual(stats["regression_reasons"]["2026-07-19:codex"], "unpriced_cost_regression")

    def test_complete_pinned_reprice_can_lower_cost_with_audit_record(self):
        existing = local_row("2026-07-19", codex_tokens=100)
        incoming = local_row("2026-07-19", codex_tokens=100)
        incoming["codex_cost"] = 0.5
        incoming["codex_models"] = [{"model": "codex-test", "tokens": 100, "cost": 0.5}]

        rows, stats = merge_local([existing], [incoming], today="2026-07-20", mutable_from="2026-07-19")

        self.assertEqual(rows[0]["codex_cost"], 0.5)
        self.assertEqual(stats["pricing_changes"][0]["pricing_complete"], True)

    def test_model_over_attribution_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "model tokens exceed tool total"):
            usage_report.models_with_remainder(
                [{"model": "bad", "tokens": 101, "cost": 1}],
                total_tokens=100,
                total_cost=1,
            )

    def test_successful_local_capture_atomically_advances_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machines = root / "machines"
            machines.mkdir()
            fragment = {
                "machine_id": "mac-test",
                "timezone": "Asia/Shanghai",
                "seeded_at": "2026-07-19T15:03:00+08:00",
                "daily": [local_row("2026-07-19", codex_tokens=100)],
            }
            machine_fragments.write_json_atomic(
                machines / "mac-test.json", fragment
            )

            def fake_ccusage(tool, _timezone, since="", until=""):
                self.assertEqual(since, "2026-07-19")
                self.assertEqual(until, "2026-07-20")
                if tool == "codex":
                    return {
                        "daily": [
                            {
                                "date": "2026-07-19",
                                "totalTokens": 160,
                                "inputTokens": 32,
                                "cacheReadTokens": 112,
                                "outputTokens": 16,
                                "costUSD": 1.6,
                            },
                            {
                                "date": "2026-07-20",
                                "totalTokens": 5,
                                "inputTokens": 1,
                                "cacheReadTokens": 3,
                                "outputTokens": 1,
                                "costUSD": 0.05,
                            },
                        ]
                    }
                return {"daily": []}

            patches = (
                mock.patch.object(usage_report, "local_today", return_value="2026-07-20"),
                mock.patch.object(usage_report, "ccusage_daily", side_effect=fake_ccusage),
                mock.patch.object(usage_report, "local_record_summary", return_value={}),
                mock.patch.object(
                    usage_report.comate_usage,
                    "parse_comate",
                    return_value={"daily_timeline": [], "total_tokens": 0},
                ),
            )
            for patcher in patches:
                patcher.start()
            try:
                usage_report.collect_local_machine(
                    root,
                    "Asia/Shanghai",
                    root / "scratch",
                    machine_id="mac-test",
                    machines_path=machines,
                )
            finally:
                for patcher in reversed(patches):
                    patcher.stop()

            updated = machine_fragments.load_machine_fragment(machines, "mac-test")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["mutable_from"], "2026-07-19")
            by_date = {row["date"]: row for row in updated["daily"]}
            self.assertEqual(by_date["2026-07-19"]["codex_tokens"], 160)


class CursorReconciliationTests(unittest.TestCase):
    def test_cursor_missing_later_page_count_cannot_hide_partial_result(self):
        event = {
            "timestamp": 1_700_000_000_000,
            "tokenUsage": {"inputTokens": 1},
        }
        responses = [
            {"totalUsageEventsCount": 5, "usageEventsDisplay": [event, event]},
            {"usageEventsDisplay": [event]},
        ]
        patches = (
            mock.patch.object(
                usage_report.cursor_api,
                "read_cursor_state",
                return_value={"access_token": "token", "dashboard_user_id": 1},
            ),
            mock.patch.object(
                usage_report.cursor_api,
                "cursor_product_version",
                return_value="test",
            ),
            mock.patch.object(
                usage_report.cursor_api, "CursorClient", return_value=object()
            ),
            mock.patch.object(
                usage_report,
                "fetch_cursor_aggregate_audit",
                return_value={"available": False, "totals": {}, "errors": ["audit"]},
            ),
            mock.patch.object(
                usage_report, "call_cursor", side_effect=responses
            ),
        )
        for patcher in patches:
            patcher.start()
        try:
            result = usage_report.fetch_cursor_usage(
                Path("/tmp"), 2, "Asia/Shanghai"
            )
        finally:
            for patcher in reversed(patches):
                patcher.stop()

        self.assertFalse(result["complete"])
        self.assertEqual(result["history"]["events"], 5)

    def test_cursor_first_page_without_count_is_incomplete(self):
        event = {
            "timestamp": 1_700_000_000_000,
            "tokenUsage": {"inputTokens": 1},
        }
        responses = [{"usageEventsDisplay": [event]}]
        patches = (
            mock.patch.object(
                usage_report.cursor_api,
                "read_cursor_state",
                return_value={"access_token": "token", "dashboard_user_id": 1},
            ),
            mock.patch.object(
                usage_report.cursor_api,
                "cursor_product_version",
                return_value="test",
            ),
            mock.patch.object(
                usage_report.cursor_api, "CursorClient", return_value=object()
            ),
            mock.patch.object(
                usage_report,
                "fetch_cursor_aggregate_audit",
                return_value={"available": False, "totals": {}, "errors": ["audit"]},
            ),
            mock.patch.object(
                usage_report, "call_cursor", side_effect=responses
            ),
        )
        for patcher in patches:
            patcher.start()
        try:
            result = usage_report.fetch_cursor_usage(
                Path("/tmp"), 2, "Asia/Shanghai"
            )
        finally:
            for patcher in reversed(patches):
                patcher.stop()

        self.assertFalse(result["complete"])

    def test_cursor_later_page_without_count_is_incomplete_even_when_rows_match(self):
        event = {
            "timestamp": 1_700_000_000_000,
            "tokenUsage": {"inputTokens": 1},
        }
        responses = [
            {"totalUsageEventsCount": 2, "usageEventsDisplay": [event]},
            {"usageEventsDisplay": [event]},
        ]
        patches = (
            mock.patch.object(
                usage_report.cursor_api,
                "read_cursor_state",
                return_value={"access_token": "token", "dashboard_user_id": 1},
            ),
            mock.patch.object(
                usage_report.cursor_api,
                "cursor_product_version",
                return_value="test",
            ),
            mock.patch.object(
                usage_report.cursor_api, "CursorClient", return_value=object()
            ),
            mock.patch.object(
                usage_report,
                "fetch_cursor_aggregate_audit",
                return_value={"available": False, "totals": {}, "errors": ["audit"]},
            ),
            mock.patch.object(usage_report, "call_cursor", side_effect=responses),
        )
        for patcher in patches:
            patcher.start()
        try:
            result = usage_report.fetch_cursor_usage(
                Path("/tmp"), 1, "Asia/Shanghai"
            )
        finally:
            for patcher in reversed(patches):
                patcher.stop()

        self.assertFalse(result["complete"])

    def test_cursor_filtered_events_are_complete_without_aggregate_and_use_charged_cost(self):
        event = {
            "timestamp": 1_785_283_200_000,
            "model": "cursor-test",
            "chargedCents": 250,
            "tokenUsage": {
                "inputTokens": 10,
                "outputTokens": 2,
                "cacheWriteTokens": 3,
                "cacheReadTokens": 5,
                "totalCents": 900,
            },
        }
        patches = (
            mock.patch.object(
                usage_report.cursor_api,
                "read_cursor_state",
                return_value={"access_token": "token", "dashboard_user_id": 1},
            ),
            mock.patch.object(
                usage_report.cursor_api,
                "cursor_product_version",
                return_value="test",
            ),
            mock.patch.object(
                usage_report.cursor_api, "CursorClient", return_value=object()
            ),
            mock.patch.object(
                usage_report,
                "fetch_cursor_aggregate_audit",
                return_value={
                    "available": False,
                    "totals": {},
                    "errors": ["HTTP 400 invalid_argument"],
                },
            ),
            mock.patch.object(
                usage_report,
                "call_cursor",
                return_value={
                    "totalUsageEventsCount": 1,
                    "usageEventsDisplay": [event],
                },
            ),
        )
        for patcher in patches:
            patcher.start()
        try:
            result = usage_report.fetch_cursor_usage(
                Path("/tmp"), 10, "Asia/Shanghai"
            )
        finally:
            for patcher in reversed(patches):
                patcher.stop()

        self.assertTrue(result["complete"])
        self.assertEqual(result["error"], "")
        self.assertEqual(result["history"]["events"], 1)
        self.assertEqual(result["history"]["total_tokens"], 20)
        self.assertEqual(result["history"]["cost"], 2.5)
        self.assertEqual(result["history"]["estimated_raw_cost"], 9.0)
        self.assertEqual(result["daily_timeline"][0]["cost"], 2.5)
        self.assertEqual(result["daily_timeline"][0]["models"][0]["cost"], 2.5)
        self.assertFalse(result["aggregate_audit"]["available"])

    def test_cursor_missing_charged_cost_keeps_snapshot_incomplete(self):
        event = {
            "timestamp": 1_785_283_200_000,
            "model": "cursor-test",
            "tokenUsage": {"inputTokens": 10, "totalCents": 900},
        }
        patches = (
            mock.patch.object(
                usage_report.cursor_api,
                "read_cursor_state",
                return_value={"access_token": "token", "dashboard_user_id": 1},
            ),
            mock.patch.object(
                usage_report.cursor_api,
                "cursor_product_version",
                return_value="test",
            ),
            mock.patch.object(
                usage_report.cursor_api, "CursorClient", return_value=object()
            ),
            mock.patch.object(
                usage_report,
                "fetch_cursor_aggregate_audit",
                return_value={"available": False, "totals": {}, "errors": []},
            ),
            mock.patch.object(
                usage_report,
                "call_cursor",
                return_value={
                    "totalUsageEventsCount": 1,
                    "usageEventsDisplay": [event],
                },
            ),
        )
        for patcher in patches:
            patcher.start()
        try:
            result = usage_report.fetch_cursor_usage(
                Path("/tmp"), 10, "Asia/Shanghai"
            )
        finally:
            for patcher in reversed(patches):
                patcher.stop()

        self.assertFalse(result["complete"])
        self.assertIn("invalid chargedCents", result["error"])
        self.assertFalse(result["daily_timeline"][0]["pricing_complete"])

    def test_cursor_aggregate_windows_split_at_known_backend_boundaries(self):
        start = int(usage_report.CURSOR_START.timestamp() * 1000)
        end = int(
            usage_report.dt.datetime(
                2026, 7, 31, tzinfo=usage_report.dt.timezone.utc
            ).timestamp()
            * 1000
        )
        boundaries = [
            int(boundary.timestamp() * 1000)
            for boundary in usage_report.cursor_api.CURSOR_AGGREGATE_BOUNDARIES
        ]

        self.assertEqual(
            usage_report.cursor_api.split_aggregate_windows(start, end),
            [
                (start, boundaries[0]),
                (boundaries[0], boundaries[1]),
                (boundaries[1], end),
            ],
        )

    def test_cursor_cost_source_upgrade_reopens_returned_history_once(self):
        legacy = {
            "cursor_mutable_from": "2026-07-30",
            "cursor_pricing_version": "cursor-billed",
        }
        upgraded = {
            **legacy,
            "cursor_pricing_version": usage_report.CURSOR_PRICING_VERSION,
        }

        self.assertEqual(usage_report.cursor_mutable_from(legacy, "2026-07-31"), "")
        self.assertEqual(
            usage_report.cursor_mutable_from(upgraded, "2026-07-31"),
            "2026-07-30",
        )

    def test_cursor_http_error_is_redacted_and_bounded(self):
        token = "secret-access-token"
        email = "private@example.com"
        payload = {
            "code": "invalid_argument",
            "message": f"Bearer {token} {email} " + ("x" * 2000),
        }
        client = mock.Mock(token=token, email=email)
        client.dashboard.return_value = (
            400,
            {"Content-Type": "application/json"},
            json.dumps(payload).encode("utf-8"),
        )

        result = usage_report.call_cursor(client, "GetAggregatedUsageEvents", {})

        self.assertEqual(result["_status"], 400)
        self.assertEqual(result["_error_code"], "invalid_argument")
        self.assertNotIn(token, result["_error_body"])
        self.assertNotIn(email, result["_error_body"])
        self.assertLessEqual(len(result["_error_body"]), 803)

    def test_cursor_refreshes_its_open_window_and_freezes_earlier_dates(self):
        frozen = usage_report.empty_daily_row("2026-07-18")
        frozen.update(
            {
                "cursor_tokens": 100,
                "cursor_cost": 1.0,
                "cursor_input": 20,
                "cursor_cache_write": 10,
                "cursor_cache_read": 60,
                "cursor_output": 10,
            }
        )
        mutable = usage_report.empty_daily_row("2026-07-19")
        mutable.update(
            {
                "cursor_tokens": 120,
                "cursor_cost": 1.2,
                "cursor_input": 20,
                "cursor_cache_write": 10,
                "cursor_cache_read": 80,
                "cursor_output": 10,
            }
        )
        points = [
            {
                "date": "2026-07-18",
                "tokens": 999,
                "cost": 9.99,
                "input": 1,
                "cache_write": 2,
                "cache_read": 3,
                "output": 4,
            },
            {
                "date": "2026-07-19",
                "tokens": 160,
                "cost": 1.6,
                "input": 30,
                "cache_write": 20,
                "cache_read": 90,
                "output": 20,
            },
            {
                "date": "2026-07-20",
                "tokens": 50,
                "cost": 0.5,
                "input": 10,
                "cache_write": 5,
                "cache_read": 30,
                "output": 5,
            },
        ]

        rows = machine_fragments.apply_cursor_points(
            [frozen, mutable],
            points,
            usage_report.empty_daily_row,
            usage_report.apply_tool_point,
            usage_report.safe_int,
            usage_report.safe_float,
            today="2026-07-20",
            cursor_mutable_from="2026-07-19",
            freeze_cursor_history=True,
        )

        by_date = {row["date"]: row for row in rows}
        self.assertEqual(by_date["2026-07-18"]["cursor_tokens"], 100)
        self.assertEqual(by_date["2026-07-19"]["cursor_tokens"], 160)
        self.assertEqual(by_date["2026-07-20"]["cursor_tokens"], 50)

    def test_cursor_partial_snapshot_cannot_reduce_open_day(self):
        existing = usage_report.empty_daily_row("2026-07-19")
        existing["cursor_tokens"] = 120
        existing["cursor_cost"] = 1.2
        stats = {}

        rows = machine_fragments.apply_cursor_points(
            [existing],
            [{"date": "2026-07-19", "tokens": 80, "cost": 0.8}],
            usage_report.empty_daily_row,
            usage_report.apply_tool_point,
            usage_report.safe_int,
            usage_report.safe_float,
            today="2026-07-20",
            cursor_mutable_from="2026-07-19",
            freeze_cursor_history=True,
            reconciliation_stats=stats,
        )

        self.assertEqual(rows[0]["cursor_tokens"], 120)
        self.assertEqual(stats["regression_dates"], ["2026-07-19"])
        self.assertEqual(
            usage_report.advance_cursor_mutable_from(
                "2026-07-19", "2026-07-20", True, stats
            ),
            "2026-07-19",
        )

    def test_frozen_cursor_legacy_model_is_backfilled_when_totals_match(self):
        existing = usage_report.empty_daily_row("2026-07-18")
        existing.update({
            "cursor_tokens": 100,
            "cursor_cost": 1.0,
            "cursor_models": [{"model": "Legacy unknown", "tokens": 100, "cost": 1.0}],
        })
        stats = {}

        rows = machine_fragments.apply_cursor_points(
            [existing],
            [{"date": "2026-07-18", "tokens": 100, "cost": 1.0, "models": [{"model": "cursor-model", "tokens": 100, "cost": 1.0}]}],
            usage_report.empty_daily_row,
            usage_report.apply_tool_point,
            usage_report.safe_int,
            usage_report.safe_float,
            today="2026-07-20",
            cursor_mutable_from="2026-07-19",
            reconciliation_stats=stats,
        )

        self.assertEqual(rows[0]["cursor_models"][0]["model"], "cursor-model")
        self.assertEqual(stats["model_backfilled_dates"], ["2026-07-18"])


class TimezoneContractTests(unittest.TestCase):
    def test_invalid_timezone_is_rejected(self):
        with self.assertRaises(ValueError):
            usage_report.resolve_tz("Invalid/Definitely-Not-A-Timezone")


class LaunchdRetryTests(unittest.TestCase):
    def test_publish_captures_local_fragment_before_pull(self):
        source = (ROOT / "scripts" / "publish.sh").read_text(encoding="utf-8")
        main = source.index("# Capture local sources before touching the network")
        capture = source.index("collect_local_usage", main)
        pull = source.index("pull_latest", capture)
        merge = source.index("remerge_usage", pull)
        self.assertLess(capture, pull)
        self.assertLess(pull, merge)

    def run_wrapper(self, fake_body: str, attempts: int):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter = root / "count"
            fake = root / "publish.sh"
            fake.write_text(fake_body, encoding="utf-8")
            env = {
                **os.environ,
                "AI_USAGE_PUBLISH_SCRIPT": str(fake),
                "AI_USAGE_JOB_RETRY_ATTEMPTS": str(attempts),
                "AI_USAGE_JOB_RETRY_DELAY_SECONDS": "0",
                "AI_USAGE_LOCK_ROOT": str(root),
                "TEST_COUNTER": str(counter),
            }
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "launchd-run.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            count = int(counter.read_text(encoding="utf-8"))
            return result, count

    def test_whole_job_retries_until_success(self):
        result, count = self.run_wrapper(
            """#!/usr/bin/env bash
count=0
[[ -f "$TEST_COUNTER" ]] && count="$(<"$TEST_COUNTER")"
count=$((count + 1))
printf '%s' "$count" > "$TEST_COUNTER"
(( count >= 3 ))
""",
            attempts=3,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(count, 3)
        self.assertEqual(result.stdout.count("retrying in"), 2)

    def test_whole_job_does_not_sleep_after_last_failure(self):
        result, count = self.run_wrapper(
            """#!/usr/bin/env bash
count=0
[[ -f "$TEST_COUNTER" ]] && count="$(<"$TEST_COUNTER")"
count=$((count + 1))
printf '%s' "$count" > "$TEST_COUNTER"
exit 7
""",
            attempts=2,
        )
        self.assertEqual(result.returncode, 7, result.stdout)
        self.assertEqual(count, 2)
        self.assertEqual(result.stdout.count("retrying in"), 1)

    def test_simultaneous_launches_coalesce_to_one_publisher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter = root / "starts"
            fake = root / "publish.sh"
            fake.write_text(
                """#!/usr/bin/env bash
printf 'start\n' >> "$TEST_COUNTER"
sleep 1
""",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "AI_USAGE_PUBLISH_SCRIPT": str(fake),
                "AI_USAGE_JOB_RETRY_ATTEMPTS": "1",
                "AI_USAGE_LOCK_ROOT": str(root),
                "TEST_COUNTER": str(counter),
            }
            first = subprocess.Popen(
                ["bash", str(ROOT / "scripts" / "launchd-run.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            deadline = time.time() + 3
            while not counter.exists() and time.time() < deadline:
                time.sleep(0.01)
            second = subprocess.run(
                ["bash", str(ROOT / "scripts" / "launchd-run.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            first_output, _ = first.communicate(timeout=5)

            self.assertEqual(first.returncode, 0, first_output)
            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertEqual(
                counter.read_text(encoding="utf-8").splitlines(),
                ["start"],
                first_output + "\nSECOND:\n" + second.stdout,
            )

    def test_term_waits_for_child_before_releasing_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_file = root / "child-pid"
            fake = root / "publish.sh"
            fake.write_text(
                """#!/usr/bin/env bash
trap 'exit 143' TERM
printf '%s' "$$" > "$TEST_CHILD_PID"
while true; do sleep 1; done
""",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "AI_USAGE_PUBLISH_SCRIPT": str(fake),
                "AI_USAGE_JOB_RETRY_ATTEMPTS": "1",
                "AI_USAGE_LOCK_ROOT": str(root),
                "TEST_CHILD_PID": str(child_file),
            }
            wrapper = subprocess.Popen(
                ["bash", str(ROOT / "scripts" / "launchd-run.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            deadline = time.time() + 3
            while not child_file.exists() and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(child_file.exists())
            child_pid = int(child_file.read_text(encoding="utf-8"))
            wrapper.terminate()
            output, _ = wrapper.communicate(timeout=5)

            self.assertEqual(wrapper.returncode, 143, output)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
            self.assertFalse(any(root.glob("*.lock")))

    def test_stale_shlock_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter = root / "count"
            fake = root / "publish.sh"
            fake.write_text(
                """#!/usr/bin/env bash
printf '1' > "$TEST_COUNTER"
""",
                encoding="utf-8",
            )
            lock = root / f"ai-usage-report-{os.getuid()}-stale-test.lock"
            lock.write_text("99999999\n", encoding="utf-8")
            env = {
                **os.environ,
                "AI_USAGE_MACHINE_ID": "stale-test",
                "AI_USAGE_PUBLISH_SCRIPT": str(fake),
                "AI_USAGE_JOB_RETRY_ATTEMPTS": "1",
                "AI_USAGE_LOCK_ROOT": str(root),
                "TEST_COUNTER": str(counter),
            }
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "launchd-run.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(counter.read_text(encoding="utf-8"), "1")
            self.assertFalse(lock.exists())

    def test_invalid_lock_root_fails_instead_of_silently_coalescing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid_root = root / "not-a-directory"
            invalid_root.write_text("file", encoding="utf-8")
            counter = root / "count"
            fake = root / "publish.sh"
            fake.write_text(
                """#!/usr/bin/env bash
printf '1' > "$TEST_COUNTER"
""",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "AI_USAGE_PUBLISH_SCRIPT": str(fake),
                "AI_USAGE_JOB_RETRY_ATTEMPTS": "1",
                "AI_USAGE_LOCK_ROOT": str(invalid_root),
                "TEST_COUNTER": str(counter),
            }
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "launchd-run.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertFalse(counter.exists())
            self.assertIn("could not create lock root", result.stdout)


class PublishCliSafetyTests(unittest.TestCase):
    def setUp(self):
        self._previous_oneapi_state_path = os.environ.get("ONEAPI_STATE_PATH")
        self._oneapi_state_dir = tempfile.TemporaryDirectory()
        os.environ["ONEAPI_STATE_PATH"] = str(
            Path(self._oneapi_state_dir.name) / "missing-oneapi-state.json"
        )

    def tearDown(self):
        if self._previous_oneapi_state_path is None:
            os.environ.pop("ONEAPI_STATE_PATH", None)
        else:
            os.environ["ONEAPI_STATE_PATH"] = self._previous_oneapi_state_path
        self._oneapi_state_dir.cleanup()

    def git(self, cwd: Path, *args: str, check: bool = True):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=check,
        )

    def configure_credential(
        self,
        repo: Path,
        sandbox: Path,
        username: str | None,
        password: str = "test-secret",
    ):
        self.git(repo, "config", "--add", "credential.helper", "")
        if username is None:
            return

        helper = sandbox / "git-credential-test"
        helper.write_text(
            f"""#!/bin/sh
if [ "${{1:-}}" = get ]; then
  printf '%s\\n' 'username={username}' 'password={password}'
fi
""",
            encoding="utf-8",
        )
        helper.chmod(0o700)
        self.git(repo, "config", "--add", "credential.helper", str(helper))

    def test_missing_git_credential_fails_after_capture_before_git_or_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            repo = sandbox / "repo"
            remote = sandbox / "remote.git"
            events = sandbox / "events.log"
            git_trace = sandbox / "git-trace.log"
            repo.mkdir()
            (repo / "scripts").mkdir()
            (repo / "public" / "machines").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / ".keep").write_text("", encoding="utf-8")
            (repo / "scripts" / "publish.sh").write_bytes(
                (ROOT / "scripts" / "publish.sh").read_bytes()
            )
            (repo / "scripts" / "usage_pipeline.py").write_text(
                """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with Path(os.environ["TEST_EVENTS"]).open("a", encoding="utf-8") as handle:
    if "--collect-local-only" in sys.argv:
        handle.write("collect-local\\n")
        (root / "public" / "machines" / "mac-test.json").write_text(
            "captured\\n", encoding="utf-8"
        )
    elif "--merge-only" in sys.argv:
        handle.write("merge\\n")
        (root / "public" / "usage.json").write_text(
            "merged\\n", encoding="utf-8"
        )
""",
                encoding="utf-8",
            )
            (repo / "public" / "machines" / "mac-test.json").write_text(
                "before\n", encoding="utf-8"
            )
            (repo / "public" / "usage.json").write_text("before\n", encoding="utf-8")
            (repo / "docs" / "index.html").write_text("before\n", encoding="utf-8")

            self.git(repo, "init")
            self.git(repo, "checkout", "-b", "main")
            self.git(repo, "add", "-A")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "initial",
            )
            self.git(sandbox, "init", "--bare", str(remote))
            self.git(repo, "remote", "add", "origin", remote.as_uri())
            self.git(repo, "push", "-u", "origin", "main")
            self.configure_credential(repo, sandbox, username=None)
            initial_head = self.git(repo, "rev-parse", "HEAD").stdout.strip()

            bash_env = sandbox / "bash-env.sh"
            bash_env.write_text(
                """npm() {
  printf 'npm:%s\\n' "$*" >> "$TEST_EVENTS"
  mkdir -p docs
  printf 'built\\n' > docs/index.html
}
""",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "AI_USAGE_MACHINE_ID": "mac-test",
                "AI_USAGE_TIMEZONE": "Asia/Shanghai",
                "BASH_ENV": str(bash_env),
                "GIT_TRACE": str(git_trace),
                "TEST_EVENTS": str(events),
            }
            result = subprocess.run(
                ["bash", "scripts/publish.sh"],
                cwd=repo,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            event_lines = events.read_text(encoding="utf-8").splitlines()
            trace = git_trace.read_text(encoding="utf-8")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("collect-local", event_lines)
            self.assertEqual(
                (repo / "public" / "machines" / "mac-test.json").read_text(
                    encoding="utf-8"
                ),
                "captured\n",
            )
            self.assertNotIn("merge", event_lines)
            self.assertFalse(any(line.startswith("npm:") for line in event_lines))
            self.assertNotIn("git fetch", trace)
            self.assertNotIn("git pull", trace)
            self.assertIn("stored Git credential", result.stdout)
            self.assertEqual(
                self.git(repo, "rev-parse", "HEAD").stdout.strip(), initial_head
            )

    def test_auth_preflight_accepts_different_credential_username_with_write_access(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            repo = sandbox / "repo"
            remote = sandbox / "remote.git"
            events = sandbox / "events.log"
            git_trace = sandbox / "git-trace.log"
            repo.mkdir()
            (repo / "scripts").mkdir()
            (repo / "public" / "machines").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / ".keep").write_text("", encoding="utf-8")
            (repo / "scripts" / "publish.sh").write_bytes(
                (ROOT / "scripts" / "publish.sh").read_bytes()
            )
            (repo / "scripts" / "usage_pipeline.py").write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path
with Path(os.environ["TEST_EVENTS"]).open("a", encoding="utf-8") as handle:
    handle.write("collector-ran\\n")
""",
                encoding="utf-8",
            )
            (repo / "public" / "usage.json").write_text("{}\n", encoding="utf-8")
            self.git(repo, "init")
            self.git(repo, "checkout", "-b", "main")
            self.git(repo, "add", "-A")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "initial",
            )
            self.git(sandbox, "init", "--bare", str(remote))
            self.git(repo, "remote", "add", "origin", remote.as_uri())
            self.git(repo, "push", "-u", "origin", "main")
            self.configure_credential(
                repo,
                sandbox,
                username="CredentialAlias",
                password="valid-write-secret",
            )
            bash_env = sandbox / "bash-env.sh"
            bash_env.write_text(
                """npm() {
  [[ "${1:-} ${2:-}" == "run build" ]]
}
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "scripts/publish.sh"],
                cwd=repo,
                env={
                    **os.environ,
                    "AI_USAGE_MACHINE_ID": "mac-test",
                    "BASH_ENV": str(bash_env),
                    "GIT_TRACE": str(git_trace),
                    "TEST_EVENTS": str(events),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(),
                ["collector-ran", "collector-ran"],
            )
            trace = git_trace.read_text(encoding="utf-8")
            self.assertIn(
                "push --dry-run --no-verify origin HEAD:refs/heads/", trace
            )
            self.assertNotIn("valid-write-secret", result.stdout)
            self.assertNotIn("valid-write-secret", trace)

    def test_auth_preflight_rejects_failed_dry_run_without_leaking_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            repo = sandbox / "repo"
            missing_remote = sandbox / "missing-remote.git"
            events = sandbox / "events.log"
            git_trace = sandbox / "git-trace.log"
            secret = "never-print-this-token"
            repo.mkdir()
            (repo / "scripts").mkdir()
            (repo / "public" / "machines").mkdir(parents=True)
            (repo / "scripts" / "publish.sh").write_bytes(
                (ROOT / "scripts" / "publish.sh").read_bytes()
            )
            (repo / "scripts" / "usage_pipeline.py").write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path
Path(os.environ["TEST_EVENTS"]).write_text("collector-ran\\n", encoding="utf-8")
""",
                encoding="utf-8",
            )
            (repo / "public" / "usage.json").write_text("{}\n", encoding="utf-8")
            self.git(repo, "init")
            self.git(repo, "checkout", "-b", "main")
            self.git(repo, "add", "-A")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "initial",
            )
            self.git(
                repo, "remote", "add", "origin", missing_remote.resolve().as_uri()
            )
            self.configure_credential(
                repo, sandbox, username="BrickerP", password=secret
            )

            result = subprocess.run(
                ["bash", "scripts/publish.sh"],
                cwd=repo,
                env={
                    **os.environ,
                    "AI_USAGE_MACHINE_ID": "mac-test",
                    "GIT_TRACE": str(git_trace),
                    "TEST_EVENTS": str(events),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(), ["collector-ran"]
            )
            self.assertIn("dry-run push probe failed", result.stdout)
            self.assertNotIn(secret, result.stdout)
            trace = git_trace.read_text(encoding="utf-8")
            self.assertIn(
                "push --dry-run --no-verify origin HEAD:refs/heads/", trace
            )
            self.assertNotIn("git fetch", trace)
            self.assertNotIn(secret, trace)

    def test_publish_refuses_feature_branch_without_switching_or_collecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            repo = sandbox / "repo"
            events = sandbox / "events.log"
            repo.mkdir()
            (repo / "scripts").mkdir()
            (repo / "public" / "machines").mkdir(parents=True)
            (repo / "scripts" / "publish.sh").write_bytes(
                (ROOT / "scripts" / "publish.sh").read_bytes()
            )
            (repo / "scripts" / "usage_pipeline.py").write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path
with Path(os.environ["TEST_EVENTS"]).open("a", encoding="utf-8") as handle:
    handle.write("collector-ran\\n")
""",
                encoding="utf-8",
            )
            (repo / "public" / "usage.json").write_text("{}\n", encoding="utf-8")

            self.git(repo, "init")
            self.git(repo, "checkout", "-b", "main")
            self.git(repo, "add", "-A")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "initial",
            )
            self.git(repo, "checkout", "-b", "feature/wip")
            initial_head = self.git(repo, "rev-parse", "HEAD").stdout.strip()

            bash_env = sandbox / "bash-env.sh"
            bash_env.write_text(
                """gh() {
  printf 'X Failed to log in to github.com account BrickerP (default)\\n'
  return 1
}
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "scripts/publish.sh"],
                cwd=repo,
                env={
                    **os.environ,
                    "AI_USAGE_MACHINE_ID": "mac-test",
                    "BASH_ENV": str(bash_env),
                    "TEST_EVENTS": str(events),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(
                self.git(repo, "branch", "--show-current").stdout.strip(),
                "feature/wip",
            )
            self.assertEqual(
                self.git(repo, "rev-parse", "HEAD").stdout.strip(), initial_head
            )
            self.assertFalse(events.exists(), result.stdout)

    def test_publish_preserves_preexisting_merge_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            repo = sandbox / "repo"
            events = sandbox / "events.log"
            repo.mkdir()
            (repo / "scripts").mkdir()
            (repo / "public" / "machines").mkdir(parents=True)
            (repo / "scripts" / "publish.sh").write_bytes(
                (ROOT / "scripts" / "publish.sh").read_bytes()
            )
            (repo / "scripts" / "usage_pipeline.py").write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path
Path(os.environ["TEST_EVENTS"]).write_text("collector-ran\\n", encoding="utf-8")
""",
                encoding="utf-8",
            )
            conflict = repo / "conflict.txt"
            conflict.write_text("base\n", encoding="utf-8")
            (repo / "public" / "usage.json").write_text("{}\n", encoding="utf-8")
            self.git(repo, "init")
            self.git(repo, "checkout", "-b", "main")
            self.git(repo, "add", "-A")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "initial",
            )
            self.git(repo, "checkout", "-b", "incoming")
            conflict.write_text("incoming\n", encoding="utf-8")
            self.git(repo, "add", "conflict.txt")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "incoming",
            )
            self.git(repo, "checkout", "main")
            conflict.write_text("main\n", encoding="utf-8")
            self.git(repo, "add", "conflict.txt")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "main",
            )
            merge = self.git(repo, "merge", "incoming", check=False)
            self.assertNotEqual(merge.returncode, 0, merge.stdout)
            merge_head = repo / ".git" / "MERGE_HEAD"
            self.assertTrue(merge_head.is_file())

            result = subprocess.run(
                ["bash", "scripts/publish.sh"],
                cwd=repo,
                env={
                    **os.environ,
                    "AI_USAGE_MACHINE_ID": "mac-test",
                    "TEST_EVENTS": str(events),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertTrue(merge_head.is_file(), result.stdout)
            self.assertIn("UU conflict.txt", self.git(repo, "status", "--short").stdout)
            self.assertFalse(events.exists(), result.stdout)

    def test_publish_stages_only_report_artifacts_created_during_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            repo = sandbox / "repo"
            remote = sandbox / "remote.git"
            remote_writer = sandbox / "remote-writer"
            git_trace = sandbox / "git-trace.log"
            pre_push_events = sandbox / "pre-push-events.log"
            secret = "ordinary-helper-secret"
            repo.mkdir()
            (repo / "scripts").mkdir()
            (repo / "public" / "machines").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / ".keep").write_text("", encoding="utf-8")
            (repo / "scripts" / "publish.sh").write_bytes(
                (ROOT / "scripts" / "publish.sh").read_bytes()
            )
            (repo / "scripts" / "usage_pipeline.py").write_text(
                """#!/usr/bin/env python3
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if "--collect-local-only" in sys.argv:
    (root / "public" / "machines" / "mac-test.json").write_text(
        "captured\\n", encoding="utf-8"
    )
    (root / "scripts" / "unrelated-after-preflight.txt").write_text(
        "do not publish\\n", encoding="utf-8"
    )
elif "--merge-only" in sys.argv:
    (root / "public" / "usage.json").write_text("merged\\n", encoding="utf-8")
""",
                encoding="utf-8",
            )
            (repo / "public" / "machines" / "mac-test.json").write_text(
                "before\n", encoding="utf-8"
            )
            (repo / "public" / "usage.json").write_text("before\n", encoding="utf-8")
            (repo / "docs" / "index.html").write_text("before\n", encoding="utf-8")

            self.git(repo, "init")
            self.git(repo, "checkout", "-b", "main")
            self.git(repo, "add", "-A")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "initial",
            )
            self.git(sandbox, "init", "--bare", str(remote))
            self.git(repo, "remote", "add", "origin", remote.as_uri())
            self.git(repo, "push", "-u", "origin", "main")
            self.configure_credential(
                repo, sandbox, username="BrickerP", password=secret
            )
            pre_push_hook = repo / ".git" / "hooks" / "pre-push"
            pre_push_hook.write_text(
                """#!/bin/sh
while read -r _local_ref _local_oid remote_ref _remote_oid; do
  printf '%s\\n' "$remote_ref" >> "$TEST_PRE_PUSH_EVENTS"
  case "$remote_ref" in
    refs/heads/ai-usage-auth-probe/*) exit 91 ;;
  esac
done
""",
                encoding="utf-8",
            )
            pre_push_hook.chmod(0o700)

            self.git(
                sandbox,
                "clone",
                "--branch",
                "main",
                remote.as_uri(),
                str(remote_writer),
            )
            (remote_writer / "docs" / "remote-only.html").write_text(
                "remote\n", encoding="utf-8"
            )
            self.git(remote_writer, "add", "docs/remote-only.html")
            self.git(
                remote_writer,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "advance remote main",
            )
            self.git(remote_writer, "push", "origin", "main")

            bash_env = sandbox / "bash-env.sh"
            bash_env.write_text(
                """npm() {
  if [[ "${1:-} ${2:-}" == "run build" ]]; then
    mkdir -p docs
    printf 'built\\n' > docs/index.html
    return 0
  fi
  return 99
}
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "scripts/publish.sh"],
                cwd=repo,
                env={
                    **os.environ,
                    "AI_USAGE_MACHINE_ID": "mac-test",
                    "BASH_ENV": str(bash_env),
                    "GIT_TRACE": str(git_trace),
                    "TEST_PRE_PUSH_EVENTS": str(pre_push_events),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            committed = self.git(
                repo, "show", "--pretty=format:", "--name-only", "HEAD"
            ).stdout.splitlines()
            self.assertNotIn("scripts/unrelated-after-preflight.txt", committed)
            self.assertEqual(
                self.git(repo, "status", "--short").stdout.splitlines(),
                ["?? scripts/unrelated-after-preflight.txt"],
            )
            self.assertEqual(
                self.git(repo, "rev-parse", "HEAD").stdout.strip(),
                self.git(repo, "rev-parse", "origin/main").stdout.strip(),
            )
            remote_refs = self.git(
                remote, "for-each-ref", "--format=%(refname)", "refs/heads"
            ).stdout.splitlines()
            self.assertEqual(remote_refs, ["refs/heads/main"])
            trace = git_trace.read_text(encoding="utf-8")
            self.assertIn(
                "push --dry-run --no-verify origin HEAD:refs/heads/", trace
            )
            self.assertIn("push origin HEAD:refs/heads/main", trace)
            self.assertNotIn("extraheader", trace)
            self.assertNotIn(secret, result.stdout)
            self.assertNotIn(secret, trace)
            self.assertEqual(
                pre_push_events.read_text(encoding="utf-8").splitlines(),
                ["refs/heads/main"],
            )

    def run_publish_with_existing_ahead_commits(self, ahead_changes):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            repo = sandbox / "repo"
            remote = sandbox / "remote.git"
            repo.mkdir()
            (repo / "scripts").mkdir()
            (repo / "public" / "machines").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / ".keep").write_text("", encoding="utf-8")
            (repo / "scripts" / "publish.sh").write_bytes(
                (ROOT / "scripts" / "publish.sh").read_bytes()
            )
            (repo / "scripts" / "usage_pipeline.py").write_text(
                "# no-op collector for retrying an existing local commit\n",
                encoding="utf-8",
            )
            (repo / "public" / "machines" / "mac-test.json").write_text(
                "stable\n", encoding="utf-8"
            )
            (repo / "public" / "usage.json").write_text("before\n", encoding="utf-8")
            (repo / "docs" / "index.html").write_text("stable\n", encoding="utf-8")

            self.git(repo, "init")
            self.git(repo, "checkout", "-b", "main")
            self.git(repo, "add", "-A")
            self.git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "initial",
            )
            self.git(sandbox, "init", "--bare", str(remote))
            self.git(repo, "remote", "add", "origin", remote.as_uri())
            self.git(repo, "push", "-u", "origin", "main")
            self.configure_credential(repo, sandbox, username="BrickerP")
            remote_before = self.git(repo, "rev-parse", "origin/main").stdout.strip()

            for index, (ahead_path, content) in enumerate(ahead_changes, start=1):
                ahead_file = repo / ahead_path
                if content is None:
                    ahead_file.unlink()
                else:
                    ahead_file.parent.mkdir(parents=True, exist_ok=True)
                    ahead_file.write_text(content, encoding="utf-8")
                self.git(repo, "add", "-A", "--", ahead_path)
                self.git(
                    repo,
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    f"unpublished report {index}",
                )
            local_ahead = self.git(repo, "rev-parse", "HEAD").stdout.strip()
            self.assertNotEqual(local_ahead, remote_before)

            bash_env = sandbox / "bash-env.sh"
            bash_env.write_text(
                """npm() {
  [[ "${1:-} ${2:-}" == "run build" ]]
}
""",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "scripts/publish.sh"],
                cwd=repo,
                env={
                    **os.environ,
                    "AI_USAGE_MACHINE_ID": "mac-test",
                    "BASH_ENV": str(bash_env),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            return (
                result,
                local_ahead,
                remote_before,
                self.git(repo, "rev-parse", "origin/main").stdout.strip(),
            )

    def test_publish_pushes_existing_generated_ahead_commits_when_report_has_no_new_diff(
        self,
    ):
        result, local_ahead, _remote_before, remote_after = (
            self.run_publish_with_existing_ahead_commits(
                [
                    ("public/usage.json", "first generated change\n"),
                    ("docs/index.html", "second generated change\n"),
                ]
            )
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(remote_after, local_ahead, result.stdout)

    def test_publish_rejects_existing_non_generated_ahead_commit(self):
        result, local_ahead, remote_before, remote_after = (
            self.run_publish_with_existing_ahead_commits(
                [("README.md", "non-report change\n")]
            )
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotEqual(local_ahead, remote_before)
        self.assertEqual(remote_after, remote_before, result.stdout)
        self.assertIn("README.md", result.stdout)
        self.assertIn("report artifact allowlist", result.stdout)

    def test_publish_rejects_non_generated_path_added_then_deleted_in_ahead_history(
        self,
    ):
        result, local_ahead, remote_before, remote_after = (
            self.run_publish_with_existing_ahead_commits(
                [
                    ("scratch.txt", "temporary non-report change\n"),
                    ("scratch.txt", None),
                ]
            )
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotEqual(local_ahead, remote_before)
        self.assertEqual(remote_after, remote_before, result.stdout)
        self.assertIn("scratch.txt", result.stdout)
        self.assertIn("report artifact allowlist", result.stdout)


class PublishConflictTests(unittest.TestCase):
    def git(self, cwd: Path, *args: str):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )

    def commit_all(self, repo: Path, message: str):
        self.git(repo, "add", "-A")
        self.git(
            repo,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            message,
        )

    def test_two_mac_generated_conflict_rebases_before_remerge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            seed.mkdir()
            self.git(seed, "init")
            self.git(seed, "checkout", "-b", "main")
            (seed / "public" / "machines").mkdir(parents=True)
            (seed / "docs").mkdir()
            (seed / "scripts").mkdir()
            (seed / "public" / "usage.json").write_text("base\n", encoding="utf-8")
            (seed / "docs" / "index.html").write_text("base\n", encoding="utf-8")
            (seed / "scripts" / "publish.sh").write_text(
                (ROOT / "scripts" / "publish.sh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            self.commit_all(seed, "base")

            remote = root / "remote.git"
            self.git(root, "clone", "--bare", str(seed), str(remote))
            mac_a = root / "mac-a"
            mac_b = root / "mac-b"
            self.git(root, "clone", str(remote), str(mac_a))
            self.git(root, "clone", str(remote), str(mac_b))
            (mac_a / "public" / "machines").mkdir(parents=True, exist_ok=True)
            (mac_b / "public" / "machines").mkdir(parents=True, exist_ok=True)

            (mac_a / "public" / "machines" / "a.json").write_text(
                "a\n", encoding="utf-8"
            )
            (mac_a / "public" / "usage.json").write_text("from-a\n", encoding="utf-8")
            (mac_a / "docs" / "index.html").write_text("from-a\n", encoding="utf-8")
            self.commit_all(mac_a, "mac a")

            (mac_b / "public" / "machines" / "b.json").write_text(
                "b\n", encoding="utf-8"
            )
            (mac_b / "public" / "usage.json").write_text("from-b\n", encoding="utf-8")
            (mac_b / "docs" / "index.html").write_text("from-b\n", encoding="utf-8")
            self.commit_all(mac_b, "mac b")
            self.git(mac_a, "push", "origin", "main")

            source = (mac_b / "scripts" / "publish.sh").read_text(encoding="utf-8")
            prefix = source.split("command -v python3", 1)[0]
            harness = mac_b / "scripts" / "test-pull.sh"
            harness.write_text(prefix + "\npull_latest\n", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(harness)],
                cwd=mac_b,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((mac_b / "public" / "machines" / "a.json").is_file())
            self.assertTrue((mac_b / "public" / "machines" / "b.json").is_file())
            self.assertEqual(
                (mac_b / "public" / "usage.json").read_text(encoding="utf-8"),
                "from-a\n",
            )
            self.assertFalse((mac_b / ".git" / "rebase-merge").exists())


if __name__ == "__main__":
    unittest.main()
