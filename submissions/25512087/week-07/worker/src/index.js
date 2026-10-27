// ggari0703 week-07 shop on Cloudflare Workers.
// A2A v1 JSON-RPC (SendMessage / GetTask / CancelTask) with the same rule-based negotiation as
// seller.py + negotiation.py + pricing.py, and Ed25519-signed receipts (sig_seller).
// All state lives in one Durable Object so a multi-turn negotiation survives across requests.

const RESERVES = { lamp: 100, chair: 180, bookshelf: 260 };
const CARD_NAME = "Ggari Furniture · Instant Signed Deals";
// The card promises: "our opening ask stays within the market's range" (ask basis A: never above the
// upper quartile of counted deal prices). marketCap() enforces it.
const FALLBACK_MARKUP = 1.08;      // cap when no p75 was ever measured for an item
// No sudden jumps for regular customers (a 1.21x ask lost our biggest buyer on 10/22 13:50):
const ASK_CAP = 1.10;              // the opening ask never exceeds reserve x 1.10
const RISE_PER_HOUR = 0.02;        // and rises by at most 2 % of the reserve per hour (falls are immediate)

// ---- customer watch (CRM): who buys from us, and who stopped
const SHOP_URL_MARK = "ggari0703-shop";    // our card URL contains this
const CHURN_MIN_VISITS = 15;               // a "regular": >= 15 visits in the 3 h before the last hour
const LEDGER_PAGES_PER_RUN = 12;

// ---- abuse limits (anyone on the internet can reach this Worker, not only market buyers)
const MAX_BODY_BYTES = 64 * 1024;          // one JSON-RPC request
const MAX_TEXT = 4000;                     // characters of one buyer message (LLM buyers can be wordy)
const MAX_HISTORY = 20;                    // messages kept per task
const MAX_OPEN = 1000;                     // open negotiations kept at once
const MAX_CRM_BUYERS = 2000;
const NEW_TASKS_PER_MIN_PER_IP = 120;      // house buyers share one IP, so this is generous
const IMPLAUSIBLE_FACTOR = 2;              // an offer above 2x our ask is treated as a typo, not accepted

// ---- market scan (market_prices.py): the shop measures the market itself, from a Cron Trigger
const MARKET = "https://market.ourolabs.ai";
const SELF_NAMES = ["ggari-shop", "ggari0703"];
const SCAN_EVERY_MS = 3600_000;            // scheduled rescan
const MARKET_MAX_AGE_MS = 3 * 3600_000;    // an older scan is ignored
const SURGE_POINTS = 0.05, SURGE_MIN_SHARE = 0.08, SURGE_MIN_DEALS = 5;
const START_MARKUP = 1.15, MIN_MARKUP = 1.05, MAX_MARKUP = 1.30, STEP_UP = 0.02, STEP_DOWN = 0.04;
const STALE_MS = 180_000;        // open negotiation with no message for this long counts as lost
const MAX_ROUNDS = 6;                // shouldEnd usually closes a dead-end haggle sooner
const FLOOR_PATIENCE = 3;
// Ask experiment (10/23, replaces the counter trial): ASK_TRIAL_RATE of tasks (stable by task id) are opened
// at a higher ask, reserve x HIGH_ASK_MARKUP, never above reserve x ASK_CAP or the highest price other shops
// closed at in the price window. Any offer at or above the floor is still signed at once in both arms, so only
// buyers who take the ask pay more. Arms are counted in storage "askTrial" and shown in /prices.json;
// POST /admin/trial {"rate": 0..1, "reset": true} switches it.
const ASK_TRIAL_DEFAULT_RATE = 0.2;
// Learned counter (10/23): the one counter to a first offer between the floor and our ask is chosen per item among
// COUNTER_ARMS by a discounted epsilon-greedy bandit. Reward = (signed price - reserve) when the task closes, 0 when
// the buyer walks. Never above our ask, always at least offer + 1. Stats shown in /prices.json ("counter_learning").
const COUNTERED_OFFERS = 2;        // a buyer's 1st and 2nd offer under our current price are countered; the 3rd is signed
const COUNTER_ARMS = ["plus1", "mid", "ask_minus1", "ask"];
const COUNTER_EXPLORE = 0.05;      // share of counters that try a random arm (10/26: the ask arm won every item)
const COUNTER_MIN_TRIES = 10;      // every arm is tried this often before the greedy choice applies
const COUNTER_DISCOUNT = 0.995;    // older results fade (about the last 200 counters per item weigh most)
const HIGH_ASK_MARKUP = 1.10;      // experiment arm (10/26: moved up from 1.05, which is now the base)
// Base ask floor (10/26 report: tasks opened at 1.05x earned 2.4x per task, conversion -2.8 pp): every opening ask is
// at least reserve x BASE_ASK_MARKUP, still never above reserve x ASK_CAP or the highest price other shops closed at.
const BASE_ASK_MARKUP = 1.05;
// Market reference price: p75 of verified receipts of OTHER shops (our own floor sales pulled it down) over the
// last PRICE_WINDOW_MS, collected from the ledger the customer watch already reads every 10 minutes.
const PRICE_WINDOW_MS = 2 * 3600_000;
const PRICE_MIN_SAMPLES = 30;

// ---------------------------------------------------------------- negotiation rules (negotiation.py)
const ITEM_ALIASES = [
  ["bookshelf", ["bookshelf", "book shelf", "bookshelves", "bookcase", "book case", "shelf", "책장", "책꽂이"]],
  ["chair", ["chair", "armchair", "stool", "seat", "의자"]],
  ["lamp", ["lamp", "desk light", "reading light", "램프", "스탠드", "조명"]],
];
const ACCEPT_WORDS = ["accept", "deal", "agreed", "agree", "ok", "okay", "yes", "sounds good", "i'll take",
  "i will take", "take it", "좋아", "좋습니다", "수락", "콜", "그래"];
