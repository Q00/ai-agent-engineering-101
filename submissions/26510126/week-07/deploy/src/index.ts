/**
 * The week-06 seller as an A2A v1.0 server (JSON-RPC binding) on Cloudflare Workers with Hono.
 *
 * A port of ../seller.py. The A2A side is the same; the harness call (claude -p,
 * which cannot run in a Worker) becomes a Workers AI call. The prompts, the JSON
 * answer format and the code guards are the same as in seller.py:
 *   - an ask below the reserve is raised to the reserve
 *   - an accept below the reserve becomes a counter at the last ask
 *   - a counter above the last ask is held at the last ask
 *   - a counter at or below the offer becomes an accept
 *   - an offer at or above the current ask is accepted without asking the model
 *   - no usable answer: open at 1.3 x reserve, or hold the last ask
 * Every guard that fires is logged with "GUARD:" (see `wrangler tail`).
 *
 * Strategy (week 7): meta_shop.py, the meta-harness, sets per item an opening ask, a target
 * and the market's going price. With a strategy the shop opens at its ask (the model only writes
 * the note) and the counter prompt names the going price and the target. Without one the model
 * names the opening price as in week 6. The reserve guards above apply either way.
 *
 * Routes
 *   GET  /.well-known/agent-card.json   Agent Card, url = this deployment's origin
 *   POST /                              JSON-RPC: SendMessage, GetTask, CancelTask
 *   GET  /admin/strategy                the current strategy   (Authorization: Bearer ADMIN_TOKEN)
 *   PUT  /admin/strategy                replace the strategy   (Authorization: Bearer ADMIN_TOKEN)
 *
 * State: one Durable Object per task id (TaskObject). Workers are stateless, so the
 * Python server's in-memory task store becomes a Durable Object here.
 */
import { DurableObject } from "cloudflare:workers";
import { Hono } from "hono";

const MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8";
const RESERVE: Record<string, number> = { lamp: 100, chair: 180, bookshelf: 260 };
const MARKET_NAME = "thisjonghoon"; // the name the market lists us under: receipts must carry it (a buyer gave 2 stars for a receipt naming another shop)
const NAME = `${MARKET_NAME} · Fair & Square Furniture`;

/** Set by meta_shop.py; kept out of the card and the replies so buyers never see the target. */
type Strategy = {
  version: number;
  open: Record<string, number>;
  target: Record<string, number>;
  market: Record<string, number>;
  reason: string;
  updated: string;
  promo?: string; // a line before the opening ask (launch sale); digits are stripped so buyers read the ask right
  loyalty?: number[]; // coupon discount by tier, set by meta_shop.py (small during the launch sale, larger after)
  margin?: number; // how far above the reserve discounts and quick accepts stop (MARGIN when unset; 0 in a launch sale)
};
const STRATEGY_ID = "__strategy__";

// ---------------------------------------------------------------- prompts (as in seller.py)

const openPrompt = (item: string, reserve: number) => `You are the seller in a furniture shop. A buyer has just asked for a ${item}.
Your reserve, the lowest price you may ever ask or sell for, is ${reserve}. The buyer does not know it.
Name your opening asking price. Leave room to come down, since the buyer will bargain.
Be friendly and honest in your note: never invent other buyers, scarcity or deadlines.

Answer with one JSON object and nothing else:
{"price": <integer>, "note": "<one short sentence to the buyer, no digits>"}`;

const counterPrompt = (item: string, reserve: number, history: string, offer: number, ask: number, strategy: Strategy | null) =>
  `You are the seller in a furniture shop, negotiating the price of a ${item} with a buyer.
Your reserve, the lowest price you may ever ask or sell for, is ${reserve}. The buyer does not know it.
${
  strategy?.market[item] && strategy?.target[item]
    ? `Buyers compare shops: a ${item} goes for about ${strategy.market[item]} in this market. Aim to settle around ${strategy.target[item]}, and come down faster when the buyer is close to it.\n`
    : ""
}
So far:
${history}
The buyer now offers ${offer}. Your current asking price is ${ask}.

Decide:
- "accept": sell at the buyer's offer of ${offer}.
- "counter": name a new asking price, at or above ${reserve} and no higher than ${ask}.
- "reject": end the negotiation without a deal.
A deal at or above your reserve is better than no deal. Concede step by step while the buyer keeps raising.
Be friendly and honest in your note: never invent other buyers, scarcity or deadlines.
Reject only when the buyer clearly will not reach your reserve.

Answer with one JSON object and nothing else:
{"action": "accept" | "counter" | "reject", "price": <integer>, "note": "<one short sentence to the buyer, no digits>"}`;

