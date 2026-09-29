// Flight Tracker dashboard: summary tiles, trip cards with charts, and the add-flight form.

const SVG_NS = "http://www.w3.org/2000/svg";
const DAY_MS = 86400000;

// ---------------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------------

const money = (n) => "$" + Math.round(n).toLocaleString("en-US");
const toDate = (iso) => new Date(iso + "T12:00:00");
const shortDate = (iso) => toDate(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
const longDate = (iso) =>
  toDate(iso).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });
const daysBetween = (a, b) => Math.round((toDate(b) - toDate(a)) / DAY_MS);
const addDays = (iso, n) => new Date(toDate(iso).getTime() + n * DAY_MS).toISOString().slice(0, 10);
// When a check ran, shown in Eastern time like the morning schedule ("11:27 AM ET").
const checkTime = (stamp) =>
  stamp ? new Date(stamp).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET" : "—";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function svg(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (parent) parent.append(node);
  return node;
}

function cityOf(code) {
  return (AIRPORT_BY_CODE[code] || {}).city || null;
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => (toast.hidden = true), 4000);
}

// Google's rating for a price: low / typical / high, shown with an icon and words.
function levelBadge(level) {
  const labels = { low: "✓ Low price", typical: "● Typical price", high: "▲ High price" };
  if (!labels[level]) return null;
  return el("span", "level " + level, labels[level]);
}

const capitalise = (word) => word[0].toUpperCase() + word.slice(1);

// ---------------------------------------------------------------------------------
// Talking to the server
// ---------------------------------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json" } });
  const data = await response.json();
  if (data.error && !response.ok) throw new Error(data.error);
  return data;
}

let state = { routes: [], today: null, next_check: null, max_dates: 5 };
let usage = null;             // { used, left, per_month } from SerpApi
const selectedDate = {};      // trip key -> route id shown in its chart
const chartRange = {};        // route id -> "14" | "30" | "all"
let sortBy = "soonest";       // "soonest" | "cheapest" | "drop", remembered in this browser
try { sortBy = localStorage.getItem("sort") || sortBy; } catch (err) { /* storage blocked */ }

async function loadRoutes() {
  state = await api("/api/routes");
  document.getElementById("demo-banner").hidden = !state.demo;
  renderSummary();
  renderTrips();
  updateBudget();
}

async function loadUsage() {
  try {
    usage = (await api("/api/usage")).usage;
  } catch (err) {
    usage = null;
  }
  renderSummary();
  updateBudget();
}

// ---------------------------------------------------------------------------------
// Grouping: several dates of the same trip share one card
// ---------------------------------------------------------------------------------

function tripKey(route) {
  return [route.origin, route.destination, route.trip_type, route.airlines.join("|")].join("/");
}

function groupTrips(routes) {
  const groups = new Map();
  for (const route of routes) {
    const key = tripKey(route);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(route);
  }
  const list = [...groups.entries()].map(([key, dates]) => ({
    key,
    dates: dates.sort((a, b) => a.depart_date.localeCompare(b.depart_date)),
    active: dates.some((d) => d.active),
  }));
  // Active trips first, then in the order picked in the Sort menu.
  const soonest = (a, b) => a.dates[0].depart_date.localeCompare(b.dates[0].depart_date);
  const price = (trip) => { const d = cheapestDate(trip); return d ? latest(d).price : Infinity; };
  const drop = (trip) => { const d = cheapestDate(trip); const w = d && weekChange(d); return w == null ? Infinity : w; };
  const order = sortBy === "cheapest" ? (a, b) => price(a) - price(b) || soonest(a, b)
    : sortBy === "drop" ? (a, b) => drop(a) - drop(b) || soonest(a, b)
    : soonest;
  return list.sort((a, b) => (b.active - a.active) || order(a, b));
}

const latest = (route) => route.history[route.history.length - 1] || null;

// Price change against the check from a week before the latest one
// (null until there is a check at least 7 days old).
function weekChange(route) {
  const point = latest(route);
  if (!point) return null;
  const weekAgo = addDays(point.checked_on, -7);
  const before = route.history.filter((p) => p.checked_on <= weekAgo);
  return before.length ? point.price - before[before.length - 1].price : null;
}

const changeText = (diff) => (diff === 0 ? "No change" : (diff < 0 ? "▼ " : "▲ ") + money(Math.abs(diff)));
const changeClass = (diff) => (diff < 0 ? "down" : diff > 0 ? "up" : "");

// Opens the same search on Google Flights, to book.
function googleFlightsUrl(route) {
  const q = "Flights from " + route.origin + " to " + route.destination + " on " + route.depart_date +
    (route.return_date ? " through " + route.return_date : " one way");
  return "https://www.google.com/travel/flights?q=" + encodeURIComponent(q) + "&curr=" + (state.currency || "USD");
}

// How far a price is below (-) or above (+) the middle of Google's typical range.
function vsTypical(point) {
  if (!point || point.typical_low == null || point.typical_high == null) return null;
  const mid = (point.typical_low + point.typical_high) / 2;
  return (point.price - mid) / mid;
}

// ---------------------------------------------------------------------------------
// Summary tiles
// ---------------------------------------------------------------------------------

function renderSummary() {
  const active = state.routes.filter((r) => r.active);
  const trips = groupTrips(active);
  document.getElementById("sum-tracking").textContent = trips.length + (trips.length === 1 ? " trip" : " trips");
  document.getElementById("sum-tracking-sub").textContent =
    active.length + (active.length === 1 ? " date" : " dates") + " checked daily";

  // Best deal: the price furthest below Google's typical range, else the cheapest.
  const priced = active.filter(latest);
  const rated = priced.filter((r) => vsTypical(latest(r)) != null);
  let best = null;
  if (rated.length) best = rated.reduce((a, b) => (vsTypical(latest(b)) < vsTypical(latest(a)) ? b : a));
  else if (priced.length) best = priced.reduce((a, b) => (latest(b).price < latest(a).price ? b : a));
  const bestValueEl = document.getElementById("sum-best");
  const bestSub = document.getElementById("sum-best-sub");
  const badge = document.getElementById("sum-best-badge");
  const photo = document.getElementById("sum-best-photo");
  if (best) {
    const point = latest(best);
    bestValueEl.textContent = money(point.price);
    bestSub.replaceChildren(el("strong", null, best.origin + " → " + best.destination), el("br"),
      [tripDates(best), point.airline].filter(Boolean).join(" · "));
    badge.hidden = !(best.history.length > 1 && point.price <= Math.min(...best.history.map((p) => p.price)));
    showCityPhoto(photo, best.destination);
  } else {
    bestValueEl.textContent = "–";
    bestSub.textContent = "No prices yet";
    badge.hidden = true;
    photo.hidden = true;
  }

  renderCountdown();
  renderGreeting();
  renderClock();

  const searches = document.getElementById("sum-searches");
  const meter = document.getElementById("sum-meter");
  const searchesSub = document.getElementById("sum-searches-sub");
  if (state.demo) {
    searches.textContent = "Demo";
    searchesSub.textContent = "No searches used";
    meter.style.width = "100%";
  } else if (usage && usage.left != null) {
    searches.replaceChildren(usage.left.toLocaleString("en-US") + " ", el("small", null, "/ " + usage.per_month));
    searchesSub.textContent = "~" + active.length * 30 + " needed a month";
    meter.style.width = Math.max(0, Math.min(100, (usage.left / usage.per_month) * 100)) + "%";
    meter.classList.toggle("low", usage.left < usage.per_month * 0.2);
  }
}

