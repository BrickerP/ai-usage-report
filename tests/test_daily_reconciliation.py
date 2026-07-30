from __future__ import annotations

import copy
import importlib.util
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
    "ai_usage_comparison_image_reconciliation_test",
    ROOT / "scripts" / "ai_usage_comparison_image.py",
)
machine_fragments = usage_report.machine_fragments


def local_row(date: str, *, codex_tokens: int, claude_tokens: int = 0):
    row = usage_report.empty_daily_row(date)
    row.update(
        {
            "codex_tokens": codex_tokens,
            "codex_cost": codex_tokens / 100,
            "codex_input": codex_tokens // 5,
            "codex_cache_read": codex_tokens * 3 // 5,
            "codex_output": codex_tokens // 10,
            "codex_reasoning": codex_tokens // 20,
            "claude_tokens": claude_tokens,
            "claude_cost": claude_tokens / 100,
            "claude_input": claude_tokens // 5,
            "claude_cache_create": claude_tokens // 10,
            "claude_cache_read": claude_tokens // 2,
            "claude_output": claude_tokens // 10,
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
            {},
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
        responses = [{}, {"usageEventsDisplay": [event]}]
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
            {},
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
