"""Thin WHOOP API client (stdlib only)."""
import datetime as dt
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from whoop_csv_sync import __version__

API = os.environ.get("WHOOP_API_URL", "https://api.prod.whoop.com")  # overridden by the end-to-end test
AUTH_URL = API + "/oauth/oauth2/auth"
TOKEN_URL = API + "/oauth/oauth2/token"
SCOPES = "offline read:sleep read:workout"


class ApiError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def http_json(req):
    # Cloudflare in front of WHOOP rejects urllib's default "Python-urllib" agent (error 1010)
    req.add_header("User-Agent", f"whoop-csv-sync/{__version__} (+https://developer.whoop.com)")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise ApiError(e.code, f"HTTP {e.code} from {req.full_url.split('?')[0]}: {detail}") from None


def form_request(url, fields):
    return urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})


NETWORK_WAIT = 180  # seconds to wait for the network before giving up (the first wait included)
NETWORK_FIRST_WAIT = 10  # seconds before the first check
NETWORK_RETRY_DELAY = 8  # seconds after a failed check; doubled each time, up to NETWORK_MAX_DELAY
NETWORK_MAX_DELAY = 30


def wait_for_network(timeout=NETWORK_WAIT):
    """Wait until WHOOP's hostname resolves (scheduled runs only). The at-login and on-wake runs often start before
    Wi-Fi has reconnected, and the first request (renewing the sign-in) would fail straight away.
    Returns without raising on timeout; the request that follows then reports the real error."""
    host = urllib.parse.urlparse(API).hostname
    deadline = time.monotonic() + timeout
    time.sleep(NETWORK_FIRST_WAIT)
    delay = NETWORK_RETRY_DELAY
    while True:
        try:
            socket.getaddrinfo(host, 443)
            return True
        except OSError:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(delay, left))
            delay = min(delay * 2, NETWORK_MAX_DELAY)


RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_DELAY = 5  # seconds; doubled after each failed attempt


def get_json(url, token, attempts=3):
    """GET with retries, so a brief WHOOP or Wi-Fi hiccup doesn't fail the whole run."""
    for attempt in range(attempts):
        try:
            return http_json(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}))
        except ApiError as e:
            if e.status not in RETRY_STATUSES or attempt == attempts - 1:
                raise
        except OSError:  # network down, DNS, timeout
            if attempt == attempts - 1:
                raise
        time.sleep(RETRY_DELAY * 2 ** attempt)


def fetch_all(path, token, days):
    """Fetch every record from a paginated collection endpoint for the last `days` days."""
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    records, next_token = [], None
    while True:
        q = {"start": start, "limit": 25}
        if next_token:
            q["nextToken"] = next_token
        page = get_json(API + path + "?" + urllib.parse.urlencode(q), token)
        records += page.get("records", [])
        next_token = page.get("next_token")
        if not next_token:
            return records
