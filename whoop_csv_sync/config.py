"""Settings and local state. Keys and tokens live in ~/.whoop_csv_sync (readable only by you),
never in the output folder or the repo."""
import datetime as dt
import json
import os
from pathlib import Path

# ---------- settings you might change ----------
WEEKS = 2                       # the CSV holds this many weeks: this week so far plus the full weeks before it.
                                # Every run rebuilds all of them, so missed runs catch up.
WEEK_START = 0                  # the day weeks start on (0 = Monday, 6 = Sunday)
RUN_TIMES = [(20, 0)]           # daily run times (24h, local). Missed runs happen when the Mac wakes.
# Activities to track: CSV column -> WHOOP workout types that count for it (lowercase). Each gets a Yes/No
# column and a "<name> Time" column with that day's total. WHOOP's API reports Strength Trainer sessions as
# "weightlifting_msk". Each sync logs the workout types it didn't count, in case you want to add one.
ACTIVITIES = {
    "Strength Workout": ["weightlifting msk"],
    "Walking": ["walking"],
    "Running": ["running"],
    "Meditation": ["meditation"],
}
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data"  # *.csv is gitignored, so your data never gets committed
# -----------------------------------------------

HOME = Path(os.environ.get("WHOOP_CSV_SYNC_HOME", Path.home() / ".whoop_csv_sync"))
CONFIG = HOME / "config.json"
TOKENS = HOME / "tokens.json"
LOG = HOME / "sync.log"


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    HOME.mkdir(mode=0o700, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save_private(path, data):
    HOME.mkdir(mode=0o700, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    # Create the file owner-only from the start, so secrets are never briefly readable by others
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write(json.dumps(data, indent=2))
    tmp.replace(path)


def load_config():
    cfg = load_json(CONFIG)
    if not cfg:
        raise RuntimeError("Not set up yet. Run: python3 -m whoop_csv_sync setup")
    return cfg
