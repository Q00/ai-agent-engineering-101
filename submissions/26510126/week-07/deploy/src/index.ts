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
const NAME = "Fair & Square Furniture";

/** Set by meta_shop.py; kept out of the card and the replies so buyers never see the target. */
type Strategy = {
  version: number;
  open: Record<string, number>;
  target: Record<string, number>;
  market: Record<string, number>;
  reason: string;
  updated: string;
  promo?: string; // a line before the opening ask (launch sale); digits are stripped so buyers read the ask right
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
async function ask(ai: Ai, prompt: string, tries = 2): Promise<Answer> {
  for (let attempt = 1; attempt <= tries; attempt++) {
    const started = Date.now();
    try {
      const out = (await ai.run(MODEL, { messages: [{ role: "user", content: prompt }], max_tokens: 200 })) as {
        response?: unknown;
      };
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

type Part = { text?: string; data?: Record<string, unknown> };
type Message = { messageId: string; contextId?: string; taskId?: string; role: string; parts: Part[] };
type State = "TASK_STATE_INPUT_REQUIRED" | "TASK_STATE_COMPLETED" | "TASK_STATE_REJECTED" | "TASK_STATE_CANCELED";
type Task = {
  id: string;
  contextId: string;
  status: { state: State; message?: Message; timestamp: string };
  artifacts?: { artifactId: string; name: string; parts: Part[] }[];
  history: Message[];
};
type Deal = { item: string; ask: number; history: string[]; offers?: number[] };
const MARGIN = 5; // a stuck buyer is accepted only at reserve + MARGIN or more
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

  private strategy(): Promise<Strategy | null> {
    return this.env.TASKS.get(this.env.TASKS.idFromName(STRATEGY_ID)).getStrategy();
  }

  /** First message of a new task: find the item, then the model names the opening price. */
  async open(id: string, contextId: string, msg: Message): Promise<Outcome> {
    const task: Task = { id, contextId, status: { state: "TASK_STATE_INPUT_REQUIRED", timestamp: "" }, history: [] };
    task.history.push({ ...msg, taskId: id, contextId });
    console.log(`IN: task ${id.slice(0, 8)} ${JSON.stringify(textOf(msg))}`);
    const item = Object.keys(RESERVE).find((k) => textOf(msg).toLowerCase().includes(k));
    if (!item) {
      this.respond(task, "TASK_STATE_REJECTED", "We only sell: " + Object.keys(RESERVE).join(", ") + ".");
    } else {
      const reserve = RESERVE[item];
      const strategy = await this.strategy();
      const answer = await ask(this.env.AI, openPrompt(item, reserve));
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
      const promo = cleanNote(strategy?.promo);
      const note = [promo, cleanNote(answer.note)].filter(Boolean).join(" ");
      await this.ctx.storage.put("deal", { item, ask: price, history: [`You asked ${price}.`] } satisfies Deal);
      console.log(`OUT: open ${item} at ${price}`);
      this.respond(task, "TASK_STATE_INPUT_REQUIRED", `${item}: ${note ? note + " " : ""}Asking ${price}. Send your offer as a number.`);
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
      this.respond(task, "TASK_STATE_INPUT_REQUIRED", `Send your offer as a number. Asking ${deal.ask}.`);
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
    const target = strategy?.target[item];
    if (target && offer >= Math.max(target, reserve)) {
      // a fair offer closes at once: haggling a reached target down by ones loses buyers, and a quick fair deal earns the review
      console.log(`OUT: offer ${offer} reaches target ${target}, accepted by code`);
      return await this.close(task, item, offer, "Agreed.");
    }

    if (prev !== undefined && offer <= prev + 1 && offer >= reserve + MARGIN) {
      // the buyer stopped raising: this is its limit, and it is above our floor. A deal earns a good review, a walkaway a bad one.
      console.log(`OUT: buyer stuck at ${offer} (last ${prev}), at or above reserve + ${MARGIN}, accepted by code`);
      return await this.close(task, item, offer, "Agreed.");
    }

    const answer = await ask(this.env.AI, counterPrompt(item, reserve, deal.history.slice(0, -1).join("\n"), offer, current, strategy));
    let action = answer.action;
    let price = asInt(answer.price);
    const note = cleanNote(answer.note);

    if (action === "accept") {
      if (offer >= reserve) return await this.close(task, item, offer, note);
      console.log(`GUARD: model accepted ${offer} below reserve ${reserve}, countering at ${current}`);
      action = "counter";
      price = current;
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
    if (price < reserve) {
      console.log(`GUARD: counter ${price} below reserve ${reserve}, countering at ${reserve}`);
      price = reserve;
    }
    if (price > current) {
      console.log(`GUARD: counter ${price} above the last ask ${current}, holding at ${current}`);
      price = current;
    }
    if (prev !== undefined && offer > prev && current - price < 0.25 * (current - offer)) {
      // the buyer is raising but the model concedes by ones (141, 142, ...): buyers walk and review it badly
      const step = Math.max(reserve, Math.round(current - 0.4 * (current - offer)));
      console.log(`GUARD: counter ${price} concedes too little to a rising buyer, countering at ${step}`);
      price = Math.min(price, step);
    }
    if (prev !== undefined && offer < reserve && prev < reserve && price > reserve + MARGIN) {
      // a buyer twice below our floor: show the floor now so it can decide fast instead of haggling for nothing
      console.log(`GUARD: buyer below reserve twice (${prev}, ${offer}), countering near the floor at ${reserve + MARGIN}`);
      price = reserve + MARGIN;
    }
    if (price <= offer) {
      // countering at or below the offer means the offer is acceptable
      console.log(`GUARD: counter ${price} at or below offer ${offer}, accepting ${offer}`);
      return await this.close(task, item, offer, note);
    }
    deal.ask = price;
    deal.history.push(`You countered ${price}.`);
    console.log(`OUT: counter ${price} to offer ${offer}`);
    this.respond(task, "TASK_STATE_INPUT_REQUIRED", `${note} Counteroffer: ${price}.`.trim());
  }

  private async close(task: Task, item: string, price: number, note: string) {
    const receipt = await signReceipt(this.env, { shop: NAME, item, price: Math.round(price), task: task.id });
    task.artifacts = [{ artifactId: crypto.randomUUID(), name: "receipt", parts: [{ data: receipt }] }];
    console.log(`OUT: deal ${item} at ${price}`);
    // reviews decide who gets the next buyer: thank the buyer and ask for an honest one (as dragonash does)
    this.respond(task, "TASK_STATE_COMPLETED",
      `${note} Deal: ${item} at ${price}. Thank you! Your signed receipt is attached; an honest review helps us a lot.`.trim());
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
    const message: Message = { messageId: crypto.randomUUID(), contextId: task.contextId, taskId: task.id, role: "ROLE_AGENT", parts: [{ text }] };
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
