// Local test of the Worker shop: run with `node test/shop.test.mjs` (no Cloudflare needed).
import assert from "node:assert/strict";
import { generateKeyPairSync, createPublicKey, verify } from "node:crypto";
import worker, { Shop, marketShares, detectSurge, summarize, parsePrice, shouldEnd } from "../src/index.js";

const { privateKey, publicKey } = generateKeyPairSync("ed25519");
const mem = new Map();
const storage = {
  get: async (k) => structuredClone(mem.get(k)),
  put: async (k, v) => { if (typeof k === "object") for (const [a, b] of Object.entries(k)) mem.set(a, b); else mem.set(k, structuredClone(v)); },
  delete: async (k) => { for (const x of [].concat(k)) mem.delete(x); },
};
const env = { BASE_ASK_MARKUP: "1", HIGH_ASK_MARKUP: "1.05", SHOP_NAME: "ggari0703", SHOP_KEY: privateKey.export({ type: "pkcs8", format: "pem" }), ADMIN_TOKEN: "t0k" };
const shop = new Shop({ storage }, env);
shop.rand = () => 0.99;
shop.storage.bypass = true;                               // most tests poke storage directly; the cache has its own test                                   // learned counter: no exploration unless a test says so
env.SHOP = { idFromName: () => "x", get: () => ({ fetch: (req, init) => shop.fetch(req instanceof Request ? req : new Request(req, init)) }) };
console.log = () => {};                                   // keep the shop's JSON logs quiet

mem.set("askTrial", { rate: 0, arms: { control: {}, high: {} } });
const seedBandit = (best) => mem.set("counterBandit", Object.fromEntries(["lamp", "chair", "bookshelf"].map((i) => [i,
  Object.fromEntries(["plus1", "mid", "ask_minus1", "ask"].map((a) => [a, { n: 20, sum: a === best ? 100 : 20, tries: 20, deals: 10 }]))])));
seedBandit("mid");                                        // tests written for the halfway counter   // ask experiment off unless a test turns it on
let market = [];
let ledger = Array.from({ length: 1200 }, (_, i) => ({ seq: i + 1, ts: 1_000, kind: "order", data: {} }));
globalThis.fetch = async (url) => {
  const u = new URL(url);
  if (u.pathname === "/api/ledger") {
    const after = +u.searchParams.get("after");
    return new Response(JSON.stringify(ledger.filter((e) => e.seq > after).slice(0, 500)));
  }
  return new Response(JSON.stringify(market));
};
const visit = (buyer, ts, deal = true) => ledger.push({ seq: ledger.length + 1, ts, kind: "result",
  data: { buyer, shop: "https://ggari0703-shop.ggari0703.workers.dev", verified: deal, stars: 5, review: "ok",
    receipt: deal ? { item: "lamp", price: 104 } : null } });
const T = (spec) => spec.flatMap(([shop, item, price, n]) => Array.from({ length: n }, () => ({ shop, item, price, counted: true })));

const send = async (text, taskId) => {
  const r = await worker.fetch(new Request("https://x/", { method: "POST", headers: { "A2A-Version": "1.0" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "SendMessage",
      params: { message: { role: "ROLE_USER", messageId: crypto.randomUUID(), taskId, parts: [{ text }] } } }) }), env);
  return (await r.json()).result.task;
};
const card = async () => (await worker.fetch(new Request("https://x/.well-known/agent-card.json"), env)).json();
const prices = async () => (await worker.fetch(new Request("https://x/prices.json"), env)).json();
const askOf = (t) => +t.status.message.parts[0].text.match(/\d+/g).at(-1);
let pass = 0; const ok = (name, fn) => fn().then(() => { pass++; }, (e) => { console.error("FAIL", name, e.message); process.exitCode = 1; });

