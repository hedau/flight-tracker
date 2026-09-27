// Dashboard: loads your routes, draws a price chart for each, and handles the form.

const SVG_NS = "http://www.w3.org/2000/svg";
const CHART_HEIGHT = 220;
const PAD = { top: 16, right: 64, bottom: 26, left: 52 };

const money = (n) => "$" + Math.round(n).toLocaleString("en-US");
const shortDate = (iso) =>
  new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
const longDate = (iso) =>
  new Date(iso + "T12:00:00").toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric", year: "numeric",
  });

let state = { routes: [], today: null };

// --- Talking to the server --------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json" },
  });
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("Please log in.");
  }
  const data = await response.json();
  if (data.error && !response.ok) throw new Error(data.error);
  return data;
}

async function loadRoutes() {
  const data = await api("/api/routes");
  state = data;
  document.getElementById("demo-banner").hidden = !data.demo;
  document.getElementById("logout").hidden = !data.login_required;
  renderRoutes();
}

async function loadUsage() {
  try {
    const { usage } = await api("/api/usage");
    if (usage && usage.left != null) {
      document.getElementById("usage").textContent =
        usage.left + " of " + usage.per_month + " searches left this month";
    }
  } catch (err) {
    // Not important enough to show an error for.
  }
}

// --- Add-route form -----------------------------------------------------------------

const form = document.getElementById("add-form");
const tripType = document.getElementById("trip-type");
const returnField = document.getElementById("return-field");

tripType.addEventListener("change", () => {
  const roundTrip = tripType.value === "round_trip";
  returnField.hidden = !roundTrip;
  form.return_date.required = roundTrip;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("form-error");
  const button = document.getElementById("add-button");
  error.textContent = "";
  button.disabled = true;
  button.textContent = "Checking price…";
  try {
    await api("/api/routes", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    form.reset();
    tripType.dispatchEvent(new Event("change"));
    await loadRoutes();
    loadUsage();
  } catch (err) {
    error.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = "Start tracking";
  }
});

async function removeRoute(route) {
  const name = route.origin + " → " + route.destination;
  if (!confirm("Stop tracking " + name + " and delete its price history?")) return;
  await api("/api/routes/" + route.id, { method: "DELETE" });
  loadRoutes();
}

// --- Route cards ----------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function renderRoutes() {
  const container = document.getElementById("routes");
  container.replaceChildren();
  if (!state.routes.length) {
    container.append(el("p", "muted", "No flights tracked yet. Add one above to start."));
    return;
  }
  for (const route of state.routes) container.append(routeCard(route));
}

function routeCard(route) {
  const card = el("section", "panel");

  const head = el("div", "route-head");
  const title = el("div");
  const heading = el("h2", null, route.origin + " → " + route.destination);
  heading.append(el("span", "tag", route.trip_type === "round_trip" ? "Round trip" : "One-way"));
  if (!route.active) heading.append(el("span", "tag", "Finished"));
  title.append(heading);
  let dates = "Departs " + longDate(route.depart_date);
  if (route.return_date) dates += " · Returns " + longDate(route.return_date);
  title.append(el("div", "muted small", dates));
  head.append(title);
  const remove = el("button", "link", "Remove");
  remove.addEventListener("click", () => removeRoute(route));
  head.append(remove);
  card.append(head);

  if (route.last_error) {
    card.append(el("p", "error", "Last check failed: " + route.last_error));
  }

  const points = route.history;
  if (!points.length) {
    card.append(el("div", "empty-chart", "No prices yet. The first one arrives at the next morning check."));
    return card;
  }

  card.append(statTiles(points));
  const chart = el("div", "chart");
  card.append(chart);
  // Draw after the card is on the page, so we know how wide it is.
  requestAnimationFrame(() => drawChart(chart, points));
  card.append(priceTable(points));
  return card;
}

function statTiles(points) {
  const latest = points[points.length - 1];
  const lowest = points.reduce((a, b) => (b.price < a.price ? b : a));
  const highest = points.reduce((a, b) => (b.price > a.price ? b : a));
  const first = points[0];

  const stats = el("div", "stats");
  const tile = (label, value, sub, subClass) => {
    const box = el("div", "stat");
    box.append(el("div", "label", label), el("div", "value", value));
    if (sub) box.append(el("div", "sub " + (subClass || ""), sub));
    stats.append(box);
  };

  tile("Latest price", money(latest.price), shortDate(latest.checked_on) + (latest.airline ? " · " + latest.airline : ""));
  tile("Lowest seen", money(lowest.price), shortDate(lowest.checked_on));
  tile("Highest seen", money(highest.price), shortDate(highest.checked_on));
  if (points.length > 1) {
    const change = latest.price - first.price;
    const arrow = change < 0 ? "▼ " : change > 0 ? "▲ " : "";
    const text = arrow + (change === 0 ? "No change" : money(Math.abs(change)));
    const box = el("div", "stat");
    box.append(el("div", "label", "Since first check"));
    box.append(el("div", "value " + (change < 0 ? "down" : change > 0 ? "up" : ""), text));
    box.append(el("div", "sub", change < 0 ? "Cheaper" : change > 0 ? "More expensive" : "Same price"));
    stats.append(box);
  }
  return stats;
}

