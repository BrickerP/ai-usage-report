from __future__ import annotations

import importlib.util
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


usage_report = load_module(
    "usage_pipeline_pricing_retry_test",
    ROOT / "scripts" / "usage_pipeline.py",
)


def breakdown(
    model_name: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation: int = 0,
    cache_read: int = 0,
    cost: float = 0.0,
) -> dict:
    return {
        "modelName": model_name,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheCreationTokens": cache_creation,
        "cacheReadTokens": cache_read,
        "cost": cost,
    }


def daily_payload(*models: dict, date: str = "2026-07-28") -> dict:
    return {
        "daily": [
            {
                "date": date,
                "modelBreakdowns": list(models),
                "totalCost": sum(float(m.get("cost") or 0) for m in models),
                "totalTokens": sum(
                    int(m.get("inputTokens") or 0)
                    + int(m.get("outputTokens") or 0)
                    + int(m.get("cacheCreationTokens") or 0)
                    + int(m.get("cacheReadTokens") or 0)
                    for m in models
                ),
            }
        ],
        "totals": {"totalCost": sum(float(m.get("cost") or 0) for m in models)},
    }


class UnpricedModelDetectionTests(unittest.TestCase):
    def test_detects_zero_cost_models_with_tokens(self):
        usage = daily_payload(
            breakdown("claude-opus-4-8", input_tokens=100, cost=0.5),
            breakdown("claude-opus-5", input_tokens=500, cost=0.0),
        )
        self.assertEqual(usage_report.unpriced_models(usage), ["claude-opus-5"])

    def test_ignores_priced_and_empty_models(self):
        usage = daily_payload(
            breakdown("claude-opus-4-8", input_tokens=100, cost=0.5),
            breakdown("empty-model", input_tokens=0, cost=0.0),
        )
        self.assertEqual(usage_report.unpriced_models(usage), [])

    def test_counts_cache_tokens_as_activity(self):
        usage = daily_payload(breakdown("new-model", cache_read=10, cost=0.0))
        self.assertEqual(usage_report.unpriced_models(usage), ["new-model"])

    def test_online_recovers_when_at_least_one_model_gets_cost(self):
        online = daily_payload(
            breakdown("claude-opus-5", input_tokens=500, cost=1.7),
            breakdown("still-missing", input_tokens=10, cost=0.0),
        )
        self.assertTrue(
            usage_report.online_recovers_unpriced_models(
                ["claude-opus-5", "still-missing"], online
            )
        )


class LiteLLMRepriceTests(unittest.TestCase):
    def test_reprices_unpriced_model_from_litellm_table(self):
        usage = daily_payload(
            breakdown("claude-opus-4-8", input_tokens=80, cost=0.4),
            breakdown(
                "claude-opus-5",
                input_tokens=1_000_000,
                output_tokens=2_000,
                cost=0.0,
            ),
        )
        prices = {
            "claude-opus-5": {
                "input_cost_per_token": 5e-6,
                "output_cost_per_token": 2.5e-5,
                "cache_read_input_token_cost": 5e-7,
                "cache_creation_input_token_cost": 6.25e-6,
            }
        }
        patched, recovered = usage_report.reprice_unpriced_models_with_litellm(
            usage, prices=prices
        )
        self.assertEqual(recovered, ["claude-opus-5"])
        opus5 = patched["daily"][0]["modelBreakdowns"][1]
        self.assertAlmostEqual(opus5["cost"], 5.05, places=6)
        self.assertAlmostEqual(patched["daily"][0]["totalCost"], 5.45, places=6)
        self.assertAlmostEqual(patched["daily"][0]["costUSD"], 5.45, places=6)
        self.assertAlmostEqual(patched["totals"]["costUSD"], 5.45, places=6)
        self.assertEqual(usage_report.unpriced_models(patched), [])

    def test_pinned_codex_models_map_is_component_priced_and_synced(self):
        payload = {
            "daily": [
                {
                    "date": "2026-07-31",
                    "costUSD": 999,
                    "models": {
                        "gpt-5.6-sol": {
                            "inputTokens": 100,
                            "cacheReadTokens": 20,
                            "outputTokens": 10,
                            "reasoningOutputTokens": 4,
                            "totalTokens": 130,
                        }
                    },
                }
            ],
            "totals": {"costUSD": 999},
        }

        result = usage_report.reprice_models_with_pinned_ledger(payload)

        expected = 100 * 5e-6 + 20 * 5e-7 + 10 * 3e-5
        self.assertAlmostEqual(result["daily"][0]["costUSD"], expected)
        self.assertAlmostEqual(result["daily"][0]["totalCost"], expected)
        self.assertAlmostEqual(result["totals"]["costUSD"], expected)
        self.assertAlmostEqual(result["totals"]["totalCost"], expected)
        self.assertTrue(result["daily"][0]["pricingComplete"])


