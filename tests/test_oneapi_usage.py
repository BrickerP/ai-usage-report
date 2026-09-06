from __future__ import annotations

import importlib.util
import io
import inspect
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
    "usage_pipeline_oneapi_test",
    ROOT / "scripts" / "usage_pipeline.py",
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


def browser_completed(payload: dict, *, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        [],
        returncode,
        stdout=json.dumps(json.dumps(payload)),
        stderr=stderr,
    )


def auth_completed(*, authenticated: bool, error_code: str = ""):
    return browser_completed(
        {
            "_authenticated": authenticated,
            "_error_code": error_code,
        }
    )


def saved_browser_state(*, expiry: float = 2_000_000_000) -> dict:
    return {
        "cookies": [
            {
                "name": "session",
                "value": "test-session-value",
                "domain": "oneapi-comate.baidu-int.com",
                "path": "/",
                "expires": expiry,
                "httpOnly": True,
                "secure": False,
            },
            {
                "name": "SECURE_ZT_GW_TOKEN",
                "value": "test-gateway-value",
                "domain": ".oneapi-comate.baidu-int.com",
                "path": "/",
                "expires": expiry + 3600,
                "httpOnly": True,
                "secure": True,
            },
            {
                "name": "UUAPTGC",
                "value": "test-uuap-value",
                "domain": ".uuap.baidu.com",
                "path": "/",
                "expires": expiry + 7200,
                "httpOnly": True,
                "secure": True,
            },
            {
                "name": "USER_BIND_TOKEN",
                "value": "test-binding-value",
                "domain": ".uuap.baidu.com",
                "expires": expiry + 7200,
            },
            {
                "name": "UUAP_TRACE_TOKEN",
                "value": "test-trace-value",
                "domain": ".baidu-int.com",
                "expires": expiry + 7200,
            },
            {
                "name": "X-MFA-AUTH",
                "value": "test-mfa-value",
                "domain": ".baidu-int.com",
                "expires": expiry + 7200,
            },
            {
                "name": "SECURE_ZT_EXTRA_INFO",
                "value": "test-zt-context-value",
                "domain": ".baidu-int.com",
                "expires": expiry + 7200,
            },
            {
                "name": "ZT_EXTRA_INFO",
                "value": "test-zt-context-value-2",
                "domain": ".baidu.com",
                "expires": expiry + 7200,
            },
            {
                "name": "session",
                "value": "unrelated-session",
                "domain": ".example.com",
                "expires": expiry + 7200,
            },
        ],
        "origins": [
            {
                "origin": "https://oneapi-comate.baidu-int.com",
                "localStorage": [],
            },
            {"origin": "https://example.com", "localStorage": []},
        ],
    }


