"""Daily summary: one row per day with your main sleep and which tracked activities you did."""
import datetime as dt
from collections import defaultdict

from whoop_csv_sync.config import ACTIVITIES, WEEK_START, WEEKS
from whoop_csv_sync.formatting import hm, local_time

SLEEP_PATH = "/developer/v2/activity/sleep"
WORKOUT_PATH = "/developer/v2/activity/workout"
FILENAME = "whoop_daily.csv"
COLUMNS = ["Date", "Bedtime", "Wake", "Sleep Duration", *(c for a in ACTIVITIES for c in (a, f"{a} Time"))]
TRACKED_SPORTS = {s for sports in ACTIVITIES.values() for s in sports}


def sport(r):
    """'strength-trainer' / 'Strength_Trainer' -> 'strength trainer'."""
    return (r.get("sport_name") or "").replace("-", " ").replace("_", " ").strip().lower()


def asleep_ms(r):
    """Time actually asleep (light + deep + REM); None until WHOOP has scored the sleep."""
    st = (r.get("score") or {}).get("stage_summary") or {}
    stages = [st.get(k) for k in ("total_light_sleep_time_milli", "total_slow_wave_sleep_time_milli",
                                  "total_rem_sleep_time_milli")]
    return sum(stages) if all(s is not None for s in stages) else None


def week_start(today):
    """The first day of the week containing `today` (a Monday, unless WEEK_START says otherwise)."""
    return today - dt.timedelta(days=(today.weekday() - WEEK_START) % 7)


def window_start(today, weeks=WEEKS):
    """First day the CSV covers: the start of the week `weeks - 1` weeks before this one. Keeping last week means
    that if the Mac is off on Monday, last week still gets its final update on the next run."""
    return week_start(today) - dt.timedelta(weeks=weeks - 1)


def build_rows(sleeps, workouts, first_day, last_day):
    """One row for every day from first_day to last_day. A sleep belongs to the day you woke up;
    a workout belongs to the day it started. Naps are ignored."""
    main = {}  # wake date -> (start, end, record), keeping the longest sleep if there are several
    for r in sleeps:
        if r.get("nap") or not r.get("end"):  # no end yet = still asleep; the next run picks it up
            continue
        start, end = local_time(r["start"], r.get("timezone_offset")), local_time(r["end"], r.get("timezone_offset"))
        day = end.date()
        if day not in main or end - start > main[day][1] - main[day][0]:
            main[day] = (start, end, r)
    activity_ms = defaultdict(int)  # (start date, activity) -> total milliseconds
    for w in workouts:
        if not w.get("end"):
            continue
        start, end = local_time(w["start"], w.get("timezone_offset")), local_time(w["end"], w.get("timezone_offset"))
        for activity, sports in ACTIVITIES.items():
            if sport(w) in sports:
                activity_ms[start.date(), activity] += (end - start).total_seconds() * 1000

    rows, day = [], first_day
    while day <= last_day:
        start, end, r = main.get(day, (None, None, None))
        row = {
            "Date": day.isoformat(),
            "Bedtime": start.strftime("%H:%M") if start else "",
            "Wake": end.strftime("%H:%M") if end else "",
            "Sleep Duration": hm(asleep_ms(r)) if r else "",
        }
        for activity in ACTIVITIES:
            ms = activity_ms.get((day, activity))
            row[activity] = "Yes" if ms is not None else "No"
            row[f"{activity} Time"] = hm(ms)
        rows.append(row)
        day += dt.timedelta(days=1)
    return rows