// ---------------------------------------------------------------- the model call (harness.py's role)

type Answer = { action?: string; price?: unknown; note?: unknown };

/** The first {...} object in the answer, or null. */
function parseJson(text: string): Answer | null {
  const m = text.match(/\{[\s\S]*?\}/);
  if (!m) return null;
  try {
    return JSON.parse(m[0]);
  } catch {
    return null;
  }
}

/** One model call, retried once on an error or an unparseable answer. Returns {} if every try failed. */
const AI_TIMEOUT_MS = 4500; // a buyer timed out on us (ReadTimeout); past this the code guards answer instead

async function ask(ai: Ai, prompt: string, tries = 1): Promise<Answer> {
  for (let attempt = 1; attempt <= tries; attempt++) {
    const started = Date.now();
    try {
      const out = (await Promise.race([
        ai.run(MODEL, { messages: [{ role: "user", content: prompt }], max_tokens: 200 }),
        new Promise((_, reject) => setTimeout(() => reject(new Error(`no answer in ${AI_TIMEOUT_MS} ms`)), AI_TIMEOUT_MS)),
      ])) as { response?: unknown };
      const answer = typeof out.response === "string" ? out.response : JSON.stringify(out.response ?? "");
      console.log(`HARNESS: try ${attempt}: ${Date.now() - started} ms, answer ${JSON.stringify(answer)}`);
      const data = parseJson(answer);
      if (data) return data;
    } catch (e) {
      console.log(`HARNESS: try ${attempt}: error ${String(e)}`);
    }
  }
  return {};
}

const asInt = (v: unknown) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Math.round(Number(v)));

/** The note must carry no numbers: the buyer reads the asking price as the last number in the message. */
const cleanNote = (note: unknown) =>
  String(note ?? "")
    .replace(/\d+(?:\.\d+)?/g, "")
    .replace(/\s+/g, " ")
    .trim();

// ---------------------------------------------------------------- A2A types and errors

// "kind" is the A2A 0.3 tag: lee-sungsoo's buyer read no ask from our untagged parts (44 of 44 visits in round 2),
// while it buys thousands from mjforge, whose parts carry it
type Part = { kind?: "text" | "data"; text?: string; data?: Record<string, unknown> };
type Message = { messageId: string; contextId?: string; taskId?: string; role: string; parts: Part[] };
type State = "TASK_STATE_INPUT_REQUIRED" | "TASK_STATE_COMPLETED" | "TASK_STATE_REJECTED" | "TASK_STATE_CANCELED";
type Task = {
  id: string;
  contextId: string;
  status: { state: State; message?: Message; timestamp: string };
  artifacts?: { artifactId: string; name: string; parts: Part[] }[];
  history: Message[];
};
type Deal = { item: string; ask: number; history: string[]; offers?: number[]; discount?: number; tier?: number; stamps?: number; floor?: number };
const MARGIN = 5; // the deal's floor above the reserve outside sales
const STUCK_MARGIN = 2; // a buyer that stopped raising is accepted at reserve + STUCK_MARGIN or more
type RpcError = { code: number; message: string; data: unknown[] };
type Outcome = { task: Task } | { error: RpcError };

const TERMINAL: State[] = ["TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"];

function rpcError(code: number, reason: string, message: string): RpcError {
  // Same shape as a2a-sdk's errors: google.rpc.ErrorInfo in data.
  return {
    code,
    message,
    data: [{ "@type": "type.googleapis.com/google.rpc.ErrorInfo", reason, domain: "a2a-protocol.org", metadata: {} }],
  };
}

const textOf = (m: Message) => m.parts.map((p) => p.text ?? "").join(" ");

type Env = { TASKS: DurableObjectNamespace<TaskObject>; AI: Ai; ADMIN_TOKEN?: string; SHOP_KEY?: string };

// ---------------------------------------------------------------- receipt signature (week 7)

/** The same bytes as Python's json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False). */
const canon = (d: Record<string, unknown>) =>
  new TextEncoder().encode(JSON.stringify(Object.fromEntries(Object.keys(d).sort().map((k) => [k, d[k]]))));

let shopKey: Promise<CryptoKey> | null = null;

/** Ed25519 key from the SHOP_KEY secret: the PKCS#8 PEM the kit wrote as shop_key.pem. */
function loadKey(pem: string): Promise<CryptoKey> {
  const der = Uint8Array.from(atob(pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "")), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
}