await ok("pure helpers", async () => {
  const before = marketShares(T([["a", "lamp", 100, 2], ["b", "lamp", 110, 18]]));
  const after = marketShares(T([["a", "lamp", 100, 8], ["b", "lamp", 110, 12]]));
  assert.deepEqual(detectSurge(before, after).map((s) => s.shop), ["a"]);
  assert.deepEqual(detectSurge(after, after), []);
  assert.deepEqual(detectSurge(before, marketShares(T([["ggari-shop", "lamp", 100, 9], ["b", "lamp", 1, 1]]))), []);
  assert.equal(summarize(T([["a", "lamp", 100, 1], ["b", "lamp", 110, 1], ["c", "lamp", 130, 1]])).items.lamp.median, 110);
});

await ok("card: no prices, promises market range and fast", async () => {
  const c = await card();
  assert.equal(c.name, "Ggari Furniture · Instant Signed Deals");
  assert.match(c.description, /within the range this market's deals actually close at/);
  assert.doesNotMatch(JSON.stringify(c), /(?<!\d)[1-3]\d\d(?!\d)/);   // no price anywhere on the card
  assert.doesNotMatch(c.description, /Price match/);
});

await ok("no scan yet: ask capped at reserve x 1.08", async () => {
  const t = await send("I want to buy a chair");
  assert.equal(askOf(t), 195);
});

await ok("scheduled scan: ask never above the market upper quartile, never below reserve", async () => {
  market = T([["a", "chair", 184, 10], ["b", "chair", 190, 9], ["z", "chair", 180, 1], ["a", "lamp", 104, 10]]);
  await worker.scheduled({}, env, { waitUntil: (p) => p });
  await new Promise((r) => setTimeout(r, 10));
  const p = await prices();
  assert.equal(p.market.reason, "scheduled (1 h)");
  assert.equal(p.market.upper_quartiles.chair, 190);  // nearest-rank p75 (median is 184)
  assert.equal(p.asks.chair, 190);
  assert.equal(p.asks.lamp, 104);
  assert.equal(p.asks.bookshelf, 281);                 // never measured: 260 * 1.08
});

await ok("no rescan within the hour without a surge", async () => {
  const r = await shop.fetch(new Request("https://shop/internal/scan", { method: "POST" }));
  assert.equal((await r.json()).scan, null);
});

await ok("share surge triggers an immediate rescan and uses the leader's price", async () => {
  market = T([["z", "bookshelf", 270, 12], ["a", "chair", 184, 4], ["b", "chair", 200, 4]]);
  const r = await shop.fetch(new Request("https://shop/internal/scan", { method: "POST" }));
  const { scan } = await r.json();
  assert.match(scan.reason, /share surge: z/);
  assert.equal((await prices()).asks.bookshelf, 270);  // the surging shop's median
  assert.equal((await prices()).asks.lamp, 104);       // not in this scan: last measured median
});

await ok("first message with a price at/above our ask closes, receipt signed with the shop key", async () => {
  let t = await send("I want to buy a chair for 199");
  assert.equal(t.status.state, "TASK_STATE_COMPLETED");
  const d = t.artifacts[0].parts[0].data;
  assert.equal(d.price, 199);
  const body = JSON.stringify({ item: d.item, price: d.price, shop: d.shop, task: d.task });
  assert.ok(verify(null, Buffer.from(body), publicKey, Buffer.from(d.sig_seller, "base64")));
});

await ok("counters never go below reserve, third counter is the reserve", async () => {
  let t = await send("I want to buy a bookshelf");
  const asks = [askOf(t)];
  for (const o of [150, 160, 170]) { t = await send(String(o), t.id); asks.push(askOf(t)); }
  assert.ok(asks.every((a) => a >= 260), String(asks));
  assert.equal(asks.at(-1), 260);
});

await ok("launch: admin token required, then reserve prices, then ramp back", async () => {
  const bad = await worker.fetch(new Request("https://x/admin/launch", { method: "POST", body: "{}" }), env);
  assert.equal(bad.status, 403);
  const good = await worker.fetch(new Request("https://x/admin/launch", { method: "POST",
    headers: { Authorization: "Bearer t0k" }, body: JSON.stringify({ flat: 1, ramp: 2 }) }), env);
  assert.equal((await good.json()).active, true);
  assert.equal((await prices()).asks.lamp, 100);
  await send("I want to buy a lamp for 100");                 // launch deal 1 of 1 flat
  const a2 = (await prices()).asks.lamp;
  assert.ok(a2 >= 100 && a2 <= 105, String(a2));
  await send("a lamp for 105"); await send("a lamp for 105");  // ramp done -> launch ends
  assert.equal((await prices()).launch, null);
  assert.equal((await prices()).asks.lamp, 100);               // launch ended: the ask climbs back at 2 %/h, not at once
});

await ok("opening ask never above reserve x 1.10, even when the market median is higher", async () => {
  mem.delete("askState");
  await storage.put("market", { ts: Date.now(), items: { chair: { median: 230 } }, leaders: {} });
  const p = await prices();
  assert.equal(p.asks.chair, 198);                     // learned 1.15 capped at 1.10
});

await ok("a served ask rises at most 2 % of reserve per hour; falls are immediate", async () => {
  mem.delete("askState");
  await storage.put("market", { ts: Date.now(), items: { chair: { median: 184 } }, leaders: {} });
  let t = await send("I want to buy a chair"); assert.equal(askOf(t), 184);
  await storage.put("market", { ts: Date.now(), items: { chair: { median: 230 } }, leaders: {} });
  t = await send("I want to buy a chair"); assert.equal(askOf(t), 184);           // no jump
  const st = await storage.get("askState"); st.chair.ts -= 2 * 3600_000; await storage.put("askState", st);
  t = await send("I want to buy a chair"); assert.equal(askOf(t), 191);           // 184 + 2 h x 3.6
  await storage.put("market", { ts: Date.now(), items: { chair: { median: 182 } }, leaders: {} });
  t = await send("I want to buy a chair"); assert.equal(askOf(t), 182);           // falls at once
});

await ok("customer watch: counts visits per buyer, alerts when a regular stops, and when they return", async () => {
  mem.delete("crm");
  const now = 1_800_000_000 * 1000, nowS = now / 1000, hour = Math.floor(nowS / 3600) * 3600;
  await shop.updateCustomers(now);                                     // first run: finds the ledger head
  assert.ok((await storage.get("crm")).after >= 700);
  for (let i = 0; i < 20; i++) visit("regular", hour - 3 * 3600 + i * 60);
  for (let i = 0; i < 3; i++) visit("casual", hour - 2 * 3600 + i * 60);
  await shop.updateCustomers(now);
  const c = await (await worker.fetch(new Request("https://x/customers.json", { headers: { Authorization: "Bearer t0k" } }), env)).json();
  assert.equal(c.customers[0].buyer, "regular");
  assert.equal(c.customers[0].visits_24h, 20);
  assert.equal(c.open_alerts.length, 1);
  assert.equal(c.open_alerts[0].buyer, "regular");
  visit("regular", nowS - 60);
  await shop.updateCustomers(now);
  const c2 = await shop.customers();
  assert.equal(c2.open_alerts.length, 0);
  assert.ok(c2.alerts.find((x) => x.buyer === "regular").recovered);
  for (let i = 0; i < 6; i++) visit("newbie", nowS - 600 + i * 10);
  await shop.updateCustomers(now);
  assert.ok((await shop.customers()).alerts.some((x) => x.kind === "new" && x.buyer === "newbie"));
});

await ok("prices: plain numbers only; '185,000' and friends are refused, never read as 185", async () => {
  assert.deepEqual(parsePrice("185"), { value: 185, problem: null });
  assert.deepEqual(parsePrice("I offer 185."), { value: 185, problem: null });
  assert.equal(parsePrice("185.5").value, 185);
  for (const bad of ["185,000", "1,850", "185.000", "1.850.000", "-5", "999999999"]) {
    const r = parsePrice(bad); assert.equal(r.value, null, bad); assert.ok(r.problem, bad);
  }
  let t = await send("I want to buy a chair");
  t = await send("185,000", t.id);
  assert.equal(t.status.state, "TASK_STATE_INPUT_REQUIRED");
  assert.match(t.status.message.parts[0].text, /plain whole number/);
  assert.ok(!t.artifacts);                                       // no receipt
  t = await send("a chair for 185,000");                       // opening: the named price is ignored
  assert.equal(t.status.state, "TASK_STATE_INPUT_REQUIRED");
  t = await send("185", t.id);
  assert.equal(t.status.state, "TASK_STATE_COMPLETED");
  assert.equal(t.artifacts[0].parts[0].data.price, 185);
});

await ok("abuse limits: long text, typo-high offers, floods, private customer list", async () => {
  let r = await worker.fetch(new Request("https://x/", { method: "POST", headers: { "A2A-Version": "1.0" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "SendMessage",
      params: { message: { role: "ROLE_USER", messageId: "m", parts: [{ text: "lamp ".repeat(1000) }] } } }) }), env);
  assert.equal((await r.json()).error.code, -32602);
  let t = await send("I want to buy a chair");
  const ask = askOf(t);
  t = await send(String(ask * 10), t.id);                       // 10x the ask: not signed
  assert.equal(t.status.state, "TASK_STATE_INPUT_REQUIRED");
  assert.match(t.status.message.parts[0].text, /far above our ask/);
  t = await send(String(ask), t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, ask);
  shop.rate = new Map();
  const flood = async () => (await worker.fetch(new Request("https://x/", { method: "POST",
    headers: { "A2A-Version": "1.0", "CF-Connecting-IP": "203.0.113.9" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "SendMessage",
      params: { message: { role: "ROLE_USER", messageId: crypto.randomUUID(), parts: [{ text: "I want to buy a lamp" }] } } }) }), env)).json();
  for (let i = 0; i < 120; i++) assert.ok((await flood()).result, "within limit " + i);
  assert.equal((await flood()).error.code, -32005);
  assert.ok((await send("I want to buy a lamp")).id);          // other clients still served
  assert.equal((await worker.fetch(new Request("https://x/customers.json"), env)).status, 403);
  assert.equal((await worker.fetch(new Request("https://x/customers.json", { headers: { Authorization: "Bearer t0k" } }), env)).status, 200);
});


