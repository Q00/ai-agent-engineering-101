// Week 06 extra credit: seller.py deployed as a Cloudflare Worker (A2A v1.0, JSON-RPC binding).
//
//   GET  /.well-known/agent-card.json   the Agent Card (its interface URL is this Worker's own origin)
//   POST /                              JSON-RPC: SendMessage, GetTask, CancelTask
//
// Same shop and rules as seller.py: lamp / chair / bookshelf with reserves 100 / 180 / 260, at most
// six buyer offers, the model decides the opening ask and accept / counter / reject, and code keeps
// the reserve (an ask below it is raised, a sale below it becomes a counter at the reserve, an ask
// never goes up). Replies come from the same template, so the ask is the LAST number in every
// INPUT_REQUIRED message; it is also in the message metadata as {"ask": N} (challenge 3).
// The harness differs: a `claude` login cannot run on a server (README), so the model is
// Workers AI (env.MODEL, see wrangler.jsonc). Tasks live in one Durable Object ("shop").
//
// Week 07: the receipt is {shop, item, price, task} plus sig_seller, the base64 Ed25519 signature over
// the canonical JSON of those four keys (sorted keys, no spaces, UTF-8, whole-number price), the same
// bytes seller.py signs. The private key is the kit's shop_key.pem, whose public key the market has
// registered for this shop; it reaches the Worker only as the secret SHOP_KEY (the base64 PKCS#8 body
// of the PEM), never through the repository. Without SHOP_KEY the shop still sells, unsigned, and logs it.

const RESERVE: Record<string, number> = { lamp: 100, chair: 180, bookshelf: 260 };
const MAX_ROUNDS = 6;
const NAME = "Harness Shop, deployed (25620024)";
const SHOP = "dragonash";   // receipt "shop": the market name, same as seller.py

export function canon(r: { shop: string; item: string; price: number; task: string }): string {
  // keys written in sorted order, no spaces: the same string as Python's
  // json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
  return JSON.stringify({ item: r.item, price: r.price, shop: r.shop, task: r.task });
}