function renderGreeting() {
  const hour = new Date().getHours();
  document.getElementById("greeting").textContent = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

// Date and time in Eastern, like the price checks.
function renderClock() {
  const now = new Date();
  const zone = { timeZone: "America/New_York" };
  const clock = document.getElementById("clock");
  clock.replaceChildren(
    now.toLocaleDateString("en-US", { ...zone, weekday: "short", month: "short", day: "numeric", year: "numeric" }),
    el("br"),
    now.toLocaleTimeString("en-US", { ...zone, hour: "numeric", minute: "2-digit" }) + " ET",
  );
}

function renderCountdown() {
  if (!state.next_check) return;
  const minutes = Math.max(0, Math.round((new Date(state.next_check) - Date.now()) / 60000));
  const hours = Math.floor(minutes / 60);
  document.getElementById("sum-next").textContent = hours ? hours + "h " + (minutes % 60) + "m" : minutes + "m";
  const day = state.next_check.slice(0, 10) === state.today ? "Today" : "Tomorrow";
  document.getElementById("sum-next-sub").textContent = day + " at 7:00 AM ET";
}
setInterval(() => { renderCountdown(); renderClock(); }, 30000);

// ---------------------------------------------------------------------------------
// Pictures: a photo of each city (from Wikipedia) and airline logos
// ---------------------------------------------------------------------------------

const photoCache = {};  // airport code -> Promise of a photo address, or null

function cityPhoto(code) {
  if (!(code in photoCache)) {
    const airport = AIRPORT_BY_CODE[code];
    let saved;
    try { saved = localStorage.getItem("photo:" + code); } catch (err) { /* storage blocked */ }
    if (saved !== null && saved !== undefined) {
      photoCache[code] = Promise.resolve(saved || null);
    } else if (!airport) {
      photoCache[code] = Promise.resolve(null);
    } else {
      photoCache[code] = fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent(airport.city))
        .then((r) => (r.ok ? r.json() : {}))
        .then((page) => {
          const url = page.type === "standard" && page.thumbnail ? page.thumbnail.source : "";
          try { localStorage.setItem("photo:" + code, url); } catch (err) { /* storage blocked */ }
          return url || null;
        })
        .catch(() => null);
    }
  }
  return photoCache[code];
}

// Put a city photo into an <img>; it stays hidden when there isn't one.
function showCityPhoto(img, code, fallback) {
  img.hidden = true;
  cityPhoto(code).then((url) => {
    if (!url) return;
    img.onload = () => { img.hidden = false; if (fallback) fallback.remove(); };
    img.src = url;
    const airport = AIRPORT_BY_CODE[code];
    img.alt = airport ? airport.city : code;
    img.title = "Photo: Wikipedia";
  });
}

// Airline codes for logos when a saved option has no logo address.
const AIRLINE_CODES = {
  "Lufthansa": "LH", "Turkish Airlines": "TK", "Qatar Airways": "QR", "Etihad": "EY", "Emirates": "EK",
  "Virgin Atlantic": "VS", "British Airways": "BA", "Air France": "AF", "KLM": "KL", "Air India": "AI",
  "IndiGo": "6E", "Air Canada": "AC", "SWISS": "LX", "Delta": "DL", "United": "UA", "American": "AA",
  "Southwest": "WN", "JetBlue": "B6", "Spirit": "NK", "Frontier": "F9", "Alaska": "AS", "Saudia": "SV",
  "Gulf Air": "GF", "Kuwait Airways": "KU", "Oman Air": "WY", "Singapore Airlines": "SQ", "Vistara": "UK",
};

function airlineLogo(option) {
  const first = (option.airline || "").split(",")[0].trim();
  const code = AIRLINE_CODES[first];
  const url = option.logo || (code ? "https://www.gstatic.com/flights/airline_logos/70px/" + code + ".png" : null);
  const initials = el("span", "logo initials", first ? first.split(" ").map((w) => w[0]).join("").slice(0, 2) : "✈");
  initials.setAttribute("aria-hidden", "true");
  if (!url) return initials;
  const img = el("img", "logo");
  img.alt = "";
  img.loading = "lazy";
  img.src = url;
  img.onerror = () => img.replaceWith(initials);
  return img;
}

// ---------------------------------------------------------------------------------
// Trips: one row each, with the details on the left and the top 5 on the right
// ---------------------------------------------------------------------------------

function renderTrips() {
  const container = document.getElementById("trip-list");
  container.replaceChildren();
  const trips = groupTrips(state.routes);
  document.getElementById("sort-box").hidden = trips.length < 2;
  if (!trips.length) {
    const empty = el("section", "panel empty");
    empty.append(el("div", "empty-icon", "🛫"), el("h2", null, "No flights tracked yet"));
    empty.append(el("p", null, "Add a route and your dates. Every morning at 7 AM Eastern the price is checked and saved, so you can see the best time to book."));
    const button = el("button", "primary", "+ Track your first flight");
    button.type = "button";
    button.addEventListener("click", openForm);
    empty.append(button);
    container.append(empty);
    return;
  }
  for (const trip of trips) container.append(tripRow(trip));
}

