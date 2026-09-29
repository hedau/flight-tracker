"""Emails after the morning check, sent with Resend (https://resend.com).

Two emails:
- "Price check done": every morning a check runs, with each trip's price.
- "New lowest price": only when a trip beats every earlier check.

Settings (no emails are sent without both):
  RESEND_API_KEY  from https://resend.com/api-keys
  ALERT_EMAIL     where the emails go. On Resend's free plan without your own
                  domain, this must be the address you signed up to Resend with.
  EMAIL_FROM      optional sender; defaults to Resend's test address
  APP_URL         optional dashboard address for the button (Render sets
                  RENDER_EXTERNAL_URL automatically, which is used otherwise)
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
APP_URL = (os.environ.get("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")

# Colours match the dashboard's light theme.
TEXT, TEXT_2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
ACCENT, GOOD, BAD = "#2a78d6", "#0a7d33", "#c2362f"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def enabled():
    return bool(RESEND_API_KEY and ALERT_EMAIL)


def send_check_emails(checked, now):
    """Send the morning emails for the routes just checked. Returns what was sent."""
    if not enabled():
        return []
    sent = []
    subject, body = daily_email(checked, now)
    send(subject, body)
    sent.append(subject)
    lows = [c for c in checked if c["new_lowest"]]
    if lows:
        subject, body = lowest_email(lows)
        send(subject, body)
        sent.append(subject)
    return sent


def send(subject, body):
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


# --- The two emails -----------------------------------------------------------------

def daily_email(checked, now):
    trips = {(c["route"]["origin"], c["route"]["destination"]) for c in checked}
    subject = "✓ Price check done · %s · %d %s" % (
        now.strftime("%a, %b %-d"), len(trips), "trip" if len(trips) == 1 else "trips")

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
        p("Today's %s price check ran at <strong>%s</strong>." % ("evening" if now.hour >= 19 else "morning", clock(now)))
        + table(["Trip", "Dates", "Price", "vs last check"], rows, right_from=2)
        + p("Nonstop or 1 stop · 1 adult · incl. taxes", small=True)
        + button("Open dashboard")
        + footer("Trips that couldn't be checked show ⚠ with the reason. "
                 "No email by about noon Eastern means the check didn't run.")
    )
    return subject, page(body)


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


# --- Small pieces of HTML (email apps need the styles written inline) -----------------

def page(inner):
    return (
        '<div style="background:#f4f3f0;padding:24px 12px;font-family:%s;color:%s">'
        '<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:14px;padding:24px 20px">'
        '<div style="font-weight:700;font-size:15px;color:%s;margin-bottom:16px">✈ Flight Tracker</div>'
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


def footer(text):
    return ('<p style="margin:16px 0 0;padding-top:12px;border-top:1px solid %s;font-size:12px;color:%s">%s</p>'
            % (GRID, MUTED, text))


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