await ok("below-floor offers: final price at once, dead-end haggle ends early", async () => {
  assert.equal(shouldEnd(100, [95, 97], 1), false);            // still climbing
  assert.equal(shouldEnd(100, [95, 95], 1), true);             // fixed budget under the floor
  assert.equal(shouldEnd(100, [95], 0), false);
  let t = await send("I want to buy a lamp");                   // market median 104 -> ask 104
  t = await send("90", t.id);
  assert.equal(askOf(t), 100);                                  // midpoint 97 is under the floor -> 100, final
  assert.match(t.status.message.parts[0].text, /market floor/);
  t = await send("90", t.id);                                   // same number again: end politely
  assert.equal(t.status.state, "TASK_STATE_REJECTED");
  assert.match(t.status.message.parts[0].text, /ask again/);
  t = await send("I want to buy a lamp");
  t = await send("95", t.id); t = await send("98", t.id); t = await send("100", t.id);   // a climbing ladder buyer still closes
  assert.equal(t.artifacts[0].parts[0].data.price, 100);
  t = await send("I want to buy a lamp for 95");                // opening below the floor: floor shown at once
  assert.equal(askOf(t), 100);
  t = await send("ok", t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, 100);
});

await ok("security: chunked body cap, odd contextId, prototype buyer names, price reading", async () => {
  shop.rate = new Map();
  const big = new ReadableStream({ start(c) { for (let i = 0; i < 80; i++) c.enqueue(new TextEncoder().encode("x".repeat(1024))); c.close(); } });
  let r = await worker.fetch(new Request("https://x/", { method: "POST", headers: { "A2A-Version": "1.0" }, body: big, duplex: "half" }), env);
  assert.ok(r.status === 413 || (await r.json()).error, "chunked oversize refused");
  r = await worker.fetch(new Request("https://x/", { method: "POST", headers: { "A2A-Version": "1.0" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "SendMessage",
      params: { message: { role: "ROLE_USER", messageId: { a: 1 }, contextId: { x: "y".repeat(5000) }, parts: [{ text: "lamp" }] } } }) }), env);
  const task = (await r.json()).result.task;
  assert.equal(typeof task.contextId, "string"); assert.ok(task.contextId.length <= 100);
  const nowS = Math.floor(Date.now() / 1000);
  for (const b of ["constructor", "__proto__", "toString"]) visit(b, nowS);
  ledger.push({ seq: ledger.length + 1, ts: nowS, kind: "result", data: null });          // malformed entry skipped
  await shop.updateCustomers();
  const c = await (await worker.fetch(new Request("https://x/customers.json", { headers: { Authorization: "Bearer t0k" } }), env)).json();
  for (const b of ["constructor", "__proto__", "toString"]) assert.ok(c.customers.some((x) => x.buyer === b), b);
  assert.equal({}.polluted, undefined);
  const v = (t) => parsePrice(t).value;
  assert.equal(v("Chair 198, offer valid 10 min"), 198);
  assert.equal(v("I accept 110 for 1 lamp"), 110);
  assert.equal(parsePrice("185,000").value, null);
  assert.equal(v("185원"), 185); assert.equal(v("가격185"), 185); assert.equal(v("１８５"), null);   // same as Python parse_price
});