const sortSelect = document.getElementById("sort");
sortSelect.value = sortBy;
if (!sortSelect.value) sortSelect.value = sortBy = "soonest";
sortSelect.addEventListener("change", () => {
  sortBy = sortSelect.value;
  try { localStorage.setItem("sort", sortBy); } catch (err) { /* storage blocked */ }
  renderTrips();
});

function tripRow(trip) {
  const first = trip.dates[0];
  const row = el("div", "trip-row");
  const card = el("section", "panel trip stripe");
  const side = el("aside", "panel opts stripe");
  row.append(card, side);

  // Heading: photo, ATL → BOM, city names, note, tags and buttons
  const head = el("div", "trip-head");
  const placeholder = el("div", "trip-photo", "🌍");
  placeholder.setAttribute("aria-hidden", "true");
  const photo = el("img", "trip-photo");
  head.append(placeholder, photo);
  showCityPhoto(photo, first.destination, placeholder);

  const titleBox = el("div", "trip-name");
  const title = el("div", "trip-title");
  title.append(first.origin, el("span", "arrow", "→"), first.destination);
  titleBox.append(title);
  const fromCity = cityOf(first.origin);
  const toCity = cityOf(first.destination);
  if (fromCity || toCity) titleBox.append(el("div", "trip-cities", (fromCity || first.origin) + " to " + (toCity || first.destination)));
  if (first.note) titleBox.append(el("div", "trip-note", "📝 " + first.note));

  const tags = el("div", "tags");
  tags.append(el("span", "tag", first.trip_type === "round_trip" ? "Round trip" : "One-way"));
  if (first.airlines.length === 1) {
    tags.append(el("span", "tag", first.airlines[0] + " only"));
  } else if (first.airlines.length > 1) {
    const details = el("details", "airline-tag");
    details.append(el("summary", "tag", first.airlines.length + " airlines ▾"));
    details.append(el("div", "airline-names", first.airlines.join(" · ")));
    tags.append(details);
  } else {
    tags.append(el("span", "tag", "Any airline"));
  }
  tags.append(el("span", "tag", "Max 1 stop"));
  if (!trip.active) tags.append(el("span", "tag", "Finished"));
  titleBox.append(tags);
  head.append(titleBox);

  const buttons = el("div", "head-buttons");
  if (trip.active) {
    const edit = el("button", "outline", "✏️ Edit trip");
    edit.type = "button";
    edit.addEventListener("click", () => openEditForm(trip));
    buttons.append(edit);
  }
  const deleteTrip = el("button", "outline remove", "🗑 Delete trip");
  deleteTrip.type = "button";
  deleteTrip.addEventListener("click", () => removeTrip(trip));
  buttons.append(deleteTrip);
  head.append(buttons);
  card.append(head);

  // Which date is shown in detail (the cheapest, until you pick another)
  let chosen = trip.dates.find((d) => d.id === selectedDate[trip.key]) || cheapestDate(trip) || first;
  selectedDate[trip.key] = chosen.id;

  const detailBox = el("div");
  const show = () => {
    detailBox.replaceChildren(dateDetail(chosen, trip));
    side.replaceChildren(...optionsSection(chosen, trip));
  };
  card.append(dateTable(trip, chosen, (route) => {
    chosen = route;
    selectedDate[trip.key] = route.id;
    card.querySelectorAll(".date-table tbody tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.id === String(route.id)));
    show();
  }));
  card.append(detailBox);
  show();
  return row;
}

function cheapestDate(trip) {
  const priced = trip.dates.filter((d) => d.active && latest(d));
  if (!priced.length) return null;
  return priced.reduce((a, b) => (latest(b).price < latest(a).price ? b : a));
}

function tripDates(route) {
  return route.return_date ? shortDate(route.depart_date) + " – " + shortDate(route.return_date) : shortDate(route.depart_date);
}

function dateTable(trip, chosen, onSelect) {
  const cheapest = trip.dates.length > 1 ? cheapestDate(trip) : null;
  const box = el("div", "date-box");
  const table = el("table", "date-table");
  const head = el("tr");
  [["Dates"], ["Price"], ["Change", "hide-sm"], ["Trend"], [""]].forEach(([h, cls]) => head.append(el("th", cls, h)));
  const thead = el("thead");
  thead.append(head);
  table.append(thead);
  const body = el("tbody");
  for (const route of trip.dates) {
    const row = el("tr", route.id === chosen.id ? "selected" : "");
    row.dataset.id = route.id;
    row.tabIndex = 0;
    row.setAttribute("aria-label", "Show " + tripDates(route));

    const dates = el("td", "dates-cell");
    if (cheapest && route.id === cheapest.id) dates.append(el("span", "pill cheap", "Cheapest date"));
    if (!route.active) dates.append(el("span", "pill", "Finished"));
    dates.append(tripDates(route));

    const point = latest(route);
    const price = el("td", "price-cell", point ? money(point.price) : "–");
    const change = el("td", "hide-sm");
    if (route.history.length > 1) {
      const diff = point.price - route.history[0].price;
      change.textContent = diff === 0 ? "— No change" : changeText(diff);
      change.className = "hide-sm " + (changeClass(diff) || "muted");
    } else {
      change.textContent = "New";
      change.className = "hide-sm muted";
    }
    const trend = el("td", "spark-cell");
    trend.append(sparkline(route));

    row.append(dates, price, change, trend, el("td", "chev", "›"));
    row.addEventListener("click", () => onSelect(route));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(route); }
    });
    body.append(row);
  }
  table.append(body);
  box.append(table);
  return box;
}