class OneApiExclusiveAggregationTests(unittest.TestCase):
    def test_splits_codex_and_claude_into_own_series_and_keeps_other_models(self):
        result = oneapi_usage.aggregate_records(
            [
                record("gpt-5.6-sol", prompt=100, quota=1000),
                record("openai/o4-mini", prompt=200, quota=2000),
                record("claude-opus-5", prompt=300, quota=3000),
                record("grok-4.5", prompt=400, quota=4000),
                record("deepseek-v4-flash", cache_read=500, quota=5000),
                record("anthropic.claude-opus-5", prompt=600, quota=6000),
                record("openai.gpt-5.6-sol", prompt=700, quota=7000),
                record("provider.o3", prompt=800, quota=8000),
                record("provider.deepseek-v4", prompt=50, quota=500),
            ],
            timezone="Asia/Shanghai",
            window_start="2026-07-29",
            window_end="2026-07-29",
        )

        self.assertEqual(result["totals"]["total_tokens"], 950)
        self.assertEqual(result["totals"]["requests"], 3)
        self.assertEqual(
            [row["model"] for row in result["daily_timeline"][0]["model_breakdowns"]],
            ["deepseek-v4-flash", "grok-4.5", "provider.deepseek-v4"],
        )
        self.assertEqual(result["excluded"], {})
        self.assertEqual(result["codex"]["totals"]["requests"], 4)
        self.assertEqual(result["codex"]["totals"]["total_tokens"], 1800)
        self.assertEqual(
            result["claude"]["totals"]["requests"],
            2,
        )
        self.assertEqual(
            result["claude"]["totals"]["total_tokens"],
            900,
        )
        self.assertEqual(result["included_request_count"], 9)

    def test_codex_series_folds_gateway_cache_write_into_cache_read(self):
        result = oneapi_usage.aggregate_records(
            [record("gpt-5.6-sol", prompt=10, cache_write=5, quota=1000)],
            timezone="Asia/Shanghai",
            window_start="2026-07-29",
            window_end="2026-07-29",
        )
        point = result["codex"]["daily_timeline"][0]
        self.assertEqual(point["tokens"], 15)
        self.assertEqual(point["input"], 10)
        self.assertEqual(point["cache_read"], 5)
        self.assertEqual(point["output"], 0)
        model = point["model_breakdowns"][0]
        self.assertEqual(model["cache_read_tokens"], 5)
        self.assertEqual(model["cache_write_tokens"], 0)
        self.assertEqual(model["total_tokens"], 15)

    def test_provider_prefixed_owned_models_have_stable_canonical_names(self):
        cases = {
            "anthropic.claude-opus-5": ("claude", "claude-opus-5"),
            "openai/gpt-5.6-sol": ("codex", "gpt-5.6-sol"),
            "gateway:codex-mini": ("codex", "codex-mini"),
            "provider.o3": ("codex", "o3"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    (
                        oneapi_usage.classify_model(raw),
                        oneapi_usage.canonical_model_name(raw),
                    ),
                    expected,
                )

        for residual in ("claudette-v1", "gptx-1", "provider.oasis-v2"):
            with self.subTest(residual=residual):
                self.assertEqual(oneapi_usage.classify_model(residual), "oneapi")

    def test_snapshot_metadata_is_account_scoped_complete_and_content_stable(self):
        records = [
            record("DeepSeek-V4", prompt=10, quota=100),
            record("Grok-4.5", output=20, quota=200),
        ]
        first = oneapi_usage.aggregate_records(
            records,
            timezone="Asia/Shanghai",
            window_start="2026-07-27",
            window_end="2026-07-31",
        )
        reordered = oneapi_usage.aggregate_records(
            list(reversed(records)),
            timezone="Asia/Shanghai",
            window_start="2026-07-27",
            window_end="2026-07-31",
        )

        self.assertEqual(first["snapshot_id"], reordered["snapshot_id"])
        self.assertRegex(first["snapshot_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(first["scope"]["kind"], "account")
        self.assertEqual(first["scope"]["scope_id"], "oneapi:self")
        self.assertEqual(
            first["ownership_rule_version"],
            oneapi_usage.OWNERSHIP_RULE_VERSION,
        )
        self.assertEqual(
            first["window"],
            {
                "start": "2026-07-27",
                "end": "2026-07-31",
                "timezone": "Asia/Shanghai",
                "calendar_days": 5,
                "complete": True,
            },
        )
        captured = first["captured_at"]
        self.assertRegex(captured, r"[+-]\d\d:\d\d$")
        models = first["daily_timeline"][0]["model_breakdowns"]
        self.assertEqual(models[0]["canonical_model"], "grok-4.5")
        self.assertEqual(models[0]["raw_model"], "Grok-4.5")
        self.assertEqual(
            models[0]["ownership_rule_version"],
            oneapi_usage.OWNERSHIP_RULE_VERSION,
        )

        changed = oneapi_usage.aggregate_records(
            [record("DeepSeek-V4", prompt=11, quota=100), records[1]],
            timezone="Asia/Shanghai",
            window_start="2026-07-27",
            window_end="2026-07-31",
        )
        self.assertNotEqual(first["snapshot_id"], changed["snapshot_id"])

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
    def test_default_collection_window_is_five_calendar_days(self):
        default = inspect.signature(oneapi_usage.collect_oneapi).parameters[
            "days"
        ].default
        self.assertEqual(default, 5)
        self.assertEqual(oneapi_usage.DEFAULT_DAYS, 5)

        with self.assertRaisesRegex(ValueError, "at least 1"):
            oneapi_usage.collect_oneapi(state_path="missing.json", days=0)

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
            state_path.write_text(
                json.dumps(saved_browser_state()), encoding="utf-8"
            )
            browser_result = {
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
            completed = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                auth_completed(authenticated=True),
                browser_completed(browser_result),
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
                mock.patch.object(
                    oneapi_usage,
                    "save_session_state_atomic",
                    return_value={
                        "state_refreshed": True,
                        "warning": "",
                    },
                ) as save_state,
            ):
                result = oneapi_usage.collect_oneapi(
                    timezone="Asia/Shanghai",
                    state_path=str(state_path),
                    since="2026-07-29",
                    until="2026-07-29",
                )

        open_command = run.call_args_list[0].args[0]
        auth_command = run.call_args_list[1].args[0]
        eval_command = run.call_args_list[2].args[0]
        close_command = run.call_args_list[3].args[0]
        self.assertIn("--launch", open_command)
        self.assertIn("--state", open_command)
        launch_state = Path(open_command[open_command.index("--state") + 1])
        self.assertNotEqual(launch_state, state_path)
        self.assertFalse(launch_state.exists())
        self.assertIn("--session", open_command)
        self.assertIn("--session", auth_command)
        self.assertIn("--session", eval_command)
        self.assertIn("--session", close_command)
        save_state.assert_called_once()
        self.assertEqual(result["totals"]["total_tokens"], 100)
        self.assertTrue(result["complete"])
        self.assertTrue(result["session_health"]["state_refreshed"])
        self.assertFalse(result["session_health"]["silent_sso_attempted"])

    def test_incomplete_browser_result_is_rejected_instead_of_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(
                json.dumps(saved_browser_state()), encoding="utf-8"
            )
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
                auth_completed(authenticated=True),
                browser_completed(json.loads(browser_result)),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ]

            with (
                mock.patch.object(
                    oneapi_usage.subprocess,
                    "run",
                    side_effect=completed,
                ),
                mock.patch.object(
                    oneapi_usage,
                    "chrome_use_path",
                    return_value="/usr/bin/true",
                ),
                mock.patch.object(
                    oneapi_usage,
                    "save_session_state_atomic",
                    return_value={"state_refreshed": True, "warning": ""},
                ),
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
            state_path.write_text(
                json.dumps(saved_browser_state()), encoding="utf-8"
            )
            first = record("deepseek-v4-flash", prompt=100, quota=1000)
            second = record("grok-4.5", prompt=200, quota=2000)
            completed = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                auth_completed(authenticated=True),
                browser_completed(
                    {
                        "_complete": False,
                        "_rate_limited": True,
                        "_next_page": 7,
                        "_pages": 7,
                        "_records": [first],
                    }
                ),
                browser_completed(
                    {
                        "_complete": True,
                        "_next_page": 8,
                        "_pages": 1,
                        "_records": [first, second],
                    }
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
                mock.patch.object(
                    oneapi_usage,
                    "save_session_state_atomic",
                    return_value={"state_refreshed": True, "warning": ""},
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
        self.assertEqual(result["pagination"]["duplicates_removed"], 1)
        self.assertTrue(result["pagination"]["complete"])
        self.assertIn("const START_PAGE = 0", run.call_args_list[2].kwargs["input"])
        self.assertIn("const START_PAGE = 7", run.call_args_list[3].kwargs["input"])

    def test_initial_auth_failure_attempts_silent_sso_once_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(
                json.dumps(saved_browser_state()), encoding="utf-8"
            )
            completed = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                auth_completed(
                    authenticated=False,
                    error_code="oneapi_reauth_required",
                ),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                auth_completed(authenticated=True),
                browser_completed(
                    {
                        "_complete": True,
                        "_pages": 1,
                        "_records": [record("grok-4.5", prompt=10)],
                    }
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
                mock.patch.object(
                    oneapi_usage,
                    "save_session_state_atomic",
                    return_value={"state_refreshed": True, "warning": ""},
                ) as save_state,
                mock.patch.object(oneapi_usage.time, "sleep") as sleep,
            ):
                result = oneapi_usage.collect_oneapi(
                    timezone="Asia/Shanghai",
                    state_path=str(state_path),
                    since="2026-07-29",
                    until="2026-07-29",
                )

        log_opens = [
            call.args[0]
            for call in run.call_args_list
            if call.args and call.args[0][-2:] == ["open", oneapi_usage.ONEAPI_BASE + "/log"]
        ]
        self.assertEqual(len(log_opens), 1)
        sleep.assert_called_once_with(2)
        self.assertEqual(
            sum(
                1
                for call in run.call_args_list
                if call.args
                and call.args[0][-2:]
                == ["open", oneapi_usage.ONEAPI_BASE + "/api/user/self"]
            ),
            2,
        )
        save_state.assert_called_once()
        self.assertTrue(result["session_health"]["silent_sso_attempted"])

    def test_reauth_required_after_one_silent_sso_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(
                json.dumps(saved_browser_state()), encoding="utf-8"
            )
            completed = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                auth_completed(
                    authenticated=False,
                    error_code="oneapi_reauth_required",
                ),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                auth_completed(
                    authenticated=False,
                    error_code="oneapi_reauth_required",
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
                mock.patch.object(oneapi_usage.time, "sleep"),
            ):
                with self.assertRaises(oneapi_usage.OneApiReauthRequired) as raised:
                    oneapi_usage.collect_oneapi(
                        state_path=str(state_path),
                        since="2026-07-29",
                        until="2026-07-29",
                    )

        self.assertEqual(
            raised.exception.error_code,
            "oneapi_reauth_required",
        )
        self.assertTrue(raised.exception.metadata["silent_sso_attempted"])
        self.assertEqual(
            sum(
                1
                for call in run.call_args_list
                if call.args
                and call.args[0][-2:]
                == ["open", oneapi_usage.ONEAPI_BASE + "/log"]
            ),
            1,
        )

    def test_network_auth_failure_does_not_attempt_silent_sso(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(
                json.dumps(saved_browser_state()), encoding="utf-8"
            )
            completed = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                auth_completed(
                    authenticated=False,
                    error_code="oneapi_network_unavailable",
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
            ):
                with self.assertRaises(oneapi_usage.OneApiNetworkUnavailable):
                    oneapi_usage.collect_oneapi(
                        state_path=str(state_path),
                        since="2026-07-29",
                        until="2026-07-29",
                    )

        self.assertFalse(
            any(
                call.args
                and call.args[0][-2:]
                == ["open", oneapi_usage.ONEAPI_BASE + "/log"]
                for call in run.call_args_list
            )
        )

    def test_daemon_failure_has_distinct_code_without_raw_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(
                json.dumps(saved_browser_state()), encoding="utf-8"
            )
            failure = subprocess.CompletedProcess(
                [],
                1,
                stdout="",
                stderr="Failed to connect to daemon token=must-not-escape",
            )
            with (
                mock.patch.object(
                    oneapi_usage.subprocess,
                    "run",
                    return_value=failure,
                ),
                mock.patch.object(
                    oneapi_usage,
                    "chrome_use_path",
                    return_value="/usr/bin/false",
                ),
            ):
                with self.assertRaises(oneapi_usage.OneApiBrowserUnavailable) as raised:
                    oneapi_usage.collect_oneapi(
                        state_path=str(state_path),
                        since="2026-07-29",
                        until="2026-07-29",
                    )

        self.assertEqual(
            raised.exception.error_code,
            "oneapi_browser_unavailable",
        )
        self.assertNotIn("must-not-escape", str(raised.exception))

    def test_state_save_is_validated_secured_and_atomically_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text('{"old": true}\n', encoding="utf-8")
            now = oneapi_usage.dt.datetime(
                2026,
                7,
                31,
                12,
                tzinfo=oneapi_usage.dt.timezone.utc,
            )
            saved = saved_browser_state(
                expiry=now.timestamp() + 47 * 3600,
            )

            def save_state(command, **_kwargs):
                Path(command[-1]).write_text(
                    json.dumps(saved),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(
                oneapi_usage.subprocess,
                "run",
                side_effect=save_state,
            ):
                health = oneapi_usage.save_session_state_atomic(
                    "/usr/bin/true",
                    "oneapi-test",
                    str(state_path),
                    now=now,
                )

            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            persisted_names = {cookie["name"] for cookie in persisted["cookies"]}
            self.assertIn("session", persisted_names)
            self.assertIn("USER_BIND_TOKEN", persisted_names)
            self.assertIn("X-MFA-AUTH", persisted_names)
            self.assertIn("SECURE_ZT_EXTRA_INFO", persisted_names)
            self.assertIn("UUAP_TRACE_TOKEN", persisted_names)
            self.assertNotIn("unrelated-session", {
                cookie["value"] for cookie in persisted["cookies"]
            })
            self.assertEqual(
                [origin["origin"] for origin in persisted["origins"]],
                [oneapi_usage.ONEAPI_BASE],
            )
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(health["state_refreshed"])
            self.assertEqual(health["warning"], "oneapi_auth_expiring")
            self.assertIn("oneapi_session", health["expiring_components"])

    def test_launch_state_is_scoped_before_isolated_browser_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "state.json"
            source.write_text(
                json.dumps(saved_browser_state()), encoding="utf-8"
            )
            launch_state = oneapi_usage._scoped_launch_state(str(source))
            try:
                scoped = json.loads(launch_state.read_text(encoding="utf-8"))
                self.assertEqual(launch_state.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(
                    "unrelated-session",
                    {cookie["value"] for cookie in scoped["cookies"]},
                )
                self.assertEqual(
                    [origin["origin"] for origin in scoped["origins"]],
                    [oneapi_usage.ONEAPI_BASE],
                )
            finally:
                launch_state.unlink(missing_ok=True)

    def test_invalid_state_save_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            original = '{"old": true}\n'
            state_path.write_text(original, encoding="utf-8")

            def save_invalid(command, **_kwargs):
                Path(command[-1]).write_text(
                    json.dumps({"cookies": [], "origins": []}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(
                oneapi_usage.subprocess,
                "run",
                side_effect=save_invalid,
            ):
                with self.assertRaises(oneapi_usage.OneApiRefreshFailed):
                    oneapi_usage.save_session_state_atomic(
                        "/usr/bin/true",
                        "oneapi-test",
                        str(state_path),
                    )

            self.assertEqual(state_path.read_text(encoding="utf-8"), original)

    def test_reauth_status_requests_one_daily_deduplicated_notification(self):
        error = oneapi_usage.OneApiReauthRequired(
            "One API authentication check failed",
            metadata={"silent_sso_attempted": True},
        )
        status = oneapi_usage.failed_status_metadata(
            error,
            timezone="Asia/Shanghai",
        )

        self.assertEqual(status["status"], "reauth_required")
        self.assertEqual(status["error_code"], "oneapi_reauth_required")
        self.assertTrue(status["notification"]["required"])
        self.assertRegex(
            status["notification"]["dedupe_key"],
            r"^oneapi_reauth_required:\d{4}-\d{2}-\d{2}$",
        )
        self.assertNotIn("cookie", json.dumps(status).lower())
        self.assertNotIn("token", json.dumps(status).lower())

    def test_failed_collection_status_requests_daily_deduplicated_notification(self):
        for error_cls in (
            oneapi_usage.OneApiBrowserUnavailable,
            oneapi_usage.OneApiNetworkUnavailable,
            oneapi_usage.OneApiRefreshFailed,
        ):
            with self.subTest(error_code=error_cls.error_code):
                error = error_cls("collection failed")
                status = oneapi_usage.failed_status_metadata(
                    error,
                    timezone="Asia/Shanghai",
                )
                self.assertEqual(status["status"], "failed")
                self.assertEqual(status["error_code"], error_cls.error_code)
                self.assertTrue(status["notification"]["required"])
                self.assertRegex(
                    status["notification"]["dedupe_key"],
                    rf"^{error_cls.error_code}:\d{{4}}-\d{{2}}-\d{{2}}$",
                )
                self.assertNotIn("cookie", json.dumps(status).lower())
                self.assertNotIn("token", json.dumps(status).lower())


class OneApiPublishFlowTests(unittest.TestCase):
    def test_account_snapshot_is_atomic_five_day_and_collected_after_pull(self):
        source = (ROOT / "scripts" / "publish.sh").read_text(encoding="utf-8")
        main = source.index("# Capture local sources before touching the network")
        recover = source.index("\nrecover_leftover_generated_git_state", main)
        pull = source.index("\npull_latest", main)
        collect = source.index("collect_oneapi_cache", pull)
        merge = source.index("remerge_usage", collect)

        self.assertLess(recover, pull)
        self.assertLess(pull, collect)
        self.assertLess(collect, merge)
        self.assertIn("backup_local_machine_fragment", source)
        self.assertIn("sidecar backup", source)
        self.assertIn("--skip-oneapi-live", source)
        self.assertNotIn("passing the existing One API cache", source)
        self.assertIn("--days 5", source)
        self.assertIn('--status-out "$ONEAPI_STATUS_PATH"', source)
        self.assertIn("mktemp", source)
        self.assertIn('mv -f -- "$temp_path" "$cache_path"', source)
        self.assertNotIn('> "$cache_path"', source)
        self.assertNotIn('rm -f "$cache_path"', source)


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

    def test_complete_cache_must_satisfy_snapshot_contract(self):
        snapshot = oneapi_usage.aggregate_records(
            [record("deepseek-v4", prompt=10, quota=100)],
            timezone="Asia/Shanghai",
            window_start="2026-07-27",
            window_end="2026-07-31",
        )
        snapshot["captured_at"] = "2026-07-31T12:00:00+08:00"
        snapshot["pagination"] = {
            "complete": True,
            "records_after_deduplication": snapshot["request_count"],
        }

        self.assertIs(
            usage_report.validate_oneapi_snapshot(
                snapshot,
                timezone="Asia/Shanghai",
                today="2026-07-31",
                calendar_days=5,
            ),
            snapshot,
        )

        broken = dict(snapshot)
        broken["pagination"] = {"complete": False}
        with self.assertRaisesRegex(ValueError, "pagination"):
            usage_report.validate_oneapi_snapshot(
                broken,
                timezone="Asia/Shanghai",
                today="2026-07-31",
                calendar_days=5,
            )

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

    def test_codex_gateway_series_appends_onto_local_codex(self):
        row = usage_report.empty_daily_row("2026-07-29")
        row.update(
            {
                "codex_tokens": 100,
                "codex_cost": 1.0,
                "codex_input": 50,
                "codex_cache_read": 30,
                "codex_output": 20,
                "codex_models": [{"model": "local-codex", "tokens": 100, "cost": 1.0}],
            }
        )
        codex_data = {
            "daily_timeline": [
                {
                    "date": "2026-07-29",
                    "tokens": 15,
                    "input": 10,
                    "output": 0,
                    "cache_read": 5,
                    "cost_usd": 0.01,
                    "model_breakdowns": [
                        {"model": "gpt-5.6-sol", "total_tokens": 15, "cost_usd": 0.01}
                    ],
                }
            ]
        }

        rows = usage_report.reconcile_codex_rows([row], codex_data)
        merged = rows[0]

        self.assertEqual(merged["codex_tokens"], 115)
        self.assertAlmostEqual(merged["codex_cost"], 1.01)
        self.assertEqual(merged["codex_input"], 60)
        self.assertEqual(merged["codex_cache_read"], 35)
        self.assertEqual(merged["codex_output"], 20)
        self.assertEqual(merged["total_tokens"], 115)
        self.assertEqual(
            {m["model"] for m in merged["codex_models"]},
            {"local-codex", "gpt-5.6-sol"},
        )
        self.assertEqual(merged.get("codex_pricing_version", ""), "")

    def test_codex_gateway_point_on_empty_date_sets_oneapi_provenance(self):
        row = usage_report.empty_daily_row("2026-07-29")
        codex_data = {
            "daily_timeline": [
                {
                    "date": "2026-07-29",
                    "tokens": 15,
                    "input": 10,
                    "output": 0,
                    "cache_read": 5,
                    "cost_usd": 0.01,
                    "model_breakdowns": [
                        {"model": "gpt-5.6-sol", "total_tokens": 15, "cost_usd": 0.01}
                    ],
                }
            ]
        }

        rows = usage_report.reconcile_codex_rows([row], codex_data)
        merged = rows[0]

        self.assertEqual(merged["codex_tokens"], 15)
        self.assertEqual(merged["codex_pricing_provenance"], "oneapi")
        self.assertEqual(merged["codex_pricing_version"], "oneapi")

    def test_codex_payload_merge_keeps_prior_days_outside_fresh_window(self):
        prior = {
            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
            "daily_timeline": [],
            "codex": {
                "daily_timeline": [
                    {
                        "date": "2026-07-27",
                        "tokens": 3,
                        "input": 3,
                        "output": 0,
                        "cache_read": 0,
                        "cost_usd": 0.001,
                    }
                ]
            },
        }
        fetched = {
            "available": True,
            "complete": True,
            "accounting_version": oneapi_usage.ACCOUNTING_VERSION,
            "window": {"start": "2026-07-28", "end": "2026-07-29"},
            "daily_timeline": [],
            "codex": {
                "daily_timeline": [
                    {
                        "date": "2026-07-29",
                        "tokens": 7,
                        "input": 7,
                        "output": 0,
                        "cache_read": 0,
                        "cost_usd": 0.002,
                    }
                ]
            },
        }

        result = usage_report.reconcile_oneapi_payload(
            prior, fetched, {"daily_timeline": [], "total_tokens": 0}
        )
        codex_days = [p["date"] for p in result["codex"]["daily_timeline"]]
        self.assertEqual(codex_days, ["2026-07-27", "2026-07-29"])

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

    def test_model_over_attribution_is_rejected_instead_of_scaled(self):
        with self.assertRaisesRegex(ValueError, "exceed"):
            usage_report.models_with_remainder(
                [
                    {"model": "a", "tokens": 80, "cost": 8},
                    {"model": "b", "tokens": 40, "cost": 4},
                ],
                total_tokens=90,
                total_cost=9,
            )

    def test_positive_model_remainder_is_labeled_legacy_unknown(self):
        models = usage_report.models_with_remainder(
            [{"model": "known", "tokens": 80, "cost": 8}],
            total_tokens=90,
            total_cost=9,
        )

        self.assertEqual(sum(model["tokens"] for model in models), 90)
        self.assertAlmostEqual(sum(model["cost"] for model in models), 9)
        self.assertIn(
            {"model": "Legacy unknown", "tokens": 10, "cost": 1.0},
            models,
        )


class SourceStatusTests(unittest.TestCase):
    def test_public_oneapi_status_preserves_actionable_failure_codes(self):
        for error_code in (
            "oneapi_reauth_required",
            "oneapi_browser_unavailable",
            "oneapi_network_unavailable",
        ):
            with self.subTest(error_code=error_code):
                self.assertEqual(
                    usage_report.public_source_error("oneapi", error_code),
                    error_code,
                )

    def test_merge_does_not_retry_oneapi_after_publish_collection_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machines = root / "machines"
            machines.mkdir()
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
            usage_path = root / "usage.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "timezone": "Asia/Shanghai",
                        "cursor_pricing_version": usage_report.CURSOR_PRICING_VERSION,
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )
            state_path = root / "oneapi-state.json"
            state_path.write_text("{}", encoding="utf-8")
            status_path = root / "oneapi-status.json"
            status_path.write_text(
                json.dumps({"error_code": "oneapi_reauth_required"}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    usage_report,
                    "local_today",
                    return_value="2026-07-31",
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
                mock.patch.object(
                    usage_report.oneapi_usage,
                    "collect_oneapi",
                ) as collect_oneapi,
                mock.patch.dict(
                    usage_report.os.environ,
                    {
                        "ONEAPI_STATE_PATH": str(state_path),
                        "ONEAPI_STATUS_PATH": str(status_path),
                    },
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
                    skip_oneapi_live=True,
                )

        collect_oneapi.assert_not_called()
        self.assertEqual(
            result["source_status"]["oneapi"]["error"],
            "oneapi_reauth_required",
        )

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

        for source in ("codex",):
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

    def test_local_status_ignores_retired_machine_fragments(self):
        attempts = usage_report.local_fragment_source_attempt(
            [
                {
                    "machine_id": "mac-fresh",
                    "collected_at": "2026-07-30T08:15:00+08:00",
                },
                {
                    "machine_id": "mac-retired",
                    "collected_at": "2026-07-01T03:35:00+08:00",
                    "retired": True,
                },
            ],
            "Asia/Shanghai",
            "2026-07-30",
            attempted=False,
        )

        result = usage_report.reconcile_source_status(
            {},
            {"codex": attempts},
            attempted_at="2026-07-30T12:00:00+08:00",
            today="2026-07-30",
        )

        self.assertEqual(result["codex"]["status"], "fresh")
        self.assertEqual(result["codex"]["window_end"], "2026-07-30")
        self.assertEqual(result["codex"]["lag_days"], 0)

    def test_retired_machine_metadata_survives_a_future_fragment_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            machines = Path(tmp)
            fragment = machines / "mac-test.json"
            fragment.write_text(
                json.dumps(
                    {
                        "machine_id": "mac-test",
                        "hostname": "mac-test.local",
                        "retired": True,
                        "retired_at": "2026-07-30T12:00:00+08:00",
                        "retirement_note": "historical",
                        "daily": [],
                    }
                ),
                encoding="utf-8",
            )

            usage_report.machine_fragments.write_machine_fragment_append(
                machines,
                "mac-test",
                "Asia/Shanghai",
                [],
                usage_report.TOOL_TOKEN_FIELDS,
                usage_report.safe_int,
                usage_report.safe_float,
                today="2026-07-30",
                hostname="mac-test.local",
            )

            written = json.loads(fragment.read_text(encoding="utf-8"))
            self.assertTrue(written["retired"])
            self.assertEqual(written["retired_at"], "2026-07-30T12:00:00+08:00")
            self.assertEqual(written["retirement_note"], "historical")

    def test_retired_machine_does_not_block_hostname_reuse(self):
        fragments = [
            {
                "machine_id": "mac-retired",
                "hostname": "same-mac.local",
                "retired": True,
                "daily": [],
            },
            {
                "machine_id": "mac-current",
                "hostname": "same-mac.local",
                "daily": [],
            },
        ]

        usage_report.machine_fragments.validate_unique_fragment_hostnames(fragments)

    def test_incomplete_model_scan_does_not_advance_breakdown_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            machines = Path(tmp)
            fragment = machines / "mac-test.json"
            fragment.write_text(
                json.dumps(
                    {
                        "machine_id": "mac-test",
                        "daily": [],
                        "model_breakdown_version": usage_report.MODEL_BREAKDOWN_VERSION - 1,
                    }
                ),
                encoding="utf-8",
            )

            usage_report.persist_local_model_metadata(
                fragment,
                [],
                {},
                model_seed_complete=False,
            )

            written = json.loads(fragment.read_text(encoding="utf-8"))
            self.assertEqual(
                written["model_breakdown_version"],
                usage_report.MODEL_BREAKDOWN_VERSION - 1,
            )

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
                        "usage_pipeline.py",
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
                        "usage_pipeline.py",
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