await ok("ask trial: high arm opens higher, floor offers signed from the third offer, arms in prices.json", async () => {
  mem.delete("askState");
  await storage.put("market", { ts: Date.now(), items: { lamp: { median: 100, p75: 101, max: 104 }, chair: { median: 180, p75: 181, max: 250 } }, leaders: {} });
  const H = { Authorization: "Bearer t0k" };
  const setRate = async (rate) => (await worker.fetch(new Request("https://x/admin/trial", { method: "POST", headers: H,
    body: JSON.stringify({ rate, reset: true }) }), env)).json();
  assert.equal((await worker.fetch(new Request("https://x/admin/trial", { method: "POST", body: "{\"rate\":1}" }), env)).status, 403);
  await setRate(1);                                              // everyone in the high arm
  let t = await send("I want to buy a chair");
  assert.equal(askOf(t), 189);                                   // 180 x 1.05
  t = await send("ok", t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, 189);         // an ask-taker pays the high ask
  t = await send("I want to buy a lamp");
  assert.equal(askOf(t), 104);                                   // 105 capped at the market max 104
  t = await send("100", t.id);
  assert.equal(askOf(t), 102);                                   // first floor offer: a counter, halfway to 104
  assert.match(t.status.message.parts[0].text, /is close/);
  t = await send("100", t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, 100);         // the same floor offer twice: signed at once
  await setRate(0);
  t = await send("I want to buy a chair");
  assert.equal(askOf(t), 181);                                   // control: the p75
  t = await send("ok", t.id);
  const p = await prices();
  assert.deepEqual(p.ask_trial.arms.control, { opened: 1, deals: 1, profit: 1, took_ask: 1, lost: 0 });
  assert.equal(p.high_asks.chair, 189);
  assert.equal((await setRate(5)).error, "rate must be 0..1");
});