function dateDetail(route, trip) {
  const point = latest(route);
  const box = el("div", "detail");

  if (!point) {
    box.classList.add("waiting");
    box.append(el("p", "muted", route.last_error ? "Couldn't get a price: " + route.last_error : "Waiting for the first price."));
    box.append(cardFoot(route, trip));
    return box;
  }

  // 1. The price, with Google's rating (or "lowest so far") and the Book button
  const main = el("div");
  const lowest = route.history.reduce((a, b) => (b.price < a.price ? b : a));
  const badge = levelBadge(point.price_level);
  if (badge) main.append(badge);
  else if (route.history.length > 1 && point.price <= lowest.price) main.append(el("span", "pill cheap", "Lowest so far"));
  main.append(el("div", "hero-price", money(point.price)));
  main.append(el("div", "hero-when", [tripDates(route), point.airline].filter(Boolean).join(" · ")));
  if (point.typical_low != null) main.append(el("div", "hero-sub", "Typical " + money(point.typical_low) + "–" + money(point.typical_high)));
  main.append(el("div", "hero-sub", "Checked " + shortDate(point.checked_on) + ", " + checkTime(point.checked_at)));
  if (route.active) {
    const book = el("a", "book", "Book on Google Flights ↗");
    book.href = googleFlightsUrl(route);
    book.target = "_blank";
    book.rel = "noopener";
    main.append(book);
  }

  // 2. Key facts
  const facts = el("div", "facts");
  const fact = (label, value, cls) => {
    const f = el("div");
    f.append(el("div", "fact-label", label), el("div", "fact-value " + (cls || ""), value));
    facts.append(f);
  };
  if (route.history.length > 1) {
    const diff = point.price - route.history[0].price;
    fact("Since first check", diff === 0 ? "↔ No change" : changeText(diff), changeClass(diff));
    fact("Lowest seen", "🏆 " + money(lowest.price) + " · " + shortDate(lowest.checked_on));
    const week = weekChange(route);
    if (week != null) fact("Last 7 days", week === 0 ? "↔ No change" : changeText(week), changeClass(week));
    else fact("Last 7 days", "From " + shortDate(addDays(route.history[0].checked_on, 7)), "soon");
  }
  const daysLeft = daysBetween(state.today, route.depart_date);
  fact("Departs", "📅 " + (daysLeft > 0 ? "in " + daysLeft + " days" : daysLeft === 0 ? "today" : "departed"));

  // 3. Price trend
  const trend = el("div", "trend");
  trend.append(el("h3", null, "Price trend"));
  if (route.history.length > 1 || route.google_history.length > 1) {
    const from = route.google_history.length ? route.google_history[0][0] : route.history[0].checked_on;
    trend.append(el("div", "trend-sub", "From " + longDate(from) + " to " + longDate(point.checked_on)));
    trend.append(chartSection(route, point));
  } else if (point.typical_low != null) {
    trend.append(rangeView(point));
  } else {
    trend.append(el("p", "muted small", "The chart starts after the next morning check."));
  }

  box.append(main, facts, trend);
  const wrap = el("div");
  if (route.last_error) wrap.append(el("p", "error", "Latest check failed: " + route.last_error));
  wrap.append(box, cardFoot(route, trip));
  return wrap;
}

// The cheapest few flights from the latest check, for the right-hand column.
function optionsSection(route, trip) {
  const point = latest(route);
  const heading = el("h3", null, "Top " + (route.options.length || 5) + " options");
  if (!point) return [heading, el("p", "muted small", "Options appear after the first price check.")];
  const when = point.checked_on === state.today ? "today's check" : "the check on " + shortDate(point.checked_on);
  const sub = el("div", "opts-sub", "From " + when + " at " + checkTime(point.checked_at) + " · Nonstop or 1 stop");
  if (!route.options.length) return [heading, sub, el("p", "muted small", "Options appear after the next check.")];

  const cheapest = route.options[0];
  const value = bestValue(route.options);
  const list = el("div", "opt-list");
  for (const o of route.options) {
    const row = el("div", "opt" + (o === cheapest || o === value ? " hl" : ""));
    const info = el("div");
    const name = el("div", "opt-name", o.airline || "—");
    if (o === cheapest) name.append(el("span", "pill cheap", "Lowest fare"));
    if (o === value) name.append(el("span", "pill value", "Best value"));
    const stops = o.stops ? o.stops + (o.stops === 1 ? " stop" : " stops") : "Nonstop";
    info.append(name, el("div", "opt-info", [stops].concat(o.via || [], duration(o.minutes)).join(" · ")));
    info.append(el("div", "opt-info", "Departs " + clockTime(o.depart)));
    const price = el("div", "opt-price", money(o.price));
    price.append(o === cheapest
      ? el("div", "opt-extra zero", "Cheapest")
      : el("div", "opt-extra", "+" + money(o.price - cheapest.price)));
    row.append(airlineLogo(o), info, price);
    list.append(row);
  }
  const parts = [heading, sub, list];
  if (value) parts.push(el("p", "opts-foot", "Best value: at least 3 hours faster than the cheapest, for no more than $75 extra."));
  if (trip.dates.length > 1) parts.push(el("p", "opts-foot", "For " + tripDates(route) + ". Pick another date on the left to see its options."));
  return parts;
}

// Same rule as best_value() in prices.py.
function bestValue(options) {
  const cheapest = options[0];
  if (!cheapest || cheapest.minutes == null) return null;
  const faster = options.slice(1).filter((o) =>
    o.minutes != null && o.minutes <= cheapest.minutes - 180 && o.price <= cheapest.price + 75);
  return faster.length ? faster.reduce((a, b) => (b.price < a.price ? b : a)) : null;
}

const duration = (minutes) => (minutes == null ? "—" : Math.floor(minutes / 60) + "h " + String(minutes % 60).padStart(2, "0") + "m");

// "16:30" -> "4:30 PM"
function clockTime(hhmm) {
  if (!hhmm) return "—";
  const [h, m] = hhmm.split(":").map(Number);
  return (h % 12 || 12) + ":" + String(m).padStart(2, "0") + (h < 12 ? " AM" : " PM");
}

function cardFoot(route, trip) {
  const foot = el("div", "card-foot");
  foot.append(route.history.length ? historyTable(route) : el("span"));
  // A single-date trip is deleted with the button at the top of its card.
  if (trip.dates.length > 1) {
    const remove = el("button", "ghost small-btn remove", "🗑 Delete these dates");
    remove.type = "button";
    remove.addEventListener("click", () => removeRoute(route));
    foot.append(remove);
  }
  return foot;
}

function historyTable(route) {
  const details = el("details");
  details.append(el("summary", null, "All prices (" + route.history.length + ")"));
  const table = el("table", "history-table");
  const head = el("tr");
  ["Date", "Checked at", "Price", "Google says", "Cheapest airline"].forEach((h) => head.append(el("th", null, h)));
  table.append(head);
  for (const p of [...route.history].reverse()) {
    const row = el("tr");
    row.append(
      el("td", null, longDate(p.checked_on)),
      el("td", null, checkTime(p.checked_at)),
      el("td", null, money(p.price)),
      el("td", null, p.price_level ? capitalise(p.price_level) : "—"),
      el("td", null, p.airline || "—"),
    );
    table.append(row);
  }
  details.append(table);
  return details;
}