function priceTable(points) {
  const details = el("details");
  details.append(el("summary", null, "Show all prices (" + points.length + ")"));
  const table = el("table");
  const header = el("tr");
  ["Date", "Price", "Cheapest airline"].forEach((h) => header.append(el("th", null, h)));
  table.append(header);
  for (const p of [...points].reverse()) {
    const row = el("tr");
    row.append(el("td", null, longDate(p.checked_on)), el("td", null, money(p.price)), el("td", null, p.airline || "—"));
    table.append(row);
  }
  details.append(table);
  return details;
}

// --- Price chart (one line per route, drawn with SVG) ---------------------------------

function svg(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.append(node);
  return node;
}

function niceTicks(min, max, count) {
  if (min === max) { min -= 50; max += 50; }
  const rough = (max - min) / count;
  const power = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough);
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step / 2; v += step) ticks.push(v);
  return ticks;
}

function drawChart(container, points) {
  const width = container.clientWidth;
  const chart = svg("svg", { viewBox: "0 0 " + width + " " + CHART_HEIGHT, role: "img" });
  chart.setAttribute("aria-label", "Price over time, " + points.length + " checks");
  const plotW = width - PAD.left - PAD.right;
  const plotH = CHART_HEIGHT - PAD.top - PAD.bottom;

  const values = points.map((p) => p.price);
  const ticks = niceTicks(Math.min(...values), Math.max(...values), 4);
  const yMin = ticks[0];
  const yMax = ticks[ticks.length - 1];
  const day = (iso) => new Date(iso + "T12:00:00").getTime();
  const firstDay = day(points[0].checked_on);
  const lastDay = day(points[points.length - 1].checked_on);
  const x = (p) => PAD.left + (lastDay === firstDay ? plotW / 2 : ((day(p.checked_on) - firstDay) / (lastDay - firstDay)) * plotW);
  const y = (v) => PAD.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  // Gridlines and price labels
  const grid = svg("g", { class: "grid" }, chart);
  const axis = svg("g", { class: "axis" }, chart);
  for (const t of ticks) {
    svg("line", { x1: PAD.left, x2: PAD.left + plotW, y1: y(t), y2: y(t) }, grid);
    svg("text", { x: PAD.left - 8, y: y(t) + 4, "text-anchor": "end" }, axis).textContent = money(t);
  }
  // Date labels: first, last, and a middle one if there's room
  const dateLabels = [points[0], points[points.length - 1]];
  if (points.length > 2 && plotW > 300) dateLabels.splice(1, 0, points[Math.floor(points.length / 2)]);
  for (const p of new Set(dateLabels)) {
    svg("text", { x: x(p), y: CHART_HEIGHT - 6, "text-anchor": "middle" }, axis).textContent = shortDate(p.checked_on);
  }

  // The price line and the last point
  svg("path", { class: "line", d: points.map((p, i) => (i ? "L" : "M") + x(p) + "," + y(p.price)).join(" ") }, chart);
  const last = points[points.length - 1];
  svg("circle", { class: "dot", cx: x(last), cy: y(last.price), r: 4.5 }, chart);
  svg("text", { class: "end-label", x: x(last) + 10, y: y(last.price) + 4 }, chart).textContent = money(last.price);

  // Hover: a vertical line snaps to the nearest day and a tooltip shows the price
  const crosshair = svg("line", { class: "crosshair", y1: PAD.top, y2: PAD.top + plotH, visibility: "hidden" }, chart);
  const hoverDot = svg("circle", { class: "dot", r: 4.5, visibility: "hidden" }, chart);
  const tooltip = el("div", "tooltip");
  const hitArea = svg("rect", { x: PAD.left - 10, y: 0, width: plotW + 20, height: CHART_HEIGHT, fill: "transparent" }, chart);

  hitArea.addEventListener("pointermove", (event) => {
    const box = chart.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const nearest = points.reduce((a, b) => (Math.abs(x(b) - px) < Math.abs(x(a) - px) ? b : a));
    const cx = x(nearest);
    const cy = y(nearest.price);
    crosshair.setAttribute("x1", cx);
    crosshair.setAttribute("x2", cx);
    crosshair.setAttribute("visibility", "visible");
    hoverDot.setAttribute("cx", cx);
    hoverDot.setAttribute("cy", cy);
    hoverDot.setAttribute("visibility", "visible");
    tooltip.replaceChildren(
      el("div", "muted small", longDate(nearest.checked_on)),
      el("strong", null, money(nearest.price)),
      el("span", "muted small", nearest.airline ? " · " + nearest.airline : ""),
    );
    tooltip.style.display = "block";
    const left = Math.min(Math.max(cx - tooltip.offsetWidth / 2, 0), width - tooltip.offsetWidth);
    tooltip.style.left = left + "px";
    tooltip.style.top = Math.max(cy - tooltip.offsetHeight - 12, 0) + "px";
  });
  hitArea.addEventListener("pointerleave", () => {
    crosshair.setAttribute("visibility", "hidden");
    hoverDot.setAttribute("visibility", "hidden");
    tooltip.style.display = "none";
  });

  container.replaceChildren(chart, tooltip);
}

// Redraw charts when the window size changes
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderRoutes, 150);
});

loadRoutes();
loadUsage();
