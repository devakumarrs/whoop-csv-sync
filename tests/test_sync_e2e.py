"""End-to-end dry runs: the real `sync` command against a fake WHOOP server on 127.0.0.1.

Each test gets a throwaway settings folder (WHOOP_CSV_SYNC_HOME) and output folder, so your real
sign-in, CSV and schedule are never touched, and nothing is sent to WHOOP."""
import csv
import datetime as dt
import http.server
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from whoop_csv_sync import api, cli, scheduler

ROOT = Path(__file__).resolve().parent.parent


def utc(local_dt):
    return local_dt.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeWhoop(http.server.BaseHTTPRequestHandler):
    """Token endpoint plus paginated sleep/workout collections, configured per test via class attributes."""
    sleeps, workouts, token_status, calls = [], [], 200, []

    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        FakeWhoop.calls.append(("token", form["grant_type"][0], form.get("refresh_token", [""])[0],
                                self.headers["User-Agent"]))
        if FakeWhoop.token_status != 200:
            return self.reply(FakeWhoop.token_status, {"error": "invalid_grant"})
        self.reply(200, {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600})

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        FakeWhoop.calls.append(("get", url.path, self.headers["Authorization"], q.get("nextToken", [""])[0]))
        if self.headers["Authorization"] != "Bearer access-2":
            return self.reply(401, {"error": "bad token"})
        records = FakeWhoop.sleeps if url.path.endswith("/sleep") else FakeWhoop.workouts
        page = int(q.get("nextToken", ["0"])[0])  # one record per page, to exercise pagination
        body = {"records": records[page:page + 1]}
        if page + 1 < len(records):
            body["next_token"] = str(page + 1)
        self.reply(200, body)

    def log_message(self, *args):
        pass


class SyncDryRun(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeWhoop)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = Path(tempfile.mkdtemp())
        self.home, self.out = self.tmp / "home", self.tmp / "out"
        self.home.mkdir()
        (self.home / "config.json").write_text(json.dumps(
            {"client_id": "cid", "client_secret": "secret", "output_dir": str(self.out)}))
        # expired access token, so the run has to refresh first, like the evening job does
        (self.home / "tokens.json").write_text(json.dumps(
            {"access_token": "access-1", "refresh_token": "refresh-1", "expires_at": 0}))

        now = dt.datetime.now().astimezone()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        offset = now.strftime("%z")  # this Mac's offset, so "today" matches the test's today
        FakeWhoop.calls, FakeWhoop.token_status = [], 200
        FakeWhoop.sleeps = [
            {"id": "s1", "start": utc(today - dt.timedelta(hours=1)), "end": utc(today + dt.timedelta(hours=7)),
             "timezone_offset": offset, "nap": False, "score": {"stage_summary": {
                 "total_light_sleep_time_milli": 14_400_000, "total_slow_wave_sleep_time_milli": 5_400_000,
                 "total_rem_sleep_time_milli": 5_400_000}}},
            {"id": "s2", "start": utc(today + dt.timedelta(hours=14)), "end": utc(today + dt.timedelta(hours=15)),
             "timezone_offset": offset, "nap": True},
        ]
        FakeWhoop.workouts = [
            {"id": f"w{i}", "start": utc(today + dt.timedelta(hours=h)),
             "end": utc(today + dt.timedelta(hours=h, minutes=m)), "timezone_offset": offset, "sport_name": s}
            for i, (h, m, s) in enumerate([(8, 30, "walking"), (12, 15, "walking"),
                                           (18, 45, "weightlifting_msk"), (19, 20, "yoga")])
        ]
        self.today = today.date().isoformat()
        self.week_days = 7 + today.weekday() + 1  # last week + Monday..today

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)

    def run_sync(self, *args):
        env = {**os.environ, "WHOOP_CSV_SYNC_HOME": str(self.home),
               "WHOOP_API_URL": f"http://127.0.0.1:{self.server.server_port}",
               "PATH": str(self.tmp)}  # no osascript on PATH, so a failure test can't pop a real banner
        return subprocess.run([sys.executable, "-m", "whoop_csv_sync", "sync", *args],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)

    def test_scheduled_style_run(self):
        r = self.run_sync()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # refreshed once with the saved token, sent a real User-Agent, and saved the rotated token privately
        token_calls = [c for c in FakeWhoop.calls if c[0] == "token"]
        self.assertEqual([c[1:3] for c in token_calls], [("refresh_token", "refresh-1")])
        self.assertTrue(token_calls[0][3].startswith("whoop-csv-sync/"))
        tokens = json.loads((self.home / "tokens.json").read_text())
        self.assertEqual((tokens["access_token"], tokens["refresh_token"]), ("access-2", "refresh-2"))
        self.assertEqual((self.home / "tokens.json").stat().st_mode & 0o777, 0o600)

        # followed every page: 2 sleeps + 4 workouts, one record per page
        self.assertEqual(sum(c[0] == "get" for c in FakeWhoop.calls), 2 + 4)

        with open(self.out / "whoop_daily.csv", newline="") as f:
            rows = {row["Date"]: row for row in csv.DictReader(f)}
        self.assertEqual(len(rows), self.week_days)
        self.assertEqual(max(rows), self.today)
        self.assertEqual(dt.date.fromisoformat(min(rows)).weekday(), 0)  # starts on a Monday
        t = rows[self.today]
        self.assertEqual((t["Bedtime"], t["Wake"], t["Sleep Duration"]), ("23:00", "07:00", "7:00"))
        self.assertEqual((t["Walking"], t["Walking Time"]), ("Yes", "0:45"))
        self.assertEqual((t["Strength Workout"], t["Strength Workout Time"]), ("Yes", "0:45"))
        self.assertEqual((t["Running"], t["Meditation"]), ("No", "No"))
        self.assertIn("Workout types not tracked: yoga", (self.home / "sync.log").read_text())

    def test_second_run_reuses_token_and_drops_old_days(self):
        self.out.mkdir()
        (self.out / "whoop_daily.csv").write_text("Date,Bedtime\n2020-01-01,23:00\n")  # e.g. last week's rows
        self.assertEqual(self.run_sync().returncode, 0)
        r = self.run_sync()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sum(c[0] == "token" for c in FakeWhoop.calls), 1)  # still valid, no second refresh
        with open(self.out / "whoop_daily.csv", newline="") as f:
            dates = [row["Date"] for row in csv.DictReader(f)]
        self.assertEqual(len(dates), self.week_days)
        self.assertNotIn("2020-01-01", dates)

    def test_weeks_option_then_back_to_default(self):
        r = self.run_sync("--weeks", "4")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.out / "whoop_daily.csv", newline="") as f:
            self.assertEqual(len(list(csv.DictReader(f))), self.week_days + 14)
        self.assertEqual(self.run_sync().returncode, 0)  # like the next scheduled run
        with open(self.out / "whoop_daily.csv", newline="") as f:
            self.assertEqual(len(list(csv.DictReader(f))), self.week_days)

    def test_rejects_zero_weeks(self):
        r = self.run_sync("--weeks", "0")
        self.assertEqual(r.returncode, 2)
        self.assertIn("must be 1 or more", r.stderr)

    def test_revoked_sign_in_fails_clearly(self):
        FakeWhoop.token_status = 400
        r = self.run_sync()
        self.assertEqual(r.returncode, 1)
        self.assertIn("Couldn't renew WHOOP sign-in", r.stdout)
        self.assertIn("whoop_csv_sync login", r.stdout)
        self.assertFalse((self.out / "whoop_daily.csv").exists())