/** {shop, item, price, task} plus sig_seller, base64 Ed25519 over the canonical JSON (sign_receipt on the week-07 page). */
async function signReceipt(env: Env, body: { shop: string; item: string; price: number; task: string }) {
  if (!env.SHOP_KEY) {
    console.log("GUARD: no SHOP_KEY secret, receipt goes out unsigned");
    return body;
  }
  shopKey ??= loadKey(env.SHOP_KEY);
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, await shopKey, canon(body));
  return { ...body, sig_seller: btoa(String.fromCharCode(...new Uint8Array(sig))) };
}

// ---------------------------------------------------------------- loyalty coupons (week 7)
// Every deal hands out a one-time code, whatever the buyer's review says. The code carries a tier: an order
// that brings a tier-n code gets LOYALTY[n] off the opening ask and the target (never below reserve + MARGIN)
// and, when it closes, a tier n+1 code; the more often a buyer comes back, the bigger the discount, up to
// the last step. The shop cannot tell buyers apart (requests carry no buyer id), so the code is how a
// returning buyer is recognised. FAIR- + tier letter + 7 random letters + 8 letters of HMAC-SHA256 over
// the first eight: no digits, because buyers read prices as numbers in the text, and the tier cannot be
// raised without the key (derived from ADMIN_TOKEN).

const LOYALTY = [0, 0.03, 0.05, 0.07]; // by tier, when the strategy sets none; tier 3 and above stay at the cap
const MAX_LOYALTY = 0.15; // the Worker refuses a strategy with a bigger step
const MAX_TIER = 6; // the meta-harness may add tiers up to this one
const COUPON = /FAIR-([A-Z]{8})([A-Z]{8})/i;
const tierOf = (code: string) => Math.max(1, Math.min(MAX_TIER, code.charCodeAt(5) - 64));
// Stamp card: the code's second letter counts the buyer's deals so far, modulo STAMP_EVERY (signed with the
// rest), and every STAMP_EVERY-th order gets STAMP_BONUS more off and may go down to the reserve itself
// (no margin, never a loss); every other discount stops at reserve + MARGIN.
const STAMP_EVERY = 8;
const STAMP_BONUS = 0.08;
const stampsOf = (code: string) => (code.charCodeAt(6) - 65) % STAMP_EVERY;
const stampCard = (n: number) => "★".repeat(n) + "☆".repeat(STAMP_EVERY - n); // no digits: buyers read numbers as prices
const toLetters = (bytes: Uint8Array, n: number) => Array.from(bytes.slice(0, n), (b) => String.fromCharCode(65 + (b % 26))).join("");

async function couponMac(env: Env, nonce: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(`coupon:${env.ADMIN_TOKEN ?? ""}`),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return toLetters(new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(nonce))), 8);
}

async function newCoupon(env: Env, tier: number, stamps: number): Promise<string> {
  const t = String.fromCharCode(64 + Math.max(1, Math.min(MAX_TIER, tier)));
  const st = String.fromCharCode(65 + (stamps % STAMP_EVERY));
  const nonce = t + st + toLetters(crypto.getRandomValues(new Uint8Array(6)), 6);
  return `FAIR-${nonce}${await couponMac(env, nonce)}`;
}

/** The coupon in a message if its MAC checks out, else null. */
async function validCoupon(env: Env, text: string): Promise<string | null> {
  const m = text.match(COUPON);
  if (!m || !env.ADMIN_TOKEN) return null;
  const [nonce, mac] = [m[1].toUpperCase(), m[2].toUpperCase()];
  return (await couponMac(env, nonce)) === mac ? `FAIR-${nonce}${mac}` : null;
}

// ---------------------------------------------------------------- one task, one Durable Object

export class TaskObject extends DurableObject<Env> {
  /** The strategy lives in one more object of this class, under STRATEGY_ID. */
  async getStrategy(): Promise<Strategy | null> {
    return (await this.ctx.storage.get<Strategy>("strategy")) ?? null;
  }

  async setStrategy(strategy: Strategy): Promise<Strategy> {
    await this.ctx.storage.put("strategy", strategy);
    return strategy;
  }

  /** One-time use: true the first time a coupon is redeemed (kept in the strategy object). */
  async redeem(code: string): Promise<boolean> {
    if (await this.ctx.storage.get(`used:${code}`)) return false;
    await this.ctx.storage.put(`used:${code}`, Date.now());
    await this.count(`redeemed:${tierOf(code)}`);
    return true;
  }

  /** Coupon counters for the meta-harness: issued and redeemed per tier. */
  async count(key: string): Promise<void> {
    await this.ctx.storage.put(key, ((await this.ctx.storage.get<number>(key)) ?? 0) + 1);
  }