const OFFER_WORDS = ["offer", "pay", "for ", " at ", "price", "budget of", "제안", "원"];
const NEGATION = /(?<![a-z])(no|not|don't|won't|cannot|can't|reject|decline|walk|pass)(?![a-z])|거절|안 /;
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function findItem(text) {
  const t = text.toLowerCase();
  for (const [item, aliases] of ITEM_ALIASES) if (aliases.some((a) => t.includes(a))) return item;
  return null;
}
// A price token is read whole ("185,000" is judged as one number, never cut to 185). Only plain numbers are
// trusted: 185, 185.0, 185.5. "185,000", "185.000", "1.850.000", negatives and absurd values are refused.
const MAX_PRICE = 100_000;
const PRICE_TOKEN = /(?<![A-Za-z0-9_.,#\/])-?[0-9][0-9.,]*(?![A-Za-z0-9_\/])/g;
const NOT_PRICE_AFTER = /^\s*(?:%|x\b|percent|min|mins|minutes?|h\b|hrs?|hours?|days?|sec|seconds?|times|rounds?|offers?|items?|units?|pieces?|pcs|lamps?|chairs?|bookshel(?:f|ves)|shelves|sold|left|in stock|stars?)/i;
const NOT_PRICE_BEFORE = /(?:\bwas|\bbudget(?: of| is)?|\bpreviously|\bbefore|\binstead of|\border)\s*$/i;
// The token that reads as the price: the last number that is not a quantity, duration or reference
// ("1 lamp", "valid 10 min", "order #48213", "was 200"). Same rules as negotiation.py.
function priceToken(text) {
  const found = [...String(text || "").matchAll(PRICE_TOKEN)];
  for (let i = found.length - 1; i >= 0; i--) {
    const m = found[i], t = String(text);
    const end = m.index + m[0].replace(/[.,]+$/, "").length;
    const unitFollows = !",.;)".includes(t[end] ?? "") && NOT_PRICE_AFTER.test(t.slice(end));
    if (unitFollows || NOT_PRICE_BEFORE.test(t.slice(Math.max(0, m.index - 20), m.index))) continue;
    return m[0];
  }
  return null;
}
export function parsePrice(text) {
  let token = priceToken(text);
  if (token === null) return { value: null, problem: null };
  token = token.replace(/[.,]+$/, "");
  if (token.startsWith("-")) return { value: null, problem: `negative price ${token}` };
  if (token.includes(",")) return { value: null, problem: `ambiguous number ${token} (comma)` };
  const dots = token.split(".");
  if (dots.length > 2) return { value: null, problem: `ambiguous number ${token} (several dots)` };
  if (dots.length === 2 && dots[1].length === 3) return { value: null, problem: `ambiguous number ${token} (three decimals)` };
  const value = Math.floor(parseFloat(token));
  if (value > MAX_PRICE) return { value: null, problem: `price ${token} is out of range` };
  return { value, problem: null };
}
function parseOffer(text) {
  const { value, problem } = parsePrice(text);
  return problem ? null : value;
}
function saysAccept(text) {
  const t = ` ${text.toLowerCase()} `;
  if (NEGATION.test(t)) return false;
  return ACCEPT_WORDS.some((w) => new RegExp(`(?<![a-z])${esc(w.trim())}(?![a-z])`).test(t));
}
function openingOffer(text) {
  const t = text.toLowerCase();
  return OFFER_WORDS.some((w) => t.includes(w)) ? parseOffer(text) : null;
}
// Pricing rule (10/23): an offer at or above our current price closes at once; the buyer's 1st and 2nd offers between
// the floor and our current price are countered by Shop.counterOffer (learned arm, see COUNTER_ARMS); from the 3rd
// offer on, any offer at or above the floor is signed at exactly that price. fastRespond handles the rest.
function fastRespond(reserve, ask, offer, rounds) {
  if (offer >= ask) return { action: "accept", price: offer };                 // sign exactly the buyer's number
  if (offer >= reserve) {
    if (rounds === 0) return { action: "counter", price: Math.min(ask, Math.max(offer + 1, Math.floor((offer + ask) / 2))) };
    return { action: "accept", price: offer };
  }
  if (ask <= reserve || rounds >= 1) return { action: "counter", price: reserve };   // final price, shown fast
  return { action: "counter", price: Math.max(reserve, Math.min(ask, Math.floor((offer + ask) / 2))) };
}
// after the final price was shown, a below-floor offer that does not rise (or the FLOOR_PATIENCE-th) ends the haggle
function shouldEnd(reserve, offers, floorQuotes) {
  if (floorQuotes < 1 || !offers.length || offers.at(-1) >= reserve) return false;
  if (floorQuotes >= FLOOR_PATIENCE) return true;
  return offers.length >= 2 && offers.at(-1) <= offers.at(-2);
}
const openingMessage = (item, ask, reserve) => ask <= reserve
  ? `${item}: this is our final price, the market floor for a ${item}, so no haggling is needed. Reply ok to take it at once. Asking ${ask}.`
  : `${item}: asking ${ask}. Send your offer as a number.`;
const counterMessage = (offer, counter, reserve, item) => offer >= reserve
  ? `${offer} is close. Counteroffer: ${counter}.`
  : counter <= reserve
  ? `${offer} is below the market floor for a ${item}; no shop here may sign below it. Reply ok and the signed receipt comes back at once. Our final price: ${counter}.`
  : `${offer} is too low. Counteroffer: ${counter}.`;
const endMessage = (item, reserve) =>
  `Sorry, we cannot sign below the market floor for a ${item}. If your budget reaches it, ask again and it is yours in one message: ${reserve}.`;

// ---------------------------------------------------------------- market scan helpers (market_prices.py)
const counted = (trades) => trades.filter((t) => t.counted && t.item && typeof t.price === "number");
// nearest rank, same as market_prices._q (Python round() is half-to-even, so match it)
const quantile = (xs, q) => { const a = [...xs].sort((p, r) => p - r); const x = q * (a.length - 1);
  let k = Math.round(x); if (Math.abs(x - Math.floor(x) - 0.5) < 1e-9 && Math.floor(x) % 2 === 0) k = Math.floor(x);
  return a[Math.min(a.length - 1, Math.max(0, k))]; };
const medianOf = (xs) => { const a = [...xs].sort((p, q) => p - q); const n = a.length;
  return n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2; };
export { shouldEnd };
export function marketShares(trades) {
  const deals = counted(trades), total = deals.length || 1, out = {};
  for (const t of deals) (out[t.shop] ||= { deals: 0, share: 0 }).deals += 1;
  for (const r of Object.values(out)) r.share = +(r.deals / total).toFixed(4);
  return out;
}
export function detectSurge(prev, cur, selfNames = SELF_NAMES) {
  const out = [];
  for (const [shop, now] of Object.entries(cur)) {
    if (selfNames.includes(shop) || now.deals < SURGE_MIN_DEALS) continue;
    const before = (prev[shop] || { share: 0 }).share;
    if (now.share - before >= SURGE_POINTS || (now.share >= SURGE_MIN_SHARE && now.share >= 2 * Math.max(before, 1e-9)))
      out.push({ shop, share_before: before, share_now: now.share, deals: now.deals });
  }
  return out.sort((a, b) => (b.share_now - b.share_before) - (a.share_now - a.share_before));
}
export function summarize(trades, leaders = []) {
  const deals = counted(trades), items = {}, leaderPrices = {};
  for (const item of [...new Set(deals.map((t) => t.item))].sort()) {
    const p = deals.filter((t) => t.item === item).map((t) => t.price);
    items[item] = { n: p.length, min: Math.min(...p), median: medianOf(p), p75: quantile(p, 0.75), max: Math.max(...p), mean: +(p.reduce((a, b) => a + b, 0) / p.length).toFixed(1) };
  }
  for (const shop of leaders) {
    const mine = deals.filter((t) => t.shop === shop);
    leaderPrices[shop] = Object.fromEntries([...new Set(mine.map((t) => t.item))]
      .map((item) => [item, medianOf(mine.filter((t) => t.item === item).map((t) => t.price))]));
  }
  return { items, leaders: leaderPrices, window_deals: deals.length };
}
const kst = (ms) => new Date(ms).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 16) + " KST";

// ---------------------------------------------------------------- signing
let keyPromise = null;
function signingKey(pem) {
  if (!keyPromise) {
    const b64 = pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
    const der = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    keyPromise = crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
  }
  return keyPromise;
}
// Python: json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)
function canon(obj) {
  const sorted = {};
  for (const k of Object.keys(obj).sort()) sorted[k] = obj[k];
  return new TextEncoder().encode(JSON.stringify(sorted));
}
async function signReceipt(env, receipt) {
  const body = { shop: receipt.shop, item: receipt.item, price: receipt.price, task: receipt.task };
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, await signingKey(env.SHOP_KEY), canon(body));
  return { ...receipt, sig_seller: btoa(String.fromCharCode(...new Uint8Array(sig))) };
}

// ---------------------------------------------------------------- A2A helpers
const uuid = () => crypto.randomUUID();
const newArm = () => ({ opened: 0, deals: 0, profit: 0, took_ask: 0, lost: 0 });
const newArms = () => ({ control: newArm(), high: newArm() });
// stable 0..1 bucket from the task id, so a retried message never flips arms
const bucket = (id) => { let h = 2166136261; for (const c of String(id)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0) / 2 ** 32; };
const now = () => new Date().toISOString();
const rpcResult = (id, result) => Response.json({ jsonrpc: "2.0", id, result });
const rpcError = (id, code, message) => Response.json({ jsonrpc: "2.0", id, error: { code, message } });

// No prices on the card (prices change every hour, and a printed number is what buyers compare);
// the card promises how the price is set, and the code keeps that promise.
function card(env, origin) {
  return {
    name: CARD_NAME,
    // The card states facts only (10/23): no "signed from the Nth offer" rule, which floor-only buyers used to
    // wait out the counters. Same practice as the market's house shops ("Prices are negotiable").
    description: "Lamps, chairs and bookshelves at fair market prices, closed fast. We scan this market's counted "
      + "deals every hour, and again the moment another shop's share jumps, so our opening ask always stays "
      + "within the range this market's deals actually close at. Every message is answered in milliseconds by "
      + "rule-based code: an offer at our current price closes in that one message, a counter never goes above "
      + "our opening ask, and we never sign below the market floor. Every receipt is Ed25519-signed over "
      + "{shop, item, price, task} and shows exactly the agreed price, so your deal counts in Round 2.",
    supportedInterfaces: [{ url: `${origin}/`, protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
    version: "1.12.0",
    capabilities: { streaming: false },
    defaultInputModes: ["text/plain"],
    defaultOutputModes: ["application/json", "text/plain"],
    skills: [{
      id: "negotiate-furniture",
      name: "Buy a lamp, chair or bookshelf, fast",
      description: "Name the item; our ask is the last number in each reply. Send a number to offer, or say ok "
        + "to take the ask and get a signed receipt at once.",
      tags: ["shopping", "furniture", "lamp", "chair", "bookshelf", "fast", "signed-receipt", "market-price"],
      examples: ["I want to buy a chair", "ok"],
    }],
  };
}

const shopStub = (env) => env.SHOP.get(env.SHOP.idFromName("shop"));
async function quote(env) {
  const r = await shopStub(env).fetch("https://shop/internal/quote");
  return r.json();
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/.well-known/agent-card.json") {
      return Response.json(card(env, url.origin));
    }
    if (request.method === "GET" && url.pathname === "/prices.json") {
      return Response.json(await quote(env));
    }
    if (request.method === "GET" && url.pathname === "/customers.json") {
      // who our customers are is business data: owner only (npx wrangler secret put ADMIN_TOKEN)
      if (!env.ADMIN_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) {
        return new Response("forbidden", { status: 403 });
      }
      return Response.json(await (await shopStub(env).fetch("https://shop/internal/customers")).json());
    }
    if (request.method === "GET" && url.pathname === "/") {
      return new Response(`${env.SHOP_NAME}: A2A shop. Agent Card at /.well-known/agent-card.json\n`);
    }
    if (request.method === "POST" && url.pathname === "/admin/trial") {
      if (!env.ADMIN_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) {
        return new Response("forbidden", { status: 403 });
      }
      const text = (await request.text()).slice(0, 1000);
      return shopStub(env).fetch("https://shop/internal/trial", { method: "POST", body: text });
    }
    if (request.method === "POST" && url.pathname === "/admin/launch") {
      // launch phase on/off; needs the ADMIN_TOKEN secret (npx wrangler secret put ADMIN_TOKEN)
      if (!env.ADMIN_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) {
        return new Response("forbidden", { status: 403 });
      }
      return shopStub(env).fetch("https://shop/internal/launch", { method: "POST", body: await request.text() });
    }
    if (request.method === "POST" && (url.pathname === "/" || url.pathname === "")) {
      // read the body ourselves with a cap: Content-Length can be absent (chunked) or wrong
      const reader = request.body?.getReader();
      const chunks = []; let size = 0;
      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_BODY_BYTES) { await reader.cancel(); return rpcError(null, -32600, "Request too large"); }
        chunks.push(value);
      }
      const body = new Uint8Array(size); let at = 0;
      for (const c of chunks) { body.set(c, at); at += c.byteLength; }
      return shopStub(env).fetch(new Request(request.url, { method: "POST", headers: request.headers, body }));
    }
    return new Response("not found", { status: 404 });
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(shopStub(env).fetch("https://shop/internal/scan", { method: "POST" }));
  },
};

