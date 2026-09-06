from __future__ import annotations

import copy
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
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


usage_report = load_module(
    "usage_pipeline_cache_backfill_test",
    ROOT / "scripts" / "usage_pipeline.py",
)
machine_fragments = usage_report.machine_fragments


class CodexDailyPointTests(unittest.TestCase):
    def point(self, **overrides):
        row = {
            "date": "2026-07-19",
            "totalTokens": 100,
            "costUSD": 1.25,
            "inputTokens": 20,
            "cacheReadTokens": 70,
            "outputTokens": 10,
            "reasoningOutputTokens": 3,
        }
        row.update(overrides)
        return usage_report.codex_daily_points([row])[0]

    def test_uses_cache_read_tokens_from_current_ccusage_schema(self):
        point = self.point(cacheReadTokens=17, cachedInputTokens=99)
        self.assertEqual(point["cache_read"], 17)

    def test_falls_back_to_legacy_cached_input_tokens(self):
        point = self.point(cacheReadTokens=None, cachedInputTokens=99)
        self.assertEqual(point["cache_read"], 99)

    def test_preserves_explicit_zero_without_falsey_fallback(self):
        point = self.point(cacheReadTokens=0, cachedInputTokens=99)
        self.assertEqual(point["cache_read"], 0)


class HistoricalCacheBackfillTests(unittest.TestCase):
    def test_reconstructs_known_frozen_july_16_snapshot(self):
        row = {
            "date": "2026-07-16",
            "codex_tokens": 557_400_354,
            "codex_input": 21_631_092,
            "codex_output": 1_761_710,
            "codex_cache_read": 0,
        }
        updated, _stats = machine_fragments.backfill_codex_cache_daily([row])
        self.assertEqual(updated[0]["codex_cache_read"], 534_007_552)

    def test_derives_cache_without_changing_any_other_field(self):
        original = {
            "date": "2026-07-16",
            "codex_tokens": 100,
            "codex_cost": 1.5,
            "codex_input": 20,
            "codex_cache_read": 0,
            "codex_output": 10,
            "codex_reasoning": 3,
            "claude_tokens": 44,
            "custom": {"sentinel": True},
        }
        before = copy.deepcopy(original)

        rows, stats = machine_fragments.backfill_codex_cache_daily([original])

        self.assertEqual(rows[0]["codex_cache_read"], 70)
        rows_without_cache = copy.deepcopy(rows[0])
        before_without_cache = copy.deepcopy(before)
        rows_without_cache.pop("codex_cache_read")
        before_without_cache.pop("codex_cache_read")
        self.assertEqual(rows_without_cache, before_without_cache)
        self.assertEqual(stats["updated_days"], 1)
        self.assertEqual(stats["cache_tokens_added"], 70)

    def test_preserves_order_and_is_idempotent(self):
        rows = [
            {
                "date": "2026-07-02",
                "codex_tokens": 30,
                "codex_input": 8,
                "codex_output": 5,
                "codex_cache_read": 17,
            },
            {
                "date": "2026-07-01",
                "codex_tokens": 20,
                "codex_input": 5,
                "codex_output": 5,
                "codex_cache_read": 10,
            },
            {"date": "2026-06-30", "claude_tokens": 7},
        ]
        before = copy.deepcopy(rows)

        updated, stats = machine_fragments.backfill_codex_cache_daily(rows)

        self.assertEqual([row["date"] for row in updated], [row["date"] for row in rows])
        self.assertEqual(updated[0], before[0])
        self.assertEqual(updated[1], before[1])
        self.assertEqual(updated[2], before[2])
        self.assertEqual(stats["unchanged_days"], 2)
        self.assertEqual(stats["updated_days"], 0)

    def test_rejects_negative_derived_cache(self):
        row = {
            "date": "2026-07-03",
            "codex_tokens": 10,
            "codex_input": 8,
            "codex_output": 5,
            "codex_cache_read": 0,
        }
        with self.assertRaisesRegex(ValueError, "negative"):
            machine_fragments.backfill_codex_cache_daily([row])

    def test_rejects_active_rows_missing_required_components(self):
        row = {
            "date": "2026-07-03",
            "codex_tokens": 50,
            "codex_input": 10,
            "codex_cache_read": 0,
        }
        with self.assertRaisesRegex(ValueError, "missing"):
            machine_fragments.backfill_codex_cache_daily([row])

    def test_rejects_conflicting_existing_cache(self):
        row = {
            "date": "2026-07-03",
            "codex_tokens": 50,
            "codex_input": 10,
            "codex_output": 5,
            "codex_cache_read": 34,
        }
        with self.assertRaisesRegex(ValueError, "conflicts"):
            machine_fragments.backfill_codex_cache_daily([row])

    def test_rejects_duplicate_dates(self):
        rows = [
            {"date": "2026-07-03", "codex_tokens": 0},
            {"date": "2026-07-03", "codex_tokens": 0},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            machine_fragments.backfill_codex_cache_daily(rows)

    def test_rejects_malformed_numeric_component(self):
        row = {
            "date": "2026-07-03",
            "codex_tokens": 50,
            "codex_input": "corrupt",
            "codex_output": 5,
            "codex_cache_read": 0,
        }
        with self.assertRaisesRegex(ValueError, "invalid integer codex_input"):
            machine_fragments.backfill_codex_cache_daily([row])

    def test_rejects_non_object_daily_row(self):
        with self.assertRaisesRegex(ValueError, "not an object"):
            machine_fragments.backfill_codex_cache_daily(["corrupt"])

    def test_rejects_zero_total_with_nonzero_cache(self):
        row = {
            "date": "2026-07-03",
            "codex_tokens": 0,
            "codex_input": 0,
            "codex_output": 0,
            "codex_cache_read": 9,
        }
        with self.assertRaisesRegex(ValueError, "zero Codex total"):
            machine_fragments.backfill_codex_cache_daily([row])

    def test_machine_writer_changes_only_daily_cache_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            machines_dir = Path(tmp)
            path = machines_dir / "mac-test.json"
            original = {
                "machine_id": "mac-test",
                "collected_at": "2026-07-19T15:03:00+08:00",
                "custom_meta": {"keep": True},
                "daily": [
                    {
                        "date": "2026-07-19",
                        "codex_tokens": 100,
                        "codex_input": 20,
                        "codex_output": 10,
                        "codex_cache_read": 0,
                        "claude_tokens": 5,
                    }
                ],
            }
            path.write_text(usage_report.json.dumps(original), encoding="utf-8")

            written, stats = machine_fragments.backfill_machine_codex_cache(
                machines_dir, "mac-test"
            )

            after = usage_report.json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(after["daily"][0]["codex_cache_read"], 70)
            after["daily"][0]["codex_cache_read"] = 0
            self.assertEqual(after, original)
            self.assertEqual(stats["updated_days"], 1)

    def test_machine_writer_does_not_write_when_validation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            machines_dir = Path(tmp)
            path = machines_dir / "mac-test.json"
            original = {
                "machine_id": "mac-test",
                "daily": [
                    {
                        "date": "2026-07-19",
                        "codex_tokens": 10,
                        "codex_input": 8,
                        "codex_output": 5,
                        "codex_cache_read": 0,
                    }
                ],
            }
            path.write_text(usage_report.json.dumps(original), encoding="utf-8")
            before = path.read_bytes()

            with self.assertRaisesRegex(ValueError, "negative"):
                machine_fragments.backfill_machine_codex_cache(machines_dir, "mac-test")

            self.assertEqual(path.read_bytes(), before)

    def test_merged_usage_sums_machine_cache_and_preserves_other_fields(self):
        usage_rows = [
            {
                "date": "2026-07-19",
                "codex_tokens": 300,
                "codex_cache_read": 0,
                "cursor_tokens": 99,
                "total_tokens": 399,
                "custom": "keep",
            }
        ]
        fragments = [
            {
                "machine_id": "a",
                "daily": [
                    {"date": "2026-07-19", "codex_tokens": 100, "codex_cache_read": 70}
                ],
            },
            {
                "machine_id": "b",
                "daily": [
                    {"date": "2026-07-19", "codex_tokens": 200, "codex_cache_read": 110}
                ],
            },
        ]
        before = copy.deepcopy(usage_rows)

        updated, stats = machine_fragments.backfill_merged_usage_codex_cache_daily(
            usage_rows, fragments
        )

        self.assertEqual(updated[0]["codex_cache_read"], 180)
        updated[0]["codex_cache_read"] = 0
        self.assertEqual(updated, before)
        self.assertEqual(stats["updated_days"], 1)

    def test_merged_usage_rejects_fragment_date_missing_from_usage(self):
        fragments = [
            {
                "machine_id": "a",
                "daily": [
                    {"date": "2026-07-19", "codex_tokens": 100, "codex_cache_read": 70}
                ],
            }
        ]
        with self.assertRaisesRegex(ValueError, "missing"):
            machine_fragments.backfill_merged_usage_codex_cache_daily([], fragments)

    def test_strict_loader_rejects_malformed_sibling_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            machines_dir = Path(tmp)
            (machines_dir / "mac-a.json").write_text(
                usage_report.json.dumps({"machine_id": "mac-a", "daily": []}),
                encoding="utf-8",
            )
            (machines_dir / "mac-b.json").write_text("{not json", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "could not read"):
                machine_fragments.load_machine_fragments_strict(machines_dir)

    def test_strict_loader_rejects_duplicate_sibling_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            machines_dir = Path(tmp)
            row = {"date": "2026-07-19", "codex_tokens": 0}
            (machines_dir / "mac-a.json").write_text(
                usage_report.json.dumps(
                    {"machine_id": "mac-a", "daily": [row, copy.deepcopy(row)]}
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate"):
                machine_fragments.load_machine_fragments_strict(machines_dir)

    def test_json_transaction_restores_first_file_if_second_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            first.write_bytes(b'{"before":1}\n')
            second.write_bytes(b'{"before":2}\n')
            before_first = first.read_bytes()
            before_second = second.read_bytes()
            real_writer = machine_fragments.write_json_atomic

            def fail_second(path, payload):
                if path == second.resolve():
                    raise OSError("forced second write failure")
                real_writer(path, payload)

            with mock.patch.object(
                machine_fragments, "write_json_atomic", side_effect=fail_second
            ):
                with self.assertRaisesRegex(OSError, "forced"):
                    machine_fragments.write_json_transaction(
                        [(first, {"after": 1}), (second, {"after": 2})]
                    )

            self.assertEqual(first.read_bytes(), before_first)
            self.assertEqual(second.read_bytes(), before_second)

    def test_json_transaction_restores_second_file_if_replace_then_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            first.write_bytes(b'{"before":1}\n')
            second.write_bytes(b'{"before":2}\n')
            before_first = first.read_bytes()
            before_second = second.read_bytes()
            real_writer = machine_fragments.write_json_atomic

            def replace_then_fail(path, payload):
                real_writer(path, payload)
                if path == second.resolve():
                    raise OSError("forced failure after second replace")

            with mock.patch.object(
                machine_fragments, "write_json_atomic", side_effect=replace_then_fail
            ):
                with self.assertRaisesRegex(OSError, "after second replace"):
                    machine_fragments.write_json_transaction(
                        [(first, {"after": 1}), (second, {"after": 2})]
                    )

            self.assertEqual(first.read_bytes(), before_first)
            self.assertEqual(second.read_bytes(), before_second)

    def test_json_transaction_recovers_after_process_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            first.write_bytes(b'{"before":1}\n')
            second.write_bytes(b'{"before":2}\n')
            before_first = first.read_bytes()
            before_second = second.read_bytes()
            real_writer = machine_fragments.write_json_atomic
            calls = 0

            def interrupt_second(path, payload):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise KeyboardInterrupt()
                real_writer(path, payload)

            with mock.patch.object(
                machine_fragments, "write_json_atomic", side_effect=interrupt_second
            ):
                with self.assertRaises(KeyboardInterrupt):
                    machine_fragments.write_json_transaction(
                        [(first, {"after": 1}), (second, {"after": 2})]
                    )

            journal = machine_fragments.json_transaction_journal_path([first, second])
            self.assertTrue(journal.is_file())
            self.assertNotEqual(first.read_bytes(), before_first)
            self.assertEqual(second.read_bytes(), before_second)

            recovered = machine_fragments.recover_json_transaction(
                journal, [first.resolve(), second.resolve()]
            )
            self.assertTrue(recovered)
            self.assertEqual(first.read_bytes(), before_first)
            self.assertEqual(second.read_bytes(), before_second)
            self.assertFalse(journal.exists())

    def test_json_transaction_fsyncs_target_directories_before_journal_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            machines = root / "machines"
            machines.mkdir()
            fragment = machines / "fragment.json"
            usage = root / "usage.json"
            fragment.write_text('{"before":1}\n', encoding="utf-8")
            usage.write_text('{"before":2}\n', encoding="utf-8")
            fsync_calls = []

            with mock.patch.object(
                machine_fragments,
                "fsync_directory",
                side_effect=lambda path: fsync_calls.append(Path(path).resolve()),
            ):
                machine_fragments.write_json_transaction(
                    [(fragment, {"after": 1}), (usage, {"after": 2})]
                )

            self.assertIn(machines, fsync_calls)
            self.assertEqual(fsync_calls[-1], root)
            self.assertLess(fsync_calls.index(machines), len(fsync_calls) - 1)

    def test_backfill_dry_run_rejects_pending_transaction_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machines = root / "machines"
            machines.mkdir()
            fragment = machines / "mac-test.json"
            usage = root / "usage.json"
            fragment_payload = {
                "machine_id": "mac-test",
                "daily": [
                    {
                        "date": "2026-07-19",
                        "codex_tokens": 100,
                        "codex_input": 20,
                        "codex_output": 10,
                        "codex_cache_read": 0,
                    }
                ],
            }
            usage_payload = {
                "daily": [
                    {
                        "date": "2026-07-19",
                        "codex_tokens": 100,
                        "codex_cache_read": 0,
                    }
                ]
            }
            machine_fragments.write_json_atomic(fragment, fragment_payload)
            machine_fragments.write_json_atomic(usage, usage_payload)
            before_fragment = fragment.read_bytes()
            before_usage = usage.read_bytes()

            with mock.patch.object(
                machine_fragments,
                "write_json_atomic",
                side_effect=KeyboardInterrupt(),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    machine_fragments.write_json_transaction(
                        [(fragment, fragment_payload), (usage, usage_payload)]
                    )

            journal = machine_fragments.json_transaction_journal_path([fragment, usage])
            self.assertTrue(journal.is_file())
            with self.assertRaisesRegex(ValueError, "pending Codex cache transaction"):
                usage_report.backfill_codex_cache_report(
                    machines_dir=machines,
                    machine_id="mac-test",
                    usage_json_path=usage,
                    dry_run=True,
                )
            self.assertEqual(fragment.read_bytes(), before_fragment)
            self.assertEqual(usage.read_bytes(), before_usage)
            self.assertTrue(journal.is_file())


class CliContractTests(unittest.TestCase):
    def test_main_forwards_backfill_flag(self):
        result = {"fragment": {"updated_days": 1}, "usage": {"updated_days": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            json_out = Path(tmp) / "usage.json"
            argv = [
                "collector",
                "--json-out",
                str(json_out),
                "--machine-id",
                "mac-test",
                "--backfill-codex-cache",
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                usage_report, "backfill_codex_cache_report", return_value=result
            ) as backfill:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(usage_report.main(), 0)
        self.assertEqual(backfill.call_args.kwargs["machine_id"], "mac-test")
        self.assertFalse(backfill.call_args.kwargs["dry_run"])

    def test_publish_help_documents_backfill_flag(self):
        proc = subprocess.run(
            ["bash", "scripts/publish.sh", "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--backfill-codex-cache", proc.stdout)

    def test_publish_rejects_unknown_option_before_collecting(self):
        proc = subprocess.run(
            ["bash", "scripts/publish.sh", "--backfill-codex-cach"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown option", proc.stderr)

    def test_publish_rejects_clean_branch_with_unpushed_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            remote = root / "remote.git"
            scripts = repo / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "publish.sh").write_bytes(
                (ROOT / "scripts" / "publish.sh").read_bytes()
            )
            (scripts / "machine_fragments.py").write_bytes(
                (ROOT / "scripts" / "machine_fragments.py").read_bytes()
            )

            def git(cwd, *args):
                return subprocess.run(
                    ["git", *args],
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                )

            git(repo, "init")
            git(repo, "checkout", "-b", "main")
            git(repo, "add", "scripts/publish.sh", "scripts/machine_fragments.py")
            git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "initial",
            )
            git(root, "init", "--bare", str(remote))
            git(repo, "remote", "add", "origin", str(remote))
            git(repo, "push", "-u", "origin", "main")

            (repo / "unrelated.txt").write_text("local only\n", encoding="utf-8")
            git(repo, "add", "unrelated.txt")
            git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "unrelated local commit",
            )

            env = os.environ.copy()
            env.update(
                {
                    "AI_USAGE_MACHINE_ID": "mac-test",
                    "AI_USAGE_TIMEZONE": "Asia/Shanghai",
                }
            )
            proc = subprocess.run(
                ["bash", "scripts/publish.sh", "--backfill-codex-cache"],
                cwd=repo,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("requires HEAD to exactly match origin/main", proc.stdout)
            self.assertFalse((repo / "public").exists())

    def test_publish_recovers_pending_transaction_before_pull(self):
        for partial_write in (False, True):
            with self.subTest(partial_write=partial_write), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "repo"
                remote = root / "remote.git"
                updater = root / "updater"
                (repo / "scripts").mkdir(parents=True)
                (repo / "public" / "machines").mkdir(parents=True)
                (repo / "scripts" / "publish.sh").write_bytes(
                    (ROOT / "scripts" / "publish.sh").read_bytes()
                )
                for source in (ROOT / "scripts").glob("*.py"):
                    (repo / "scripts" / source.name).write_bytes(source.read_bytes())
                (repo / "scripts" / "model_prices.v1.json").write_bytes(
                    (ROOT / "scripts" / "model_prices.v1.json").read_bytes()
                )
                (repo / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())

                fragment = repo / "public" / "machines" / "mac-test.json"
                usage = repo / "public" / "usage.json"
                fragment_payload = {
                    "machine_id": "mac-test",
                    "remote_marker": "base",
                    "daily": [
                        {
                            "date": "2026-07-19",
                            "codex_tokens": 100,
                            "codex_input": 20,
                            "codex_output": 10,
                            "codex_cache_read": 0,
                        }
                    ],
                }
                usage_payload = {
                    "remote_marker": "base",
                    "daily": [
                        {
                            "date": "2026-07-19",
                            "codex_tokens": 100,
                            "codex_cache_read": 0,
                        }
                    ],
                }
                machine_fragments.write_json_atomic(fragment, fragment_payload)
                machine_fragments.write_json_atomic(usage, usage_payload)

                def git(cwd, *args):
                    return subprocess.run(
                        ["git", *args],
                        cwd=cwd,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                    )

                git(repo, "init")
                git(repo, "checkout", "-b", "main")
                git(repo, "add", "-A")
                git(
                    repo,
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    "initial",
                )
                git(root, "init", "--bare", str(remote))
                git(repo, "remote", "add", "origin", str(remote))
                git(repo, "push", "-u", "origin", "main")

                updated_fragment = copy.deepcopy(fragment_payload)
                updated_fragment["daily"][0]["codex_cache_read"] = 70
                updated_usage = copy.deepcopy(usage_payload)
                updated_usage["daily"][0]["codex_cache_read"] = 70
                real_writer = machine_fragments.write_json_atomic
                write_calls = 0

                def interrupt_transaction(path, payload):
                    nonlocal write_calls
                    write_calls += 1
                    interrupt_at = 2 if partial_write else 1
                    if write_calls == interrupt_at:
                        raise KeyboardInterrupt()
                    real_writer(path, payload)

                with mock.patch.object(
                    machine_fragments,
                    "write_json_atomic",
                    side_effect=interrupt_transaction,
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        machine_fragments.write_json_transaction(
                            [(fragment, updated_fragment), (usage, updated_usage)]
                        )

                git(root, "clone", "--branch", "main", str(remote), str(updater))
                remote_fragment = usage_report.json.loads(
                    (updater / "public" / "machines" / "mac-test.json").read_text(
                        encoding="utf-8"
                    )
                )
                remote_usage = usage_report.json.loads(
                    (updater / "public" / "usage.json").read_text(encoding="utf-8")
                )
                remote_fragment["remote_marker"] = "fresh-remote"
                remote_usage["remote_marker"] = "fresh-remote"
                machine_fragments.write_json_atomic(
                    updater / "public" / "machines" / "mac-test.json",
                    remote_fragment,
                )
                machine_fragments.write_json_atomic(
                    updater / "public" / "usage.json", remote_usage
                )
                git(updater, "add", "-A")
                git(
                    updater,
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    "remote update",
                )
                git(updater, "push", "origin", "main")

                (repo / "node_modules").mkdir()
                bash_env = root / "bash-env.sh"
                bash_env.write_text(
                    "npm() { [[ \"${1:-}\" == run && \"${2:-}\" == build ]]; }\n",
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env.update(
                    {
                        "AI_USAGE_MACHINE_ID": "mac-test",
                        "AI_USAGE_TIMEZONE": "Asia/Shanghai",
                        "BASH_ENV": str(bash_env),
                    }
                )
                proc = subprocess.run(
                    [
                        "bash",
                        "scripts/publish.sh",
                        "--backfill-codex-cache",
                        "--skip-push",
                    ],
                    cwd=repo,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout)

                final_fragment = usage_report.json.loads(fragment.read_text(encoding="utf-8"))
                final_usage = usage_report.json.loads(usage.read_text(encoding="utf-8"))
                self.assertEqual(final_fragment["remote_marker"], "fresh-remote")
                self.assertEqual(final_usage["remote_marker"], "fresh-remote")
                self.assertEqual(final_fragment["daily"][0]["codex_cache_read"], 70)
                self.assertEqual(final_usage["daily"][0]["codex_cache_read"], 70)
                journal = machine_fragments.json_transaction_journal_path([fragment, usage])
                self.assertFalse(journal.exists())


if __name__ == "__main__":
    unittest.main()
