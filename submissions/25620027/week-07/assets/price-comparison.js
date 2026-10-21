(() => {
  "use strict";
  const rows = document.getElementById("comparison-rows");
  const status = document.getElementById("comparison-status");
  const updated = document.getElementById("comparison-updated");
  const headline = document.getElementById("comparison-headline");
  const highlights = document.getElementById("comparison-highlights");
  if (!rows || !status || !updated) return;
  const items = ["lamp", "chair", "bookshelf"];
  const defaults = [110, 198, 286];
  const maxAge = 900000;
  let payload = null;
  let failed = false;
  let pending = false;
  let expiryTimer;
  const positive = value => Number.isSafeInteger(value) && value > 0;
  const stamp = value => typeof value === "string" ? Date.parse(value) : NaN;
  const time = value => Number.isFinite(stamp(value))
    ? new Intl.DateTimeFormat("en-GB", {timeZone: "Asia/Seoul", month: "short",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
      hourCycle: "h23"}).format(new Date(value)) + " KST" : "Unavailable";
  function element(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = String(text);
    if (className) result.className = className;
    return result;
  }
  function safeLink(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }
  function observed(quote, now) {
    const age = now - stamp(quote?.observed_at);
    return positive(quote?.ask) && !quote.error && age >= 0 && age < maxAge;
  }
  function render() {
    clearTimeout(expiryTimer);
    const now = Date.now();
    const snapshot = payload?.snapshot;
    const age = now - stamp(snapshot?.observed_at);
    const expiry = Math.min(stamp(snapshot?.expires_at), stamp(snapshot?.observed_at) + maxAge);
    const fresh = !failed && payload?.enabled === true && payload?.fresh === true
      && snapshot?.price_kind === "observed_initial_ask"
      && age >= 0 && age < maxAge && now < expiry;
    const shops = Array.isArray(snapshot?.shops) ? snapshot.shops.slice(0, 5) : [];
    rows.replaceChildren();
    if (highlights) highlights.replaceChildren();
    const relations = [];
    status.textContent = failed ? "Price feed unavailable. Comparisons are paused."
      : payload?.enabled === false ? "Price monitoring is disabled. No comparison is available."
      : fresh ? "Recent observations available — comparisons use observed shops only."
      : snapshot ? "Observations expired or unverified. No current price comparison."
      : "Waiting for price observations. No price comparison is available yet.";
    updated.textContent = "Snapshot: " + time(snapshot?.observed_at)
      + (snapshot ? " · expires " + (Number.isFinite(expiry) ? time(new Date(expiry).toISOString()) : "Unavailable") : "");
    const ownRow = element("tr", undefined, "comparison-own");
    const ownName = element("th", "SungjinShop (ours)");
    ownName.scope = "row";
    ownRow.append(ownName, element("td", "—"));
    for (const [index, item] of items.entries()) {
      const own = Array.isArray(payload?.own) ? payload.own.find(row => row?.item === item) : null;
      const baseline = positive(own?.baseline_ceiling) ? own.baseline_ceiling : defaults[index];
      const cap = fresh && positive(own?.ceiling) ? own.ceiling : baseline;
      const cell = element("td");
      cell.append(element("span", "≤ " + cap, "comparison-value"));
      const uniqueShops = shops.filter((shop, index) => shop && typeof shop.name === "string"
        && shops.findIndex(peer => peer?.name === shop.name) === index);
      const asks = uniqueShops.map(shop => (Array.isArray(shop?.quotes) ? shop.quotes : [])
        .find(quote => quote?.item === item)).filter(quote => observed(quote, now)).map(quote => quote.ask);
      let detail = "Baseline opening cap; comparison unavailable";
      let summary = "Comparison unavailable";
      if (fresh && own && asks.length > 0 && own.observed_shop_count === asks.length
          && own.lowest_peer_ask === Math.min(...asks) && positive(own.ceiling)) {
        const relation = cap < own.lowest_peer_ask ? "Lower" : cap === own.lowest_peer_ask ? "Equal" : "Higher";
        detail = relation + (relation === "Equal" ? " to" : " than") + " lowest observed ask · " + asks.length + " of 5 peer shops checked";
        relations.push(relation);
        summary = (relation === "Lower" ? "단독 최저 · below observed first asks"
          : relation === "Equal" ? "공동최저 · tied with lowest observed first ask"
          : "Above lowest observed first ask")
          + " · lowest observed first ask " + own.lowest_peer_ask
          + " · observed " + asks.length + " other active shops (top 5)";
      } else if (fresh) detail = "New opening cap; comparison unavailable";
      if (highlights) {
        const highlight = element("div", undefined, "comparison-highlight");
        highlight.append(element("strong", item[0].toUpperCase() + item.slice(1)),
          element("span", "≤ " + cap, "comparison-value"),
          element("span", summary, "comparison-detail"));
        highlights.append(highlight);
      }
      cell.append(element("span", detail, "comparison-detail"));
      ownRow.append(cell);
    }
    if (headline) headline.textContent = relations.length !== items.length
      ? "Opening caps · comparison unavailable for one or more items"
      : relations.every(relation => relation === "Lower")
        ? "Opening caps below observed first asks"
        : relations.every(relation => relation !== "Higher")
          ? "Opening caps lower than or tied with observed first asks"
          : "Compare our opening caps with observed first asks";
    ownRow.append(element("td", fresh ? "Current opening ceilings" : "Baseline caps; no live comparison", "comparison-time"));
    rows.append(ownRow);
    for (let index = 0; index < 5; index++) {
      const shop = shops[index];
      const row = element("tr", undefined, fresh ? "" : "comparison-stale");
      const name = element("th");
      name.scope = "row";
      const label = typeof shop?.name === "string" ? shop.name.slice(0, 160) : "Peer slot " + (index + 1) + " · not returned";
      const href = safeLink(shop?.url);
      if (href) {
        const link = element("a", label);
        link.href = href;
        link.rel = "noopener noreferrer";
        name.append(link);
      } else name.textContent = label;
      row.append(name, element("td", positive(shop?.rank) ? shop.rank : "—"));
      const checked = element("td", undefined, "comparison-time");
      for (const item of items) {
        const quote = (Array.isArray(shop?.quotes) ? shop.quotes : []).find(value => value?.item === item);
        const valid = observed(quote, now);
        const cell = element("td");
        const hasAsk = positive(quote?.ask) && !quote.error;
        cell.append(element("span", hasAsk ? quote.ask : "—", "comparison-value"));
        cell.append(element("span", !hasAsk ? "Unavailable" : fresh && valid ? "Observed first ask" : "Expired / unverified", "comparison-detail"));
        if (!fresh || !valid) cell.className = "comparison-stale";
        row.append(cell);
        checked.append(element("div", item + ": " + time(quote?.observed_at)));
      }
      row.append(checked);
      rows.append(row);
    }
    if (fresh) {
      // Expire claims even if a later network request hangs or the tab resumes late.
      const quoteExpiries = shops.flatMap(shop => Array.isArray(shop?.quotes) ? shop.quotes : [])
        .map(quote => stamp(quote?.observed_at) + maxAge).filter(value => value > now);
      expiryTimer = setTimeout(render, Math.max(1, Math.min(expiry, ...quoteExpiries) - now));
    }
  }
  async function refresh() {
    render();
    if (pending) return;
    pending = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/price-comparison.json", {
        method: "GET", credentials: "same-origin", cache: "no-store", signal: controller.signal
      });
      if (!response.ok) throw new Error("Price feed unavailable");
      const next = await response.json();
      if (!next || typeof next !== "object" || Array.isArray(next)) throw new Error("Invalid feed");
      payload = next;
      failed = false;
    } catch { failed = true; }
    finally {
      clearTimeout(timeout);
      pending = false;
      render();
    }
  }
  render();
  void refresh();
  setInterval(() => { void refresh(); }, 60000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refresh();
  });
})();