// First day: show where today's price sits within Google's typical range.
function rangeView(point) {
  const box = el("div", "range-view");
  box.append(el("div", "range-title", "Where today's price sits"));
  box.append(el("div", "muted small", "The shaded part is Google's typical price range for this trip. Your price chart builds up from the next morning check."));
  const low = Math.min(point.typical_low, point.price);
  const high = Math.max(point.typical_high, point.price);
  const pad = (high - low) * 0.15 || 50;
  const min = low - pad;
  const max = high + pad;
  const pos = (v) => ((v - min) / (max - min)) * 100 + "%";

  const track = el("div", "range-track");
  const typical = el("div", "range-typical");
  typical.style.left = pos(point.typical_low);
  typical.style.width = "calc(" + pos(point.typical_high) + " - " + pos(point.typical_low) + ")";
  const marker = el("div", "range-marker");
  marker.style.left = pos(point.price);
  const label = el("div", "range-marker-label", "Today " + money(point.price));
  label.style.left = pos(point.price);
  const lowLabel = el("div", "range-end", money(point.typical_low));
  lowLabel.style.left = pos(point.typical_low);
  const highLabel = el("div", "range-end", money(point.typical_high));
  highLabel.style.left = pos(point.typical_high);
  track.append(typical, lowLabel, highLabel, marker, label);
  box.append(track);
  return box;
}

// ---------------------------------------------------------------------------------
// Price chart
// ---------------------------------------------------------------------------------

function chartSection(route, point) {
  const box = el("div");
  const checks = route.history.map((p) => ({ date: p.checked_on, price: p.price, airline: p.airline, at: p.checked_at }));
  // Google's history is shown for the days before you started tracking.
  const firstCheck = checks.length ? checks[0].date : null;
  const context = route.google_history
    .filter(([date]) => !firstCheck || date < firstCheck)
    .map(([date, price]) => ({ date, price }));
  const band = point.typical_low != null ? [point.typical_low, point.typical_high] : null;

  const bar = el("div", "chart-bar");
  const legend = el("div", "legend");
  const key = (cls, text) => {
    const item = el("span");
    item.append(el("i", cls), text);
    legend.append(item);
  };
  key("key-line", "Your daily checks");
  if (context.length) key("key-line context", "Google's price history");
  if (band) key("key-band", "Typical range");
  bar.append(legend);

  const all = context.concat(checks);
  const span = daysBetween(all[0].date, all[all.length - 1].date);
  const chart = el("div", "chart");
  if (!chartRange[route.id]) chartRange[route.id] = "all";
  if (span > 20) {
    const ranges = el("div", "ranges");
    ranges.setAttribute("role", "group");
    ranges.setAttribute("aria-label", "Time range");
    for (const [value, text] of [["14", "2W"], ["30", "1M"], ["all", "All"]]) {
      const b = el("button", null, text);
      b.type = "button";
      b.setAttribute("aria-pressed", String(chartRange[route.id] === value));
      b.addEventListener("click", () => {
        chartRange[route.id] = value;
        ranges.querySelectorAll("button").forEach((other) => other.setAttribute("aria-pressed", String(other === b)));
        drawChart(chart, { checks, context, band, range: value });
      });
      ranges.append(b);
    }
    bar.append(ranges);
  }
  box.append(bar, chart);
  requestAnimationFrame(() => drawChart(chart, { checks, context, band, range: chartRange[route.id] }));
  return box;
}

function niceTicks(min, max, count) {
  if (min === max) { min -= 10; max += 10; }
  const rough = (max - min) / count;
  const power = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= rough);
  const ticks = [];
  for (let v = Math.floor(min / step) * step; v <= max + step * 0.001; v += step) ticks.push(Math.round(v));
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

