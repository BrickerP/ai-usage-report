from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cursor_usage_api_under_test", ROOT / "scripts" / "cursor_usage_api.py"
)
assert spec is not None and spec.loader is not None
cursor_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cursor_api)

# "header.payload.signature" encrypted the way Cursor's safeStorage does with the
# keychain secret "test-secret".
ENCRYPTED_TOKEN = "cursor-safe-storage:v1:djEwLHByF9nOforlDqsAo03dhlBvXx9FGVKQ3MSD5GiCyig="


def write_state(home: Path, token: str) -> None:
    db_dir = home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage"
    db_dir.mkdir(parents=True)
    with sqlite3.connect(db_dir / "state.vscdb") as conn:
        conn.execute("create table ItemTable (key text unique on conflict replace, value blob)")
        conn.execute(
            "insert into ItemTable values (?, ?)", ("cursorAuth/accessToken", token)
        )


def keychain(returncode: int, secret: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=secret, stderr="")


class SafeStorageTokenTests(unittest.TestCase):
    def test_encrypted_token_is_decrypted_with_the_keychain_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_state(home, ENCRYPTED_TOKEN)
            with mock.patch.object(
                cursor_api.subprocess, "run", return_value=keychain(0, "test-secret\n")
            ) as run:
                state = cursor_api.read_cursor_state(home)

        self.assertEqual(state["access_token"], "header.payload.signature")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/security")
        self.assertIn("Cursor Safe Storage", command)
        self.assertNotIn("token_error", state)

    def test_unreadable_keychain_secret_leaves_no_token_and_says_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_state(home, ENCRYPTED_TOKEN)
            with mock.patch.object(cursor_api.subprocess, "run", return_value=keychain(51)):
                state = cursor_api.read_cursor_state(home)

        self.assertEqual(state["access_token"], "")
        self.assertIn("could not be decrypted", state["token_error"])
        self.assertIn("Cursor Safe Storage", state["token_error"])

    def test_plain_token_is_used_without_reading_the_keychain(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_state(home, "plain.jwt.token")
            with mock.patch.object(cursor_api.subprocess, "run") as run:
                state = cursor_api.read_cursor_state(home)

        self.assertEqual(state["access_token"], "plain.jwt.token")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