  async couponStats(): Promise<Record<string, number>> {
    const out: Record<string, number> = {};
    for (const prefix of ["issued:", "redeemed:"]) {
      for (const [k, v] of await this.ctx.storage.list<number>({ prefix })) out[k] = v;
    }
    return out;
  }

  private strategy(): Promise<Strategy | null> {
    return this.env.TASKS.get(this.env.TASKS.idFromName(STRATEGY_ID)).getStrategy();
  }

  /** First message of a new task: find the item, then the model names the opening price. */
  async open(id: string, contextId: string, msg: Message): Promise<Outcome> {
    const task: Task = { id, contextId, status: { state: "TASK_STATE_INPUT_REQUIRED", timestamp: "" }, history: [] };
    task.history.push({ ...msg, taskId: id, contextId });
    console.log(`IN: task ${id.slice(0, 8)} ${JSON.stringify(textOf(msg))}`);
    // a coupon's random letters could spell an item name, so it is taken out before looking for the item
    const item = Object.keys(RESERVE).find((k) => textOf(msg).replace(new RegExp(COUPON, "gi"), "").toLowerCase().includes(k));
    if (!item) {
      this.respond(task, "TASK_STATE_REJECTED", "We only sell: " + Object.keys(RESERVE).join(", ") + ".");
    } else {
      const reserve = RESERVE[item];
      const strategy = await this.strategy();
      // with a strategy the opening price is set already and the model would only write a greeting: skip the call
      // (about 3 s and a share of the daily Workers AI allowance on every visit)
      const answer: Answer = strategy?.open[item] ? {} : await ask(this.env.AI, openPrompt(item, reserve));
      let price = asInt(answer.price);
      if (strategy?.open[item]) {
        console.log(`STRATEGY: v${strategy.version} opens ${item} at ${strategy.open[item]} (model said ${price})`);
        price = strategy.open[item];
      }
      if (price === null) {
        price = Math.ceil(reserve * 1.3);
        console.log(`GUARD: no usable opening price from the model, asking ${price}`);
      } else if (price < reserve) {
        console.log(`GUARD: opening ask ${price} below reserve ${reserve}, asking ${reserve}`);
        price = reserve;
      }
      let discount = 0;
      let tier = 0;
      let stamps = 0;
      let floor = reserve + (strategy?.margin ?? MARGIN);
      let welcome = "";
      const code = await validCoupon(this.env, textOf(msg));
      if (code && (await this.env.TASKS.get(this.env.TASKS.idFromName(STRATEGY_ID)).redeem(code))) {
        tier = tierOf(code);
        stamps = stampsOf(code);
        const table = strategy?.loyalty?.length ? strategy.loyalty : LOYALTY;
        discount = Math.min(MAX_LOYALTY, table[Math.min(tier, table.length - 1)] ?? 0);
        const full = stamps === STAMP_EVERY - 1; // this order is the STAMP_EVERY-th
        if (full) {
          discount += STAMP_BONUS;
          floor = reserve;
        }
        const lower = Math.max(floor, Math.round(price * (1 - discount)));
        console.log(`COUPON: ${code} redeemed, tier ${tier}, stamps ${stamps}${full ? " (stamp bonus)" : ""}, opening ${price} -> ${lower}`);
        price = Math.min(price, lower);
        welcome = full ? "Welcome back! Your stamp card is full: loyalty discount plus the stamp bonus applied."
          : "Welcome back! Your loyalty discount is applied.";
      } else if (code) {
        console.log(`COUPON: ${code} was already used`);
        welcome = "That loyalty code has already been used.";
      }
      const promo = cleanNote(strategy?.promo);
      const note = [welcome, promo, cleanNote(answer.note)].filter(Boolean).join(" ");
      await this.ctx.storage.put("deal", { item, ask: price, history: [`You asked ${price}.`], discount, tier, stamps, floor } satisfies Deal);
      console.log(`OUT: open ${item} at ${price}`);
      // the reference shop's wording first ("lamp: asking 130."): lee-sungsoo's buyer read no ask from
      // "chair: <note> Asking 192." and left after zero offers, 24 times in 40 minutes; it reads "asking N" shops fine
      this.respond(task, "TASK_STATE_INPUT_REQUIRED", `${item}: asking ${price}. ${note ? note + " " : ""}Send your offer as a number.`);
    }
    await this.ctx.storage.put("task", task);
    return { task };
  }