// ---------------------------------------------------------------- Durable Object: the shop
// ---- storage write budget (10/23 outage 08:25-09:30 KST: every SendMessage returned "Internal error").
// Before, each message rewrote several large keys (open deals, a task index of thousands of ids) and every counter
// separately: ~15 row writes per visit, and the index value grew with traffic. Now a task's negotiation state lives
// in its own task record, there is no global index, and hot counters are written behind: tasks every TASK_FLUSH_MS,
// counters every HOT_FLUSH_MS, each flush one batched put.
const HOT_KEYS = new Set(["pricing", "askState", "askTrial", "counterBandit", "launch"]);
const TASK_FLUSH_MS = 10_000;
const HOT_FLUSH_MS = 15_000;        // 10/23: counters went backwards between two reads 40 s apart -> the object restarts often; keep the loss window small
const TASK_CACHE_MAX = 3000;
class CachedStore {
  constructor(raw) { this.raw = raw; this.cache = new Map(); this.dirty = new Set(); this.lastTask = 0; this.lastHot = 0; this.bypass = false; }
  cached(k) { return !this.bypass && typeof k === "string" && (HOT_KEYS.has(k) || k.startsWith("task:")); }
  async get(k) {
    if (!this.cached(k)) return this.raw.get(k);
    if (this.cache.has(k)) return this.cache.get(k);
    const v = await this.raw.get(k);
    if (v !== undefined || HOT_KEYS.has(k)) this.cache.set(k, v);     // unknown task ids are not remembered
    return v;
  }
  async put(k, v) {
    if (typeof k === "object") { for (const [a, b] of Object.entries(k)) await this.put(a, b); return; }
    if (!this.cached(k)) return this.raw.put(k, v);
    this.cache.delete(k); this.cache.set(k, v); this.dirty.add(k);       // re-insert: Map order = recency
  }
  async delete(k) { for (const x of [].concat(k)) { this.cache.delete(x); this.dirty.delete(x); } return this.raw.delete(k); }
  async flush(force = false, keep = () => false) {
    const t = Date.now();
    const keys = [...this.dirty].filter((k) => force || (k.startsWith("task:") ? t - this.lastTask >= TASK_FLUSH_MS : t - this.lastHot >= HOT_FLUSH_MS));
    for (let i = 0; i < keys.length; i += 100) {                         // put() takes at most 128 keys
      const chunk = keys.slice(i, i + 100);
      await this.raw.put(Object.fromEntries(chunk.map((k) => [k, this.cache.get(k)])));
      for (const k of chunk) this.dirty.delete(k);
    }
    if (keys.some((k) => k.startsWith("task:"))) this.lastTask = t;
    if (keys.some((k) => HOT_KEYS.has(k))) this.lastHot = t;
    if (this.cache.size > TASK_CACHE_MAX) {                              // forget the oldest clean tasks
      for (const k of this.cache.keys()) {
        if (this.cache.size <= TASK_CACHE_MAX * 0.8) break;
        if (k.startsWith("task:") && !this.dirty.has(k) && !keep(k.slice(5))) this.cache.delete(k);
      }
    }
    return keys.length;
  }
}
const pub = (task) => { if (!task) return task; const { _deal, ...rest } = task; return rest; };