export async function importShopKey(b64: string): Promise<CryptoKey> {
  const der = Uint8Array.from(atob(b64.replace(/-----[^-]+-----|\s/g, "")), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
}

export async function signedReceipt(key: CryptoKey | null, item: string, price: number, task: string) {
  const r = { shop: SHOP, item, price: Math.round(price), task };
  if (!key) return r;
  const sig = new Uint8Array(await crypto.subtle.sign({ name: "Ed25519" }, key, new TextEncoder().encode(canon(r))));
  return { ...r, sig_seller: btoa(String.fromCharCode(...sig)) };
}
const TERMINAL = ["TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"];

const OPEN_PROMPT = (item: string, reserve: number) =>
  `You are the seller in a furniture shop, negotiating with a buyer agent.
Item: ${item}. Your lowest acceptable price (reserve) is ${reserve}. Keep the reserve private.
The buyer just asked to buy the ${item}. Choose your opening asking price: high enough to leave
room to negotiate, but realistic.
Reply with one JSON object only: {"price": <whole number>}`;

const TURN_PROMPT = (d: Deal, offer: number) =>
  `You are the seller in a furniture shop, negotiating with a buyer agent.
Item: ${d.item}. Your lowest acceptable price (reserve) is ${d.reserve}. Keep the reserve private.
Never ask for or accept less than the reserve.
Conversation so far (oldest first):
${d.history.join("\n")}
The buyer's latest offer is ${offer}. Your current asking price is ${d.ask}.
This is buyer offer ${d.rounds} of at most ${MAX_ROUNDS}.
Decide your move:
- "accept": sell at the buyer's offer (only if the offer is at or above your reserve)
- "counter": name a new asking price, lower than your current ask but not below the reserve
- "reject": end the negotiation with no deal
Concede step by step instead of jumping to the reserve. If the offer is already at or above
the reserve and the negotiation has gone a few rounds, accepting is usually better than losing the sale.
Reply with one JSON object only: {"action": "accept" or "counter" or "reject", "price": <whole number>}`;

interface Deal { item: string; reserve: number; ask: number; rounds: number; history: string[] }
interface Stored { task: any; deal: Deal | null; log: string[] }

function card(origin: string) {
  return {
    name: NAME,
    description: "Sells desk lamps, chairs and bookshelves. Deployed on Cloudflare Workers; prices are negotiated " +
      "by a Workers AI model with the reserve enforced in code.",
    version: "1.0.0",
    supportedInterfaces: [{ url: origin + "/", protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
    capabilities: { streaming: false },
    defaultInputModes: ["text/plain"],
    defaultOutputModes: ["application/json", "text/plain"],
    skills: [{
      id: "negotiate", name: "Negotiate a purchase",
      description: "Name an item (lamp, chair, bookshelf), then send offers as numbers. The shop answers with a " +
        "counteroffer, a receipt, or no deal. The current ask is also in message metadata as {\"ask\": N}.",
      tags: ["shopping", "negotiation"], examples: ["I want to buy a lamp", "90"],
    }],
  };
}

const json = (obj: unknown, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

export default {
  async fetch(request: Request, env: any): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/.well-known/agent-card.json") return json(card(url.origin));
    if (request.method === "POST" && url.pathname === "/") {
      const shop = env.SHOP.get(env.SHOP.idFromName("shop"));
      return shop.fetch(request);
    }
    if (request.method === "GET" && url.pathname === "/") {
      return new Response(`${NAME}\nAgent Card: ${url.origin}/.well-known/agent-card.json\n`);
    }
    return new Response("not found", { status: 404 });
  },
};

export class Shop {
  state: any;
  env: any;
  key: Promise<CryptoKey | null> | undefined;
  constructor(state: any, env: any) { this.state = state; this.env = env; }

  shopKey(): Promise<CryptoKey | null> {
    this.key ??= this.env.SHOP_KEY ? importShopKey(this.env.SHOP_KEY) : Promise.resolve(null);
    return this.key;
  }

  async fetch(request: Request): Promise<Response> {
    let body: any;
    try { body = await request.json(); } catch { return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }); }
    const id = body?.id ?? null;
    const ok = (result: unknown) => json({ jsonrpc: "2.0", id, result });
    const err = (code: number, message: string) => json({ jsonrpc: "2.0", id, error: { code, message } });
    const p = body?.params ?? {};
    try {
      if (body?.method === "SendMessage") return await this.sendMessage(p, ok, err);
      if (body?.method === "GetTask") {
        const s: Stored | undefined = await this.state.storage.get(`task:${p.id}`);
        return s ? ok(s.task) : err(-32001, `Task ${p.id} not found`);
      }
      if (body?.method === "CancelTask") {
        const s: Stored | undefined = await this.state.storage.get(`task:${p.id}`);
        if (!s) return err(-32001, `Task ${p.id} not found`);
        if (TERMINAL.includes(s.task.status.state)) return err(-32002, `Task ${p.id} is in terminal state: ${s.task.status.state}`);
        s.task.status = { state: "TASK_STATE_CANCELED", timestamp: new Date().toISOString() };
        s.log.push("CANCELED by the buyer");
        await this.state.storage.put(`task:${p.id}`, s);
        return ok(s.task);
      }
      return err(-32601, `Method not found: ${body?.method}`);
    } catch (e: any) {
      return err(-32603, `Internal error: ${e?.message ?? e}`);
    }
  }

  async sendMessage(p: any, ok: (r: unknown) => Response, err: (c: number, m: string) => Response) {
    const msg = p.message ?? {};
    const text: string = (msg.parts ?? []).map((x: any) => x.text ?? "").join(" ");
    const now = () => new Date().toISOString();
    const agentMsg = (t: string, ask?: number) => ({
      role: "ROLE_AGENT", messageId: crypto.randomUUID(), parts: [{ text: t }],
      ...(ask !== undefined ? { metadata: { ask } } : {}),
    });

    let s: Stored;
    if (msg.taskId) {
      const found: Stored | undefined = await this.state.storage.get(`task:${msg.taskId}`);
      if (!found) return err(-32001, `Task ${msg.taskId} not found`);
      if (TERMINAL.includes(found.task.status.state)) {
        return err(-32004, `Task ${msg.taskId} is in terminal state: ${found.task.status.state}`);
      }
      s = found;
    } else {
      const tid = crypto.randomUUID();
      s = { task: { id: tid, contextId: msg.contextId ?? crypto.randomUUID(), status: {}, artifacts: [] }, deal: null, log: [] };
    }
    const task = s.task;
    const logged = s.log.length;
    const save = async () => {
      // v2: every log line also goes to the Workers log (wrangler tail / dashboard), so a failing
      // model call is visible instead of hiding behind the 1.3 x reserve fallback
      for (const line of s.log.slice(logged)) console.log(`[${task.id.slice(0, 8)}] ${line}`);
      await this.state.storage.put(`task:${task.id}`, s);
      return ok({ task });
    };
    s.log.push(`buyer: ${text}`);

    if (!s.deal) {                                   // first message: the item and the opening ask
      const low = text.toLowerCase();
      const item = Object.keys(RESERVE).find((k) => low.includes(k));
      if (!item) {
        task.status = { state: "TASK_STATE_REJECTED", message: agentMsg("We only sell: lamp, chair, bookshelf."), timestamp: now() };
        return save();
      }
      const reserve = RESERVE[item];
      let ask = Math.round(reserve * 1.3);
      try {
        const a = await this.model(OPEN_PROMPT(item, reserve));
        if (Number.isFinite(Number(a.price))) ask = Math.round(Number(a.price));
        s.log.push(`model opening ask ${ask}`);
      } catch (e: any) { s.log.push(`model error on opening (${e?.message}); fallback ${ask}`); }
      if (ask < reserve) { s.log.push(`GUARD opening ask ${ask} < reserve ${reserve}: raised`); ask = reserve; }
      const reply = `${item}: asking ${ask}. Send your offer as a number.`;
      s.deal = { item, reserve, ask, rounds: 0, history: [`buyer: ${text}`, `seller: ${reply}`] };
      task.status = { state: "TASK_STATE_INPUT_REQUIRED", message: agentMsg(reply, ask), timestamp: now() };
      return save();
    }

    const d = s.deal;
    const nums = text.match(/\d+/g);
    if (!nums) {
      task.status = { state: "TASK_STATE_INPUT_REQUIRED", message: agentMsg(`Send your offer as a number. Current ask: ${d.ask}.`, d.ask), timestamp: now() };
      return save();
    }
    const offer = parseInt(nums[0], 10);
    d.rounds += 1;
    d.history.push(`buyer: ${text}`);

    let action = "counter", price = d.ask;
    if (offer >= d.ask) {
      action = "accept"; price = offer;
      s.log.push(`offer ${offer} >= current ask ${d.ask}: accept without asking the model`);
    } else {
      try {
        const a = await this.model(TURN_PROMPT(d, offer));
        action = String(a.action); price = Math.round(Number(a.price ?? d.ask));
        if (!Number.isFinite(price)) price = d.ask;
      } catch (e: any) { s.log.push(`model error (${e?.message}); holding ask ${d.ask}`); }
      s.log.push(`offer ${offer} -> model ${JSON.stringify({ action, price })}`);
      if (action === "accept" && offer < d.reserve) { s.log.push(`GUARD accept ${offer} < reserve ${d.reserve}: counter at reserve`); action = "counter"; price = d.reserve; }
      if (!["accept", "counter", "reject"].includes(action)) { s.log.push(`GUARD unknown action ${action}`); action = "counter"; price = d.ask; }
      if (action === "counter") {
        if (price < d.reserve) { s.log.push(`GUARD counter ${price} < reserve ${d.reserve}: raised`); price = d.reserve; }
        if (price > d.ask) { s.log.push(`GUARD counter ${price} above current ask ${d.ask}: kept`); price = d.ask; }
      }
    }

    if (action === "accept") {
      let receipt: any;
      try {
        receipt = await signedReceipt(await this.shopKey(), d.item, offer, task.id);
        if (!receipt.sig_seller) s.log.push("SHOP_KEY secret not set: receipt unsigned");
      } catch (e: any) {
        s.log.push(`signing failed (${e?.message}): receipt unsigned`);
        receipt = await signedReceipt(null, d.item, offer, task.id);
      }
      task.artifacts = [{ artifactId: crypto.randomUUID(), name: "receipt", parts: [{ data: receipt }] }];
      task.status = { state: "TASK_STATE_COMPLETED", message: agentMsg(`Deal: ${d.item} at ${offer}.`), timestamp: now() };
      s.log.push(`DEAL ${d.item} at ${offer} (reserve ${d.reserve})`);
      return save();
    }
    if (action === "reject" || d.rounds >= MAX_ROUNDS) {
      task.status = { state: "TASK_STATE_REJECTED", message: agentMsg(`No deal for the ${d.item} at that price.`), timestamp: now() };
      s.log.push(`REJECT after ${d.rounds} offers (last offer ${offer})`);
      return save();
    }
    d.ask = price;
    const reply = `${offer} is too low. Counteroffer: ${price}.`;
    d.history.push(`seller: ${reply}`);
    task.status = { state: "TASK_STATE_INPUT_REQUIRED", message: agentMsg(reply, price), timestamp: now() };
    return save();
  }

  async model(prompt: string): Promise<any> {
    const r = await this.env.AI.run(this.env.MODEL, { messages: [{ role: "user", content: prompt }], max_tokens: 120 });
    const out = typeof r?.response === "string" ? r.response : JSON.stringify(r?.response ?? r);
    const m = out.match(/\{[\s\S]*?\}/);
    if (!m) throw new Error(`no JSON in model answer: ${out.slice(0, 120)}`);
    return JSON.parse(m[0]);
  }
}
