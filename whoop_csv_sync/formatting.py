"""Helpers that turn WHOOP API values into spreadsheet-friendly strings."""
import datetime as dt
import re


def local_time(ts, offset):
    """Parse a WHOOP UTC timestamp and shift it to the record's own time zone offset."""
    t = dt.datetime.fromisoformat(re.sub(r"\.\d+", "", ts.replace("Z", "+00:00")))
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", offset or "")
    if not m:
        return t.astimezone()  # fall back to this machine's time zone
    sign = -1 if m.group(1) == "-" else 1
    return t.astimezone(dt.timezone(sign * dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))))


def hm(ms):
    """Milliseconds -> 'H:MM' (blank if missing). Excel reads this as a duration."""
    if ms is None:
        return ""
    mins = round(ms / 60000)
    return f"{mins // 60}:{mins % 60:02d}"
