# whoop-csv-sync

Writes one row per day of WHOOP data for last week and this week so far to a CSV file and keeps it up to date every evening. Each row has your bedtime, wake time, sleep duration, and whether you did a strength workout, walk, run, or meditation that day, plus how long each took. You can open the CSV in Excel, Google Sheets, Numbers, or pandas.

- Uses only Python's standard library, so there's nothing to `pip install`.
- Writes to `data/whoop_daily.csv` in this repo by default. `*.csv` is gitignored, so your data never gets committed.
- Keeps credentials in `~/.whoop_csv_sync/` (mode `700`), never in the output folder or this repo.
- Works on macOS, Linux, and Windows. Daily scheduling is built in on macOS. On Linux or Windows, run `sync` from cron or Task Scheduler ([see below](#scheduling-on-linux-or-windows)).

> Not affiliated with or endorsed by WHOOP. Uses the official [WHOOP Developer API](https://developer.whoop.com/) (v2).

## Setup

1. Create an app at the [WHOOP Developer Dashboard](https://developer-dashboard.whoop.com/):
   - **Redirect URL:** `http://localhost:8765/callback`
   - **Scopes:** `read:sleep`, `read:workout`. (`offline` isn't listed there; the tool requests it at sign-in to get a refresh token.)
2. Clone and run setup:
   ```sh
   git clone https://github.com/devakumarrs/whoop-csv-sync.git
   cd whoop-csv-sync
   python3 -m whoop_csv_sync setup
   ```
   Setup asks for your Client ID and Client Secret, plus an output folder (press Enter for `data/`). It then opens WHOOP sign-in in your browser and writes the first CSV. Keys are saved, so this is a one-time step.
3. Turn on daily syncing (macOS):
   ```sh
   python3 -m whoop_csv_sync schedule
   ```
   This runs one sync first, and installs the daily job only if that sync works. On Linux or Windows, see [Scheduling on Linux or Windows](#scheduling-on-linux-or-windows) instead.

On Windows, type `python` (or `py`) wherever this README says `python3`. To get a `whoop-csv-sync` command instead, run `pip install -e .`.

## Commands

| Command | What it does |
|---|---|
| `setup` | Save app keys and output folder, sign in, write the first CSV |
| `login` | Sign in again (for example if WHOOP revoked access) |
| `sync [--weeks N]` | Rewrite the CSV with last week and this week so far. `--weeks 4` goes back further once, for example after a trip; the next scheduled run goes back to 2. |
| `schedule` | macOS: run a sync, and if it works, turn on the daily job (20:00, plus at login) |
| `unschedule` | macOS: turn the daily job off |

Each run fetches last week and this week from WHOOP and rewrites the whole CSV, so a sleep that gets scored late, or a workout logged after the evening run, is filled in on the next run. If the Mac is off for a few days, including over a weekend, the next run catches up. If it was off longer, run `sync --weeks N` once to cover the gap. If a run fails, the previous CSV is left as it was. If the Mac is asleep at 20:00, the run happens when it wakes. If it's shut down, the run happens at your next login. On macOS, if a scheduled sync fails, you get a notification titled "WHOOP sync failed" that says what to do. Requests to WHOOP are retried a few times before a run counts as failed.

To change the run time, the number of weeks kept (`WEEKS`), the day your week starts (`WEEK_START`), or tracked activities, edit [whoop_csv_sync/config.py](whoop_csv_sync/config.py), then run `schedule` again.

## Output

**`whoop_daily.csv`** has one row per day from last Monday to today (8 to 14 rows), with no gaps. Monday's row has Sunday night's bedtime and Monday morning's wake time:

| Column | Meaning |
|---|---|
| `Date` | `YYYY-MM-DD` |
| `Bedtime`, `Wake` | Local times (`HH:MM`) of that morning's main sleep. A sleep belongs to the day you woke up, and naps are ignored. Bedtimes after midnight show as early-morning times, such as `01:30`. |
| `Sleep Duration` | Time actually asleep (`H:MM`, excluding time awake in bed). It stays blank until WHOOP scores the sleep. |
| `Strength Workout`, `Walking`, `Running`, `Meditation` | `Yes` if you logged that activity that day (by local start date), otherwise `No` |
| `… Time` (one per activity) | Total time across that day's sessions (`H:MM`), blank if none |

Days with no data have blank sleep fields. To track a different activity, or to count more WHOOP workout types toward one, edit `ACTIVITIES` in `config.py`. The next sync applies it to the whole week. Each sync logs the workout types it didn't count. Strength Trainer sessions appear in WHOOP's API as `weightlifting_msk`.

## Checking on it

```sh
launchctl list | grep whoopcsvsync   # macOS: on? the middle number is the last exit code (0 = OK)
tail ~/.whoop_csv_sync/sync.log      # one line per run
```

## Scheduling on Linux or Windows

`schedule` only works on macOS. On other systems, run `sync` once a day yourself. Run `setup` first, and check that `sync` works by hand.

**Linux (cron):** run `crontab -e` and add the line below, replacing the path with where you cloned the repo. Cron skips runs while the computer is off. That's usually fine, because every run rebuilds both weeks, but you can use `anacron` or a systemd timer with `Persistent=true` if you want missed runs to happen at the next boot.

```
0 21 * * * cd /path/to/whoop-csv-sync && python3 -m whoop_csv_sync sync
```

**Windows (Task Scheduler):** in Command Prompt, run:

```
schtasks /Create /SC DAILY /ST 20:00 /TN "WHOOP CSV sync" /TR "cmd /c cd /d C:\path\to\whoop-csv-sync && python -m whoop_csv_sync sync"
```

To also run after a missed start (for example if the PC was off at 20:00), open Task Scheduler, open the task's **Settings** tab, and tick **Run task as soon as possible after a scheduled start is missed**.

Failure notifications are macOS-only. On other systems, check `sync.log`.

## Troubleshooting

- **Logs:** `~/.whoop_csv_sync/sync.log`. Errors that happen before logging starts go to `launchd.err` in the same folder.
- **"Couldn't renew WHOOP sign-in":** WHOOP no longer accepts the saved sign-in. Run `python3 -m whoop_csv_sync login`.
- **HTTP 401/403 on workouts:** your sign-in predates the `read:workout` scope. Add the scope to your app, then run `login`.
- **HTTP 403 "error code: 1010":** Cloudflare rejected the request. The tool sends its own User-Agent to avoid this, so if you see it, open an issue.
- **macOS asks to let Python find devices on your local network:** you can say no. Sign-in only uses `localhost`.
- **No failure notifications (macOS):** allow notifications for **Script Editor** in System Settings → Notifications.
- **Windows: "PermissionError" writing the CSV:** the CSV is open directly in Excel, and Windows won't let the file be replaced while it's open. Close it. Reading it through Power Query (Data → Get Data) doesn't keep it open.

## Development

```sh
python3 -m unittest discover tests
```

`tests/test_sync_e2e.py` runs the real `sync` command against a fake WHOOP server on `127.0.0.1`, with a temporary settings folder and output folder. It never contacts WHOOP or touches your sign-in, CSV, or schedule.
