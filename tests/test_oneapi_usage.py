from __future__ import annotations

import importlib.util
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
            ) as run:
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


if __name__ == "__main__":
    unittest.main()