export class Shop {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.storage = new CachedStore(ctx.storage);
    this.openIdx = new Map();        // task id -> last seen, for open negotiations (rebuilt as buyers write)
    this.rand = Math.random;         // tests replace it
    this.started = Date.now();       // shown in /prices.json: how long this object instance has been alive
  }
  // ---- learned counter (bandit over COUNTER_ARMS, per item)
  counterPrice(arm, offer, ask) {
    const mid = Math.floor((offer + ask) / 2);
    const p = { plus1: offer + 1, mid, ask_minus1: ask - 1, ask }[arm];
    return Math.min(ask, Math.max(offer + 1, p));
  }
  async pickCounter(item, offer, ask) {
    const all = (await this.storage.get("counterBandit")) || {};
    const st = all[item] || {};
    const stat = (a) => (st[a]?.n > 0 ? st[a] : { n: 1, sum: 0 });
    let arm;
    const untried = COUNTER_ARMS.filter((a) => (st[a]?.tries || 0) < COUNTER_MIN_TRIES);   // raw count, not the faded one
    if (untried.length) arm = untried[Math.floor(this.rand() * untried.length)];
    else if (this.rand() < COUNTER_EXPLORE) arm = COUNTER_ARMS[Math.floor(this.rand() * COUNTER_ARMS.length)];
    else arm = COUNTER_ARMS.reduce((best, a) => (stat(a).sum / stat(a).n > stat(best).sum / stat(best).n ? a : best));
    return { arm, price: this.counterPrice(arm, offer, ask) };
  }
  async counterResult(item, arm, reward, deal) {
    if (!COUNTER_ARMS.includes(arm) || !Object.hasOwn(RESERVES, item)) return;
    const all = (await this.storage.get("counterBandit")) || {};
    const st = (all[item] ||= {});
    for (const a of COUNTER_ARMS) if (st[a]) { st[a].n *= COUNTER_DISCOUNT; st[a].sum *= COUNTER_DISCOUNT; }
    const x = (st[arm] ||= { n: 0, sum: 0, tries: 0, deals: 0 });
    x.n += 1; x.sum += reward; x.tries = (x.tries || 0) + 1; if (deal) x.deals = (x.deals || 0) + 1;
    await this.storage.put("counterBandit", all);
  }
  async counterLearning() {
    const all = (await this.storage.get("counterBandit")) || {};
    return Object.fromEntries(Object.keys(RESERVES).map((item) => {
      const st = all[item] || {};
      const arms = Object.fromEntries(COUNTER_ARMS.map((a) => [a, st[a] ? { tries: st[a].tries || 0,
        deal_rate: st[a].tries ? +((st[a].deals || 0) / st[a].tries).toFixed(3) : null,
        profit_per_counter: st[a].n ? +(st[a].sum / st[a].n).toFixed(2) : null } : null]));
      return [item, arms];
    }));
  }
  // an offer in [reserve, current price), the buyer's 1st or 2nd: a counter from the learned arm (picked once per task)
  async counterOffer(task, deal, offer) {
    let arm = deal.cArm, price;
    if (!arm) ({ arm, price } = await this.pickCounter(deal.item, offer, deal.ask));
    else price = this.counterPrice(arm, offer, deal.ask);
    Object.assign(deal, { rounds: (deal.rounds || 0) + 1, ask: price, cArm: arm });
    this.log("response", { task_id: task.id, item: deal.item, offer, reserve: deal.reserve, decision: "counter", decision_price: price, counter_arm: arm });
    this.setStatus(task, "TASK_STATE_INPUT_REQUIRED", counterMessage(offer, price, deal.reserve, deal.item));
  }

  log(event, values) {
    console.log(JSON.stringify({ event, ...values }));
  }

  // ---- pricing (pricing.py), state in DO storage
  async pricing() {
    if (!this._pricing) this._pricing = (await this.storage.get("pricing")) || {};
    return this._pricing;
  }
  async item(item) {
    const p = await this.pricing();
    return (p[item] ||= { markup: START_MARKUP, visits: 0, deals: 0, revenue: 0, took_ask: 0, left_at_ask: 0, below_floor: 0 });
  }
  // opening ask = learned markup, trimmed by the latest market scan (never below reserve x MIN_MARKUP),
  // or the launch-phase cap; never below the reserve.
  // serve = true only when a buyer is actually quoted (openDeal); card and /prices.json just read it
  async ask(item, serve = false) {
    const s = await this.item(item);
    const reserve = RESERVES[item];
    const up = (v) => Math.ceil(v - 1e-9);               // 180 * 1.10 is 198.00000000000003 in floats
    let ask = up(reserve * Math.min(ASK_CAP, Math.max(MIN_MARKUP, s.markup)));
    const launch = await this.launchCap();
    if (launch !== null) ask = Math.min(ask, up(reserve * launch));
    else ask = Math.max(Math.min(ask, await this.marketCap(item)),
      await this.highAsk(item, Number(this.env.BASE_ASK_MARKUP) || BASE_ASK_MARKUP));   // base floor, within the market range
    ask = Math.max(reserve, ask);
    const state = (await this.storage.get("askState")) || {};
    const prev = state[item];
    const t = Date.now();
    if (prev && ask > prev.ask) {
      const allowed = prev.ask + Math.floor(reserve * RISE_PER_HOUR * (t - prev.ts) / 3600_000);
      ask = Math.max(prev.ask, Math.min(ask, allowed));
    }
    if (serve && (!prev || ask !== prev.ask)) {          // the clock restarts only when the ask changes
      state[item] = { ask, ts: t };
      await this.storage.put("askState", state);
    }
    return ask;
  }
  // Ceiling that keeps the card's promise (ask basis A): the upper quartile (p75) of counted deal prices from
  // the latest scan, else the last p75 measured, else reserve x FALLBACK_MARKUP. Never below the reserve.
  // A surging shop no longer pulls the cap down to its median; a surge only triggers a rescan.
  // the experiment arm's ask: reserve x HIGH_ASK_MARKUP, within reserve x ASK_CAP and the market's highest price
  async highAsk(item, markup = Number(this.env.HIGH_ASK_MARKUP) || HIGH_ASK_MARKUP) {
    const reserve = RESERVES[item];
    const up = (v) => Math.ceil(v - 1e-9);
    const scan = await this.storage.get("market");
    const max = Number(scan?.items?.[item]?.max);
    let v = Math.min(up(reserve * markup), up(reserve * ASK_CAP));
    if (Number.isFinite(max)) v = Math.min(v, Math.floor(max));
    return Math.max(reserve, v);
  }
  async marketCap(item) {
    const reserve = RESERVES[item];
    const scan = await this.storage.get("market");
    let ref = null;
    if (scan && Date.now() - scan.ts <= MARKET_MAX_AGE_MS && scan.items?.[item]) {
      ref = scan.items[item].p75 ?? scan.items[item].median;
    } else {
      const last = (await this.storage.get("lastMedians")) || {};
      ref = last[item] ?? null;
    }
    return Math.max(reserve, ref === null ? Math.ceil(reserve * FALLBACK_MARKUP - 1e-9) : Math.floor(ref));
  }
  // ---- launch phase (pricing.py --start-launch): first `flat` deals at the reserve, then a ramp
  async launchCap() {
    const l = await this.storage.get("launch");
    if (!l?.active) return null;
    if (l.deals < l.flat) return 1.0;
    if (l.deals < l.flat + l.ramp) return 1.0 + (ASK_CAP - 1.0) * (l.deals - l.flat) / l.ramp;
    return null;
  }
  async countLaunchDeal() {
    const l = await this.storage.get("launch");
    if (!l?.active) return;
    l.deals += 1;
    if (l.deals >= l.flat + l.ramp) { l.active = false; l.ended = Date.now(); }
    await this.storage.put("launch", l);
  }
  async quote() {
    const asks = {};
    for (const item of Object.keys(RESERVES)) asks[item] = await this.ask(item);
    const scan = await this.storage.get("market");
    const launch = await this.storage.get("launch");
    return { shop: this.env.SHOP_NAME, asks, updated: kst(Date.now()),
      market: scan ? { scanned: kst(scan.ts), reason: scan.reason, medians: Object.fromEntries(
        Object.entries(scan.items || {}).map(([k, v]) => [k, v.median])), upper_quartiles: Object.fromEntries(
        Object.entries(scan.items || {}).map(([k, v]) => [k, v.p75 ?? null])) } : null,
      launch: launch?.active ? { deals: launch.deals, flat: launch.flat, ramp: launch.ramp } : null,
      ask_trial: await this.trialState(),
      counter_learning: await this.counterLearning(),
      instance_age_s: Math.round((Date.now() - this.started) / 1000),
      high_asks: Object.fromEntries(await Promise.all(Object.keys(RESERVES).map(async (i) => [i, Math.max(asks[i], await this.highAsk(i))]))) };
  }
  // ---- customer watch: read the market ledger since our cursor, count each buyer's visits to us
  async headSeq() {
    let lo = 0, hi = 4_000_000;
    while (hi - lo > 500) {
      const mid = Math.floor((lo + hi) / 2);
      const a = await fetch(`${MARKET}/api/ledger?after=${mid}`).then((r) => r.json());
      if (a.length) lo = mid; else hi = mid;
    }
    return lo;
  }
  async updateCustomers(now = Date.now()) {
    const crm = (await this.storage.get("crm")) || { after: null, buyers: {}, alerts: [] };
    if (crm.after === null) { crm.after = await this.headSeq(); await this.storage.put("crm", crm); return crm; }
    const hist = (await this.storage.get("priceHist")) || {};      // {"t<10-min bucket>": {item: {price: n}}}
    let pages = 0;
    for (; pages < LEDGER_PAGES_PER_RUN; pages++) {
      const a = await fetch(`${MARKET}/api/ledger?after=${crm.after}`).then((r) => r.json());
      if (!a.length) break;
      for (const e of a) {
        try {
          if (e?.kind !== "result") continue;
          if (!String(e.data?.shop || "").includes(SHOP_URL_MARK)) {          // another shop: record its counted price
            const r = e.data?.receipt, ts = Number(e.ts);
            if (e.data?.verified === true && r && Object.hasOwn(RESERVES, r.item) && Number.isFinite(ts)
                && Number.isInteger(Number(r.price)) && r.price >= RESERVES[r.item] && r.price <= 2 * RESERVES[r.item]) {
              const k = "t" + Math.floor(ts / 600) * 600;
              const byItem = (hist[k] ||= {}); const prices = (byItem[r.item] ||= {});
              prices["p" + r.price] = (prices["p" + r.price] || 0) + 1;
            }
            continue;
          }
          const d = e.data, ts = Number(e.ts);
          if (typeof d.buyer !== "string" || !d.buyer || !Number.isFinite(ts)) continue;
          const key = "b:" + d.buyer.slice(0, 100);            // prefixed: "__proto__"/"constructor" are plain keys
          if (!Object.hasOwn(crm.buyers, key) && Object.keys(crm.buyers).length >= MAX_CRM_BUYERS) continue;
          const b = Object.hasOwn(crm.buyers, key) ? crm.buyers[key] : (crm.buyers[key] = { hours: {}, deals: 0, first: ts });
          const hour = "h" + Math.floor(ts / 3600) * 3600;
          b.hours[hour] = (b.hours[hour] || 0) + 1;
          if (d.verified === true) b.deals += 1;
          b.last = { ts, stars: Number.isFinite(d.stars) ? d.stars : null, review: String(d.review || "").slice(0, 200),
            deal: d.receipt ? `${String(d.receipt.item).slice(0, 20)} ${Number(d.receipt.price)}` : null };
        } catch (err) { this.log("crm_entry_skipped", { seq: e?.seq, error: String(err).slice(0, 100) }); }
      }
      const lastSeq = Number(a.at(-1)?.seq);
      if (!Number.isFinite(lastSeq) || lastSeq <= Number(crm.after)) break;
      crm.after = lastSeq;
    }
    for (const k of Object.keys(hist)) if (+k.slice(1) < now / 1000 - PRICE_WINDOW_MS / 1000) delete hist[k];
    await this.storage.put("priceHist", hist);
    const nowS = now / 1000;
    const hr = (h) => +String(h).replace(/^h/, "");
    const visits = (b, from, to) => Object.entries(b.hours).reduce((n, [h, c]) => (hr(h) >= from && hr(h) < to ? n + c : n), 0);
    for (const [key, b] of Object.entries(crm.buyers)) {
      const name = key.startsWith("b:") ? key.slice(2) : key;
      for (const h of Object.keys(b.hours)) if (hr(h) < nowS - 24 * 3600) delete b.hours[h];
      if (!Object.keys(b.hours).length && (b.last?.ts || 0) < nowS - 24 * 3600) { delete crm.buyers[key]; continue; }
      const hourNow = Math.floor(nowS / 3600) * 3600;
      const before = visits(b, hourNow - 4 * 3600, hourNow - 3600);   // the 3 full hours before the last one
      const recent = (b.last?.ts || 0) > nowS - 3600;
      const open = crm.alerts.find((x) => x.buyer === name && x.kind === "churn" && !x.recovered);
      if (!recent && before >= CHURN_MIN_VISITS && !open) {
        const alert = { kind: "churn", buyer: name, at: kst(now), visits_before: before, last: b.last };
        crm.alerts.push(alert);
        this.log("customer_alert", alert);
      } else if (recent && open) {
        open.recovered = kst(now);
        this.log("customer_back", { buyer: name, at: open.recovered });
      }
      if (!b.announced && visits(b, hourNow - 3600, hourNow + 3600) >= 5 && b.first > nowS - 2 * 3600) {
        b.announced = true;
        const alert = { kind: "new", buyer: name, at: kst(now), visits_last_hours: visits(b, hourNow - 3600, hourNow + 3600) };
        crm.alerts.push(alert);
        this.log("customer_new", alert);
      }
    }
    crm.alerts = crm.alerts.slice(-50);
    crm.pages = pages;
    await this.storage.put("crm", crm);
    return crm;
  }
  async trialState() {
    const t = (await this.storage.get("askTrial")) || { rate: ASK_TRIAL_DEFAULT_RATE, since: kst(Date.now()), arms: newArms() };
    return t;
  }
  // arm stats: tasks opened, deals, profit over reserve, deals at or above the opening ask, lost
  async trialCount(arm, field, amount = 1) {
    if (!arm) return;
    const t = await this.trialState();
    if (!Object.hasOwn(t.arms, arm)) return;
    t.arms[arm][field] = (t.arms[arm][field] || 0) + amount;
    await this.storage.put("askTrial", t);
  }
  async customers() {
    const crm = (await this.storage.get("crm")) || { buyers: {}, alerts: [] };
    const nowS = Date.now() / 1000;
    const hr = (h) => +String(h).replace(/^h/, "");
    const rows = Object.entries(crm.buyers).map(([key, b]) => ({
      buyer: key.startsWith("b:") ? key.slice(2) : key,
      visits_24h: Object.values(b.hours).reduce((a, c) => a + c, 0),
      visits_this_and_last_hour: Object.entries(b.hours).reduce((n, [h, c]) => (hr(h) >= Math.floor(nowS / 3600) * 3600 - 3600 ? n + c : n), 0),
      deals: b.deals, last_seen: b.last ? kst(b.last.ts * 1000) : null, last_stars: b.last?.stars ?? null,
      last_review: b.last?.review || "",
    })).sort((a, b) => b.visits_24h - a.visits_24h);
    const ask_trial = await this.trialState();
    return { updated: kst(Date.now()), cursor: crm.after, ask_trial, customers: rows.slice(0, 20),
      open_alerts: crm.alerts.filter((x) => x.kind === "churn" && !x.recovered), alerts: crm.alerts.slice(-20) };
  }

  // nearest-rank p75 and max of other shops' counted prices in the window, per item (null below PRICE_MIN_SAMPLES)
  async windowStats() {
    const hist = (await this.storage.get("priceHist")) || {};
    const cutoff = Date.now() / 1000 - PRICE_WINDOW_MS / 1000;
    const out = {};
    for (const item of Object.keys(RESERVES)) {
      const prices = [];
      for (const [k, byItem] of Object.entries(hist)) {
        if (+k.slice(1) < cutoff) continue;
        for (const [p, n] of Object.entries(byItem[item] || {})) for (let i = 0; i < Math.min(n, 5000); i++) prices.push(+p.slice(1));
      }
      out[item] = prices.length >= PRICE_MIN_SAMPLES ? { n: prices.length, p75: quantile(prices, 0.75), max: Math.max(...prices) } : null;
    }
    return out;
  }
  async scan(force = false) {
    const res = await fetch(`${MARKET}/api/trades`, { headers: { accept: "application/json" } });
    if (!res.ok) { this.log("market_scan_failed", { status: res.status }); return null; }
    const trades = await res.json();
    const state = (await this.storage.get("scanState")) || {};
    const cur = marketShares(trades);
    const surges = state.shares ? detectSurge(state.shares, cur) : [];
    const t = Date.now();
    let reason = null;
    if (surges.length) reason = "share surge: " + surges.map((x) => `${x.shop} ${(x.share_before * 100).toFixed(0)}%->${(x.share_now * 100).toFixed(0)}%`).join(", ");
    else if (force) reason = "manual";
    else if (!state.last || t - state.last >= SCAN_EVERY_MS) reason = "scheduled (1 h)";
    state.shares = cur;
    let scan = null;
    if (reason) {
      // reference prices leave our own trades out: at ~25 % of the market our floor sales pulled the p75 down
      scan = { ts: t, reason, surges, ...summarize(trades.filter((x) => !SELF_NAMES.includes(x.shop)), surges.map((x) => x.shop)) };
      const w = await this.windowStats();
      for (const [item, v] of Object.entries(w)) {
        if (!v) continue;
        scan.items[item] = { ...(scan.items[item] || {}), p75: v.p75, max: v.max, window_n: v.n, source: "ledger 2 h, other shops" };
      }
      await this.storage.put("market", scan);
      const last = (await this.storage.get("lastMedians")) || {};
      for (const [item, v] of Object.entries(scan.items)) last[item] = v.p75 ?? v.median;   // key name kept for stored state
      await this.storage.put("lastMedians", last);
      state.last = t;
      this.log("market_scan", { reason, items: scan.items, leaders: scan.leaders });
    }
    await this.storage.put("scanState", state);
    return scan;
  }
  async savePricing() { await this.storage.put("pricing", await this.pricing()); }
  async priceEvent(item, event, extra) {
    const s = await this.item(item);
    this.log("pricing", { item, event, markup: +s.markup.toFixed(3), ask: await this.ask(item), visits: s.visits,
      deals: s.deals, avg_price: s.deals ? +(s.revenue / s.deals).toFixed(1) : null, ...extra });
  }
  async opened(item) { (await this.item(item)).visits += 1; await this.savePricing(); }
  async dealDone(item, price, ask, rounds) {
    const s = await this.item(item);
    s.deals += 1; s.revenue += price;
    const old = s.markup;
    const launch = (await this.launchCap()) !== null;
    if (rounds === 0 && price >= ask && !launch) { s.took_ask += 1; s.markup = Math.min(MAX_MARKUP, old + STEP_UP); }
    await this.savePricing();
    if (launch) await this.countLaunchDeal();
    await this.priceEvent(item, "deal", { price, rounds, markup_change: +(s.markup - old).toFixed(3) });
  }
  async lost(item, offers) {
    const s = await this.item(item);
    const old = s.markup;
    let why;
    if (!offers.length && (await this.launchCap()) !== null) why = "left at a launch ask; no change";
    else if (!offers.length) { s.left_at_ask += 1; s.markup = Math.max(MIN_MARKUP, old - STEP_DOWN); why = "left after the opening ask"; }
    else if (Math.max(...offers) < RESERVES[item]) { s.below_floor += 1; why = "best offer below reserve"; }
    else why = "left mid-negotiation";
    await this.savePricing();
    await this.priceEvent(item, "lost", { offers, why, markup_change: +(s.markup - old).toFixed(3) });
  }

  // ---- task bookkeeping
  async sweep() {
    const t = Date.now();
    for (const [id, seen] of this.openIdx) {
      if (t - seen <= STALE_MS) continue;
      this.openIdx.delete(id);
      const task = await this.storage.get(`task:${id}`);
      const deal = task?._deal;
      if (!deal) continue;
      delete task._deal;
      await this.storage.put(`task:${id}`, task);
      await this.lost(deal.item, deal.offers);
      await this.trialCount(deal.arm, "lost");
      if (deal.cArm) await this.counterResult(deal.item, deal.cArm, 0, false);
      this.log("lost", { task_id: id, item: deal.item, offers: deal.offers });
    }
  }
  async saveTask(task) {
    await this.storage.put(`task:${task.id}`, task);
  }

  agentMessage(task, text) {
    return { messageId: uuid(), contextId: task.contextId, taskId: task.id, role: "ROLE_AGENT", parts: [{ text }] };
  }
  setStatus(task, state, text) {
    task.status = { state, message: this.agentMessage(task, text), timestamp: now() };
  }

  async fetch(request) {
    try { return await this.handle(request); }
    finally {
      const force = new URL(request.url).pathname === "/internal/scan";   // cron: write counters too
      try { await this.storage.flush(force, (id) => this.openIdx.has(id)); }
      catch (e) { this.log("flush_failed", { error: String(e).slice(0, 200) }); }
    }
  }
  async handle(request) {
    const path = new URL(request.url).pathname;
    if (path === "/internal/quote") return Response.json(await this.quote());
    if (path === "/internal/scan") {
      let crm = null;
      try { crm = await this.updateCustomers(); } catch (e) { this.log("customer_watch_failed", { error: String(e) }); }
      return Response.json({ scan: await this.scan(request.method === "PUT"), customer_pages: crm?.pages ?? null });
    }
    if (path === "/internal/customers") return Response.json(await this.customers());
    if (path === "/internal/trial") {
      const cfg = await request.json().catch(() => ({}));
      const rate = Number(cfg?.rate);
      if (!Number.isFinite(rate) || rate < 0 || rate > 1) return Response.json({ error: "rate must be 0..1" }, { status: 400 });
      const t = await this.trialState();
      if (cfg.reset) { t.arms = newArms(); t.since = kst(Date.now()); }
      t.rate = rate; t.changed = kst(Date.now());
      await this.storage.put("askTrial", t);
      this.log("trial_rate", { rate });
      return Response.json(t);
    }
    if (path === "/internal/launch") {
      const cfg = await request.json().catch(() => ({}));
      const l = cfg.stop ? { active: false, ended: Date.now() }
        : { active: true, flat: +cfg.flat || 10, ramp: +cfg.ramp || 20, deals: 0, started: Date.now() };
      await this.storage.put("launch", l);
      this.log("launch", l);
      return Response.json(l);
    }
    let body;
    try { body = await request.json(); } catch { return rpcError(null, -32700, "Parse error"); }
    const id = body?.id ?? null;
    if (body?.jsonrpc !== "2.0" || typeof body.method !== "string") return rpcError(id, -32600, "Invalid Request");
    const version = request.headers.get("A2A-Version");
    if (version && version !== "1.0") return rpcError(id, -32009, `A2A version '${version}' is not supported. Expected '1.0'.`);
    try {
      if (body.method === "SendMessage") {
        const ip = request.headers.get("CF-Connecting-IP") || "unknown";
        return rpcResult(id, { task: await this.sendMessage(body.params || {}, ip) });
      }
      if (body.method === "GetTask") {
        const task = await this.storage.get(`task:${String(body.params?.id).slice(0, 100)}`);
        return task ? rpcResult(id, pub(task)) : rpcError(id, -32001, "Task not found");
      }
      if (body.method === "CancelTask") {
        const task = await this.storage.get(`task:${String(body.params?.id).slice(0, 100)}`);
        if (!task) return rpcError(id, -32001, "Task not found");
        if (!task._deal) return rpcError(id, -32002, "Task cannot be canceled");
        delete task._deal; this.openIdx.delete(task.id);
        this.setStatus(task, "TASK_STATE_CANCELED", "Canceled.");
        await this.saveTask(task);
        return rpcResult(id, pub(task));
      }
      return rpcError(id, -32601, "Method not found");
    } catch (e) {
      if (e && e.rpc) return rpcError(id, e.rpc, e.message);
      this.log("error", { message: String(e && e.stack || e) });
      return rpcError(id, -32603, "Internal error");
    }
  }

  allowNewTask(ip) {
    const t = Date.now();
    this.rate ||= new Map();
    const recent = (this.rate.get(ip) || []).filter((x) => t - x < 60_000);
    if (recent.length >= NEW_TASKS_PER_MIN_PER_IP) { this.rate.set(ip, recent); return false; }
    recent.push(t);
    this.rate.set(ip, recent);
    if (this.rate.size > 5000) this.rate.clear();         // never let the limiter itself grow without bound
    return true;
  }

  async sendMessage(params, ip = "unknown") {
    const msg = params.message;
    if (!msg || !Array.isArray(msg.parts)) throw Object.assign(new Error("message with parts is required"), { rpc: -32602 });
    const text = msg.parts.map((p) => (typeof p.text === "string" ? p.text : "")).join(" ").trim();
    if (text.length > MAX_TEXT) throw Object.assign(new Error(`message longer than ${MAX_TEXT} characters`), { rpc: -32602 });
    if (!msg.taskId && !this.allowNewTask(ip)) {
      this.log("rate_limited", { ip_hash: ip.length });     // no IP in the logs
      throw Object.assign(new Error("Too many new tasks; try again in a minute"), { rpc: -32005 });
    }
    await this.sweep();

    let task = msg.taskId ? await this.storage.get(`task:${String(msg.taskId).slice(0, 100)}`) : null;
    if (msg.taskId && !task) throw Object.assign(new Error("Task not found"), { rpc: -32001 });
    if (task && ["TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"].includes(task.status.state)) {
      throw Object.assign(new Error("Task is already in a terminal state"), { rpc: -32004 });
    }
    if (!task) {
      const tid = uuid();
      const cid = typeof msg.contextId === "string" && msg.contextId.length <= 100 ? msg.contextId : uuid();
      task = { id: tid, contextId: cid, status: { state: "TASK_STATE_SUBMITTED", timestamp: now() }, history: [] };
    }
    if (task.status.message) task.history.push(task.status.message);
    task.history.push({ messageId: typeof msg.messageId === "string" ? msg.messageId.slice(0, 100) : uuid(), contextId: task.contextId, taskId: task.id,
      role: "ROLE_USER", parts: [{ text }] });
    if (task.history.length > MAX_HISTORY) task.history = task.history.slice(-MAX_HISTORY);

    if (!task._deal && this.openIdx.size >= MAX_OPEN) {   // flood guard: stop tracking the stalest, no pricing signal
      const stalest = [...this.openIdx].sort((a, b) => a[1] - b[1]).slice(0, this.openIdx.size - MAX_OPEN + 1);
      for (const [k] of stalest) this.openIdx.delete(k);
    }
    const open = task._deal ? { [task.id]: task._deal } : {};
    const deal = open[task.id];
    if (!deal) await this.openDeal(task, text, open);
    else await this.continueDeal(task, text, deal, open);

    if (open[task.id]) { task._deal = open[task.id]; this.openIdx.set(task.id, Date.now()); }
    else { delete task._deal; this.openIdx.delete(task.id); }
    await this.saveTask(task);
    return pub(task);
  }

  async openDeal(task, text, open) {
    const item = findItem(text);
    if (!item) {
      this.setStatus(task, "TASK_STATE_REJECTED", "We sell lamps, chairs and bookshelves. Name one of them to start.");
      return;
    }
    const reserve = RESERVES[item];
    const exp = await this.trialState();
    const arm = bucket(task.id) < exp.rate ? "high" : "control";
    let ask = await this.ask(item, true);
    if (arm === "high") ask = Math.max(ask, await this.highAsk(item));
    await this.opened(item);
    const deal = { item, reserve, ask, opening_ask: ask, rounds: 0, offers: [], seen: Date.now(), arm };
    await this.trialCount(arm, "opened");
    open[task.id] = deal;
    this.log("opening", { task_id: task.id, item, ask, reserve, arm });
    const named = openingOffer(text);
    if (named !== null && named >= ask && named <= IMPLAUSIBLE_FACTOR * Math.max(ask, reserve)) {           // "a lamp for 110": close in one turn
      deal.offers.push(named);
      await this.accept(task, deal, named, open);
      return;
    }
    if (named !== null && named >= reserve && named < ask) {       // a first offer under our ask: one learned counter
      deal.offers.push(named);
      await this.counterOffer(task, deal, named);
      return;
    }
    if (named !== null && named > 0 && named < reserve) {          // "a lamp for 95": show the floor now
      deal.offers.push(named);
      Object.assign(deal, { rounds: 1, floorQuotes: 1, ask: reserve });
      this.log("response", { task_id: task.id, item, offer: named, reserve, decision: "counter", decision_price: reserve });
      this.setStatus(task, "TASK_STATE_INPUT_REQUIRED", counterMessage(named, reserve, reserve, item));
      return;
    }
    this.setStatus(task, "TASK_STATE_INPUT_REQUIRED", openingMessage(item, ask, reserve));
  }

  async continueDeal(task, text, deal, open) {
    deal.seen = Date.now();
    const { reserve, ask, rounds } = deal;
    const { value: offer, problem } = parsePrice(text);
    if (problem) {                                       // never guess what "185,000" or "-5" meant
      this.log("price_refused", { task_id: task.id, text: text.slice(0, 80), problem });
      this.setStatus(task, "TASK_STATE_INPUT_REQUIRED",
        `We could not read that price (${problem}). Send your offer as a plain whole number, for example ${ask}. Current ask is ${ask}.`);
      return;
    }
    if (offer !== null && offer > IMPLAUSIBLE_FACTOR * Math.max(ask, reserve)) {   // 1850 for a 185 chair: a typo
      this.log("price_refused", { task_id: task.id, offer, problem: "far above ask" });
      this.setStatus(task, "TASK_STATE_INPUT_REQUIRED",
        `That offer (${offer}) is far above our ask, so we did not take it. Send the price you mean as a plain number. Current ask is ${ask}.`);
      return;
    }
    if (offer === null) {
      if (saysAccept(text)) return this.accept(task, deal, ask, open);
      this.setStatus(task, "TASK_STATE_INPUT_REQUIRED", `Current ask is ${ask}. Send your offer as a number.`);
      return;
    }
    deal.offers.push(offer);
    // the same offer twice in a row (at or above the floor): the buyer will not move, so sign it now instead of
    // another counter that only costs a round and a star (10/23: fair-furniture repeated the floor through both counters)
    if (offer >= reserve && deal.offers.length >= 2 && offer === deal.offers.at(-2)) return this.accept(task, deal, offer, open);
    if (offer >= reserve && offer < ask && deal.offers.length <= COUNTERED_OFFERS) return this.counterOffer(task, deal, offer);
    const d = fastRespond(reserve, ask, offer, rounds);
    this.log("response", { task_id: task.id, item: deal.item, offer, reserve, decision: d.action, decision_price: d.price });
    if (d.action === "accept" && offer >= reserve) return this.accept(task, deal, offer, open);
    deal.rounds = rounds + 1;
    if (deal.rounds >= MAX_ROUNDS || shouldEnd(reserve, deal.offers, deal.floorQuotes || 0)) {
      delete open[task.id];
      await this.lost(deal.item, deal.offers);
      await this.trialCount(deal.arm, "lost");
      if (deal.cArm) await this.counterResult(deal.item, deal.cArm, 0, false);
      this.log("ended", { task_id: task.id, item: deal.item, offers: deal.offers });
      this.setStatus(task, "TASK_STATE_REJECTED", endMessage(deal.item, reserve));
      return;
    }
    deal.ask = Math.max(reserve, Math.min(ask, d.price ?? reserve));
    if (deal.ask <= reserve) deal.floorQuotes = (deal.floorQuotes || 0) + 1;
    this.setStatus(task, "TASK_STATE_INPUT_REQUIRED", counterMessage(offer, deal.ask, reserve, deal.item));
  }

  async accept(task, deal, price, open) {
    price = Math.trunc(price);
    if (deal.cArm) await this.counterResult(deal.item, deal.cArm, price - deal.reserve, true);
    if (deal.arm) {
      await this.trialCount(deal.arm, "deals");
      await this.trialCount(deal.arm, "profit", price - deal.reserve);
      if (price >= deal.opening_ask) await this.trialCount(deal.arm, "took_ask");
    }
    const receipt = await signReceipt(this.env, { shop: this.env.SHOP_NAME, item: deal.item, price, task: task.id });
    task.artifacts = [{ artifactId: uuid(), name: "receipt", parts: [{ data: receipt }] }];
    this.setStatus(task, "TASK_STATE_COMPLETED", `Deal: ${deal.item} at ${price}. Signed receipt attached.`);
    delete open[task.id];
    await this.dealDone(deal.item, price, deal.opening_ask, deal.rounds);
    this.log("deal", { task_id: task.id, item: deal.item, price, rounds: deal.rounds });
  }
}
