"""Alerts after the price checks.

- Phone: a short summary after every check (7 AM and 7 PM Eastern).
- Email and phone: an alert when a check finds a price lower than every
  earlier check of that trip date (never on its first check).

Email, sent with Resend (https://resend.com). The address and which emails
to send are chosen in the dashboard's Notifications panel (saved in the
database); ALERT_EMAIL is only used until an address is saved there.
  RESEND_API_KEY  from https://resend.com/api-keys (needed for any email)
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

import db
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


def email_settings():
    """The Notifications panel's choices: address, and which emails to send."""
    saved = db.get_settings()
    return {
        "email": saved.get("alert_email") or ALERT_EMAIL,
        "email_lowest": saved.get("email_lowest", "1") == "1",   # on unless switched off
        "email_every": saved.get("email_every", "0") == "1",     # off unless switched on
    }


def email_enabled():
    return bool(RESEND_API_KEY and email_settings()["email"])


def push_enabled():
    return bool(NTFY_TOPIC)


def send_alerts(checked, now=None):
    """Send the phone summary of this check, then the new-lowest-price alerts.

    Returns a list of what was sent, or of what failed (a failure in one
    doesn't stop the others).
    """
    results = []
    if not checked:
        return results
    now = now or prices.now_eastern()
    settings = email_settings()
    if push_enabled():
        try:
            send_push(*summary_push(checked, now), priority=3)
            results.append("Push: check summary")
        except RuntimeError as err:
            results.append("Push failed: %s" % err)
    if email_enabled() and settings["email_every"]:
        subject, body = summary_email(checked, now)
        try:
            send_email(subject, body)
            results.append("Email: " + subject)
        except RuntimeError as err:
            results.append("Email failed: %s" % err)
    lows = [c for c in checked if c["new_lowest"]]
    if not lows:
        return results
    if email_enabled() and settings["email_lowest"]:
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


def send_push(title, message, priority=4):
    """Send a phone notification through ntfy (priority 3 = normal, 4 = high)."""
    payload = {"topic": NTFY_TOPIC, "title": title, "message": message, "tags": ["airplane"], "priority": priority}
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
        data=json.dumps({"from": EMAIL_FROM, "to": [email_settings()["email"]], "subject": subject, "html": body}).encode(),
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

def summary_email(checked, now):
    """The after-every-check email: each trip date's price and its change."""
    which = "7 PM" if now.hour >= prices.EVENING_HOUR else "7 AM"
    subject = "✓ %s price check · %s · %d %s" % (
        which, now.strftime("%a, %b %-d"), len(checked), "date" if len(checked) == 1 else "dates")
    rows = ""
    for c in checked:
        if c["price"] is None:
            price = '<span style="color:%s">⚠ Not checked</span>' % BAD
            change = '<span style="color:%s">%s</span>' % (TEXT_2, esc(c["error"] or "Unknown error"))
        else:
            price = "<strong>%s</strong>" % money(c["price"])
            change = change_text(c["price"], c["previous_price"])
            if c["new_lowest"]:
                change += ' <span style="color:%s;font-weight:700">· new low</span>' % GOOD
        rows += "<tr>%s%s%s%s</tr>" % (
            td(route_name(c["route"])), td(trip_dates(c["route"])),
            td(price, right=True), td(change, right=True))
    body = (
        p("The %s price check ran at <strong>%s</strong>." % (which, clock(now)))
        + table(["Trip", "Dates", "Price", "vs last check"], rows, right_from=2)
        + p("Nonstop or 1 stop · 1 adult · incl. taxes", small=True)
        + button("Open dashboard")
    )
    return subject, page(body)


def test_email():
    """A short email to check the address works (the panel's Send test email)."""
    body = (p("✓ This is a test from your Flight Tracker. Emails will arrive here.")
            + p("Change what you get in the dashboard's 🔔 Notifications panel.", small=True)
            + button("Open dashboard"))
    return "✓ Flight Tracker test email", page(body)


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


def summary_push(checked, now):
    """Title and text of the after-every-check phone notification."""
    which = "7 PM" if now.hour >= prices.EVENING_HOUR else "7 AM"
    title = "✓ %s price check · %d %s" % (which, len(checked), "date" if len(checked) == 1 else "dates")
    lines = []
    for c in checked:
        name = "%s %s" % (route_name(c["route"]), trip_dates(c["route"]))
        if c["price"] is None:
            lines.append("⚠ %s: not checked (%s)" % (name, c["error"] or "unknown error"))
            continue
        prev = c["previous_price"]
        if prev is None:
            change = "first check"
        elif c["price"] == prev:
            change = "no change"
        else:
            change = "%s %s" % ("▼" if c["price"] < prev else "▲", money(abs(c["price"] - prev)))
        low = " · 🔻 new low" if c["new_lowest"] else ""
        lines.append("%s: %s (%s)%s" % (name, money(c["price"]), change, low))
    return title, "\n".join(lines)


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


def change_text(price, previous):
    if previous is None:
        return '<span style="color:%s">first check</span>' % MUTED
    diff = price - previous
    if diff == 0:
        return '<span style="color:%s">no change</span>' % MUTED
    colour, arrow = (GOOD, "▼") if diff < 0 else (BAD, "▲")
    return '<span style="color:%s">%s %s</span>' % (colour, arrow, money(abs(diff)))


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
    db.DATABASE_URL = os.environ.get("DATABASE_URL", "")  # use the same database as the app
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
    sample["previous_price"] = 1171
    sample["error"] = None
    for line in send_alerts([sample]):
        print(line)
