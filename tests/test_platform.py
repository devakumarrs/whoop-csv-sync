"""Linux/Windows behavior, simulated on whatever machine runs the tests."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from whoop_csv_sync import cli

ROOT = Path(__file__).resolve().parent.parent

# Runs the CLI in a Python where `fcntl` can't be imported (like Windows), with a stand-in msvcrt.
NO_FCNTL = """
import subprocess, sys, types  # subprocess first: it detects Windows by trying to import msvcrt
sys.modules["fcntl"] = None
fake = types.ModuleType("msvcrt"); fake.LK_LOCK, fake.LK_UNLCK = 1, 0
fake.locking = lambda fd, mode, n: print("msvcrt.locking", mode)
sys.modules["msvcrt"] = fake
sys.argv = ["whoop_csv_sync", "sync"]
from whoop_csv_sync.cli import main
main()
"""


class PlatformTests(unittest.TestCase):
    def test_runs_without_fcntl(self):
        with tempfile.TemporaryDirectory() as home:
            r = subprocess.run([sys.executable, "-c", NO_FCNTL], cwd=ROOT, capture_output=True, text=True,
                               env={"WHOOP_CSV_SYNC_HOME": home, "PATH": home})
        # imports fine, locks and unlocks with msvcrt, then fails only because nothing is set up
        self.assertEqual(r.stdout.count("msvcrt.locking"), 2, r.stdout + r.stderr)
        self.assertIn("msvcrt.locking 1", r.stdout)
        self.assertIn("msvcrt.locking 0", r.stdout)
        self.assertIn("Not set up yet", r.stdout)
        self.assertEqual(r.returncode, 1)

    def test_schedule_refuses_off_macos_before_syncing(self):
        with mock.patch.object(cli.sys, "platform", "linux"), mock.patch.object(cli, "sync") as sync:
            for command in (cli.schedule, cli.unschedule):
                with self.assertRaisesRegex(RuntimeError, "only works on macOS"):
                    command()
        sync.assert_not_called()

    def test_no_notification_off_macos(self):
        with mock.patch.object(cli.sys, "platform", "win32"), mock.patch.object(cli.subprocess, "run") as run:
            cli.notify("boom")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
