// Shared helpers for index.html and strategy.html. No framework, no build
// step -- this is meant to keep working as plain static files forever.

async function fetchJSON(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`failed to load ${path}: HTTP ${response.status}`);
  }
  return response.json();
}

function formatMoney(value, { signed = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = signed && value > 0 ? "+" : "";
  return sign + value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

function formatPct(value, { signed = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

// Builds a DOM element with textContent (never innerHTML) for anything that
// could contain untrusted data -- strategy names/descriptions/reasons all
// come from the ledger, not from us.
function el(tag, { className, text, attrs } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      node.setAttribute(key, value);
    }
  }
  return node;
}

function lowConfidenceBadge() {
  const badge = el("span", { className: "badge badge-low-confidence" });
  badge.append("⚠ low confidence"); // warning triangle, plain text -- never color alone
  return badge;
}

function renderNoticePanel(container, gaps, errors) {
  container.replaceChildren();
  if (gaps.length === 0 && errors.length === 0) {
    container.append(el("p", {
      className: "notice-empty",
      text: "No missed sessions or run failures recorded.",
    }));
    return;
  }

  const panel = el("div", { className: "notice-panel" });
  panel.append(el("h2", { text: "Gaps & failed runs" }));
  const list = el("ul");

  for (const date of errors) {
    list.append(el("li", { text: `${date} — run failed (see ledger notes)` }));
  }
  for (const date of gaps) {
    list.append(el("li", { text: `${date} — no run recorded (missed session)` }));
  }

  panel.append(list);
  container.append(panel);
}

function renderStatTiles(container, stats) {
  container.replaceChildren();
  const tiles = [
    { label: "Net return", value: formatPct(stats.net_return_pct, { signed: true }) },
    { label: "Sharpe-like", value: formatNumber(stats.sharpe_like) },
    { label: "Max drawdown", value: `${formatMoney(stats.max_drawdown_dollars)} (${formatPct(stats.max_drawdown_pct)})` },
    { label: "Win rate (daily)", value: formatPct((stats.win_rate ?? 0) * 100) },
    { label: "Days tracked", value: String(stats.days_tracked) },
  ];
  for (const tile of tiles) {
    const node = el("div", { className: "stat-tile" });
    node.append(el("div", { className: "label", text: tile.label }));
    node.append(el("div", { className: "value", text: tile.value }));
    container.append(node);
  }
}
