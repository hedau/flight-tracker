# ✈️ Flight Tracker

Track flight prices twice a day (7 AM and 7 PM Eastern) and watch how they change on a dashboard.

- Add a route: from, to, one-way or round trip, and dates.
- Every day at **7:00 AM Eastern**, each route's cheapest price (in **USD**) is saved.
- The dashboard shows the latest, lowest and highest price, plus a chart per route.
- Routes stop being checked automatically after their departure date.

Prices come from Google Flights via [SerpApi](https://serpapi.com). Without a
SerpApi key the app runs in **demo mode** with made-up prices.

## Run it on your Mac

```bash
python3 app.py
```

Then open http://localhost:8000. With no settings, it uses demo prices, saves to
`flights.db` in this folder.

The dashboard has no login: anyone with the link can see and change the trips.

To use real prices, copy `.env.example` to `.env` and fill it in.
`.env` holds your secrets and is never uploaded to GitHub.

Using the Neon database from your Mac needs the `psycopg` package, installed in a
private virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

## How it works

```
GitHub Actions  --7 AM & 7 PM ET-->  app on Render  --asks-->  SerpApi (Google Flights)
(the alarm clock)             (dashboard)    --saves--> Neon PostgreSQL database
```

| File | What it does |
|---|---|
| `app.py` | Web server: dashboard, adding/removing routes, the `/api/check` endpoint |
| `prices.py` | Gets prices from SerpApi (or demo prices) and runs the twice-daily check |
| `db.py` | Saves routes and prices (SQLite on your Mac, PostgreSQL in the cloud) |
| `static/` | The dashboard page, its styles, and the chart code |
| `.github/workflows/morning-check.yml` | The alarm that triggers the 7 AM and 7 PM checks |

## Put it online (free)

You need three free accounts: **SerpApi**, **Neon** and **Render**.

1. **SerpApi:** sign up and copy your API key from https://serpapi.com/manage-api-key.
2. **Neon:** create a project at https://neon.tech and copy its connection
   string (starts with `postgresql://`).
3. **Render:** New > Web Service > this repository, then set:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python3 app.py`
   - Instance type: Free
   - Environment variables: `SERPAPI_KEY`,
     `DATABASE_URL` (from Neon) and `CRON_SECRET` (any long random text).
4. **GitHub:** in this repository go to Settings > Secrets and variables >
   Actions and add:
   - `APP_URL`: your Render address, e.g. `https://flight-tracker-xxxx.onrender.com`
   - `CRON_SECRET`: the same value you gave Render
5. Test it: Actions tab > **Price checks (7 AM and 7 PM ET)** > **Run workflow**.

### Alerts for a new lowest price (optional)

When a check finds a price lower than every earlier check of that trip date,
the app can email you and send a notification to your phone. Nothing is sent
otherwise. Add these on Render (Environment), then run `python3 notify.py test`
on your Mac (with the same values in `.env`) to send a sample.

- **Email:** sign up at https://resend.com, create an API key, and set
  `RESEND_API_KEY` and `ALERT_EMAIL` (your Resend sign-up address).
- **Phone:** install the free **ntfy** app, subscribe to a hard-to-guess topic
  name, and set `NTFY_TOPIC` to that name.

### Good to know

- Each route uses about 60 SerpApi searches a month (2 checks a day). The dashboard shows how many
  are left.
- GitHub pauses scheduled workflows in repositories with no commits for 60 days.
  If that happens, GitHub emails you; re-enable it from the Actions tab.
- The free Render plan sleeps when unused, so the dashboard may take up to a
  minute to open. The alarm wakes it up and retries automatically.
