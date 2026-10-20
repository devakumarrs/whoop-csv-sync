"""WHOOP OAuth sign-in and token refresh."""
import http.server
import secrets
import time
import urllib.parse
import webbrowser

from whoop_csv_sync.api import AUTH_URL, SCOPES, TOKEN_URL, ApiError, form_request, http_json
from whoop_csv_sync.config import TOKENS, load_json, save_private

REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"


def store_tokens(t):
    t["expires_at"] = time.time() + int(t.get("expires_in", 3600)) - 60
    save_private(TOKENS, t)


def access_token(cfg):
    t = load_json(TOKENS)
    if not t:
        raise RuntimeError("Not signed in. Run: python3 -m whoop_csv_sync login")
    if time.time() < t.get("expires_at", 0):
        return t["access_token"]
    try:
        new = http_json(form_request(TOKEN_URL, {
            "grant_type": "refresh_token", "refresh_token": t["refresh_token"],
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "scope": "offline"}))
    except ApiError as e:
        raise RuntimeError(f"Couldn't renew WHOOP sign-in ({e}). Run: python3 -m whoop_csv_sync login") from None
    new.setdefault("refresh_token", t["refresh_token"])
    store_tokens(new)  # WHOOP rotates refresh tokens, so save the new one right away
    return new["access_token"]


def login(cfg):
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cfg["client_id"], "redirect_uri": REDIRECT_URI,
        "scope": SCOPES, "state": state})
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Done. You can close this tab and go back to Terminal.</h2>".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), Handler)
    server.timeout = 1
    print("\nOpening WHOOP sign-in in your browser. If nothing opens, paste this link into it:\n"
          + url + "\n")
    webbrowser.open(url)
    deadline = time.time() + 300
    while not result and time.time() < deadline:
        server.handle_request()
    server.server_close()

    if not result:
        raise RuntimeError("Timed out waiting for WHOOP sign-in (5 minutes).")
    if "error" in result:
        raise RuntimeError(f"WHOOP sign-in failed: {result['error']} {result.get('error_description', '')}")
    if result.get("state") != state:
        raise RuntimeError("Sign-in check failed (state mismatch). Run login again.")

    store_tokens(http_json(form_request(TOKEN_URL, {
        "grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT_URI,
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"]})))
    print("Signed in to WHOOP.")