function drawChart(container, { checks, context, band, range }) {
  const width = container.clientWidth;
  if (!width) return;
  const height = width < 520 ? 210 : 260;
  const pad = { top: 18, right: 64, bottom: 28, left: 52 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  // Keep only the chosen time range
  const all = context.concat(checks);
  const lastDate = all[all.length - 1].date;
  const startDate = range === "all" ? all[0].date : addDays(lastDate, -Number(range));
  const inView = (p) => p.date >= startDate;
  const ctx = context.filter(inView);
  const mine = checks.filter(inView);
  const shown = ctx.concat(mine);
  const firstDate = shown[0].date;
  const spanDays = Math.max(1, daysBetween(firstDate, lastDate));

  // Scales: zoom in on the prices. The typical band is included only when it is
  // close by; a very wide band just runs off the top or bottom of the chart.
  const prices = shown.map((p) => p.price);
  let lo = Math.min(...prices);
  let hi = Math.max(...prices);
  const reach = Math.max(hi - lo, hi * 0.1) * 0.5;
  if (band) {
    lo = Math.min(lo, Math.max(band[0], lo - reach));
    hi = Math.max(hi, Math.min(band[1], hi + reach));
  }
  const room = (hi - lo) * 0.08 || hi * 0.05;
  const ticks = niceTicks(lo - room, hi + room, 4);
  lo = ticks[0];
  hi = ticks[ticks.length - 1];
  const x = (date) => pad.left + (shown.length === 1 ? plotW / 2 : (daysBetween(firstDate, date) / spanDays) * plotW);
  const y = (v) => pad.top + plotH - ((v - lo) / (hi - lo)) * plotH;

  const chart = svg("svg", { viewBox: "0 0 " + width + " " + height, height, role: "img" });
  chart.setAttribute("aria-label", "Price chart from " + longDate(firstDate) + " to " + longDate(lastDate));

  // Typical-range band
  if (band) {
    const top = y(Math.min(band[1], hi));
    const bottom = y(Math.max(band[0], lo));
    if (bottom > top) svg("rect", { class: "band", x: pad.left, width: plotW, y: top, height: bottom - top }, chart);
  }

  // Gridlines and price labels
  const grid = svg("g", { class: "grid" }, chart);
  const axis = svg("g", { class: "axis" }, chart);
  for (const t of ticks) {
    svg("line", { x1: pad.left, x2: pad.left + plotW, y1: y(t), y2: y(t) }, grid);
    svg("text", { x: pad.left - 8, y: y(t) + 4, "text-anchor": "end" }, axis).textContent = money(t);
  }

  // Date labels spread evenly, never crowded
  const labelCount = Math.max(2, Math.min(6, Math.floor(plotW / 90)));
  const step = Math.max(1, Math.round(spanDays / (labelCount - 1)));
  for (let d = 0; d < spanDays - step * 0.5; d += step) {
    const date = addDays(firstDate, d);
    svg("text", { x: x(date), y: height - 6, "text-anchor": "middle" }, axis).textContent = shortDate(date);
  }
  svg("text", { x: x(lastDate), y: height - 6, "text-anchor": "middle" }, axis).textContent = shortDate(lastDate);

  const path = (points) => points.map((p, i) => (i ? "L" : "M") + x(p.date).toFixed(1) + "," + y(p.price).toFixed(1)).join(" ");

  // Google's history (grey), then your checks (blue)
  if (ctx.length > 1) svg("path", { class: "line context", d: path(ctx) }, chart);
  if (ctx.length && mine.length) {
    const sx = x(mine[0].date);
    svg("line", { class: "start-line", x1: sx, x2: sx, y1: pad.top, y2: pad.top + plotH }, chart);
    svg("text", { class: "note", x: sx - 6, y: pad.top + 10, "text-anchor": "end" }, chart).textContent = "Tracking started";
  }
  if (mine.length > 1) svg("path", { class: "line", d: path(mine) }, chart);
  if (mine.length <= 40) {
    for (const p of mine.slice(0, -1)) svg("circle", { class: "dot", cx: x(p.date), cy: y(p.price), r: 3.5 }, chart);
  }

  // ⭐ on the lowest of your checks (the first one, if it happened twice)
  if (mine.length > 1) {
    const low = mine.reduce((a, b) => (b.price < a.price ? b : a));
    svg("text", { class: "star", x: x(low.date), y: y(low.price) - 10, "text-anchor": "middle" }, chart).textContent = "⭐";
    if (low !== mine[mine.length - 1]) {
      svg("text", { class: "note", x: x(low.date), y: y(low.price) + 18, "text-anchor": "middle" }, chart).textContent = "Lowest " + money(low.price);
    }
  }

  // Latest point with its price
  const end = mine.length ? mine[mine.length - 1] : ctx[ctx.length - 1];
  svg("circle", { class: mine.length ? "dot" : "dot context", cx: x(end.date), cy: y(end.price), r: 5 }, chart);
  svg("text", { class: "end-label", x: x(end.date) + 10, y: y(end.price) + 4 }, chart).textContent = money(end.price);

  // Hover: a vertical line snaps to the nearest day and a tooltip lists the prices
  const crosshair = svg("line", { class: "crosshair", y1: pad.top, y2: pad.top + plotH, visibility: "hidden" }, chart);
  const hoverDot = svg("circle", { class: "dot", r: 5, visibility: "hidden" }, chart);
  const hit = svg("rect", { x: pad.left - 10, y: 0, width: plotW + 20, height, fill: "transparent" }, chart);
  const tooltip = el("div", "tooltip");
  tooltip.hidden = true;
  const byDate = new Map();
  for (const p of ctx) byDate.set(p.date, { date: p.date, google: p.price });
  for (const p of mine) byDate.set(p.date, Object.assign(byDate.get(p.date) || { date: p.date }, { mine: p.price, airline: p.airline, at: p.at }));
  const days = [...byDate.values()];

  const move = (event) => {
    const box = chart.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const near = days.reduce((a, b) => (Math.abs(x(b.date) - px) < Math.abs(x(a.date) - px) ? b : a));
    const cx = x(near.date);
    const value = near.mine != null ? near.mine : near.google;
    crosshair.setAttribute("x1", cx);
    crosshair.setAttribute("x2", cx);
    crosshair.setAttribute("visibility", "visible");
    hoverDot.setAttribute("cx", cx);
    hoverDot.setAttribute("cy", y(value));
    hoverDot.setAttribute("class", near.mine != null ? "dot" : "dot context");
    hoverDot.setAttribute("visibility", "visible");

    tooltip.replaceChildren(el("div", "t-date", longDate(near.date)));
    const row = (keyClass, label, price) => {
      const r = el("div", "t-row");
      r.append(el("i", keyClass), label, el("strong", null, money(price)));
      tooltip.append(r);
    };
    if (near.mine != null) row("key-line", ["Your check · " + checkTime(near.at), near.airline].filter(Boolean).join(" · "), near.mine);
    if (near.google != null) row("key-line context", "Google", near.google);
    tooltip.hidden = false;
    const left = Math.min(Math.max(cx - tooltip.offsetWidth / 2, 0), width - tooltip.offsetWidth);
    tooltip.style.left = left + "px";
    tooltip.style.top = Math.max(y(value) - tooltip.offsetHeight - 14, 0) + "px";
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", () => {
    crosshair.setAttribute("visibility", "hidden");
    hoverDot.setAttribute("visibility", "hidden");
    tooltip.hidden = true;
  });

  container.replaceChildren(chart, tooltip);
}

// Tiny trend line for the dates table
function sparkline(route) {
  const w = 100;
  const h = 28;
  let points = route.history.map((p) => ({ date: p.checked_on, price: p.price }));
  if (points.length < 2) {
    points = route.google_history.slice(-30).map(([date, price]) => ({ date, price })).concat(points);
  }
  const box = svg("svg", { viewBox: "0 0 " + w + " " + h, width: w, height: h, "aria-hidden": "true" });
  if (points.length < 2) return box;
  const values = points.map((p) => p.price);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x = (i) => 2 + (i / (points.length - 1)) * (w - 8);
  const y = (v) => (hi === lo ? h / 2 : 3 + (1 - (v - lo) / (hi - lo)) * (h - 6));
  svg("path", {
    d: points.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + "," + y(p.price).toFixed(1)).join(" "),
    fill: "none", stroke: "var(--series)", "stroke-width": 1.5, "stroke-linejoin": "round",
  }, box);
  svg("circle", { cx: x(points.length - 1), cy: y(values[values.length - 1]), r: 2.5, fill: "var(--series)" }, box);
  return box;
}

async function removeRoute(route) {
  const name = route.origin + " → " + route.destination + " (" + tripDates(route) + ")";
  if (!confirm("Stop tracking " + name + " and delete its price history?")) return;
  await api("/api/routes/" + route.id, { method: "DELETE" });
  showToast("Stopped tracking " + name);
  loadRoutes();
}