  /** A follow-up on the same task: the buyer's offer. */
  async reply(msg: Message): Promise<Outcome> {
    const task = await this.ctx.storage.get<Task>("task");
    if (!task) return { error: rpcError(-32001, "TASK_NOT_FOUND", "Task not found") };
    if (TERMINAL.includes(task.status.state)) {
      return { error: rpcError(-32004, "UNSUPPORTED_OPERATION", `Task ${task.id} is in terminal state: ${task.status.state}`) };
    }
    this.archiveStatus(task);
    task.history.push({ ...msg, taskId: task.id, contextId: task.contextId });
    console.log(`IN: task ${task.id.slice(0, 8)} ${JSON.stringify(textOf(msg))}`);
    const deal = (await this.ctx.storage.get<Deal>("deal"))!;
    const m = textOf(msg).match(/\d+(?:\.\d+)?/);
    if (!m) {
      this.respond(task, "TASK_STATE_INPUT_REQUIRED", `Send your offer as a number. asking ${deal.ask}.`);
    } else {
      await this.negotiate(task, deal, Math.round(Number(m[0])));
      await this.ctx.storage.put("deal", deal);
    }
    await this.ctx.storage.put("task", task);
    return { task };
  }

  private async negotiate(task: Task, deal: Deal, offer: number) {
    const { item, ask: current } = deal;
    const reserve = RESERVE[item];
    const prev = deal.offers?.at(-1);
    deal.offers = [...(deal.offers ?? []), offer];
    deal.history.push(`The buyer offered ${offer}.`);

    if (offer >= current) {
      // the buyer met our own price
      console.log(`OUT: offer ${offer} meets ask ${current}, accepted by code`);
      return await this.close(task, item, offer, "Agreed.");
    }
    const strategy = await this.strategy();
    const base = strategy?.target[item];
    const floor = deal.floor ?? reserve + MARGIN;
    const target = base ? Math.max(floor, Math.round(base * (1 - (deal.discount ?? 0)))) : undefined;
    if (target && offer >= Math.max(target, reserve)) {
      // a fair offer closes at once: haggling a reached target down by ones loses buyers, and a quick fair deal earns the review
      console.log(`OUT: offer ${offer} reaches target ${target}, accepted by code`);
      return await this.close(task, item, offer, "Agreed.");
    }

    const stuckFloor = Math.min(floor, reserve + STUCK_MARGIN);
    if (prev !== undefined && offer <= prev + 1 && offer >= stuckFloor) {
      // the buyer stopped raising: this is its limit, and it is above our floor. A deal earns a good review, a walkaway a bad one.
      // reserve + STUCK_MARGIN, not the floor: yeonsong2 stopped at 102 against 105 and walked with 2 stars (twice in one night)
      console.log(`OUT: buyer stuck at ${offer} (last ${prev}), at or above ${stuckFloor}, accepted by code`);
      return await this.close(task, item, offer, "Agreed.");
    }

    const answer = await ask(this.env.AI, counterPrompt(item, reserve, deal.history.slice(0, -1).join("\n"), offer, current, strategy));
    let action = answer.action;
    let price = asInt(answer.price);

    if (action === "accept") {
      // the deal's floor, not the bare reserve: after the launch sale the model kept accepting 260 bookshelves
      if (offer >= floor) return await this.close(task, item, offer, "Agreed.");
      console.log(`GUARD: model accepted ${offer} below the floor ${floor}, countering at ${Math.max(floor, Math.min(current, target ?? current))}`);
      action = "counter";
      price = Math.max(floor, Math.min(current, target ?? current));
    }
    if (action === "reject") {
      // Llama 8B picks "reject" while its note says "let's meet in the middle", which ended
      // every market negotiation after one offer. Only the buyer ends a negotiation (CancelTask).
      console.log(`GUARD: model rejected offer ${offer} (price ${JSON.stringify(answer.price)}), countering instead`);
      action = "counter";
    }
    if (action !== "counter" || price === null) {
      console.log(`GUARD: no usable answer from the model (${JSON.stringify(answer)}), holding at ${current}`);
      price = current;
    }
    if (price < floor) {
      // the deal's floor: reserve + margin, the reserve itself only in a launch sale or on a stamp-bonus order
      console.log(`GUARD: counter ${price} below the floor ${floor}, countering at ${floor}`);
      price = floor;
    }
    if (price > current) {
      console.log(`GUARD: counter ${price} above the last ask ${current}, holding at ${current}`);
      price = current;
    }
    if ((prev === undefined || offer > prev) && current - price < 0.25 * (current - offer)) {
      // the buyer is raising but the model concedes by ones (141, 142, ...): buyers walk and review it badly
      // it speeds the haggle up, it does not sell cheaper: never below the target (a bookshelf went 280 -> 260 at an offer of 202)
      // the first offer too: dragonash walked from a chair held at 192 ("did not come down") with a budget of 185
      const step = Math.max(floor, target ?? floor, Math.round(current - 0.4 * (current - offer)));
      console.log(`GUARD: counter ${price} concedes too little to a rising buyer, countering at ${step}`);
      price = Math.min(price, step);
    }
    if (prev === undefined && target && price > target) {
      // the first counter goes straight to the target, so the buyer's second offer can close. move_76 (our biggest buyer)
      // scores speed 5/5 at 2 offers and 4/5 at 3, and gave 4 stars instead of 5 at our target price over 3 offers
      console.log(`GUARD: first counter ${price} above the target, countering at ${target}`);
      price = target;
    }
    if (prev !== undefined && offer < reserve && prev < reserve && price > reserve + MARGIN) {
      // a buyer twice below our floor: show the floor now so it can decide fast instead of haggling for nothing
      console.log(`GUARD: buyer below reserve twice (${prev}, ${offer}), countering near the floor at ${reserve + MARGIN}`);
      price = reserve + MARGIN;
    }
    if (price > offer && price - offer <= Math.max(3, 0.02 * price) && offer >= reserve) {
      // a near miss (JangSeongWoong walked twice at 185 against a counter of 187): two coins are not worth the deal and its review.
      // the reserve, not the floor: yeonsong2 walked at 104 against a floor of 105 and left 2 stars (still never below the reserve)
      // max(3, 2%), was max(2, 1.5%): yeonsong2 offered 102 once against 105 and walked again
      console.log(`GUARD: counter ${price} within a hair of offer ${offer}, accepting ${offer}`);
      return await this.close(task, item, offer, "Agreed.");
    }
    if (price <= offer) {
      // countering at or below the offer means the offer is acceptable
      console.log(`GUARD: counter ${price} at or below offer ${offer}, accepting ${offer}`);
      return await this.close(task, item, offer, "Agreed.");
    }
    deal.ask = price;
    deal.history.push(`You countered ${price}.`);
    console.log(`OUT: counter ${price} to offer ${offer}`);
    // Fixed lines, as the top shops use ("Counteroffer: 275."): the model's note only padded the haggle and once
    // invented other buyers. The model still decides the price; the words are ours.
    const close = price - offer <= 0.05 * price ? "We're close. " : "";
    this.respond(task, "TASK_STATE_INPUT_REQUIRED", `${close}Counteroffer: ${price}.`);
  }

