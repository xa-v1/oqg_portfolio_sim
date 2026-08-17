(async function () {
  const params = new URLSearchParams(window.location.search);
  const strategyId = params.get("id");
  const header = document.getElementById("strategy-header");

  if (!strategyId) {
    header.replaceChildren(el("h1", { text: "No strategy specified" }));
    return;
  }

  let data;
  try {
    data = await fetchJSON(`data/${encodeURIComponent(strategyId)}.json`);
  } catch (err) {
    header.replaceChildren(el("h1", { text: "Strategy not found" }));
    return;
  }

  document.title = `${data.name} — OQG Portfolio Simulator`;
  header.replaceChildren(
    el("h1", { text: data.name }),
    el("p", { text: data.description || "" }),
  );

  renderStatTiles(document.getElementById("stat-row"), data.stats);
  renderNoticePanel(document.getElementById("notice-container"), data.gaps || [], data.errors || []);
  renderEquityChart(data.equity_curve || [], data.gaps || []);
  renderTradeTable(data.trades || []);

  function renderEquityChart(equityCurve, gaps) {
    const canvas = document.getElementById("equity-chart");
    if (equityCurve.length === 0) {
      canvas.replaceWith(el("p", {
        className: "empty-state",
        text: "No completed, reconciled runs yet for this strategy.",
      }));
      return;
    }

    // Merge real equity dates with known gap dates, chronologically, so a
    // missing session renders as an actual break in the line (null point)
    // rather than silently connecting across it.
    const byDate = new Map(equityCurve.map((row) => [row.date, row]));
    const allDates = Array.from(new Set([...byDate.keys(), ...gaps])).sort();

    const labels = allDates;
    const values = allDates.map((date) => {
      const row = byDate.get(date);
      return row ? row.cum_net_pnl : null;
    });
    const detail = allDates.map((date) => byDate.get(date) || null);

    const styles = getComputedStyle(document.body);
    const seriesColor = styles.getPropertyValue("--series-1").trim();
    const seriesWash = styles.getPropertyValue("--series-1-wash").trim();
    const gridColor = styles.getPropertyValue("--gridline").trim();
    const textMuted = styles.getPropertyValue("--text-muted").trim();
    const surface = styles.getPropertyValue("--surface").trim();
    const textPrimary = styles.getPropertyValue("--text-primary").trim();

    new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Cumulative net P&L",
          data: values,
          borderColor: seriesColor,
          backgroundColor: seriesWash,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHitRadius: 12,
          fill: true,
          tension: 0,
          spanGaps: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false }, // single series -- the title already names it
          tooltip: {
            backgroundColor: surface,
            borderColor: gridColor,
            borderWidth: 1,
            titleColor: textPrimary,
            bodyColor: textPrimary,
            padding: 10,
            callbacks: {
              label(ctx) {
                const row = detail[ctx.dataIndex];
                if (!row) return "No run recorded (gap)";
                return [
                  `Cumulative: ${formatMoney(row.cum_net_pnl, { signed: true })}`,
                  `Day net: ${formatMoney(row.net_pnl, { signed: true })}`,
                  `Margin used: ${formatMoney(row.margin_used)}`,
                ];
              },
            },
          },
        },
        scales: {
          x: {
            grid: { color: gridColor },
            ticks: { color: textMuted, maxTicksLimit: 8 },
          },
          y: {
            grid: { color: gridColor },
            ticks: {
              color: textMuted,
              callback: (v) => formatMoney(v),
            },
          },
        },
      },
    });
  }

  function renderTradeTable(trades) {
    const tbody = document.getElementById("trades-body");
    if (trades.length === 0) {
      const emptyRow = el("tr");
      emptyRow.append(el("td", { attrs: { colspan: "8" }, className: "empty-state", text: "No trades yet." }));
      tbody.replaceChildren(emptyRow);
      return;
    }

    const rows = trades.map((trade) => {
      const row = el("tr");
      row.append(el("td", { text: trade.date }));
      row.append(el("td", { text: trade.instrument_id }));
      row.append(el("td", { className: "num", text: formatNumber(trade.qty, 2) }));
      row.append(el("td", { className: "num", text: formatNumber(trade.fill_price, 4) }));
      row.append(el("td", { className: "num", text: formatMoney(trade.explicit_cost) }));
      row.append(el("td", { className: "num", text: formatMoney(trade.implicit_cost) }));

      const confCell = el("td");
      if (trade.fill_confidence === "low") {
        confCell.append(lowConfidenceBadge());
      } else {
        confCell.append(el("span", { text: "normal" }));
      }
      row.append(confCell);

      row.append(el("td", { text: trade.reason || "" }));
      return row;
    });

    tbody.replaceChildren(...rows);
  }
})();
