from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


oneapi_usage = load_module(
    "oneapi_usage_under_test",
    ROOT / "scripts" / "oneapi_usage.py",
)
usage_report = load_module(
    "ai_usage_comparison_image_under_test",
    ROOT / "scripts" / "ai_usage_comparison_image.py",
)


def record(
    model: str,
    *,
    created_at: int = 1785254400,
    prompt: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    quota: int = 0,
) -> dict:
    return {
        "created_at": created_at,
        "model_name": model,
        "prompt_tokens": prompt,
        "completion_tokens": output,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "quota": quota,
        "request_id": f"{model}-{created_at}-{prompt}",
    }


class OneApiExclusiveAggregationTests(unittest.TestCase):
    def test_excludes_codex_and_claude_families_but_keeps_other_models(self):
        result = oneapi_usage.aggregate_records(
            [
                record("gpt-5.6-sol", prompt=100, quota=1000),
                record("openai/o4-mini", prompt=200, quota=2000),
                record("claude-opus-5", prompt=300, quota=3000),
                record("grok-4.5", prompt=400, quota=4000),
                record("deepseek-v4-flash", cache_read=500, quota=5000),
            ],
            timezone="Asia/Shanghai",
            window_start="2026-07-29",
            window_end="2026-07-29",
        )

        self.assertEqual(result["totals"]["total_tokens"], 900)
        self.assertEqual(result["totals"]["requests"], 2)
        self.assertEqual(
            [row["model"] for row in result["daily_timeline"][0]["model_breakdowns"]],
            ["deepseek-v4-flash", "grok-4.5"],
        )
        self.assertEqual(result["excluded"]["codex"]["requests"], 2)
        self.assertEqual(result["excluded"]["claude"]["requests"], 1)

    def test_quota_conversion_uses_oneapi_display_unit(self):
        self.assertAlmostEqual(oneapi_usage.quota_to_cny(212_468), 0.849872)
        self.assertAlmostEqual(oneapi_usage.quota_to_usd(212_468), 0.11898208)

    def test_empty_model_is_not_added_to_exclusive_totals(self):
        result = oneapi_usage.aggregate_records(
            [record("", prompt=123, quota=456)],
            timezone="Asia/Shanghai",
            window_start="2026-07-29",
            window_end="2026-07-29",
        )

        self.assertEqual(result["totals"]["total_tokens"], 0)
        self.assertEqual(result["unclassified"]["requests"], 1)
        self.assertEqual(result["daily_timeline"], [])

    def test_daily_cost_is_derived_from_filtered_quota(self):
        result = oneapi_usage.aggregate_records(
            [
                record("claude-opus-5", prompt=100, quota=250_000),
                record("deepseek-v4-flash", prompt=100, quota=250_000),
            ],
            timezone="Asia/Shanghai",
            window_start="2026-07-29",
            window_end="2026-07-29",
        )

        day = result["daily_timeline"][0]
        self.assertEqual(day["quota"], 250_000)
        self.assertAlmostEqual(day["cost_cny"], 1.0)
        self.assertAlmostEqual(day["cost_usd"], 0.14)


