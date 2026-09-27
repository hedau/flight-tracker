"""Looking up flight prices and running the daily check.

With a SERPAPI_KEY set, prices come from Google Flights through SerpApi.
Without one, the app runs in demo mode and makes up believable prices,
so you can try everything before signing up.
"""

import datetime
import hashlib
import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import db

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
CURRENCY = "USD"
TIMEZONE = ZoneInfo("America/New_York")
MORNING_HOUR = 7  # the scheduled check runs from 7 AM Eastern Time


def demo_mode():
    return not SERPAPI_KEY


def now_eastern():
    return datetime.datetime.now(TIMEZONE)


def today_eastern():
    return now_eastern().date().isoformat()


# --- Getting one price --------------------------------------------------------

def fetch_price(route):
    """Return (price, airline) for a route, or raise RuntimeError."""
    if demo_mode():
        return demo_price(route)
    return serpapi_price(route)


def serpapi_price(route):
    params = {
        "engine": "google_flights",
        "departure_id": route["origin"],
        "arrival_id": route["destination"],
        "outbound_date": route["depart_date"],
        "type": "1" if route["trip_type"] == "round_trip" else "2",
        "currency": CURRENCY,
        "hl": "en",
        "api_key": SERPAPI_KEY,
    }
    if route["trip_type"] == "round_trip":
        params["return_date"] = route["return_date"]
    data = _get_json("https://serpapi.com/search.json?" + urllib.parse.urlencode(params))
    if data.get("error"):
        raise RuntimeError(data["error"])

    flights = data.get("best_flights", []) + data.get("other_flights", [])
    priced = [f for f in flights if isinstance(f.get("price"), (int, float))]
    if priced:
        cheapest = min(priced, key=lambda f: f["price"])
        legs = cheapest.get("flights") or [{}]
        return int(round(cheapest["price"])), legs[0].get("airline")

    lowest = (data.get("price_insights") or {}).get("lowest_price")
    if lowest:
        return int(lowest), None
    raise RuntimeError("No flights found for this route and date")


def demo_price(route):
    """A made-up price that drifts a little from day to day."""
    key = "{origin}-{destination}-{trip_type}-{depart_date}".format(**route)
    base = 150 + int(hashlib.sha256(key.encode()).hexdigest(), 16) % 500
    if route["trip_type"] == "round_trip":
        base = int(base * 1.8)
    rng = random.Random(key + today_eastern())
    airline = rng.choice(["Delta", "United", "American", "JetBlue", "Alaska"])
    return int(base * rng.uniform(0.85, 1.2)), airline


def _get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        try:
            message = json.load(err).get("error")
        except ValueError:
            message = None
        raise RuntimeError(message or "Price service returned HTTP %s" % err.code)
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach the price service: %s" % err.reason)


# --- Checking routes ------------------------------------------------------------

def check_route(route):
    """Look up today's price for one route and save it (or the error)."""
    price, airline, error = None, None, None
    try:
        price, airline = fetch_price(route)
    except RuntimeError as err:
        error = str(err)
    db.save_price(
        route["id"], today_eastern(), now_eastern().isoformat(timespec="seconds"),
        price, airline, "demo" if demo_mode() else "serpapi", error,
    )
    return {"route_id": route["id"], "price": price, "error": error}


def run_daily_check(scheduled=False):
    """Check every active route once per day.

    Safe to call many times: a route that already has today's price is skipped.
    When scheduled=True, nothing happens before 7 AM Eastern, which lets the
    GitHub alarm fire at two UTC times and still work across daylight saving.
    """
    now = now_eastern()
    if scheduled and now.hour < MORNING_HOUR:
        return {"skipped": "Before %d AM Eastern" % MORNING_HOUR, "checked": []}

    today = now.date().isoformat()
    checked, finished, already_done = [], [], 0
    for route in db.active_routes():
        if route["depart_date"] < today:
            db.deactivate_route(route["id"])
            finished.append(route["id"])
        elif db.has_price_for(route["id"], today):
            already_done += 1
        else:
            checked.append(check_route(route))
    return {"checked": checked, "already_done": already_done, "finished": finished}


def search_usage():
    """How many SerpApi searches are left this month (None in demo mode)."""
    if demo_mode():
        return None
    url = "https://serpapi.com/account.json?" + urllib.parse.urlencode({"api_key": SERPAPI_KEY})
    data = _get_json(url)
    return {
        "used": data.get("this_month_usage"),
        "left": data.get("plan_searches_left"),
        "per_month": data.get("searches_per_month"),
    }
