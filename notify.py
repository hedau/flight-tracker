"""Alerts when a trip hits a new lowest price: an email and a phone notification.

An alert goes out only when a check finds a price lower than every earlier
check of that trip date (never on its first check). Nothing is sent otherwise.

Email, sent with Resend (https://resend.com); needs both:
  RESEND_API_KEY  from https://resend.com/api-keys
  ALERT_EMAIL     where the emails go. On Resend's free plan without your own
                  domain, this must be the address you signed up to Resend with.
  EMAIL_FROM      optional sender; defaults to Resend's test address

Phone notification, sent with ntfy (https://ntfy.sh, free app for iPhone/Android):
  NTFY_TOPIC      a hard-to-guess topic name; subscribe to it in the ntfy app.
                  Anyone who knows the name can read the alerts, so keep it private.
  NTFY_SERVER     optional; defaults to https://ntfy.sh

  APP_URL         optional dashboard address for the links (Render sets
                  RENDER_EXTERNAL_URL automatically, which is used otherwise)

Send a sample alert to check the settings:  python3 notify.py test
"""

import datetime
import html
import json
import os
import urllib.error
import urllib.request

import prices

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip() or "Flight Tracker <onboarding@resend.dev>"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").strip().rstrip("/")
APP_URL = (os.environ.get("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")

# Colours match the dashboard's light theme.
TEXT, TEXT_2, MUTED, GRID = "#0d1b3a", "#4a5a7a", "#7c89a3", "#e3e9f3"
ACCENT, GOOD, BAD = "#2f6bff", "#0e8a4a", "#d23b30"
FONT = "'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def email_enabled():
    return bool(RESEND_API_KEY and ALERT_EMAIL)


def push_enabled():
    return bool(NTFY_TOPIC)


def send_alerts(checked):
    """Email and notify about the routes that just hit a new lowest price.

    Returns a list of what was sent, or of what failed (a failure in one
    doesn't stop the other).
    """
    lows = [c for c in checked if c["new_lowest"]]
    if not lows:
        return []
    results = []
    if email_enabled():
        subject, body = lowest_email(lows)
        try:
            send_email(subject, body)
            results.append("Email: " + subject)
        except RuntimeError as err:
            results.append("Email failed: %s" % err)
    if push_enabled():
        for c in lows:
            try:
                send_push(*lowest_push(c))
                results.append("Push: %s %s" % (route_name(c["route"]), money(c["price"])))
            except RuntimeError as err:
                results.append("Push failed: %s" % err)
    return results


def send_push(title, message):
    """Send a phone notification through ntfy."""
    payload = {"topic": NTFY_TOPIC, "title": title, "message": message, "tags": ["airplane"], "priority": 4}
    if APP_URL:
        payload["click"] = APP_URL
    request = urllib.request.Request(
        NTFY_SERVER + "/",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "flight-tracker"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.HTTPError as err:
        raise RuntimeError("ntfy returned HTTP %s" % err.code)
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach ntfy: %s" % err.reason)


def send_email(subject, body):
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": EMAIL_FROM, "to": [ALERT_EMAIL], "subject": subject, "html": body}).encode(),
        headers={
            "Authorization": "Bearer " + RESEND_API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "flight-tracker",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.HTTPError as err:
        try:
            message = json.load(err).get("message")
        except ValueError:
            message = None
        raise RuntimeError(message or "Resend returned HTTP %s" % err.code)
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach Resend: %s" % err.reason)


# --- What the alerts say -------------------------------------------------------------

def lowest_email(lows):
    if len(lows) == 1:
        c = lows[0]
        subject = "🔻 New lowest price: %s %s" % (route_name(c["route"]), money(c["price"]))
    else:
        subject = "🔻 New lowest prices on %d trips" % len(lows)

    body = ""
    for i, c in enumerate(lows):
        if i:
            body += '<hr style="border:0;border-top:1px solid %s;margin:24px 0">' % GRID
        body += p("<strong>%s · %s</strong> just hit its lowest price since you started tracking."
                  % (route_name(c["route"]), trip_dates(c["route"])))
        level = {"low": "✓ Low price", "typical": "● Typical price", "high": "▲ High price"}.get(c["level"])
        body += '<div style="font-size:32px;font-weight:800;margin:4px 0 2px">%s%s</div>' % (
            money(c["price"]),
            ' <span style="font-size:13px;font-weight:700;color:%s">%s</span>' % (TEXT_2, level) if level else "")
        facts = ["Previous lowest %s (%s)" % (money(c["previous_lowest"]), short_date(c["previous_lowest_on"])),
                 '<span style="color:%s">▼ %s</span>' % (GOOD, money(c["previous_lowest"] - c["price"]))]
        if c["typical_low"] is not None:
            facts.append("Google's typical range %s–%s" % (money(c["typical_low"]), money(c["typical_high"])))
        body += p(" · ".join(facts), small=True)

        options = c["options"]
        picks = options[:1]
        value = prices.best_value(options)
        if value:
            picks.append(value)
        if picks:
            rows = ""
            for o in picks:
                tag = tag_html("CHEAPEST", GOOD) if o is options[0] else tag_html("BEST VALUE", ACCENT)
                rows += "<tr>%s%s%s%s</tr>" % (
                    td(esc(o["airline"] or "—") + " " + tag), td(stops_text(o)),
                    td(duration(o["minutes"])), td("<strong>%s</strong>" % money(o["price"]), right=True))
            body += table(["Option", "Stops", "Time", "Price"], rows, right_from=3)

    body += button("See on dashboard")
    return subject, page(body)


def lowest_push(c):
    """Title and text of the phone notification for one route."""
    title = "🔻 New lowest: %s %s" % (route_name(c["route"]), money(c["price"]))
    lines = ["%s · %s" % (trip_dates(c["route"]), c["airline"] or "any airline"),
             "Was %s (%s) · ▼ %s" % (money(c["previous_lowest"]), short_date(c["previous_lowest_on"]),
                                      money(c["previous_lowest"] - c["price"]))]
    if c["typical_low"] is not None:
        lines.append("Google's typical range %s–%s" % (money(c["typical_low"]), money(c["typical_high"])))
    return title, "\n".join(lines)


# --- Small pieces of HTML (email apps need the styles written inline) -----------------

def page(inner):
    return (
        '<div style="background:#f4f3f0;padding:24px 12px;font-family:%s;color:%s">'
        '<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:14px;padding:24px 20px">'
        '<div style="font-weight:800;font-size:15px;color:%s;margin-bottom:16px">✈ Flight Tracker</div>'
        "%s</div></div>" % (FONT, TEXT, ACCENT, inner)
    )


def p(text, small=False):
    style = "margin:0 0 12px;" + ("font-size:13px;color:%s" % TEXT_2 if small else "font-size:15px")
    return '<p style="%s">%s</p>' % (style, text)


def table(headings, rows, right_from):
    head = "".join(
        '<th style="text-align:%s;font-size:11px;text-transform:uppercase;letter-spacing:.04em;'
        'color:%s;padding:6px 8px;font-weight:600">%s</th>' % ("right" if i >= right_from else "left", TEXT_2, h)
        for i, h in enumerate(headings))
    return ('<table style="width:100%%;border-collapse:collapse;font-size:14px;margin:0 0 12px">'
            "<tr>%s</tr>%s</table>" % (head, rows))


def td(content, right=False):
    return '<td style="padding:8px;border-top:1px solid %s;text-align:%s">%s</td>' % (
        GRID, "right" if right else "left", content)


def tag_html(text, colour):
    return ('<span style="font-size:10px;font-weight:700;letter-spacing:.03em;color:%s;'
            'white-space:nowrap">%s</span>' % (colour, text))


def button(label):
    if not APP_URL:
        return ""
    return ('<p style="margin:16px 0"><a href="%s" style="display:inline-block;background:%s;color:#ffffff;'
            'font-weight:600;font-size:14px;padding:10px 18px;border-radius:10px;text-decoration:none">%s</a></p>'
            % (esc(APP_URL), ACCENT, label))


# --- Formatting ---------------------------------------------------------------------

def esc(text):
    return html.escape(str(text))


def money(n):
    return "$" + format(int(round(n)), ",")


def route_name(route):
    return "%s → %s" % (route["origin"], route["destination"])


def short_date(iso):
    return datetime.date.fromisoformat(iso).strftime("%b %-d")


def trip_dates(route):
    if route.get("return_date"):
        return "%s – %s" % (short_date(route["depart_date"]), short_date(route["return_date"]))
    return short_date(route["depart_date"])


def clock(moment):
    return moment.strftime("%-I:%M %p") + " ET"


def duration(minutes):
    if minutes is None:
        return "—"
    return "%dh %02dm" % divmod(minutes, 60)


def stops_text(option):
    if not option["stops"]:
        return "Nonstop"
    return "%d · %s" % (option["stops"], ", ".join(option["via"])) if option["via"] else str(option["stops"])


# --- Try it: python3 notify.py test ------------------------------------------------------

if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["test"]:
        raise SystemExit("Usage: python3 notify.py test   (sends a sample alert)")
    # Read the settings from .env too, as the app does.
    import app
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
    ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "").strip()
    EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip() or EMAIL_FROM
    NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
    APP_URL = (os.environ.get("APP_URL") or APP_URL).strip().rstrip("/")
    sample = {
        "route": {"origin": "ATL", "destination": "BOM", "depart_date": "2026-12-30", "return_date": "2027-01-20"},
        "price": 1099, "airline": "Lufthansa", "level": "low", "typical_low": 1150, "typical_high": 1400,
        "previous_lowest": 1171, "previous_lowest_on": "2026-09-27", "new_lowest": True,
        "options": [{"airline": "Lufthansa", "stops": 1, "via": ["FRA"], "minutes": 1325, "depart": "16:30", "price": 1099}],
    }
    if not (email_enabled() or push_enabled()):
        raise SystemExit("No alerts set up: add RESEND_API_KEY + ALERT_EMAIL and/or NTFY_TOPIC.")
    for line in send_alerts([sample]):
        print(line)