async function removeTrip(trip) {
  const first = trip.dates[0];
  const count = trip.dates.length;
  const name = first.origin + " → " + first.destination + (count > 1 ? " (all " + count + " dates)" : " (" + tripDates(first) + ")");
  if (!confirm("Delete " + name + " and its price history?")) return;
  for (const route of trip.dates) await api("/api/routes/" + route.id, { method: "DELETE" });
  showToast("Deleted " + name);
  loadRoutes();
}

// Redraw charts when the window width changes
let resizeTimer;
let lastWidth = window.innerWidth;
window.addEventListener("resize", () => {
  if (window.innerWidth === lastWidth) return; // phones fire resize when scrolling
  lastWidth = window.innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderTrips, 150);
});

// ---------------------------------------------------------------------------------
// Add-flight form
// ---------------------------------------------------------------------------------

const form = document.getElementById("add-form");
const formPanel = document.getElementById("form-panel");
const dateRows = document.getElementById("date-rows");
const addDateButton = document.getElementById("add-date");
const tripType = () => form.querySelector("input[name=trip_type]:checked").value;
let editing = null;       // the trip being edited, or null when adding a new one
let keptAirlines = null;  // when editing: the trip's airlines, until "Find airlines" is used

function openForm() {
  formPanel.hidden = false;
  document.getElementById("open-form").hidden = true;
  formPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  form.origin.focus({ preventScroll: true });
}

function closeForm() {
  formPanel.hidden = true;
  document.getElementById("open-form").hidden = false;
  resetForm();
}

// Edit: the same form, filled in with the trip's route, dates, airlines and note.
function openEditForm(trip) {
  resetForm();
  editing = trip;
  const first = trip.dates[0];
  form.origin.value = first.origin;
  form.destination.value = first.destination;
  showAirportHint(form.origin);
  showAirportHint(form.destination);
  form.querySelector("input[name=trip_type][value=" + first.trip_type + "]").checked = true;
  form.note.value = first.note || "";
  dateRows.replaceChildren();
  for (const route of trip.dates.filter((d) => d.active)) {
    const row = addDateRow();
    row.querySelector(".depart").value = route.depart_date;
    row.querySelector(".return").value = route.return_date || "";
  }
  updateDateRows();
  if (first.airline_codes.length) {
    keptAirlines = first.airline_codes.map((code, i) => ({ code, name: first.airlines[i] || code }));
    airlineHint.textContent = "Tracking only " + first.airlines.join(", ") +
      ". To change this, use Find airlines (uses 1 search).";
  }
  document.getElementById("form-title").textContent = "Edit trip";
  document.getElementById("add-button").textContent = "Save changes";
  document.getElementById("edit-dates-hint").hidden = false;
  openForm();
}

document.getElementById("open-form").addEventListener("click", openForm);
document.getElementById("close-form").addEventListener("click", closeForm);

// Airport suggestions: type a city or code, pick from the list
const airportList = document.getElementById("airport-list");
for (const [code, city, name] of AIRPORTS) {
  const option = document.createElement("option");
  option.value = code;
  option.label = city + " · " + name;
  airportList.append(option);
}

function normaliseAirport(input) {
  const text = input.value.trim();
  if (!text) return;
  const upper = text.toUpperCase();
  if (!AIRPORT_BY_CODE[upper] && text.length > 3) {
    // Typed a city name: use its first airport
    const lower = text.toLowerCase();
    const match = AIRPORTS.find(([, city]) => city.toLowerCase() === lower)
      || AIRPORTS.find(([, city]) => city.toLowerCase().startsWith(lower));
    if (match) input.value = match[0];
  } else {
    input.value = upper;
  }
}

function showAirportHint(input) {
  const hint = document.getElementById(input.name + "-hint");
  const airport = AIRPORT_BY_CODE[input.value.trim().toUpperCase()];
  hint.textContent = airport ? airport.city + " · " + airport.name : "";
}

for (const input of [form.origin, form.destination]) {
  input.addEventListener("change", () => { normaliseAirport(input); showAirportHint(input); });
  input.addEventListener("input", () => showAirportHint(input));
}

document.getElementById("swap").addEventListener("click", () => {
  [form.origin.value, form.destination.value] = [form.destination.value, form.origin.value];
  showAirportHint(form.origin);
  showAirportHint(form.destination);
  routeChanged();
});

// Date rows
function addDateRow() {
  const row = el("div", "date-row");
  const depart = el("label", "field", "Depart");
  const departInput = el("input", "depart");
  departInput.type = "date";
  departInput.required = true;
  depart.append(departInput);

  const back = el("label", "field return-field", "Return");
  const backInput = el("input", "return");
  backInput.type = "date";
  back.append(backInput);

  const remove = el("button", "ghost small-btn remove-date", "Remove");
  remove.type = "button";
  remove.addEventListener("click", () => {
    row.remove();
    updateDateRows();
  });

  row.append(depart, back, remove);
  dateRows.append(row);
  updateDateRows();
  return row;
}

function updateDateRows() {
  const roundTrip = tripType() === "round_trip";
  const today = state.today || "";
  const rows = [...dateRows.children];
  for (const row of rows) {
    const depart = row.querySelector(".depart");
    const back = row.querySelector(".return");
    row.querySelector(".return-field").hidden = !roundTrip;
    back.required = roundTrip;
    depart.min = today;
    back.min = depart.value || today;
    row.querySelector(".remove-date").hidden = rows.length === 1;
  }
  addDateButton.hidden = rows.length >= (state.max_dates || 5);
  updateBudget();
}

function updateBudget() {
  const rows = [...dateRows.children].map((row) => row.querySelector(".depart").value + "|" + row.querySelector(".return").value);
  let adding = rows.length;
  let tracking = state.routes.filter((r) => r.active).length;
  if (editing) {
    // Dates that stay the same don't need a new search.
    const mine = editing.dates.filter((d) => d.active);
    const first = mine[0];
    const sameRoute = first && form.origin.value.trim().toUpperCase() === first.origin &&
      form.destination.value.trim().toUpperCase() === first.destination && tripType() === first.trip_type;
    const kept = new Set(sameRoute ? mine.map((d) => d.depart_date + "|" + (tripType() === "round_trip" ? d.return_date || "" : "")) : []);
    adding = rows.filter((key) => !kept.has(key)).length;
    tracking -= mine.length - (rows.length - adding);
  }
  const perMonth = (tracking + adding) * 30;
  let text = "Uses " + adding + (adding === 1 ? " search" : " searches") + " now, then about " + perMonth +
    " a month for all " + (tracking + adding) + " tracked dates";
  if (usage && usage.left != null) text += " (" + usage.left + " left this month)";
  text += ".";
  if (usage && usage.per_month && perMonth > usage.per_month) {
    text += " ⚠️ That's more than your " + usage.per_month + " a month, so some checks would fail near the end of the month.";
  }
  document.getElementById("budget").textContent = text;
}