await ok("reference price: p75 of other shops' verified receipts in the ledger window, our own trades left out", async () => {
  const nowS = Math.floor(Date.now() / 1000);
  const other = (price) => ledger.push({ seq: ledger.length + 1, ts: nowS, kind: "result",
    data: { buyer: "b1", shop: "https://other.example", verified: true, stars: 5, review: "",
      receipt: { item: "bookshelf", price } } });
  for (let i = 0; i < 30; i++) other(260); for (let i = 0; i < 20; i++) other(270);
  for (let i = 0; i < 200; i++) visit("fair", nowS);               // our own floor sales: ignored
  ledger.push({ seq: ledger.length + 1, ts: nowS, kind: "result", data: { verified: true, shop: "https://o", receipt: { item: "__proto__", price: 1 } } });
  await shop.updateCustomers();
  const w = await shop.windowStats();
  assert.equal(w.bookshelf.n, 50);
  assert.equal(w.bookshelf.p75, 270);
  assert.equal(w.bookshelf.max, 270);
  assert.equal(w.lamp, null);                                      // too few samples: fall back to /api/trades
  market = T([["ggari-shop", "bookshelf", 260, 300], ["a", "bookshelf", 266, 10]]);
  const r = await shop.fetch(new Request("https://shop/internal/scan", { method: "PUT" }));
  const scan = (await r.json()).scan;
  assert.equal(scan.items.bookshelf.p75, 270);
  assert.equal(scan.items.bookshelf.source, "ledger 2 h, other shops");
});

