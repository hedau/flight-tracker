"""Flight price tracker: web dashboard and API.

Run locally:  python3 app.py   then open http://localhost:8000

Settings come from environment variables (or a .env file on your Mac):
  DASHBOARD_PASSWORD  password for the dashboard (required in the cloud)
  SERPAPI_KEY         SerpApi key; leave empty for demo prices
  DATABASE_URL        PostgreSQL address; leave empty to use a local file
  CRON_SECRET         secret the morning alarm (GitHub Actions) must send
"""

import os


def load_env_file(path=".env"):
    """Read KEY=value lines from .env so secrets never live in the code."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# Must run before importing db/prices, which read their settings on import.
load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import datetime
import hashlib
import hmac
import json
import re
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import db
import prices

IN_CLOUD = "PORT" in os.environ
PORT = int(os.environ.get("PORT", 8000))
HOST = os.environ.get("HOST", "0.0.0.0" if IN_CLOUD else "127.0.0.1")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}
MAX_BODY_BYTES = 2000
SESSION_DAYS = 30
AIRPORT_CODE = re.compile(r"^[A-Z]{3}$")

# Signing key for login cookies. It is derived from the password, so
# changing the password logs everyone out.
SESSION_KEY = hashlib.sha256(b"flight-tracker-session:" + PASSWORD.encode()).digest()

failed_logins = []  # times of recent wrong passwords, to slow down guessing
failed_lock = threading.Lock()


# --- Login cookies ----------------------------------------------------------------

def make_session():
    expires = str(int(time.time()) + SESSION_DAYS * 86400)
    signature = hmac.new(SESSION_KEY, expires.encode(), "sha256").hexdigest()
    return expires + "." + signature


def valid_session(token):
    expires, _, signature = token.partition(".")
    expected = hmac.new(SESSION_KEY, expires.encode(), "sha256").hexdigest()
    return (
        hmac.compare_digest(signature, expected)
        and expires.isdigit()
        and int(expires) > time.time()
    )


def too_many_failed_logins():
    with failed_lock:
        cutoff = time.time() - 15 * 60
        failed_logins[:] = [t for t in failed_logins if t > cutoff]
        return len(failed_logins) >= 10


# --- Checking what the user typed ---------------------------------------------------

def parse_route(data):
    """Turn the add-route form into clean values, or raise ValueError."""
    origin = str(data.get("origin", "")).strip().upper()
    destination = str(data.get("destination", "")).strip().upper()
    trip_type = data.get("trip_type")
    depart = str(data.get("depart_date", ""))
    back = str(data.get("return_date") or "")

    if not AIRPORT_CODE.match(origin) or not AIRPORT_CODE.match(destination):
        raise ValueError("Use 3-letter airport codes, like JFK or LAX.")
    if origin == destination:
        raise ValueError("From and To must be different airports.")
    if trip_type not in ("one_way", "round_trip"):
        raise ValueError("Choose one-way or round trip.")
    try:
        depart_day = datetime.date.fromisoformat(depart)
        back_day = datetime.date.fromisoformat(back) if trip_type == "round_trip" else None
    except ValueError:
        raise ValueError("Pick the travel date(s).")
    if depart < prices.today_eastern():
        raise ValueError("The departure date is in the past.")
    if back_day and back_day < depart_day:
        raise ValueError("The return date must be on or after the departure date.")
    return origin, destination, trip_type, depart, back or None


def route_summary(route):
    history = db.prices_for(route["id"])
    known = [p for p in history if p["price"] is not None]
    return {
        "id": route["id"],
        "origin": route["origin"],
        "destination": route["destination"],
        "trip_type": route["trip_type"],
        "depart_date": route["depart_date"],
        "return_date": route["return_date"],
        "active": bool(route["active"]),
        "history": known,
        "last_error": history[-1]["error"] if history and history[-1]["price"] is None else None,
    }


# --- Web server ---------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        # Hosting services send HEAD requests to check the app is up.
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self.send_json({"ok": True})
        if path == "/login":
            return self.send_static("login.html")
        if path == "/style.css":
            return self.send_static("style.css")
        if not self.logged_in():
            if path.startswith("/api/"):
                return self.send_json({"error": "Please log in."}, 401)
            return self.redirect("/login")
        if path == "/":
            return self.send_static("index.html")
        if path == "/app.js":
            return self.send_static("app.js")
        if path == "/api/routes":
            routes = [route_summary(r) for r in db.list_routes()]
            return self.send_json({
                "routes": routes,
                "demo": prices.demo_mode(),
                "today": prices.today_eastern(),
                "currency": prices.CURRENCY,
                "login_required": bool(PASSWORD),
            })
        if path == "/api/usage":
            try:
                return self.send_json({"usage": prices.search_usage()})
            except RuntimeError as err:
                return self.send_json({"error": str(err)})
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self.read_body()
        if body is None:
            return

        if path == "/login":
            return self.handle_login(body)
        if path == "/api/check":
            return self.handle_scheduled_check()
        if not self.logged_in():
            return self.send_json({"error": "Please log in."}, 401)
        if path == "/logout":
            self.send_response(303)
            self.send_header("Set-Cookie", "session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if path == "/api/routes":
            return self.handle_add_route(body)
        self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        match = re.fullmatch(r"/api/routes/(\d+)", self.path)
        if not self.logged_in():
            return self.send_json({"error": "Please log in."}, 401)
        if not match:
            return self.send_json({"error": "Not found"}, 404)
        db.delete_route(int(match.group(1)))
        self.send_json({"ok": True})

    # --- Actions ---

    def handle_login(self, body):
        if too_many_failed_logins():
            return self.redirect("/login?error=wait")
        password = parse_qs(body.decode("utf-8", "replace")).get("password", [""])[0]
        if PASSWORD and hmac.compare_digest(password.encode(), PASSWORD.encode()):
            self.send_response(303)
            secure = "; Secure" if IN_CLOUD else ""
            self.send_header(
                "Set-Cookie",
                "session=%s; Max-Age=%d; Path=/; HttpOnly; SameSite=Strict%s"
                % (make_session(), SESSION_DAYS * 86400, secure),
            )
            self.send_header("Location", "/")
            self.end_headers()
            return
        with failed_lock:
            failed_logins.append(time.time())
        time.sleep(1)
        self.redirect("/login?error=wrong")

    def handle_add_route(self, body):
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return self.send_json({"error": "Invalid request."}, 400)
        try:
            origin, destination, trip_type, depart, back = parse_route(data)
        except ValueError as err:
            return self.send_json({"error": str(err)}, 400)
        route_id = db.add_route(
            origin, destination, trip_type, depart, back,
            prices.now_eastern().isoformat(timespec="seconds"),
        )
        # Get the first price straight away so the chart isn't empty until tomorrow.
        prices.check_route(db.get_route(route_id))
        self.send_json({"route": route_summary(db.get_route(route_id))})

    def handle_scheduled_check(self):
        sent = self.headers.get("Authorization", "")
        if not CRON_SECRET or not hmac.compare_digest(sent.encode(), ("Bearer " + CRON_SECRET).encode()):
            return self.send_json({"error": "Not allowed"}, 403)
        scheduled = "scheduled=1" in self.path
        self.send_json(prices.run_daily_check(scheduled=scheduled))

    # --- Helpers ---

    def logged_in(self):
        if not PASSWORD:
            return True  # only allowed on your own Mac; see main()
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return "session" in cookie and valid_session(cookie["session"].value)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self.send_json({"error": "Request too large."}, 413)
            return None
        return self.rfile.read(length)

    def send_static(self, name):
        with open(os.path.join(STATIC_DIR, name), "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", STATIC_TYPES[os.path.splitext(name)[1]] + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data, status=200):
        content = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()


def main():
    if IN_CLOUD and not PASSWORD:
        raise SystemExit("Set DASHBOARD_PASSWORD before running in the cloud.")
    db.init()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    where = "cloud database" if db.using_postgres() else "local file " + db.SQLITE_PATH
    mode = "DEMO prices" if prices.demo_mode() else "live SerpApi prices"
    print("Flight tracker at http://localhost:%d  (%s, %s; Ctrl+C to stop)" % (PORT, mode, where), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