form.querySelectorAll("input[name=trip_type]").forEach((radio) => radio.addEventListener("change", () => {
  updateDateRows();
  routeChanged();
}));
dateRows.addEventListener("change", updateDateRows);
addDateButton.addEventListener("click", () => addDateRow().querySelector(".depart").focus());

function resetForm() {
  editing = null;
  keptAirlines = null;
  document.getElementById("form-title").textContent = "Track a flight";
  document.getElementById("add-button").textContent = "Start tracking";
  document.getElementById("edit-dates-hint").hidden = true;
  form.reset();
  dateRows.replaceChildren();
  addDateRow();
  clearAirlines();
  showAirportHint(form.origin);
  showAirportHint(form.destination);
  document.getElementById("form-error").textContent = "";
}

// Finding the airlines that fly a route
const findButton = document.getElementById("find-airlines");
const airlineHint = document.getElementById("airline-hint");
const airlineList = document.getElementById("airline-list");
const airlineBoxes = document.getElementById("airlines");
const selectAll = document.getElementById("select-all");
const DEFAULT_HINT = airlineHint.textContent;

function routeFromForm() {
  normaliseAirport(form.origin);
  normaliseAirport(form.destination);
  return {
    origin: form.origin.value,
    destination: form.destination.value,
    trip_type: tripType(),
    dates: [...dateRows.children].map((row) => ({
      depart_date: row.querySelector(".depart").value,
      return_date: row.querySelector(".return").value || null,
    })),
  };
}

findButton.addEventListener("click", async () => {
  const error = document.getElementById("form-error");
  error.textContent = "";
  findButton.disabled = true;
  findButton.textContent = "Finding airlines…";
  try {
    const route = routeFromForm();
    route.dates = route.dates.slice(0, 1);
    const { airlines } = await api("/api/airlines", { method: "POST", body: JSON.stringify(route) });
    showAirlines(airlines, route);
    loadUsage();
  } catch (err) {
    error.textContent = err.message;
  } finally {
    findButton.disabled = false;
    findButton.textContent = "Find airlines";
  }
});

function showAirlines(airlines, route) {
  airlineBoxes.replaceChildren();
  for (const airline of airlines) {
    const chip = el("label", "chip");
    const box = el("input");
    box.type = "checkbox";
    box.checked = true;
    box.value = airline.code;
    box.dataset.name = airline.name;
    chip.append(box, document.createTextNode(airline.name), el("span", "from", "from " + money(airline.price)));
    airlineBoxes.append(chip);
  }
  airlineHint.textContent = airlines.length + " airlines fly " + route.origin + " → " + route.destination + " on " +
    shortDate(route.dates[0].depart_date) + ". Untick any you don't want. With all ticked, any airline counts.";
  airlineList.hidden = false;
  findButton.hidden = true;
  updateSelectAll();
}

function clearAirlines(message) {
  airlineBoxes.replaceChildren();
  airlineList.hidden = true;
  findButton.hidden = false;
  airlineHint.textContent = message || DEFAULT_HINT;
}

function routeChanged() {
  if (!airlineList.hidden || keptAirlines) {
    keptAirlines = null;
    clearAirlines("The route changed, so find its airlines again (or skip to track any airline).");
  }
}
form.origin.addEventListener("change", routeChanged);
form.destination.addEventListener("change", routeChanged);

const airlineCheckboxes = () => [...airlineBoxes.querySelectorAll("input[type=checkbox]")];

function updateSelectAll() {
  const boxes = airlineCheckboxes();
  const ticked = boxes.filter((b) => b.checked).length;
  selectAll.checked = ticked === boxes.length;
  selectAll.indeterminate = ticked > 0 && ticked < boxes.length;
}

selectAll.addEventListener("change", () => {
  airlineCheckboxes().forEach((b) => (b.checked = selectAll.checked));
  updateSelectAll();
});
airlineBoxes.addEventListener("change", updateSelectAll);

// Which airlines to send: [] means any airline.
function chosenAirlines() {
  if (airlineList.hidden && keptAirlines) return keptAirlines;
  const boxes = airlineCheckboxes();
  const ticked = boxes.filter((b) => b.checked);
  if (ticked.length === boxes.length) return [];
  return ticked.map((b) => ({ code: b.value, name: b.dataset.name }));
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("form-error");
  const button = document.getElementById("add-button");
  if (!airlineList.hidden && !airlineCheckboxes().some((b) => b.checked)) {
    error.textContent = "Tick at least one airline, or tick “Select all”.";
    return;
  }
  const request = Object.assign(routeFromForm(), { airlines: chosenAirlines(), note: form.note.value });
  const wasEditing = editing;
  if (wasEditing) request.route_ids = wasEditing.dates.filter((d) => d.active).map((d) => d.id);
  error.textContent = "";
  button.disabled = true;
  button.textContent = wasEditing ? "Saving…"
    : request.dates.length > 1 ? "Checking " + request.dates.length + " prices…" : "Checking price…";
  try {
    const { routes } = await api(wasEditing ? "/api/routes/edit" : "/api/routes", { method: "POST", body: JSON.stringify(request) });
    if (wasEditing) {
      showToast("Saved changes to " + routes[0].origin + " → " + routes[0].destination);
      closeForm();
      await loadRoutes();
      loadUsage();
      return;
    }
    const found = routes.map(latest).filter(Boolean).map((p) => p.price);
    showToast("Now tracking " + routes[0].origin + " → " + routes[0].destination +
      (found.length ? " · from " + money(Math.min(...found)) : ""));
    delete selectedDate[tripKey(routes[0])]; // open the new trip on its cheapest date
    closeForm();
    await loadRoutes();
    loadUsage();
  } catch (err) {
    error.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = editing ? "Save changes" : "Start tracking";
  }
});

// ---------------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------------

addDateRow();
loadRoutes().then(() => {
  if (!state.routes.length) openForm();
  updateDateRows();
});
loadUsage();