await ok("first and second offers under the price get a counter (at least +1); the third offer at or above the floor is signed", async () => {
  mem.delete("askState");
  await storage.put("market", { ts: Date.now(), items: { lamp: { median: 100, p75: 101, max: 104 }, chair: { median: 180, p75: 184, max: 250 } }, leaders: {} });
  let t = await send("I want to buy a lamp");
  assert.equal(askOf(t), 101);
  t = await send("100", t.id);
  assert.equal(askOf(t), 101);                                   // midpoint 100 would not be a counter: offer + 1
  t = await send("100", t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, 100);         // the same offer again: signed, no second counter
  t = await send("I want to buy a chair"); t = await send("180", t.id);
  assert.equal(askOf(t), 182);
  t = await send("181", t.id);
  assert.equal(askOf(t), 182);                                   // a raised second offer: countered again (never above 184)
  t = await send("181", t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, 181);         // repeated: signed
  t = await send("I want to buy a chair for 181");              // a named first offer under the ask: countered
  assert.equal(t.status.state, "TASK_STATE_INPUT_REQUIRED");
  assert.equal(askOf(t), 182);
  t = await send("ok", t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, 182);         // ok takes the counter
  t = await send("I want to buy a chair"); t = await send("184", t.id);
  assert.equal(t.artifacts?.[0]?.parts[0].data.price, 184, "at ask");         // at the ask: closes at once
  t = await send("I want to buy a chair"); t = await send("150", t.id);
  assert.equal(askOf(t), 180, "below-floor counter");          // below the floor: midpoint 167 is under the floor, so the floor
  t = await send("181", t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, 181);
  const c = await card();
  assert.match(c.description, /a counter never goes above our opening ask, and we never sign below the market floor/);
  assert.doesNotMatch(c.description, /third offer|second offer|from your/);   // the signing rule is not published
});

await ok("learned counter: tries every arm first, then the arm with the best profit per counter; results recorded", async () => {
  mem.delete("askState"); mem.delete("counterBandit");
  await storage.put("market", { ts: Date.now(), items: { chair: { median: 180, p75: 186, max: 250 } }, leaders: {} });
  const seen = new Set();
  for (let i = 0; i < 40; i++) {                                // untried arms (fewer than 10 tries) are picked first
    let t = await send("I want to buy a chair");
    assert.equal(askOf(t), 186);
    t = await send("180", t.id);
    const c = askOf(t); seen.add(c);
    assert.ok(c > 180 && c <= 186, "counter within (offer, ask]");
    t = await send("ok", t.id);                                    // this buyer takes the counter
  }
  assert.deepEqual([...seen].sort(), [181, 183, 185, 186]);
  const st = mem.get("counterBandit").chair;
  assert.ok(["plus1", "mid", "ask_minus1", "ask"].every((a) => st[a].tries === 10));
  st.plus1 = { n: 20, sum: 20, tries: 20, deals: 20 }; st.mid = { n: 20, sum: 60, tries: 20, deals: 20 };
  st.ask_minus1 = { n: 20, sum: 0, tries: 20, deals: 20 }; st.ask = { n: 20, sum: 0, tries: 20, deals: 20 };
  let t = await send("I want to buy a chair"); t = await send("180", t.id);
  assert.equal(askOf(t), 183);                                   // greedy: mid has the best profit per counter
  t = await send("ok", t.id);
  assert.equal(t.artifacts[0].parts[0].data.price, 183);
  const learn = (await prices()).counter_learning.chair;
  assert.ok(learn.mid.tries >= 21 && learn.mid.profit_per_counter > 2);
  seedBandit("mid");
});