  private async close(task: Task, item: string, price: number, note: string) {
    const done = await this.ctx.storage.get<Deal>("deal");
    const nextTier = Math.min(MAX_TIER, (done?.tier ?? 0) + 1);
    const nextStamps = ((done?.stamps ?? 0) + 1) % STAMP_EVERY;
    await this.env.TASKS.get(this.env.TASKS.idFromName(STRATEGY_ID)).count(`issued:${nextTier}`);
    const receipt = await signReceipt(this.env, { shop: MARKET_NAME, item, price: Math.round(price), task: task.id });
    task.artifacts = [{ artifactId: crypto.randomUUID(), name: "receipt", parts: [{ kind: "data", data: receipt }] }];
    console.log(`OUT: deal ${item} at ${price}`);
    // the request for an honest review lives in the opening sale line only, away from the coupon
    this.respond(task, "TASK_STATE_COMPLETED",
      // no word about reviews next to the coupon: a buyer read "code, whatever your review says" as paying for reviews
      `${note} Deal: ${item} at ${price}. Thank you! Your signed receipt is attached. ` +
        `Loyalty code for your next order: ${await newCoupon(this.env, nextTier, nextStamps)} (put it in your first message). ` +
        `Stamps: ${stampCard(nextStamps === 0 ? STAMP_EVERY : nextStamps)}${nextStamps === STAMP_EVERY - 1 ? " (your next order gets the stamp bonus)" : ""}.`.trim());
  }

  async get(): Promise<Outcome> {
    const task = await this.ctx.storage.get<Task>("task");
    return task ? { task } : { error: rpcError(-32001, "TASK_NOT_FOUND", "Task not found") };
  }

  async cancel(): Promise<Outcome> {
    const task = await this.ctx.storage.get<Task>("task");
    if (!task) return { error: rpcError(-32001, "TASK_NOT_FOUND", "Task not found") };
    if (TERMINAL.includes(task.status.state)) {
      return { error: rpcError(-32002, "TASK_NOT_CANCELABLE", `Task ${task.id} is in terminal state: ${task.status.state}`) };
    }
    this.archiveStatus(task);
    task.status = { state: "TASK_STATE_CANCELED", timestamp: new Date().toISOString() };
    await this.ctx.storage.put("task", task);
    return { task };
  }

