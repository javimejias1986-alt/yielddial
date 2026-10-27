(() => {
  "use strict";
  const STALE_HOURS = 36;
  const FILTERS = [["all", "All"], ["Treasury", "Treasuries"], ["Money-market fund", "Money-market funds"], ["DeFi lending", "DeFi (USDC)"], ["Exchange USDC rewards", "Exchange rewards"]];
  const state = { rows: [], key: "apy", dir: -1, filter: "all", data: null };
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (n) => (n == null ? "n/a" : n.toFixed(2) + "%");
  const ageHours = (iso) => (Date.now() - new Date(iso.length === 10 ? iso + "T23:59:59Z" : iso).getTime()) / 36e5;

  function fmtChecked(r) {
    const d = new Date(r.last_checked.length === 10 ? r.last_checked + "T12:00:00Z" : r.last_checked);
    const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    if (r.source_kind === "manual") return date;
    const h = ageHours(r.last_checked);
    return h < 1 ? "just now" : h < 48 ? `${Math.round(h)}h ago` : date;
  }
  function isStale(r) { return r.source_kind === "live" && ageHours(r.last_checked) > STALE_HOURS; }

  function render() {
    const rows = state.rows.filter((r) => state.filter === "all" || r.type === state.filter);
    const k = state.key;
    rows.sort((a, b) => {
      const x = a[k], y = b[k];
      if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * state.dir;
    });
    $("#tbody").innerHTML = rows.map((r) => {
      const sp = r.spread_vs_3m;
      const spCls = sp == null ? "" : sp > 0.005 ? "pos" : sp < -0.005 ? "neg" : "";
      const spTxt = sp == null ? "n/a" : (sp > 0 ? "+" : sp < 0 ? "−" : "") + Math.abs(sp).toFixed(2) + " pp";
      const aff = r.affiliate_url && r.affiliate_url !== "#" ? `<a href="${esc(r.affiliate_url)}" rel="sponsored noopener" data-placeholder="${r.affiliate_url === "#"}">Open account</a> · ` : "";
      const showAff = r.type === "Money-market fund" || r.type === "Exchange USDC rewards";
      return `<tr>
        <td class="opt" data-label="Option"><span class="name">${esc(r.name)}</span><span class="badge ${r.source_kind}">${r.source_kind}</span>${isStale(r) ? '<span class="badge stale">stale</span>' : ""}
          <div class="links">${showAff ? aff : ""}<a href="${esc(r.source_url)}" rel="noopener" target="_blank">Source</a></div></td>
        <td class="typ" data-label="Type">${esc(r.type)}</td>
        <td class="num apy" data-label="Yield" title="${esc(r.yield_basis)}">${pct(r.apy)}</td>
        <td class="num spr ${spCls}" data-label="vs 3-mo T-bill">${spTxt}</td>
        <td class="rsk" data-label="Risk"><div class="tags">${r.risk_tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div><div class="risk">${esc(r.risk_note)}</div>${r.source_note ? `<div class="risk"><em>${esc(r.source_note)}</em></div>` : ""}</td>
        <td class="chk sm" data-label="Checked" title="Data as of ${esc(r.as_of)}">${fmtChecked(r)}</td>
      </tr>`;
    }).join("") || '<tr><td colspan="6" class="loading">No rows.</td></tr>';
    document.querySelectorAll("th[data-sort]").forEach((th) => {
      const on = th.dataset.sort === k;
      th.classList.toggle("sorted", on);
      th.classList.toggle("asc", on && state.dir === 1);
      th.setAttribute("aria-sort", on ? (state.dir === 1 ? "ascending" : "descending") : "none");
    });
  }

  function renderStats(d) {
    const live = d.rows.filter((r) => r.source_kind === "live");
    const best = [...d.rows].sort((a, b) => b.apy - a.apy)[0];
    const bestSafe = d.rows.filter((r) => r.risk_tags.includes("government-backed")).sort((a, b) => b.apy - a.apy)[0];
    const defi = d.rows.filter((r) => r.type === "DeFi lending");
    const defiMax = defi.sort((a, b) => b.apy - a.apy)[0];
    $("#stats").innerHTML = [
      [pct(d.tbill_3m), "3-month T-bill"],
      bestSafe && [pct(bestSafe.apy), `Best government-backed (${bestSafe.name.replace("US Treasury ", "")})`],
      defiMax && [pct(defiMax.apy), `Top DeFi USDC (${defiMax.name.replace(/ USDC/, "")})`],
      [pct(best.apy), "Highest on the board, before risk"],
    ].filter(Boolean).map(([v, l]) => `<div class="stat"><b>${v}</b><span>${esc(l)}</span></div>`).join("");
  }

  function renderCurve(c) {
    if (!c) { $("#curve-chart").innerHTML = '<p class="muted">Curve unavailable.</p>'; return; }
    $("#curve-sub").textContent = `Par yields as of ${c.as_of}. Source: US Treasury daily yield curve.`;
    const W = 720, H = 260, L = 44, R = 18, T = 18, B = 34;
    const ys = c.points.map((p) => p.yield);
    const lo = Math.floor((Math.min(...ys) - 0.2) * 2) / 2, hi = Math.ceil((Math.max(...ys) + 0.2) * 2) / 2;
    const x = (i) => L + (i * (W - L - R)) / (c.points.length - 1);
    const y = (v) => T + ((hi - v) * (H - T - B)) / (hi - lo);
    let g = "";
    for (let v = lo; v <= hi + 1e-9; v += 0.5) g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="currentColor" opacity=".12"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="currentColor" opacity=".6">${v.toFixed(1)}%</text>`;
    const path = c.points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.yield).toFixed(1)}`).join("");
    const dots = c.points.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.yield)}" r="4" fill="var(--accent)"/><text x="${x(i)}" y="${y(p.yield) - 10}" text-anchor="middle" font-size="11" fill="currentColor">${p.yield.toFixed(2)}</text><text x="${x(i)}" y="${H - 10}" text-anchor="middle" font-size="11" fill="currentColor" opacity=".7">${p.label}</text>`).join("");
    $("#curve-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" style="color:var(--text)">${g}<path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>${dots}</svg>`;
  }

  function renderBanner(d) {
    const msgs = [];
    const gen = ageHours(d.generated_at);
    if (gen > STALE_HOURS) msgs.push(`Data was last refreshed ${Math.round(gen)} hours ago.`);
    const staleRows = d.rows.filter(isStale);
    if (staleRows.length) msgs.push(`${staleRows.length} live row${staleRows.length > 1 ? "s are" : " is"} older than ${STALE_HOURS} hours (${[...new Set(staleRows.map((r) => r.type))].join(", ")}).`);
    if (d.errors && d.errors.length) msgs.push("The last update had errors.");
    const b = $("#stale-banner");
    b.hidden = !msgs.length;
    if (msgs.length) b.textContent = "Stale data: " + msgs.join(" ") + " Check each source before acting.";
  }

  function init(d) {
    state.data = d; state.rows = d.rows;
    $("#filters").innerHTML = FILTERS.map(([v, l]) => `<button type="button" class="chip" data-f="${esc(v)}" aria-pressed="${v === "all"}">${l}</button>`).join("");
    $("#filters").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      state.filter = b.dataset.f;
      document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", c === b));
      render();
    });
    document.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.sort;
      state.dir = state.key === k ? -state.dir : (k === "apy" || k === "spread_vs_3m" || k === "last_checked" ? -1 : 1);
      state.key = k; render();
    }));
    document.addEventListener("click", (e) => {
      const a = e.target.closest('a[data-placeholder="true"]');
      if (a) e.preventDefault();  // affiliate links are placeholders until the owner signs up
    });
    renderBanner(d); renderStats(d); renderCurve(d.curve); render();
    $("#gen").textContent = "Last refreshed " + new Date(d.generated_at).toLocaleString() + ".";
  }

  fetch("data/rates.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(init)
    .catch((err) => {
      $("#tbody").innerHTML = `<tr><td colspan="6" class="loading">Couldn't load rates (${esc(err.message)}). If you opened this file directly, serve it over HTTP instead.</td></tr>`;
    });
})();