class OneApiBrowserCollectionTests(unittest.TestCase):
    def test_explicit_chrome_use_precedes_path_and_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            explicit = home / "explicit-chrome-use"
            path_candidate = home / "path-chrome-use"
            fallback = home / ".local" / "bin" / "chrome-use"
            fallback.parent.mkdir(parents=True)
            for candidate in (explicit, path_candidate, fallback):
                candidate.write_text("#!/bin/sh\n", encoding="utf-8")
                candidate.chmod(0o755)

            with (
                mock.patch.dict(
                    oneapi_usage.os.environ,
                    {"CHROME_USE_BIN": str(explicit)},
                    clear=False,
                ),
                mock.patch.object(
                    oneapi_usage.shutil,
                    "which",
                    return_value=str(path_candidate),
                ),
                mock.patch.object(oneapi_usage.Path, "home", return_value=home),
            ):
                self.assertEqual(oneapi_usage.chrome_use_path(), str(explicit))

    def test_explicit_chrome_use_must_be_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "chrome-use"
            explicit.write_text("#!/bin/sh\n", encoding="utf-8")
            explicit.chmod(0o644)

            with mock.patch.dict(
                oneapi_usage.os.environ,
                {"CHROME_USE_BIN": str(explicit)},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "CHROME_USE_BIN is not executable",
                ):
                    oneapi_usage.chrome_use_path()

    def test_chrome_use_falls_back_to_executable_in_local_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            invalid_path_entry = home / "path-chrome-use"
            invalid_path_entry.write_text("#!/bin/sh\n", encoding="utf-8")
            invalid_path_entry.chmod(0o644)
            fallback = home / ".local" / "bin" / "chrome-use"
            fallback.parent.mkdir(parents=True)
            fallback.write_text("#!/bin/sh\n", encoding="utf-8")
            fallback.chmod(0o755)

            with (
                mock.patch.dict(
                    oneapi_usage.os.environ,
                    {"CHROME_USE_BIN": ""},
                    clear=False,
                ),
                mock.patch.object(
                    oneapi_usage.shutil,
                    "which",
                    return_value=str(invalid_path_entry),
                ),
                mock.patch.object(oneapi_usage.Path, "home", return_value=home),
            ):
                self.assertEqual(oneapi_usage.chrome_use_path(), str(fallback))

    def test_chrome_use_uses_executable_from_path_before_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path_candidate = home / "path-chrome-use"
            fallback = home / ".local" / "bin" / "chrome-use"
            fallback.parent.mkdir(parents=True)
            for candidate in (path_candidate, fallback):
                candidate.write_text("#!/bin/sh\n", encoding="utf-8")
                candidate.chmod(0o755)

            with (
                mock.patch.dict(
                    oneapi_usage.os.environ,
                    {"CHROME_USE_BIN": ""},
                    clear=False,
                ),
                mock.patch.object(
                    oneapi_usage.shutil,
                    "which",
                    return_value=str(path_candidate),
                ),
                mock.patch.object(oneapi_usage.Path, "home", return_value=home),
            ):
                self.assertEqual(
                    oneapi_usage.chrome_use_path(),
                    str(path_candidate),
                )

    def test_chrome_use_reports_all_search_locations_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                mock.patch.dict(
                    oneapi_usage.os.environ,
                    {"CHROME_USE_BIN": ""},
                    clear=False,
                ),
                mock.patch.object(oneapi_usage.shutil, "which", return_value=None),
                mock.patch.object(oneapi_usage.Path, "home", return_value=home),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"set CHROME_USE_BIN.*add chrome-use to PATH.*\.local/bin/chrome-use",
                ):
                    oneapi_usage.chrome_use_path()

    def test_collection_loads_saved_state_in_an_isolated_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            browser_result = json.dumps(
                {
                    "_complete": True,
                    "_pages": 1,
                    "_records": [
                        record(
                            "deepseek-v4-flash",
                            prompt=100,
                            quota=250_000,
                        )
                    ],
                }
            )
            completed = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=json.dumps(browser_result),
                    stderr="",
                ),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ]

            with mock.patch.object(
                oneapi_usage.subprocess,
                "run",
                side_effect=completed,
            ) as run, mock.patch.object(
                oneapi_usage,
                "chrome_use_path",
                return_value="/usr/bin/true",
            ):
                result = oneapi_usage.collect_oneapi(
                    timezone="Asia/Shanghai",
                    state_path=str(state_path),
                    since="2026-07-29",
                    until="2026-07-29",
                )

        open_command = run.call_args_list[0].args[0]
        eval_command = run.call_args_list[1].args[0]
        close_command = run.call_args_list[2].args[0]
        self.assertIn("--state", open_command)
        self.assertIn(str(state_path), open_command)
        self.assertIn("--session", open_command)
        self.assertIn("--session", eval_command)
        self.assertIn("--session", close_command)
        self.assertEqual(result["totals"]["total_tokens"], 100)
        self.assertTrue(result["complete"])

    def test_incomplete_browser_result_is_rejected_instead_of_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            browser_result = json.dumps(
                {
                    "_complete": False,
                    "_e": "http_429_page_115",
                    "_page": 115,
                    "_count": 2_280,
                    "_records": [record("deepseek-v4-flash", prompt=100)],
                }
            )
            completed = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=json.dumps(browser_result),
                    stderr="",
                ),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ]

            with mock.patch.object(
                oneapi_usage.subprocess,
                "run",
                side_effect=completed,
            ), mock.patch.object(
                oneapi_usage,
                "chrome_use_path",
                return_value="/usr/bin/true",
            ):
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    oneapi_usage.collect_oneapi(
                        timezone="Asia/Shanghai",
                        state_path=str(state_path),
                        since="2026-07-29",
                        until="2026-07-29",
                    )

    def test_rate_limited_batch_resumes_from_failed_page_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            first = record("deepseek-v4-flash", prompt=100, quota=1000)
            second = record("grok-4.5", prompt=200, quota=2000)
            completed = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=json.dumps(
                        json.dumps(
                            {
                                "_complete": False,
                                "_rate_limited": True,
                                "_next_page": 7,
                                "_pages": 7,
                                "_records": [first],
                            }
                        )
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=json.dumps(
                        json.dumps(
                            {
                                "_complete": True,
                                "_next_page": 8,
                                "_pages": 1,
                                "_records": [first, second],
                            }
                        )
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ]

            with (
                mock.patch.object(
                    oneapi_usage.subprocess,
                    "run",
                    side_effect=completed,
                ) as run,
                mock.patch.object(
                    oneapi_usage,
                    "chrome_use_path",
                    return_value="/usr/bin/true",
                ),
                mock.patch.object(oneapi_usage.time, "sleep") as sleep,
            ):
                result = oneapi_usage.collect_oneapi(
                    timezone="Asia/Shanghai",
                    state_path=str(state_path),
                    since="2026-07-29",
                    until="2026-07-29",
                )

        sleep.assert_called_once()
        self.assertEqual(result["totals"]["requests"], 2)
        self.assertEqual(result["totals"]["total_tokens"], 300)
        self.assertIn("const START_PAGE = 0", run.call_args_list[1].kwargs["input"])
        self.assertIn("const START_PAGE = 7", run.call_args_list[2].kwargs["input"])


class OneApiReconciliationTests(unittest.TestCase):
    def prior_row(self, date_key: str, tokens: int, cost: float) -> dict:
        row = usage_report.empty_daily_row(date_key)
        row.update(
            {
                "oneapi_tokens": tokens,
                "oneapi_input": tokens,
                "oneapi_cost": cost,
                "oneapi_requests": 1,
            }
        )
        return row

    def test_unavailable_collection_preserves_prior_oneapi_history(self):
        current = [usage_report.empty_daily_row("2026-07-29")]
        prior = [self.prior_row("2026-07-28", 120, 0.12)]

        rows = usage_report.reconcile_oneapi_rows(
            current,
            prior,
            {"available": False, "complete": False, "note": "session expired"},
        )

        by_date = {row["date"]: row for row in rows}
        self.assertEqual(by_date["2026-07-28"]["oneapi_tokens"], 120)
        self.assertAlmostEqual(by_date["2026-07-28"]["oneapi_cost"], 0.12)

    def test_complete_window_replaces_prior_values_and_keeps_older_history(self):
        current = [usage_report.empty_daily_row("2026-07-29")]
        prior = [
            self.prior_row("2026-07-20", 20, 0.02),
            self.prior_row("2026-07-29", 999, 9.99),
        ]
        fresh = {
            "available": True,
            "complete": True,
            "window": {"start": "2026-07-25", "end": "2026-07-29"},
            "daily_timeline": [
                {
                    "date": "2026-07-29",
                    "tokens": 300,
                    "input": 100,
                    "output": 50,
                    "cache_read": 150,
                    "cache_write": 0,
                    "requests": 2,
                    "cost_usd": 0.14,
                }
            ],
        }

        rows = usage_report.reconcile_oneapi_rows(current, prior, fresh)

        by_date = {row["date"]: row for row in rows}
        self.assertEqual(by_date["2026-07-20"]["oneapi_tokens"], 20)
        self.assertEqual(by_date["2026-07-29"]["oneapi_tokens"], 300)
        self.assertEqual(by_date["2026-07-29"]["oneapi_cache_read"], 150)
        self.assertAlmostEqual(by_date["2026-07-29"]["oneapi_cost"], 0.14)
        self.assertAlmostEqual(by_date["2026-07-29"]["total_cost"], 0.14)

    def test_payload_keeps_prior_models_and_adds_non_overlapping_comate_history(self):
        prior = {
            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
            "daily_timeline": [
                {
                    "date": "2026-07-20",
                    "tokens": 20,
                    "input": 20,
                    "cost_usd": 0.02,
                    "source": "oneapi",
                    "model_breakdowns": [
                        {"model": "grok-old", "total_tokens": 20, "cost_usd": 0.02}
                    ],
                }
            ],
        }
        fetched = {
            "available": True,
            "complete": True,
            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
            "window": {"start": "2026-07-25", "end": "2026-07-29"},
            "daily_timeline": [
                {
                    "date": "2026-07-29",
                    "tokens": 30,
                    "input": 30,
                    "cost_usd": 0.03,
                    "model_breakdowns": [
                        {"model": "deepseek-new", "total_tokens": 30, "cost_usd": 0.03}
                    ],
                }
            ],
        }
        comate = {
            "history": {"first": "2026-07-13", "last": "2026-07-29"},
            "total_tokens": 109,
            "daily_timeline": [
                {
                    "date": "2026-07-13",
                    "tokens": 9,
                    "input": 9,
                    "model_breakdowns": [
                        {"model": "GLM-5", "total_tokens": 9, "cost_usd": 0}
                    ],
                },
                {
                    "date": "2026-07-29",
                    "tokens": 100,
                    "input": 100,
                    "model_breakdowns": [
                        {"model": "duplicate-local", "total_tokens": 100, "cost_usd": 0}
                    ],
                },
            ],
        }

        result = usage_report.reconcile_oneapi_payload(prior, fetched, comate)

        by_date = {point["date"]: point for point in result["daily_timeline"]}
        self.assertEqual(set(by_date), {"2026-07-13", "2026-07-20", "2026-07-29"})
        self.assertEqual(by_date["2026-07-13"]["source"], "comate-local")
        self.assertEqual(by_date["2026-07-20"]["model_breakdowns"][0]["model"], "grok-old")
        self.assertEqual(
            by_date["2026-07-29"]["model_breakdowns"][0]["model"],
            "deepseek-new",
        )
        self.assertEqual(result["totals"]["total_tokens"], 59)

    def test_model_remainder_scales_to_card_total(self):
        models = usage_report.models_with_remainder(
            [
                {"model": "a", "tokens": 80, "cost": 8},
                {"model": "b", "tokens": 40, "cost": 4},
            ],
            total_tokens=90,
            total_cost=9,
        )

        self.assertEqual(sum(model["tokens"] for model in models), 90)
        self.assertAlmostEqual(sum(model["cost"] for model in models), 9)


class SourceStatusTests(unittest.TestCase):
    def test_reconciles_all_sources_and_preserves_last_success_when_stale(self):
        attempted_at = "2026-07-30T12:00:00+08:00"
        prior = {
            "source_status": {
                "oneapi": {
                    "status": "fresh",
                    "attempted_at": "2026-07-29T12:00:00+08:00",
                    "last_success_at": "2026-07-29T12:00:00+08:00",
                    "window_end": "2026-07-29",
                    "lag_days": 0,
                    "error": "",
                }
            }
        }
        attempts = {
            "codex": {
                "attempted": True,
                "fresh": True,
                "has_data": True,
                "window_end": "2026-07-30",
                "error": "",
            },
            "claude": {
                "attempted": True,
                "fresh": True,
                "has_data": False,
                "window_end": "2026-07-30",
                "error": "",
            },
            "cursor": {
                "attempted": True,
                "fresh": False,
                "has_data": False,
                "window_end": "",
                "error": "missing local Cursor access token",
            },
            "oneapi": {
                "attempted": True,
                "fresh": False,
                "has_data": True,
                "window_end": "",
                "error": "session expired",
            },
        }

        result = usage_report.reconcile_source_status(
            prior,
            attempts,
            attempted_at=attempted_at,
            today="2026-07-30",
        )

        self.assertEqual(set(result), {"codex", "claude", "cursor", "oneapi"})
        self.assertEqual(
            result["codex"],
            {
                "status": "fresh",
                "attempted_at": attempted_at,
                "last_success_at": attempted_at,
                "window_end": "2026-07-30",
                "lag_days": 0,
                "error": "",
            },
        )
        self.assertEqual(result["claude"]["status"], "fresh")
        self.assertEqual(result["cursor"]["status"], "failed")
        self.assertIsNone(result["cursor"]["lag_days"])
        self.assertEqual(result["oneapi"]["status"], "stale")
        self.assertEqual(
            result["oneapi"]["last_success_at"],
            "2026-07-29T12:00:00+08:00",
        )
        self.assertEqual(result["oneapi"]["window_end"], "2026-07-29")
        self.assertEqual(result["oneapi"]["lag_days"], 1)
        self.assertEqual(result["oneapi"]["error"], "oneapi_unavailable")

    def test_oneapi_failure_keeps_prior_stats_and_marks_source_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machines = root / "machines"
            machines.mkdir()
            usage_path = root / "usage.json"
            missing_state = root / "missing-oneapi-state.json"

            (machines / "mac-test.json").write_text(
                json.dumps(
                    {
                        "machine_id": "mac-test",
                        "hostname": "mac-test.local",
                        "timezone": "Asia/Shanghai",
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )
            prior_row = usage_report.empty_daily_row("2026-07-29")
            prior_row.update(
                {
                    "oneapi_tokens": 120,
                    "oneapi_input": 100,
                    "oneapi_output": 20,
                    "oneapi_requests": 2,
                    "oneapi_cost": 0.14,
                }
            )
            prior_oneapi = {
                "available": True,
                "complete": True,
                "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
                "request_count": 2,
                "pages": 7,
                "window": {"start": "2026-07-25", "end": "2026-07-29"},
                "history": {"first": "2026-07-29", "last": "2026-07-29"},
                "totals": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 120,
                    "quota": 250_000,
                    "cost_cny": 1.0,
                    "cost_usd": 0.14,
                    "requests": 2,
                },
                "daily_timeline": [
                    {
                        "date": "2026-07-29",
                        "tokens": 120,
                        "input": 100,
                        "output": 20,
                        "cache_read": 0,
                        "cache_write": 0,
                        "requests": 2,
                        "quota": 250_000,
                        "cost_cny": 1.0,
                        "cost_usd": 0.14,
                        "source": "oneapi",
                        "model_breakdowns": [],
                    }
                ],
            }
            prior_payload = {
                "generated_at": "2026-07-29T12:00:00+08:00",
                "timezone": "Asia/Shanghai",
                "cursor_mutable_from": "2026-07-29",
                "source_status": {
                    "oneapi": {
                        "status": "fresh",
                        "attempted_at": "2026-07-29T12:00:00+08:00",
                        "last_success_at": "2026-07-29T12:00:00+08:00",
                        "window_end": "2026-07-29",
                        "lag_days": 0,
                        "error": "",
                    }
                },
                "oneapi": prior_oneapi,
                "daily": [prior_row],
            }
            usage_path.write_text(json.dumps(prior_payload), encoding="utf-8")

            with (
                mock.patch.object(
                    usage_report,
                    "local_today",
                    return_value="2026-07-30",
                ),
                mock.patch.object(
                    usage_report,
                    "fetch_cursor_usage",
                    return_value={
                        "available": False,
                        "complete": False,
                        "error": "missing local Cursor access token",
                    },
                ),
                mock.patch.dict(
                    usage_report.os.environ,
                    {"ONEAPI_STATE_PATH": str(missing_state)},
                    clear=False,
                ),
            ):
                result = usage_report.collect_usage(
                    root,
                    "Asia/Shanghai",
                    root / "scratch",
                    500,
                    machine_id="mac-test",
                    machines_dir=machines,
                    merge_only=True,
                    usage_json_path=usage_path,
                )

        self.assertEqual(result["oneapi"]["request_count"], 2)
        self.assertEqual(result["oneapi"]["pages"], 7)
        self.assertEqual(result["oneapi"]["totals"], prior_oneapi["totals"])
        self.assertEqual(
            result["daily_timeline_rows"][0]["oneapi_tokens"],
            120,
        )
        status = result["source_status"]["oneapi"]
        self.assertEqual(status["status"], "stale")
        self.assertEqual(
            status["last_success_at"],
            "2026-07-29T12:00:00+08:00",
        )
        self.assertEqual(status["window_end"], "2026-07-29")
        self.assertEqual(status["lag_days"], 1)
        self.assertEqual(status["error"], "oneapi_state_unavailable")

    def test_repeated_oneapi_failure_without_data_remains_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machines = root / "machines"
            machines.mkdir()
            usage_path = root / "usage.json"
            (machines / "mac-test.json").write_text(
                json.dumps(
                    {
                        "machine_id": "mac-test",
                        "hostname": "mac-test.local",
                        "timezone": "Asia/Shanghai",
                        "collected_at": "2026-07-30T08:15:00+08:00",
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )
            usage_path.write_text(
                json.dumps(
                    {
                        "timezone": "Asia/Shanghai",
                        "cursor_mutable_from": "2026-07-29",
                        "source_status": {
                            "oneapi": {
                                "status": "failed",
                                "attempted_at": "2026-07-29T12:00:00+08:00",
                                "last_success_at": "",
                                "window_end": "",
                                "lag_days": None,
                                "error": "previous failure",
                            }
                        },
                        "oneapi": {
                            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
                            "daily_timeline": [],
                        },
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    usage_report,
                    "local_today",
                    return_value="2026-07-30",
                ),
                mock.patch.object(
                    usage_report,
                    "fetch_cursor_usage",
                    return_value={
                        "available": False,
                        "complete": False,
                        "error": "missing local Cursor access token",
                    },
                ),
                mock.patch.dict(
                    usage_report.os.environ,
                    {"ONEAPI_STATE_PATH": str(root / "missing-state.json")},
                    clear=False,
                ),
            ):
                result = usage_report.collect_usage(
                    root,
                    "Asia/Shanghai",
                    root / "scratch",
                    500,
                    machine_id="mac-test",
                    machines_dir=machines,
                    merge_only=True,
                    usage_json_path=usage_path,
                )

        status = result["source_status"]["oneapi"]
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["last_success_at"], "")
        self.assertEqual(status["window_end"], "")
        self.assertIsNone(status["lag_days"])
        self.assertEqual(status["error"], "oneapi_state_unavailable")

    def test_collect_usage_redacts_source_errors_but_logs_raw_diagnostics(self):
        sentinel = "SENTINEL_SECRET_TOKEN"
        cursor_path = f"/Users/private/{sentinel}/cursor-state.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machines = root / "machines"
            machines.mkdir()
            usage_path = root / "usage.json"
            state_path = root / sentinel / "oneapi-state.json"
            (machines / "mac-test.json").write_text(
                json.dumps(
                    {
                        "machine_id": "mac-test",
                        "hostname": "mac-test.local",
                        "timezone": "Asia/Shanghai",
                        "collected_at": "2026-07-30T08:15:00+08:00",
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )
            usage_path.write_text(
                json.dumps(
                    {
                        "timezone": "Asia/Shanghai",
                        "cursor_mutable_from": "2026-07-29",
                        "oneapi": {
                            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
                            "daily_timeline": [],
                        },
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )
            stderr = io.StringIO()

            with (
                mock.patch.object(
                    usage_report,
                    "local_today",
                    return_value="2026-07-30",
                ),
                mock.patch.object(
                    usage_report,
                    "fetch_cursor_usage",
                    return_value={
                        "available": False,
                        "complete": False,
                        "error": (
                            f"authentication failed token={sentinel} "
                            f"state={cursor_path}"
                        ),
                    },
                ),
                mock.patch.dict(
                    usage_report.os.environ,
                    {"ONEAPI_STATE_PATH": str(state_path)},
                    clear=False,
                ),
                mock.patch.object(usage_report.sys, "stderr", stderr),
            ):
                result = usage_report.collect_usage(
                    root,
                    "Asia/Shanghai",
                    root / "scratch",
                    500,
                    machine_id="mac-test",
                    machines_dir=machines,
                    merge_only=True,
                    usage_json_path=usage_path,
                )

        serialized_status = json.dumps(result["source_status"])
        self.assertNotIn(sentinel, serialized_status)
        self.assertNotIn(cursor_path, serialized_status)
        self.assertEqual(
            result["source_status"]["cursor"]["error"],
            "cursor_unavailable",
        )
        self.assertEqual(
            result["source_status"]["oneapi"]["error"],
            "oneapi_state_unavailable",
        )
        self.assertIn(sentinel, stderr.getvalue())
        self.assertIn(cursor_path, stderr.getvalue())

    def test_merge_only_derives_fresh_local_status_from_current_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machines = root / "machines"
            machines.mkdir()
            usage_path = root / "usage.json"
            collected_at = "2026-07-30T08:15:00+08:00"
            (machines / "mac-test.json").write_text(
                json.dumps(
                    {
                        "machine_id": "mac-test",
                        "hostname": "mac-test.local",
                        "timezone": "Asia/Shanghai",
                        "collected_at": collected_at,
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )
            usage_path.write_text(
                json.dumps(
                    {
                        "timezone": "Asia/Shanghai",
                        "cursor_mutable_from": "2026-07-29",
                        "oneapi": {
                            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
                            "daily_timeline": [],
                        },
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    usage_report,
                    "local_today",
                    return_value="2026-07-30",
                ),
                mock.patch.object(
                    usage_report,
                    "fetch_cursor_usage",
                    return_value={
                        "available": False,
                        "complete": False,
                        "error": "missing local Cursor access token",
                    },
                ),
                mock.patch.dict(
                    usage_report.os.environ,
                    {"ONEAPI_STATE_PATH": str(root / "missing-state.json")},
                    clear=False,
                ),
            ):
                result = usage_report.collect_usage(
                    root,
                    "Asia/Shanghai",
                    root / "scratch",
                    500,
                    machine_id="mac-test",
                    machines_dir=machines,
                    merge_only=True,
                    usage_json_path=usage_path,
                )

        for source in ("codex", "claude"):
            status = result["source_status"][source]
            self.assertEqual(status["status"], "fresh")
            self.assertEqual(status["attempted_at"], collected_at)
            self.assertEqual(status["last_success_at"], collected_at)
            self.assertEqual(status["window_end"], "2026-07-30")
            self.assertEqual(status["lag_days"], 0)
            self.assertEqual(status["error"], "")

    def test_local_status_uses_oldest_fragment_across_all_machines(self):
        attempts = usage_report.local_fragment_source_attempt(
            [
                {
                    "machine_id": "mac-fresh",
                    "collected_at": "2026-07-30T08:15:00+08:00",
                },
                {
                    "machine_id": "mac-offline",
                    "collected_at": "2026-07-29T03:35:00+08:00",
                },
            ],
            "Asia/Shanghai",
            "2026-07-30",
            attempted=False,
        )

        result = usage_report.reconcile_source_status(
            {},
            {"codex": attempts, "claude": attempts},
            attempted_at="2026-07-30T12:00:00+08:00",
            today="2026-07-30",
        )

        for source in ("codex", "claude"):
            status = result[source]
            self.assertEqual(status["status"], "stale")
            self.assertEqual(
                status["last_success_at"],
                "2026-07-29T03:35:00+08:00",
            )
            self.assertEqual(status["window_end"], "2026-07-29")
            self.assertEqual(status["lag_days"], 1)

    def test_main_writes_source_status_to_usage_json(self):
        source_status = {
            source: {
                "status": "fresh",
                "attempted_at": "2026-07-30T12:00:00+08:00",
                "last_success_at": "2026-07-30T12:00:00+08:00",
                "window_end": "2026-07-30",
                "lag_days": 0,
                "error": "",
            }
            for source in ("codex", "claude", "cursor", "oneapi")
        }
        collected = {
            "generated_at": "2026-07-30T12:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "cursor_mutable_from": "2026-07-29",
            "cursor_reconciliation": {},
            "machine_id": "mac-test",
            "machines": ["mac-test"],
            "tools": [],
            "timeline_meta": {},
            "oneapi": {},
            "daily_timeline_rows": [],
            "source_status": source_status,
            "fragment_meta": {},
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "usage.json"
            with (
                mock.patch.object(
                    usage_report.sys,
                    "argv",
                    [
                        "ai_usage_comparison_image.py",
                        "--json-out",
                        str(output),
                        "--machine-id",
                        "mac-test",
                    ],
                ),
                mock.patch.object(
                    usage_report,
                    "collect_usage",
                    return_value=collected,
                ),
                mock.patch.object(
                    usage_report.machine_fragments,
                    "write_json_atomic",
                ) as write_json,
            ):
                self.assertEqual(usage_report.main(), 0)

        written_payload = write_json.call_args.args[1]
        self.assertEqual(written_payload["source_status"], source_status)

    def test_main_redacts_untrusted_status_and_oneapi_diagnostics(self):
        sentinel = "SENTINEL_PUBLIC_SECRET"
        collected = {
            "generated_at": "2026-07-30T12:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "cursor_mutable_from": "2026-07-29",
            "cursor_reconciliation": {},
            "machine_id": "mac-test",
            "machines": ["mac-test"],
            "tools": [],
            "timeline_meta": {},
            "oneapi": {
                "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
                "totals": {"total_tokens": 120},
                "state_path": f"/Users/private/{sentinel}/state.json",
                "note": f"failed with token={sentinel}",
                "error": f"internal error {sentinel}",
            },
            "daily_timeline_rows": [],
            "source_status": {
                "cursor": {
                    "status": "failed",
                    "attempted_at": "2026-07-30T12:00:00+08:00",
                    "last_success_at": "",
                    "window_end": "",
                    "lag_days": None,
                    "error": f"token={sentinel} /Users/private/state.json",
                },
                "oneapi": {
                    "status": "failed",
                    "attempted_at": "2026-07-30T12:00:00+08:00",
                    "last_success_at": "",
                    "window_end": "",
                    "lag_days": None,
                    "error": f"state=/Users/private/{sentinel}/state.json",
                },
            },
            "fragment_meta": {},
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "usage.json"
            with (
                mock.patch.object(
                    usage_report.sys,
                    "argv",
                    [
                        "ai_usage_comparison_image.py",
                        "--json-out",
                        str(output),
                        "--machine-id",
                        "mac-test",
                    ],
                ),
                mock.patch.object(
                    usage_report,
                    "collect_usage",
                    return_value=collected,
                ),
                mock.patch.object(
                    usage_report.machine_fragments,
                    "write_json_atomic",
                ) as write_json,
            ):
                self.assertEqual(usage_report.main(), 0)

        written_payload = write_json.call_args.args[1]
        serialized = json.dumps(written_payload)
        self.assertNotIn(sentinel, serialized)
        self.assertNotIn("/Users/private", serialized)
        self.assertEqual(
            written_payload["source_status"]["cursor"]["error"],
            "cursor_unavailable",
        )
        self.assertEqual(
            written_payload["source_status"]["oneapi"]["error"],
            "oneapi_unavailable",
        )
        self.assertEqual(
            written_payload["oneapi"],
            {
                "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
                "totals": {"total_tokens": 120},
            },
        )


if __name__ == "__main__":
    unittest.main()