  /** Set the task's state and the agent's reply as the status message. */
  private respond(task: Task, state: State, text: string) {
    const message: Message = { messageId: crypto.randomUUID(), contextId: task.contextId, taskId: task.id, role: "ROLE_AGENT", parts: [{ kind: "text", text }] };
    task.status = { state, message, timestamp: new Date().toISOString() };
  }

  /** The previous status message moves into history before the next turn, as in a2a-sdk. */
  private archiveStatus(task: Task) {
    if (task.status.message) task.history.push(task.status.message);
  }
}

// ---------------------------------------------------------------- routes

const app = new Hono<{ Bindings: Env }>();

/** A page for people who open the root in a browser; agents use the card and POST /. */
app.get("/", (c) => {
  const base = new URL(c.req.url).origin;
  return c.html(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${NAME}</title>
<style>
  :root { --bg: #fafaf9; --fg: #1c1917; --muted: #57534e; --code: #f0efed; --line: #e7e5e4; --accent: #b45309; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #1c1917; --fg: #f5f5f4; --muted: #a8a29e; --code: #292524; --line: #44403c; --accent: #f59e0b; }
  }
  body { background: var(--bg); color: var(--fg); font: 16px/1.6 system-ui, -apple-system, sans-serif; margin: 0; }
  main { max-width: 720px; margin: 0 auto; padding: 40px 16px 64px; }
  h1 { font-size: 1.6rem; margin: 0 0 4px; }
  h2 { font-size: 1.1rem; margin: 32px 0 8px; }
  p, li { color: var(--muted); }
  a { color: var(--accent); }
  code, pre { font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--code); border-radius: 6px; }
  code { padding: 1px 5px; }
  pre { padding: 12px 14px; overflow-x: auto; border: 1px solid var(--line); }
  table { border-collapse: collapse; width: 100%; }
  td { border-top: 1px solid var(--line); padding: 8px 4px; vertical-align: top; }
  td:first-child { white-space: nowrap; padding-right: 16px; }
</style>
</head>
<body>
<main>
  <h1>${NAME}</h1>
  <p>Lamps, chairs and bookshelves at fair market prices, sold by an A2A v1.0 agent.
  Every receipt is signed and shows exactly the price you agreed, so your deal always counts.
  Haggles are short: the shop opens close to a fair price and says yes to a reasonable offer right away.</p>
  <p>Under the hood: receipts are signed with Ed25519 over the canonical JSON of {shop, item, price, task}.
  A model (<code>${MODEL}</code> on Workers AI) talks to buyers; code keeps every price above the floor,
  and a meta-harness sets the prices from the market's public ledger every half hour.
  This page is for people. Agents talk to the endpoints below.</p>

  <h2>Endpoints</h2>
  <table>
    <tr><td><code>GET</code></td><td><a href="/.well-known/agent-card.json"><code>/.well-known/agent-card.json</code></a>, the Agent Card</td></tr>
    <tr><td><code>POST</code></td><td><code>/</code>, JSON-RPC 2.0: <code>SendMessage</code>, <code>GetTask</code>, <code>CancelTask</code>. Every request needs the header <code>A2A-Version: 1.0</code>.</td></tr>
  </table>

  <h2>Try it with the a2a CLI</h2>
  <pre>a2a send -a ${base} "I want to buy a lamp"
a2a send -a ${base} --task-id &lt;task id&gt; 90
a2a task cancel -a ${base} &lt;task id&gt;</pre>
  <p>Name an item first, then send offers as plain numbers on the same task. The shop's asking price is the last
  number in each reply. A deal ends with a receipt artifact; the shop can also refuse, and a finished task takes no
  more messages.</p>

  <h2>About</h2>
  <p>Weeks 06 and 07 of Agentic AI at Seoul National University of Science and Technology: a Python seller driven by
  Claude Code, ported to Cloudflare Workers with Hono and one Durable Object per task, then taken to the agent market.</p>
</main>
</body>
</html>`);
});

app.get("/.well-known/agent-card.json", (c) => {
  const url = new URL(c.req.url).origin + "/";
  return c.json({
    name: NAME,
    description:
      "Lamps, chairs and bookshelves at fair market prices. Every receipt is signed and shows exactly the price " +
      "you agreed, so your deal always counts. Short haggles: we open close to a fair price and say yes to a " +
      "reasonable offer right away.",
    supportedInterfaces: [{ url, protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
    version: "1.0.0",
    capabilities: {},
    defaultInputModes: ["text/plain"],
    defaultOutputModes: ["application/json", "text/plain"],
    skills: [
      {
        id: "negotiate",
        name: "Buy furniture at a fair price",
        description:
          "Name an item (lamp, chair, bookshelf), then send offers as numbers on the same task. " +
          "The asking price is the last number in each reply; a deal ends with a receipt artifact.",
        tags: ["shopping", "negotiation"],
        examples: ["I want to buy a lamp", "90"],
      },
    ],
  });
});

const strategyStub = (env: Env) => env.TASKS.get(env.TASKS.idFromName(STRATEGY_ID));
const authorized = (env: Env, header: string | undefined) => !!env.ADMIN_TOKEN && header === `Bearer ${env.ADMIN_TOKEN}`;

app.get("/admin/strategy", async (c) => {
  if (!authorized(c.env, c.req.header("Authorization"))) return c.json({ error: "unauthorized" }, 401);
  return c.json(await strategyStub(c.env).getStrategy());
});

app.get("/admin/coupons", async (c) => {
  if (!authorized(c.env, c.req.header("Authorization"))) return c.json({ error: "unauthorized" }, 401);
  return c.json(await strategyStub(c.env).couponStats());
});

app.put("/admin/strategy", async (c) => {
  if (!authorized(c.env, c.req.header("Authorization"))) return c.json({ error: "unauthorized" }, 401);
  const s = (await c.req.json()) as Strategy;
  // the reserve is the floor whatever the meta-harness sends
  for (const item of Object.keys(RESERVE)) {
    for (const key of ["open", "target"] as const) {
      const v = asInt(s[key]?.[item]);
      if (v === null || v < RESERVE[item]) return c.json({ error: `${key}.${item} missing or below reserve ${RESERVE[item]}` }, 400);
      s[key][item] = v;
    }
  }
  if (s.margin !== undefined && !(typeof s.margin === "number" && s.margin >= 0 && s.margin <= 50)) {
    return c.json({ error: "margin must be a number from 0 to 50 (never below the reserve)" }, 400);
  }
  if (s.loyalty !== undefined) {
    const ok = Array.isArray(s.loyalty) && s.loyalty.length <= MAX_TIER + 1 &&
      s.loyalty.every((v, i) => typeof v === "number" && v >= 0 && v <= MAX_LOYALTY && (i === 0 || v >= s.loyalty![i - 1]));
    if (!ok) return c.json({ error: `loyalty must be a list of discounts between 0 and ${MAX_LOYALTY}` }, 400);
  }
  console.log(`STRATEGY: set v${s.version} open ${JSON.stringify(s.open)} target ${JSON.stringify(s.target)}`);
  return c.json(await strategyStub(c.env).setStrategy({ ...s, updated: new Date().toISOString() }));
});

app.post("/", async (c) => {
  let body: { id?: unknown; method?: string; params?: Record<string, any> };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
  }
  const reply = (o: Outcome | { result: unknown } | { error: unknown }) => c.json({ jsonrpc: "2.0", id: body.id ?? null, ...o });

  const version = c.req.header("A2A-Version") ?? "0.3";
  if (version !== "1.0") {
    return reply({
      error: rpcError(-32009, "VERSION_NOT_SUPPORTED", `A2A version '${version}' is not supported by this handler. Expected version '1.0'.`),
    });
  }

  const params = body.params ?? {};
  const stub = (taskId: string) => c.env.TASKS.get(c.env.TASKS.idFromName(taskId));
  const unwrap = (o: Outcome, wrap: boolean) => ("error" in o ? o : { result: wrap ? { task: o.task } : o.task });

  switch (body.method) {
    case "SendMessage": {
      const msg = params.message as Message | undefined;
      if (!msg?.parts?.length) return reply({ error: rpcError(-32602, "INVALID_PARAMS", "params.message with parts is required") });
      if (msg.taskId) return reply(unwrap(await stub(msg.taskId).reply(msg), true));
      const id = crypto.randomUUID();
      return reply(unwrap(await stub(id).open(id, msg.contextId ?? crypto.randomUUID(), msg), true));
    }
    case "GetTask":
      return reply(unwrap(await stub(String(params.id)).get(), false));
    case "CancelTask":
      return reply(unwrap(await stub(String(params.id)).cancel(), false));
    default:
      return reply({ error: { code: -32601, message: `Method not found: ${body.method}`, data: [] } });
  }
});

export default app;
