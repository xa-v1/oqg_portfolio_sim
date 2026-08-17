(async function () {
  const container = document.getElementById("leaderboard-container");
  const noticeContainer = document.getElementById("notice-container");

  let data;
  try {
    data = await fetchJSON("data/index.json");
  } catch (err) {
    container.replaceChildren(el("p", {
      className: "empty-state",
      text: "Track record data hasn't been published yet. Check back after the first daily run.",
    }));
    return;
  }

  renderNoticePanel(noticeContainer, data.gaps || [], data.errors || []);

  if (!data.strategies || data.strategies.length === 0) {
    container.replaceChildren(el("p", {
      className: "empty-state",
      text: "No strategies are live yet.",
    }));
    return;
  }

  const table = el("table", { className: "leaderboard" });
  const thead = el("thead");
  const headRow = el("tr");
  for (const label of ["Strategy", "Asset class", "Capital base", "Cumulative P&L", "Net return", "Last run"]) {
    headRow.append(el("th", { text: label }));
  }
  thead.append(headRow);
  table.append(thead);

  const tbody = el("tbody");
  for (const strategy of data.strategies) {
    const row = el("tr");

    const nameCell = el("td");
    const link = el("a", {
      text: strategy.name,
      attrs: { href: `strategy.html?id=${encodeURIComponent(strategy.strategy_id)}` },
    });
    nameCell.append(link);
    if (strategy.description) {
      nameCell.append(el("div", { className: "strategy-desc", text: strategy.description }));
    }
    if (!strategy.active) {
      nameCell.append(el("div", { className: "strategy-desc", text: "(inactive)" }));
    }
    row.append(nameCell);

    row.append(el("td", { text: strategy.asset_class || "—" }));
    row.append(el("td", { className: "num", text: formatMoney(strategy.capital_base) }));

    const pnlCell = el("td", {
      className: `num ${strategy.cum_net_pnl >= 0 ? "pct-good" : "pct-bad"}`,
      text: formatMoney(strategy.cum_net_pnl, { signed: true }),
    });
    row.append(pnlCell);

    const retCell = el("td", {
      className: `num ${strategy.net_return_pct >= 0 ? "pct-good" : "pct-bad"}`,
      text: formatPct(strategy.net_return_pct, { signed: true }),
    });
    row.append(retCell);

    row.append(el("td", { text: strategy.last_date || "—" }));

    tbody.append(row);
  }
  table.append(tbody);

  container.replaceChildren(table);
})();
