from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


key2 = load_module("opencode_key2_usage_under_test", ROOT / "scripts" / "opencode_key2_usage.py")
machine_fragments = load_module(
    "machine_fragments_key2_test", ROOT / "scripts" / "machine_fragments.py"
)

BASE = int(dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp() * 1000)


class OpenCodeHome:
    """A temporary home with an OpenCode database and a key-split ledger."""

    def __init__(self, tmp: str) -> None:
        self.home = Path(tmp)
        db_path = self.home / ".local" / "share" / "opencode" / "opencode.db"
        db_path.parent.mkdir(parents=True)
        self.ledger = self.home / ".local" / "state" / "oneapi-key-split"
        self.ledger.mkdir(parents=True)
        self.db = sqlite3.connect(db_path)
        self.db.execute(
            "create table session_message (id text primary key, session_id text, type text, time_created integer, data text)"
        )
        self.count = 0

    def call(
        self,
        session: str,
        created: int,
        *,
        streamed: int = 0,
        kind: str = "assistant",
        model: str = "Opus 5.5",
        input: int = 100,
        output: int = 10,
        reasoning: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> None:
        self.count += 1
        data = {
            "time": {"created": created} if kind == "compaction" else {
                "created": created,
                "streamed": streamed or created + 5_000,
                "completed": (streamed or created + 5_000) + 100,
            },
            "model": {"id": model, "providerID": "oneapi-claude"},
            "tokens": {
                "input": input,
                "output": output,
                "reasoning": reasoning,
                "cache": {"read": cache_read, "write": cache_write},
            },
        }
        self.db.execute(
            "insert into session_message values (?, ?, ?, ?, ?)",
            (f"msg_{self.count}", session, kind, created, json.dumps(data)),
        )
        self.db.commit()

    def line(self, session: str, t: int, key: int, *, kind: str = "primary", status: int = 200, backfill: bool = False) -> None:
        name = "backfill-2026-07.jsonl" if backfill else "requests-2026-07.jsonl"
        with (self.ledger / name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": t, "s": session, "k": kind, "key": key, "st": status}) + "\n")

    def collect(self) -> dict:
        return key2.collect_key2_daily(
            home=self.home,
            timezone="Asia/Shanghai",
            since="2026-07-27",
            until="2026-07-31",
            aliases={"Opus 5.5": "claude-opus-5-5"},
        )


def day_models(result: dict) -> dict:
    return {
        (point["date"], entry["model"]): entry
        for point in result["daily"]
        for entry in point["models"]
    }


class LedgerJoinTests(unittest.TestCase):
    def test_a_call_takes_its_own_line_and_not_the_next_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = OpenCodeHome(tmp)
            env.call("ses_a", BASE, streamed=BASE + 4_000)  # completes at +4.1 s
            env.call("ses_a", BASE + 4_200, streamed=BASE + 9_000, input=7)
            env.line("ses_a", BASE + 3_000, 1)
            env.line("ses_a", BASE + 4_300, 2)  # the next call's fast response

            result = env.collect()

        self.assertEqual(result["stats"]["key1_calls"], 1)
        self.assertEqual(result["stats"]["key2_calls"], 1)
        entry = day_models(result)[("2026-07-29", "claude-opus-5-5")]
        self.assertEqual((entry["requests"], entry["input"]), (1, 7))

    def test_a_retried_call_belongs_to_the_last_successful_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = OpenCodeHome(tmp)
            env.call("ses_a", BASE, streamed=BASE + 20_000)
            env.line("ses_a", BASE + 2_000, 2, status=403)
            env.line("ses_a", BASE + 3_000, 2)
            env.line("ses_a", BASE + 9_000, 1)

            result = env.collect()

        self.assertEqual(result["stats"]["key1_calls"], 1)
        self.assertEqual(result["daily"], [])

    def test_compaction_uses_the_first_compaction_line_after_it_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = OpenCodeHome(tmp)
            env.call("ses_a", BASE, kind="compaction", model="Claude Sonnet 5.5", input=900, output=40)
            env.line("ses_a", BASE + 1_000, 1)  # a primary line is not the compaction's
            env.line("ses_a", BASE + 180_000, 2, kind="compaction")

            result = env.collect()

        entry = day_models(result)[("2026-07-29", "Claude Sonnet 5.5")]
        self.assertEqual(entry["tokens"], 940)
        self.assertEqual(result["stats"]["unmapped_models"], ["Claude Sonnet 5.5"])

    def test_a_forked_copy_is_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = OpenCodeHome(tmp)
            env.call("ses_parent", BASE)
            env.call("ses_fork", BASE)  # same times and tokens, copied at fork time
            env.line("ses_parent", BASE + 2_000, 2)

            result = env.collect()

        self.assertEqual(result["stats"]["fork_copies"], 1)
        self.assertEqual(result["stats"]["key2_calls"], 1)
        self.assertEqual(day_models(result)[("2026-07-29", "claude-opus-5-5")]["requests"], 1)

    def test_backfill_lines_attribute_calls_from_before_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = OpenCodeHome(tmp)
            env.call("ses_a", BASE, output=10, reasoning=5)
            env.call("ses_a", BASE + 60_000)  # matched a key-1 log record: no line
            env.call("ses_b", BASE + 3_600_000)
            env.line("ses_a", BASE + 1, 2, backfill=True)
            env.line("ses_b", BASE + 3_602_000, 2)

            result = env.collect()

        stats = result["stats"]
        self.assertEqual((stats["key2_calls"], stats["unattributed_calls"]), (2, 1))
        self.assertEqual(stats["unattributed_after_ledger"], 0)
        self.assertEqual((stats["ledger_lines"], stats["backfill_lines"]), (1, 1))
        entry = day_models(result)[("2026-07-29", "claude-opus-5-5")]
        # The gateway counts reasoning inside completion tokens.
        self.assertEqual((entry["requests"], entry["output"], entry["tokens"]), (2, 25, 225))

    def test_calls_without_a_line_after_the_ledger_started_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = OpenCodeHome(tmp)
            env.call("ses_a", BASE)
            env.call("ses_a", BASE + 60_000)
            env.line("ses_a", BASE + 2_000, 2)

            result = env.collect()

        self.assertEqual(result["stats"]["unattributed_after_ledger"], 1)

    def test_a_mac_without_opencode_has_no_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                key2.collect_key2_daily(
                    home=Path(tmp), timezone="Asia/Shanghai", since="2026-07-27", until="2026-07-31"
                )
            )


class ModelAndSectionTests(unittest.TestCase):
    def test_gateway_model_names(self):
        aliases = key2.load_aliases()
        self.assertEqual(key2.gateway_model("Opus 5.5", aliases), ("claude-opus-5-5", True))
        self.assertEqual(key2.gateway_model("DeepSeek-V4.1-Flash", aliases), ("deepseek-v4.1-flash-1017", True))
        self.assertEqual(key2.gateway_model("gpt-6.2-sol", aliases), ("gpt-6.2-sol", True))
        self.assertEqual(key2.gateway_model("Gemini 4 Carbon", aliases), ("Gemini 4 Carbon", False))

    def test_merge_replaces_reread_days_but_never_lowers_one(self):
        existing = {
            "version": 1,
            "daily": [
                {"date": "2026-06-01", "tokens": 5, "models": []},
                {"date": "2026-07-28", "tokens": 50, "models": []},
                {"date": "2026-07-29", "tokens": 10, "models": []},
            ],
        }
        fresh = {
            "daily": [
                {"date": "2026-07-28", "tokens": 40, "models": []},
                {"date": "2026-07-29", "tokens": 20, "models": []},
                {"date": "2026-07-30", "tokens": 1, "models": []},
            ],
            "stats": {"calls": 3},
        }

        merged = key2.merge_section(existing, fresh, since="2026-07-27", collected_at="now")

        self.assertEqual(
            [(point["date"], point["tokens"]) for point in merged["daily"]],
            [("2026-06-01", 5), ("2026-07-28", 50), ("2026-07-29", 20), ("2026-07-30", 1)],
        )
        self.assertEqual(merged["stats"], {"calls": 3, "kept_higher_dates": ["2026-07-28"]})

    def test_section_daily_rejects_components_that_disagree(self):
        section = {
            "version": 1,
            "daily": [{"date": "2026-07-29", "models": [{"model": "m", "input": 1, "output": 1, "tokens": 3}]}],
        }
        with self.assertRaisesRegex(ValueError, "disagree"):
            key2.section_daily(section)
        self.assertEqual(key2.section_daily({"version": 0, "daily": []}), [])

    def test_fragment_writes_keep_the_key2_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            machines = Path(tmp)
            section = {"version": 1, "daily": [{"date": "2026-07-29", "tokens": 1, "models": []}]}
            (machines / "mac-test.json").write_text(
                json.dumps({"machine_id": "mac-test", "timezone": "Asia/Shanghai", "daily": [], "oneapi_key2": section}),
                encoding="utf-8",
            )

            machine_fragments.write_machine_fragment_append(
                machines,
                "mac-test",
                "Asia/Shanghai",
                [],
                {"codex": ["input", "cache_read", "output", "reasoning"]},
                lambda value: int(value or 0),
                lambda value: float(value or 0),
                today="2026-07-31",
                hostname="mac-test.local",
            )

            stored = json.loads((machines / "mac-test.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["oneapi_key2"], section)


if __name__ == "__main__":
    unittest.main()