await ok("write budget: tasks and counters are written behind in batched puts; deals survive in task records", async () => {
  const puts = []; const raw = shop.storage.raw;
  const counting = { get: raw.get, delete: raw.delete, put: async (k, v) => { puts.push(typeof k === "object" ? Object.keys(k) : [k]); return raw.put(k, v); } };
  shop.storage.raw = counting; shop.storage.bypass = false; shop.storage.cache.clear(); shop.storage.dirty.clear();
  shop.storage.lastTask = Date.now(); shop.storage.lastHot = Date.now();     // inside both flush windows
  let t = await send("I want to buy a chair");
  t = await send("180", t.id);
  t = await send("ok", t.id);
  assert.equal(t.status.state, "TASK_STATE_COMPLETED");
  assert.equal(t._deal, undefined);                                // internal state never leaves the shop
  const g = await (await worker.fetch(new Request("https://x/", { method: "POST", headers: { "A2A-Version": "1.0" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "GetTask", params: { id: t.id } }) }), env)).json();
  assert.equal(g.result.status.state, "TASK_STATE_COMPLETED");
  const nonCron = puts.flat().filter((k) => k.startsWith("task:") || ["pricing", "askState", "askTrial", "counterBandit", "open", "taskIndex"].includes(k));
  assert.equal(nonCron.length, 0, "nothing written inside the flush windows: " + nonCron.join(","));
  shop.storage.lastTask = 0;                                       // the task window has passed
  t = await send("I want to buy a lamp");
  const after = puts.flat();
  assert.ok(after.some((k) => k.startsWith("task:")) && !after.includes("pricing"), "tasks flushed, counters still behind");
  assert.ok(puts.every((ks) => !ks.includes("open") && !ks.includes("taskIndex")));
  const before = puts.length;
  await shop.fetch(new Request("https://shop/internal/scan", { method: "POST" }));   // cron flushes everything
  assert.ok(puts.slice(before).flat().includes("pricing"));
  t = await send("95", t.id);                                      // the open deal is read back from the task record
  assert.equal(t.status.state, "TASK_STATE_INPUT_REQUIRED");
  shop.storage.raw = raw; shop.storage.bypass = true;
});

await ok("base ask floor: every opening ask is at least reserve x 1.05 within the market range; trial arm at 1.10", async () => {
  env.BASE_ASK_MARKUP = "1.05"; env.HIGH_ASK_MARKUP = "1.10";
  mem.delete("askState");
  await storage.put("market", { ts: Date.now(), items: { lamp: { median: 100, p75: 101, max: 120 }, chair: { median: 180, p75: 181, max: 186 }, bookshelf: { median: 260, p75: 262, max: 300 } }, leaders: {} });
  const p = await prices();
  assert.equal(p.asks.lamp, 105);                                  // p75 101 lifted to 1.05x
  assert.equal(p.asks.chair, 186);                                 // 1.05x = 189, capped at the market max 186
  assert.equal(p.asks.bookshelf, 273);
  assert.equal(p.high_asks.lamp, 110); assert.equal(p.high_asks.bookshelf, 286);
  env.BASE_ASK_MARKUP = "1"; env.HIGH_ASK_MARKUP = "1.05";
});

console.error(`${pass} passed`);
