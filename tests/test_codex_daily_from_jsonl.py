from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


usage_report = load_module(
    "usage_pipeline_codex_daily_jsonl_test",
    ROOT / "scripts" / "usage_pipeline.py",
)


def token_event(ts: str, *, input_tokens: int, cached_input: int, output: int, reasoning: int = 0) -> dict:
    return {
        "type": "event_msg",
        "timestamp": ts,
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input,
                    "output_tokens": output,
                    "reasoning_output_tokens": reasoning,
                }
            },
        },
    }


def turn_context(model: str, ts: str = "2026-08-03T00:00:00.000Z") -> dict:
    return {
        "type": "turn_context",
        "payload": {"model": model, "cwd": "/tmp"},
    }


def session_meta(session_id: str = "s1") -> dict:
    return {
        "type": "session_meta",
        "payload": {"id": session_id, "cwd": "/tmp", "originator": "Codex Desktop"},
    }


def write_session(dirpath: Path, name: str, lines: list[dict]) -> Path:
    path = dirpath / name
    with path.open("w", encoding="utf-8") as handle:
        for obj in lines:
            handle.write(json.dumps(obj) + "\n")
    return path


class CodexDailyFromJsonlTests(unittest.TestCase):
    def test_sums_last_token_usage_and_attributes_to_shanghai_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions = home / ".codex" / "sessions" / "2026" / "08" / "03"
            sessions.mkdir(parents=True)
            # 2026-08-02T17:00:00Z = 2026-08-03 01:00 Shanghai
            write_session(
                sessions,
                "rollout-2026-08-03T01-00-00-abc.jsonl",
                [
                    session_meta("s1"),
                    turn_context("gpt-5.6-sol"),
                    token_event("2026-08-02T17:00:00.000Z", input_tokens=100, cached_input=80, output=10),
                    token_event("2026-08-02T17:00:01.000Z", input_tokens=200, cached_input=180, output=20),
                ],
            )
            payload = usage_report.codex_daily_from_jsonl(home, "Asia/Shanghai")
            self.assertEqual(len(payload["daily"]), 1)
            day = payload["daily"][0]
            self.assertEqual(day["date"], "2026-08-03")
            self.assertEqual(day["totalTokens"], 330)
            self.assertEqual(day["inputTokens"], 40)  # uncached: 100-80 + 200-180
            self.assertEqual(day["cacheReadTokens"], 260)
            self.assertEqual(day["outputTokens"], 30)
            self.assertEqual(payload["totals"]["totalTokens"], 330)

    def test_deduplicates_same_request_across_parent_and_subagent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions = home / ".codex" / "sessions" / "2026" / "08" / "03"
            sessions.mkdir(parents=True)
            evt = token_event(
                "2026-08-02T17:00:00.000Z", input_tokens=100, cached_input=80, output=10
            )
            write_session(
                sessions,
                "rollout-2026-08-03T01-00-00-parent.jsonl",
                [session_meta("p"), turn_context("gpt-5.6-sol"), evt],
            )
            write_session(
                sessions,
                "rollout-2026-08-03T01-00-00-child.jsonl",
                [session_meta("c"), turn_context("gpt-5.6-luna"), evt],
            )
            payload = usage_report.codex_daily_from_jsonl(home, "Asia/Shanghai")
            self.assertEqual(payload["daily"][0]["totalTokens"], 110)
            # model attribution: request was first seen in parent (gpt-5.6-sol)
            models = payload["daily"][0]["models"]
            self.assertEqual(models["gpt-5.6-sol"]["totalTokens"], 110)
            self.assertNotIn("gpt-5.6-luna", models)

    def test_maps_codex_auto_review_to_gpt_5_5_for_pricing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions = home / ".codex" / "sessions" / "2026" / "08" / "03"
            sessions.mkdir(parents=True)
            write_session(
                sessions,
                "rollout-2026-08-03T01-00-00-ar.jsonl",
                [
                    session_meta("ar"),
                    turn_context("codex-auto-review"),
                    token_event("2026-08-02T17:00:00.000Z", input_tokens=100, cached_input=80, output=10),
                ],
            )
            payload = usage_report.codex_daily_from_jsonl(home, "Asia/Shanghai")
            models = payload["daily"][0]["models"]
            self.assertIn("gpt-5.5", models)
            self.assertNotIn("codex-auto-review", models)
            self.assertEqual(models["gpt-5.5"]["totalTokens"], 110)

    def test_respects_since_until_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions = home / ".codex" / "sessions" / "2026" / "08" / "03"
            sessions.mkdir(parents=True)
            write_session(
                sessions,
                "rollout-2026-08-03T01-00-00-win.jsonl",
                [
                    session_meta("w"),
                    turn_context("gpt-5.6-sol"),
                    token_event("2026-08-02T17:00:00.000Z", input_tokens=100, cached_input=80, output=10),  # Aug3 01:00 SH
                    token_event("2026-08-03T16:00:00.000Z", input_tokens=50, cached_input=40, output=5),   # Aug4 00:00 SH
                ],
            )
            payload = usage_report.codex_daily_from_jsonl(
                home, "Asia/Shanghai", since="2026-08-03", until="2026-08-03"
            )
            self.assertEqual(len(payload["daily"]), 1)
            self.assertEqual(payload["daily"][0]["totalTokens"], 110)

    def test_keeps_files_created_before_since_that_span_into_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            # Session created Aug 2 but with events on Aug 3 Shanghai
            old = home / ".codex" / "sessions" / "2026" / "08" / "02"
            old.mkdir(parents=True)
            write_session(
                old,
                "rollout-2026-08-02T00-00-00-span.jsonl",
                [
                    session_meta("s"),
                    turn_context("gpt-5.6-sol"),
                    # Aug 3 02:00 Shanghai
                    token_event("2026-08-02T18:00:00.000Z", input_tokens=100, cached_input=80, output=10),
                ],
            )
            payload = usage_report.codex_daily_from_jsonl(
                home, "Asia/Shanghai", since="2026-08-03", until="2026-08-03"
            )
            self.assertEqual(len(payload["daily"]), 1)
            self.assertEqual(payload["daily"][0]["totalTokens"], 110)

    def test_skips_files_created_after_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            late = home / ".codex" / "sessions" / "2026" / "08" / "04"
            late.mkdir(parents=True)
            write_session(
                late,
                "rollout-2026-08-04T00-00-00-late.jsonl",
                [
                    session_meta("l"),
                    turn_context("gpt-5.6-sol"),
                    token_event("2026-08-03T18:00:00.000Z", input_tokens=100, cached_input=80, output=10),
                ],
            )
            payload = usage_report.codex_daily_from_jsonl(
                home, "Asia/Shanghai", since="2026-08-03", until="2026-08-03"
            )
            self.assertEqual(len(payload["daily"]), 0)


if __name__ == "__main__":
    unittest.main()
