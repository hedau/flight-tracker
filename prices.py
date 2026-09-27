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
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import db

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
CURRENCY = "USD"
TIMEZONE = ZoneInfo("America/New_York")
MORNING_HOUR = 7  # the scheduled check runs from 7 AM Eastern Time
AIRLINE_CODE = re.compile(r"^[A-Z0-9]{2}$")  # e.g. DL, B6

# Names for common airline codes, used in demo mode and for routes saved
# before airline names were stored.
KNOWN_AIRLINES = {
    "AA": "American",
    "DL": "Delta",
    "UA": "United",
    "WN": "Southwest",
    "B6": "JetBlue",
    "AS": "Alaska",
    "NK": "Spirit",
    "F9": "Frontier",
}


def demo_mode():
    return not SERPAPI_KEY


def now_eastern():
    return datetime.datetime.now(TIMEZONE)


def today_eastern():
    return now_eastern().date().isoformat()


# --- Getting one price --------------------------------------------------------

def fetch_price(route):
    """Look up a route's cheapest price, or raise RuntimeError.

    Returns a dict: price, airline, level ("low"/"typical"/"high" from Google),
    typical_low, typical_high, and history (Google's recent prices as
    [["2026-09-01", 172], ...]). Everything except price may be None.
    """
    if demo_mode():
        return demo_price(route)
    return serpapi_price(route)


def serpapi_search(route):
    """All priced flight options Google Flights shows for a route and date."""
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
    if route.get("airlines"):
        params["include_airlines"] = route["airlines"]  # e.g. "DL,UA"
    data = _get_json("https://serpapi.com/search.json?" + urllib.parse.urlencode(params))
    if data.get("error"):
        raise RuntimeError(data["error"])
    flights = data.get("best_flights", []) + data.get("other_flights", [])
    return [f for f in flights if isinstance(f.get("price"), (int, float))], data


def serpapi_price(route):
    priced, data = serpapi_search(route)
    insights = data.get("price_insights") or {}
    typical = insights.get("typical_price_range") or [None, None]
    result = {
        "price": None,
        "airline": None,
        "level": insights.get("price_level"),
        "typical_low": typical[0],
        "typical_high": typical[1] if len(typical) > 1 else None,
        # Google gives [unix time, price] pairs; keep one price per day.
        "history": sorted({
            datetime.datetime.fromtimestamp(ts, TIMEZONE).date().isoformat(): int(p)
            for ts, p in insights.get("price_history") or []
        }.items()),
    }
    if priced:
        cheapest = min(priced, key=lambda f: f["price"])
        legs = cheapest.get("flights") or [{}]
        result.update(price=int(round(cheapest["price"])), airline=legs[0].get("airline"))
        return result
    if insights.get("lowest_price"):
        result["price"] = int(insights["lowest_price"])
        return result
    if route.get("airlines"):
        raise RuntimeError("No flights found on the chosen airlines for this route and date")
    raise RuntimeError("No flights found for this route and date")


def demo_price(route):
    """A made-up price that drifts a little from day to day."""
    key = "{origin}-{destination}-{trip_type}-{depart_date}-{return_date}-{airlines}".format(**route)
    base = 150 + int(hashlib.sha256(key.encode()).hexdigest(), 16) % 500
    if route["trip_type"] == "round_trip":
        base = int(base * 1.8)
    rng = random.Random(key + today_eastern())
    codes = route["airlines"].split(",") if route.get("airlines") else list(KNOWN_AIRLINES)
    airline = KNOWN_AIRLINES.get(rng.choice(codes), "Demo Air")
    price = int(base * rng.uniform(0.85, 1.2))
    low, high = int(base * 0.9), int(base * 1.15)

    # 60 days of made-up history that wanders around the base price.
    history, level, today = [], base * 1.1, now_eastern().date()
    walk = random.Random(key)
    for days_ago in range(60, 0, -1):
        level = max(base * 0.7, level + walk.uniform(-0.04, 0.035) * base)
        history.append(((today - datetime.timedelta(days=days_ago)).isoformat(), int(level)))
    return {
        "price": price,
        "airline": airline,
        "level": "low" if price < low else "high" if price > high else "typical",
        "typical_low": low,
        "typical_high": high,
        "history": history,
    }


# --- Which airlines fly a route --------------------------------------------------

def airlines_on_route(route):
    """List the airlines flying a route on its date, cheapest first.

    Returns [{"code": "DL", "name": "Delta", "price": 189}, ...]. Uses one search.
    """
    if demo_mode():
        return demo_airlines(route)
    priced, _ = serpapi_search(route)
    found = {}
    for option in priced:
        for leg in option.get("flights") or []:
            # Flight numbers look like "DL 408"; the first part is the airline code.
            code = (leg.get("flight_number") or "").split(" ")[0].upper()
            if not AIRLINE_CODE.match(code):
                continue
            price = int(round(option["price"]))
            if code not in found or price < found[code]["price"]:
                found[code] = {"code": code, "name": leg.get("airline") or code, "price": price}
    if not found:
        raise RuntimeError("No flights found for this route and date")
    return sorted(found.values(), key=lambda a: a["price"])


def demo_airlines(route):
    rng = random.Random("{origin}-{destination}-{depart_date}".format(**route))
    codes = rng.sample(list(KNOWN_AIRLINES), 5)
    airlines = [{"code": c, "name": KNOWN_AIRLINES[c], "price": rng.randint(150, 600)} for c in codes]
    return sorted(airlines, key=lambda a: a["price"])


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
    result, error = {}, None
    try:
        result = fetch_price(route)
    except RuntimeError as err:
        error = str(err)
    db.save_price(
        route["id"], today_eastern(), now_eastern().isoformat(timespec="seconds"),
        result, "demo" if demo_mode() else "serpapi", error,
    )
    if result.get("history"):
        db.save_google_history(route["id"], json.dumps(result["history"]))
    return {"route_id": route["id"], "price": result.get("price"), "error": error}


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


def next_check():
    """When the next scheduled check happens (7 AM Eastern, today or tomorrow)."""
    now = now_eastern()
    target = now.replace(hour=MORNING_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return target


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