class CcusagePricingRetryTests(unittest.TestCase):
    def test_prefers_online_when_it_recovers_prices(self):
        offline_payload = daily_payload(
            breakdown("claude-opus-4-8", input_tokens=80, cost=0.4),
            breakdown("brand-new-unpinned", input_tokens=500, cost=0.0),
        )
        online_payload = daily_payload(
            breakdown("claude-opus-4-8", input_tokens=80, cost=0.4),
            breakdown("brand-new-unpinned", input_tokens=500, cost=1.7),
        )
        calls: list[bool] = []

        def side_effect(tool, timezone, since="", until="", *, offline):
            self.assertEqual(tool, "claude")
            self.assertEqual(timezone, "Asia/Shanghai")
            self.assertEqual(since, "2026-07-28")
            self.assertEqual(until, "2026-07-28")
            calls.append(offline)
            return offline_payload if offline else online_payload

        with mock.patch.object(
            usage_report, "run_ccusage_daily", side_effect=side_effect
        ):
            result = usage_report.ccusage_daily(
                "claude", "Asia/Shanghai", since="2026-07-28", until="2026-07-28"
            )

        self.assertEqual(calls, [True, False])
        self.assertAlmostEqual(result["daily"][0]["totalCost"], 1.7004)

    def test_keeps_offline_when_online_fails_and_litellm_unavailable(self):
        offline_payload = daily_payload(
            breakdown("brand-new-unpinned", input_tokens=500, cost=0.0),
        )
        calls: list[bool] = []

        def side_effect(tool, timezone, since="", until="", *, offline):
            del tool, timezone, since, until
            calls.append(offline)
            if offline:
                return offline_payload
            raise RuntimeError("network down")

        with mock.patch.object(
            usage_report, "run_ccusage_daily", side_effect=side_effect
        ), mock.patch.object(
            usage_report,
            "reprice_unpriced_models_with_litellm",
            side_effect=RuntimeError("no prices"),
        ):
            result = usage_report.ccusage_daily(
                "claude", "Asia/Shanghai", since="2026-07-28", until="2026-07-28"
            )

        self.assertEqual(calls, [True, False])
        self.assertIsNot(result, offline_payload)
        self.assertEqual(result["pricing_version"], usage_report.PRICING_VERSION)

    def test_litellm_fallback_when_online_still_unpriced(self):
        offline_payload = daily_payload(
            breakdown("new-model", input_tokens=1_000_000, output_tokens=0, cost=0.0),
        )
        online_payload = daily_payload(
            breakdown("new-model", input_tokens=1_000_000, output_tokens=0, cost=0.0),
        )
        prices = {
            "new-model": {
                "input_cost_per_token": 5e-6,
                "output_cost_per_token": 2.5e-5,
            }
        }

        def side_effect(tool, timezone, since="", until="", *, offline):
            del tool, timezone, since, until
            return offline_payload if offline else online_payload

        with mock.patch.object(
            usage_report, "run_ccusage_daily", side_effect=side_effect
        ), mock.patch.object(
            usage_report, "fetch_litellm_prices", return_value=prices
        ):
            result = usage_report.ccusage_daily(
                "claude", "Asia/Shanghai", since="2026-07-28", until="2026-07-28"
            )

        self.assertAlmostEqual(result["daily"][0]["modelBreakdowns"][0]["cost"], 5.0)
        self.assertEqual(usage_report.unpriced_models(result), [])

    def test_skips_online_when_all_models_priced(self):
        offline_payload = daily_payload(
            breakdown("claude-opus-4-8", input_tokens=100, cost=0.5),
        )
        calls: list[bool] = []

        def side_effect(tool, timezone, since="", until="", *, offline):
            del tool, timezone, since, until
            calls.append(offline)
            return offline_payload

        with mock.patch.object(
            usage_report, "run_ccusage_daily", side_effect=side_effect
        ):
            result = usage_report.ccusage_daily(
                "claude", "Asia/Shanghai", since="2026-07-28", until="2026-07-28"
            )

        self.assertEqual(calls, [True])
        self.assertIsNot(result, offline_payload)
        self.assertEqual(result["pricing_version"], usage_report.PRICING_VERSION)


if __name__ == "__main__":
    unittest.main()
