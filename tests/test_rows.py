import csv
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from whoop_csv_sync import csv_store, daily

SLEEP = {
    "id": "s-1", "start": "2026-10-18T22:45:00.000Z", "end": "2026-10-19T06:30:00.000Z",
    "timezone_offset": "+00:00", "nap": False, "score_state": "SCORED",
    "score": {"stage_summary": {"total_light_sleep_time_milli": 12_600_000,
                                "total_slow_wave_sleep_time_milli": 5_400_000,
                                "total_rem_sleep_time_milli": 7_200_000}},
}
NAP = {**SLEEP, "id": "s-2", "start": "2026-10-19T13:00:00.000Z", "end": "2026-10-19T14:00:00.000Z", "nap": True}
STRENGTH = {"id": "w-1", "start": "2026-10-19T18:00:00.000Z", "end": "2026-10-19T18:45:00.000Z",
            "timezone_offset": "+05:30", "sport_name": "weightlifting_msk"}
RUN = {**STRENGTH, "id": "w-2", "start": "2026-10-18T06:00:00.000Z", "sport_name": "running"}

OCT18, OCT19, OCT20 = dt.date(2026, 10, 18), dt.date(2026, 10, 19), dt.date(2026, 10, 20)


class DailyTests(unittest.TestCase):
    def rows(self, sleeps, workouts):
        return {r["Date"]: r for r in daily.build_rows(sleeps, workouts, OCT18, OCT20)}

    def test_week_starts_on_monday(self):
        mon = dt.date(2026, 10, 19)
        for day in (mon, OCT20, dt.date(2026, 10, 25)):  # Monday itself, Tuesday, Sunday
            self.assertEqual(daily.week_start(day), mon)
        self.assertEqual(daily.week_start(dt.date(2026, 10, 26)), dt.date(2026, 10, 26))  # next Monday

    def test_window_is_last_week_plus_this_week(self):
        last_mon = dt.date(2026, 10, 12)
        for day in (dt.date(2026, 10, 19), OCT20, dt.date(2026, 10, 25)):  # this Monday, Tuesday, Sunday
            self.assertEqual(daily.window_start(day), last_mon)
        self.assertEqual(daily.window_start(dt.date(2026, 10, 26)), dt.date(2026, 10, 19))  # next Monday
        self.assertEqual(daily.window_start(OCT20, weeks=4), dt.date(2026, 9, 28))
        self.assertEqual(daily.window_start(OCT20, weeks=1), dt.date(2026, 10, 19))

    def test_one_row_per_day(self):
        self.assertEqual(list(self.rows([], [])), ["2026-10-18", "2026-10-19", "2026-10-20"])

    def test_sleep_goes_on_wake_day_and_naps_add_to_duration(self):
        row = self.rows([SLEEP, NAP], [])["2026-10-19"]  # 7:00 asleep + a nap scored at 7:00 = 14:00
        self.assertEqual((row["Bedtime"], row["Wake"], row["Sleep Duration"]), ("22:45", "06:30", "14:00"))

    def test_nap_only_day_uses_longest_nap_times_and_adds_up_naps(self):
        short_nap = {**NAP, "id": "s-3", "start": "2026-10-19T16:00:00.000Z", "end": "2026-10-19T16:45:00.000Z",
                     "score": {"stage_summary": {"total_light_sleep_time_milli": 1_800_000,
                                                 "total_slow_wave_sleep_time_milli": 0,
                                                 "total_rem_sleep_time_milli": 600_000}}}
        row = self.rows([short_nap, NAP], [])["2026-10-19"]  # 1:00 nap wins over 0:45 nap; 7:00 + 0:40 asleep
        self.assertEqual((row["Bedtime"], row["Wake"], row["Sleep Duration"]), ("13:00", "14:00", "7:40"))

    def test_longer_nap_does_not_replace_main_sleep_times(self):
        short_sleep = {**SLEEP, "start": "2026-10-19T05:00:00.000Z"}  # 1:30 in bed
        long_nap = {**NAP, "start": "2026-10-19T12:00:00.000Z", "end": "2026-10-19T15:00:00.000Z"}
        row = self.rows([long_nap, short_sleep], [])["2026-10-19"]
        self.assertEqual((row["Bedtime"], row["Wake"]), ("05:00", "06:30"))

    def test_longest_sleep_wins_when_there_are_several(self):
        early = {**SLEEP, "id": "s-4", "start": "2026-10-19T01:00:00.000Z", "end": "2026-10-19T02:00:00.000Z"}
        row = self.rows([early, SLEEP], [])["2026-10-19"]
        self.assertEqual((row["Bedtime"], row["Wake"], row["Sleep Duration"]), ("22:45", "06:30", "14:00"))

    def test_unscored_nap_only_day_has_times_but_no_duration(self):
        row = self.rows([{**NAP, "score": None}], [])["2026-10-19"]
        self.assertEqual((row["Bedtime"], row["Wake"], row["Sleep Duration"]), ("13:00", "14:00", ""))

    def test_unscored_nap_is_not_counted_yet(self):
        row = self.rows([SLEEP, {**NAP, "score": None}], [])["2026-10-19"]
        self.assertEqual(row["Sleep Duration"], "7:00")

    def test_in_progress_records_are_skipped(self):
        rows = self.rows([{**SLEEP, "end": None}], [{**STRENGTH, "end": None}])
        self.assertEqual((rows["2026-10-19"]["Wake"], rows["2026-10-19"]["Strength Workout"]), ("", "No"))

    def test_unscored_sleep_has_times_but_no_duration(self):
        row = self.rows([{**SLEEP, "score": None}], [])["2026-10-19"]
        self.assertEqual((row["Wake"], row["Sleep Duration"]), ("06:30", ""))

    def test_activities_by_type(self):
        rows = self.rows([], [STRENGTH, RUN])
        self.assertEqual((rows["2026-10-18"]["Strength Workout"], rows["2026-10-18"]["Running"]), ("No", "Yes"))
        # 18:00 UTC is 23:30 in +05:30, still the 19th locally
        self.assertEqual((rows["2026-10-19"]["Strength Workout"], rows["2026-10-19"]["Strength Workout Time"]),
                         ("Yes", "0:45"))
        self.assertEqual((rows["2026-10-20"]["Walking"], rows["2026-10-20"]["Walking Time"]), ("No", ""))

    def test_activity_time_adds_up_sessions(self):
        walk = {**STRENGTH, "sport_name": "walking"}
        second_walk = {**walk, "id": "w-3", "start": "2026-10-19T06:00:00.000Z", "end": "2026-10-19T06:20:00.000Z"}
        self.assertEqual(self.rows([], [walk, second_walk])["2026-10-19"]["Walking Time"], "1:05")

    def test_write_replaces_whole_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / daily.FILENAME
            csv_store.write(path, daily.build_rows([], [], OCT18, OCT20), daily.COLUMNS)
            csv_store.write(path, daily.build_rows([SLEEP], [STRENGTH], OCT19, OCT20), daily.COLUMNS)
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r["Date"] for r in rows], ["2026-10-19", "2026-10-20"])
            self.assertEqual((rows[0]["Wake"], rows[0]["Strength Workout"]), ("06:30", "Yes"))
            self.assertFalse(path.with_name(path.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