class RetryTests(unittest.TestCase):
    def test_retries_server_errors_then_succeeds(self):
        responses = [api.ApiError(503, "busy"), OSError("wifi"), {"records": []}]

        def fake(req):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(api, "http_json", side_effect=fake), mock.patch.object(api.time, "sleep") as sleep:
            self.assertEqual(api.get_json("http://x", "t"), {"records": []})
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5, 10])

    def test_does_not_retry_auth_errors(self):
        with mock.patch.object(api, "http_json", side_effect=api.ApiError(401, "no")) as call, \
                mock.patch.object(api.time, "sleep"):
            with self.assertRaises(api.ApiError):
                api.get_json("http://x", "t")
        self.assertEqual(call.call_count, 1)


class WaitForNetworkTests(unittest.TestCase):
    def test_waits_until_dns_works(self):
        lookups = [OSError("no wifi")] * 5 + [[("ok",)]]

        def fake(*args):
            r = lookups.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(api.socket, "getaddrinfo", side_effect=fake), \
                mock.patch.object(api.time, "sleep") as sleep:
            self.assertTrue(api.wait_for_network())
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [api.NETWORK_FIRST_WAIT, 8, 16, 30, 30, 30])  # doubles, capped

    def test_gives_up_after_timeout(self):
        with mock.patch.object(api.socket, "getaddrinfo", side_effect=OSError("no wifi")), \
                mock.patch.object(api.time, "sleep"):
            self.assertFalse(api.wait_for_network(timeout=0))


class ScheduledRunTests(unittest.TestCase):
    """Only the daily job waits for Wi-Fi; a manual sync starts right away."""
    def run_sync(self, scheduled):
        with mock.patch.object(cli, "wait_for_network") as wait, \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli.auth, "access_token", side_effect=RuntimeError("stop here")):
            with self.assertRaisesRegex(RuntimeError, "stop here"):
                cli._sync(2, scheduled)
        return wait

    def test_scheduled_run_waits_for_network(self):
        self.run_sync(scheduled=True).assert_called_once()

    def test_manual_run_does_not_wait(self):
        self.run_sync(scheduled=False).assert_not_called()

    def test_daily_job_passes_scheduled_flag(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(scheduler, "PLIST", Path(tmp) / "job.plist"), \
                mock.patch.object(scheduler.subprocess, "run"):
            scheduler.schedule()
            args = plistlib.loads(scheduler.PLIST.read_bytes())["ProgramArguments"]
        self.assertEqual(args[-2:], ["sync", "--scheduled"])


if __name__ == "__main__":
    unittest.main()
