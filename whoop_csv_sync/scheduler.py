"""Daily background sync via macOS launchd."""
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from whoop_csv_sync.config import HOME, LOG, PROJECT_ROOT, RUN_TIMES

LABEL = "com.whoopcsvsync.daily"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def stable_python():
    """Homebrew's versioned path (python@3.15) disappears on a major upgrade, so prefer the python3 on
    PATH when it is the same interpreter. The code only needs the standard library."""
    link = shutil.which("python3")
    if link and Path(link).resolve() == Path(sys.executable).resolve():
        return link
    return sys.executable


def schedule():
    if any(part in ("Downloads", "Desktop", "Documents") for part in PROJECT_ROOT.parts):
        print("Heads-up: macOS blocks background jobs from Downloads/Desktop/Documents.\n"
              f"Move {PROJECT_ROOT} somewhere like ~/code/ first, then run schedule from there.")
        return
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST, "wb") as f:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": [stable_python(), "-m", "whoop_csv_sync", "sync", "--scheduled"],
            "WorkingDirectory": str(PROJECT_ROOT),
            "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in RUN_TIMES],
            "RunAtLoad": True,
            "StandardOutPath": str(HOME / "launchd.out"),
            "StandardErrorPath": str(HOME / "launchd.err"),
        }, f)
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    subprocess.run(["launchctl", "load", str(PLIST)], check=True)
    times = ", ".join(f"{h}:{m:02d}" for h, m in RUN_TIMES)
    print(f"Scheduled. Syncs daily at {times} (and at login). Log: {LOG}")


def unschedule():
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    PLIST.unlink(missing_ok=True)
    print("Automatic syncing turned off.")
