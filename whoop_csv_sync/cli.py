"""Command-line entry point: python3 -m whoop_csv_sync <command>"""
import argparse
import contextlib
import datetime as dt
import getpass
import json
import subprocess
import sys
from pathlib import Path

from whoop_csv_sync import auth, csv_store, daily, scheduler
from whoop_csv_sync.api import ApiError, fetch_all, wait_for_network
from whoop_csv_sync.config import (CONFIG, DEFAULT_OUTPUT_DIR, HOME, WEEKS, load_config, load_json, log,
                               save_private)

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

MACOS_ONLY = ("Built-in scheduling only works on macOS. On Linux or Windows, run `sync` daily from cron or "
              "Task Scheduler instead (see 'Scheduling on Linux or Windows' in the README).")


@contextlib.contextmanager
def run_lock():
    """One run at a time: WHOOP rotates the refresh token on every use, so two overlapping runs
    (say, the at-login run and a manual sync) would both spend the same token and sign you out."""
    HOME.mkdir(mode=0o700, exist_ok=True)
    with open(HOME / "sync.lock", "w") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
        else:  # Windows: LK_LOCK waits up to ~10 seconds for the other run, then raises
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def sync(weeks=WEEKS, scheduled=False):
    with run_lock():
        _sync(weeks, scheduled)


def _sync(weeks, scheduled=False):
    cfg = load_config()
    if scheduled:  # the daily job often starts right after wake, before Wi-Fi is back
        wait_for_network()
    token = auth.access_token(cfg)
    today = dt.date.today()
    first = daily.window_start(today, weeks)
    days = (today - first).days + 1
    try:
        # one extra day so the first day's sleep (which starts the evening before) is included
        sleeps = fetch_all(daily.SLEEP_PATH, token, days + 1)
        workouts = fetch_all(daily.WORKOUT_PATH, token, days + 1)
    except ApiError as e:
        if e.status in (401, 403):
            raise RuntimeError(f"{e} (missing permission? run: python3 -m whoop_csv_sync login)") from None
        raise
    rows = daily.build_rows(sleeps, workouts, first, today)
    csv_store.write(Path(cfg["output_dir"]) / daily.FILENAME, rows, daily.COLUMNS)
    log(f"Wrote {first:%a %d %b} to today ({days} days) to {daily.FILENAME}.")
    others = sorted({daily.sport(w) for w in workouts} - daily.TRACKED_SPORTS)
    if others:
        log("Workout types not tracked: " + ", ".join(others))


def schedule():
    if sys.platform != "darwin":
        raise RuntimeError(MACOS_ONLY)
    sync()  # raises on any failure, so a broken setup never gets scheduled
    scheduler.schedule()


def unschedule():
    if sys.platform != "darwin":
        raise RuntimeError(MACOS_ONLY)
    scheduler.unschedule()


def setup():
    print("WHOOP sync setup\n")
    print("Your WHOOP developer app's Redirect URL must be exactly:\n  " + auth.REDIRECT_URI + "\n")
    cfg = load_json(CONFIG)
    client_id = input("Client ID" + (" [Enter = keep saved]" if cfg.get("client_id") else "") + ": ").strip()
    client_secret = getpass.getpass("Client Secret (hidden as you type)"
                                    + (" [Enter = keep saved]" if cfg.get("client_secret") else "") + ": ").strip()
    default_dir = cfg.get("output_dir", str(DEFAULT_OUTPUT_DIR))
    # absolute, because the scheduled job runs from a different working directory
    output_dir = Path(input(f"Output folder [Enter = {default_dir}]: ").strip() or default_dir).expanduser().resolve()

    if not output_dir.parent.exists():
        print(f"\nWarning: {output_dir.parent} doesn't exist.")
        if input("Continue anyway? (y/n): ").strip().lower() != "y":
            return

    cfg = {"client_id": client_id or cfg.get("client_id"),
           "client_secret": client_secret or cfg.get("client_secret"),
           "output_dir": str(output_dir)}
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise RuntimeError("Client ID and Client Secret are both required.")
    save_private(CONFIG, cfg)
    auth.login(cfg)
    sync()
    print(f"\nDone. Your data is in:\n  {output_dir}\n\n"
          "Next, turn on daily syncing:\n  python3 -m whoop_csv_sync schedule")


def positive_int(s):
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def main():
    p = argparse.ArgumentParser(prog="whoop_csv_sync", description="Sync a daily WHOOP summary to CSV.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="one-time: save app keys, sign in, write the first CSV")
    sub.add_parser("login", help="sign in to WHOOP again (e.g. after new permissions are added)")
    s = sub.add_parser("sync", help=f"rewrite the CSV with the last {WEEKS} weeks (this week so far included)")
    s.add_argument("--weeks", type=positive_int, default=WEEKS,
                   help=f"weeks to include this time, e.g. 4 after a trip (default {WEEKS}). "
                        "The next scheduled run goes back to the default.")
    s.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)  # set by the daily job
    sub.add_parser("schedule", help="run sync automatically every day (macOS launchd)")
    sub.add_parser("unschedule", help="stop the automatic runs")
    args = p.parse_args()

    commands = {
        "setup": setup,
        "login": lambda: auth.login(load_config()),
        "sync": lambda: sync(args.weeks, args.scheduled),
        "schedule": schedule,
        "unschedule": unschedule,
    }
    try:
        commands[args.cmd]()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        log(f"ERROR ({args.cmd}): {e}")
        if args.cmd == "sync":  # what the background job runs, where nobody sees the log
            notify(str(e))
        sys.exit(1)


def notify(message):
    """Show a macOS notification banner. Best effort: never let it hide the original error."""
    if sys.platform != "darwin":
        return
    script = f"display notification {json.dumps(message[:200])} with title \"WHOOP sync failed\""
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
