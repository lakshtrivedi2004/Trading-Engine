// Renders every <canvas data-chart='{...}'> with Chart.js (bundled locally, works offline).
(function () {
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--muted").trim() || "#5E6B73";
  const grid = css.getPropertyValue("--rule").trim() || "#D9DEDD";
  const text = css.getPropertyValue("--ink").trim() || "#16212B";
  const col = function (c) { return Array.isArray(c) ? c.map(col) : (c === "@ink" ? text : c); };
  Chart.defaults.color = ink;
  Chart.defaults.borderColor = grid;
  Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

  function fmt(v, unit) {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    if (unit === "pct") return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
    if (unit === "pct0") return v.toFixed(0) + "%";
    if (unit === "crore") return (v >= 0 ? "+" : "") + Math.round(v).toLocaleString("en-IN");
    return Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-IN") : v.toFixed(2);
  }

  document.querySelectorAll("canvas[data-chart]").forEach(function (el) {
    const spec = JSON.parse(el.dataset.chart);
    const horizontal = spec.type === "hbar";
    const scales = {
      x: { ticks: { maxTicksLimit: horizontal ? 8 : 10, autoSkip: true, maxRotation: 0 },
           title: { display: !!spec.xTitle, text: spec.xTitle || "" }, grid: { display: horizontal } },
      y: { title: { display: !!spec.yTitle, text: spec.yTitle || "" },
           min: spec.yMin, max: spec.yMax, ticks: { callback: horizontal ? undefined : function (v) { return fmt(v, spec.unit); } } },
    };
    if (horizontal) scales.x.ticks.callback = function (v) { return fmt(v, spec.unit); };
    if (spec.y2) {
      scales.y2 = { position: "right", grid: { display: false }, title: { display: true, text: spec.y2 } };
    }
    new Chart(el, {
      type: horizontal ? "bar" : spec.type,
      data: {
        labels: spec.labels,
        datasets: spec.datasets.map(function (d) {
          const c = col(d.color);
          const ds = Object.assign({ pointRadius: 0, borderWidth: spec.type === "line" ? 1.8 : 0, tension: 0.15,
                                     spanGaps: true, borderRadius: spec.type === "line" ? 0 : 6 }, d,
                                   { backgroundColor: c, borderColor: c });
          if (d.fill && typeof c === "string") {        // soft neon gradient under a line
            const ctx = el.getContext("2d"), g = ctx.createLinearGradient(0, 0, 0, el.parentElement.clientHeight || 300);
            g.addColorStop(0, c + "55"); g.addColorStop(1, c + "00");
            ds.backgroundColor = g; ds.fill = "origin";
          }
          return ds;
        }),
      },
      options: {
        indexAxis: horizontal ? "y" : "x",
        animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: spec.datasets.length > 1, position: "bottom", labels: { boxWidth: 12 } },
          title: { display: !!spec.title, text: spec.title || "", align: "start", font: { weight: "600" } },
          tooltip: { callbacks: { label: function (c) {
            const v = horizontal ? c.parsed.x : c.parsed.y;
            return (c.dataset.label ? c.dataset.label + ": " : "") + fmt(v, c.dataset.yAxisID === "y2" ? "" : spec.unit);
          } } },
        },
        scales: scales,
      },
    });
  });

  // auto-submit selection forms
  document.querySelectorAll("form.autosubmit select, form.autosubmit input[type=range]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });
  document.querySelectorAll("input[type=range][data-out]").forEach(function (el) {
    const out = document.getElementById(el.dataset.out);
    const show = function () { out.textContent = el.value; };
    el.addEventListener("input", show); show();
  });
})();
