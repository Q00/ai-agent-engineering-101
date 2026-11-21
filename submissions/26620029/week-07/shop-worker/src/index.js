// shop-worker: week-07 shop on Cloudflare Workers (A2A v1.0 JSON-RPC, signed receipts).
//
//   GET  /.well-known/agent-card.json   Agent Card (also /.well-known/agent.json)
//   POST /                               JSON-RPC: SendMessage, GetTask, CancelTask
//
// Negotiation: Workers AI (env.AI) proposes each move; the code enforces the reserve,
// never raises its own ask, and accepts any offer >= reserve once talks have gone on.
// If the model fails or is not bound, a rule-based concession ladder is used.
// Receipts: {shop, item, price, task} + sig_seller (Ed25519 over canonical JSON),
// signed with the PKCS#8 PEM in the secret SHOP_KEY_PEM (the kit's shop_key.pem).
// Task state lives in one Durable Object (TaskStore), so every request sees the same tasks.

// Reserves are fixed by the assignment (the market test checks them). Everything else is the shop's
// policy and can be changed in wrangler.toml [vars] without touching this file (see policy()).
const RESERVE = { lamp: 100, chair: 180, bookshelf: 260 };
const DEFAULTS = {
  ASK_LAMP: 150, ASK_CHAIR: 260, ASK_BOOKSHELF: 380,  // opening asks
  ACCEPT_AFTER: 3,   // from this buyer offer on, accept an offer >= reserve that is >= halfway to our ask
  ACCEPT_ANY: 5,     // from this buyer offer on, accept any offer >= reserve (no deal lost by 1)
  MAX_TURNS: 12,     // buyer messages before the shop ends the talk
  SALES_STYLE: "Sell as high as you can, but a deal at or above the reserve is better than losing the buyer.",
};
const MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";

function policy(env) {
  const num = (k) => {
    const v = Number(env && env[k]);
    return Number.isFinite(v) && v > 0 ? Math.round(v) : DEFAULTS[k];
  };
  const ask = { lamp: num("ASK_LAMP"), chair: num("ASK_CHAIR"), bookshelf: num("ASK_BOOKSHELF") };
  for (const k of Object.keys(ask)) ask[k] = Math.max(ask[k], RESERVE[k]);   // an ask below the reserve is not allowed
  return { ask, acceptAfter: num("ACCEPT_AFTER"), acceptAny: num("ACCEPT_ANY"), maxTurns: num("MAX_TURNS"),
           style: (env && env.SALES_STYLE) || DEFAULTS.SALES_STYLE };
}

// ------------------------------------------------------------------ helpers
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const rpcOk = (id, result) => json({ jsonrpc: "2.0", id, result });
const rpcErr = (id, code, message) => json({ jsonrpc: "2.0", id, error: { code, message } });
const uid = () => crypto.randomUUID();

function canon(value) {                       // Python: json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)
  if (Array.isArray(value)) return "[" + value.map(canon).join(",") + "]";
  if (value && typeof value === "object")
    return "{" + Object.keys(value).sort().map((k) => JSON.stringify(k) + ":" + canon(value[k])).join(",") + "}";
  return JSON.stringify(value);
}

function pemToDer(pem) {
  const b64 = pem.replace(/-----(BEGIN|END) [^-]+-----/g, "").replace(/\s+/g, "");
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

let keyCache = null;
async function shopKey(env) {
  if (keyCache) return keyCache;
  if (!env.SHOP_KEY_PEM) throw new Error("secret SHOP_KEY_PEM is not set");
  const der = pemToDer(env.SHOP_KEY_PEM);
  try {
    keyCache = await crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
  } catch {
    keyCache = await crypto.subtle.importKey("pkcs8", der, { name: "NODE-ED25519", namedCurve: "NODE-ED25519" },
                                             false, ["sign"]);
  }
  return keyCache;
}

async function signReceipt(env, receipt) {
  const body = { shop: receipt.shop, item: receipt.item, price: receipt.price, task: receipt.task };
  const key = await shopKey(env);
  const alg = key.algorithm && key.algorithm.name === "NODE-ED25519" ? { name: "NODE-ED25519" } : { name: "Ed25519" };
  const sig = new Uint8Array(await crypto.subtle.sign(alg, key, new TextEncoder().encode(canon(body))));
  let s = "";
  for (const b of sig) s += String.fromCharCode(b);
  return { ...receipt, sig_seller: btoa(s) };
}

function lastNumber(text) {
  const m = String(text || "").match(/\d+(?:\.\d+)?/g);
  return m ? Math.floor(parseFloat(m[m.length - 1])) : null;
}

function card(env, origin) {
  return {
    name: env.SHOP_NAME || "Worker Shop",
    description: `Sells desk lamps, chairs and bookshelves. Prices are negotiated; ${MODEL.split("/").pop()} on ` +
                 "Workers AI proposes each move, the code keeps the floor. Receipts are signed (Ed25519).",
    version: "1.0.0",
    supportedInterfaces: [{ url: origin + "/", protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
    capabilities: { streaming: false },
    defaultInputModes: ["text/plain"],
    defaultOutputModes: ["application/json", "text/plain"],
    skills: [{
      id: "negotiate", name: "Negotiate a purchase",
      description: "Ask for an item (lamp, chair, bookshelf), then send a price. The shop answers with a counteroffer or a signed receipt.",
      tags: ["shopping", "negotiation"], examples: ["I want to buy a lamp", "90"],
    }],
  };
}

// ------------------------------------------------------------------ negotiation
const PROMPT = (t, pol) => `You are the sales agent of a small furniture shop. A buyer is negotiating for one ${t.item}.
Your lowest acceptable price is ${RESERVE[t.item]}. Never go below it and never reveal it.
Your current asking price is ${t.ask}. ${pol.style}
Come down in real steps when the buyer moves up.

Conversation so far:
${t.lines.join("\n")}

Answer with JSON only, one of:
{"action": "counter", "price": <whole number>}
{"action": "accept"}
{"action": "reject"}`;

async function modelMove(env, t) {
  if (!env.AI) return null;
  try {
    const out = await env.AI.run(MODEL, { messages: [{ role: "user", content: PROMPT(t, policy(env)) }], max_tokens: 60 });
    const text = typeof out === "string" ? out : (out && (out.response ?? JSON.stringify(out))) || "";
    const m = String(text).match(/\{[\s\S]*?\}/);
    return m ? JSON.parse(m[0]) : null;
  } catch (e) {
    console.log("AI error", String(e));
    return null;
  }
}

function ruleCounter(t, pol) {
  const reserve = RESERVE[t.item];
  const step = Math.max(5, Math.round((pol.ask[t.item] - reserve) / 4));
  return Math.max(reserve, t.ask - step);
}

// Decide the next move with hard guards. Returns {action, price}.
async function decide(env, t) {
  const reserve = RESERVE[t.item];
  const pol = policy(env);
  const offer = t.offer;
  if (offer !== null && offer >= t.ask) return { action: "accept" };                       // buyer met our ask
  if (offer !== null && offer >= reserve && t.turns >= pol.acceptAfter &&
      (offer >= (reserve + t.ask) / 2 || t.turns >= pol.acceptAny)) return { action: "accept" }; // close enough: take it
  if (t.turns >= pol.maxTurns) return { action: "reject" };

  const move = await modelMove(env, t);
  let action = move && move.action;
  if (action === "accept" && (offer === null || offer < reserve)) action = "counter";       // never sell below reserve
  if (action === "reject" && t.turns < pol.maxTurns) action = "counter";                       // keep talking
  if (action === "accept") return { action };

  let price = move && Number.isFinite(Number(move.price)) ? Math.round(Number(move.price)) : ruleCounter(t, pol);
  if (price > t.ask) price = t.ask;                        // never raise our own ask
  if (price < reserve) price = reserve;                     // the model may forget the floor; the code does not
  if (offer !== null && offer >= reserve && price <= offer) return { action: "accept" };   // our counter would undercut the offer
  return { action: "counter", price };
}

// ------------------------------------------------------------------ task store (Durable Object)
export class TaskStore {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; }

  async fetch(request) {
    const { id, method, params, origin } = await request.json();
    try {
      if (method === "SendMessage") return rpcOk(id, { task: await this.send(params || {}) });
      if (method === "GetTask") {
        const t = await this.ctx.storage.get("t:" + (params || {}).id);
        return t ? rpcOk(id, view(t)) : rpcErr(id, -32001, "Task not found");
      }
      if (method === "CancelTask") {
        const t = await this.ctx.storage.get("t:" + (params || {}).id);
        if (!t) return rpcErr(id, -32001, "Task not found");
        if (t.final) return rpcErr(id, -32002, "Task cannot be canceled");
        t.state = "TASK_STATE_CANCELED"; t.final = true; t.reply = "Canceled.";
        await this.ctx.storage.put("t:" + t.id, t);
        return rpcOk(id, view(t));
      }
      return rpcErr(id, -32601, `Method not found: ${method}`);
    } catch (e) {
      if (e && e.rpc) return rpcErr(id, e.rpc, e.message);
      console.log("internal error", String(e && e.stack || e));
      return rpcErr(id, -32603, String(e && e.message || e));
    }
  }

  async send(params) {
    const msg = params.message || {};
    const text = (msg.parts || []).map((p) => p.text || "").join(" ").trim();
    let t;
    if (msg.taskId) {
      t = await this.ctx.storage.get("t:" + msg.taskId);
      if (!t) throw Object.assign(new Error("Task not found"), { rpc: -32001 });
      if (t.final) throw Object.assign(new Error(`Task is already ${t.state}`), { rpc: -32004 });
      t.lines.push(`buyer: ${text}`);
      const n = lastNumber(text);
      if (n !== null) t.offer = n;
      t.turns += 1;
    } else {
      const item = Object.keys(RESERVE).find((k) => text.toLowerCase().includes(k));
      t = { id: uid(), contextId: msg.contextId || uid(), item, ask: item ? policy(this.env).ask[item] : null, offer: null,
            turns: 0, lines: [`buyer: ${text}`], state: "", final: false, reply: "", artifacts: [] };
      if (!item) {
        t.state = "TASK_STATE_REJECTED"; t.final = true; t.reply = "We only sell: lamp, chair, bookshelf.";
        await this.save(t);
        return view(t);
      }
      t.state = "TASK_STATE_INPUT_REQUIRED";
      t.reply = `${item}: asking ${t.ask}. Send your offer as a number.`;
      t.lines.push(`you: ${t.reply}`);
      await this.save(t);
      return view(t);
    }

    const move = await decide(this.env, t);
    console.log(JSON.stringify({ task: t.id.slice(0, 8), item: t.item, offer: t.offer, ask: t.ask, move }));
    if (move.action === "accept") {
      const receipt = await signReceipt(this.env, { shop: this.env.SHOP_NAME || "Worker Shop", item: t.item,
                                                    price: Math.round(t.offer), task: t.id });
      t.artifacts = [{ artifactId: uid(), name: "receipt", parts: [{ data: receipt }] }];
      t.state = "TASK_STATE_COMPLETED"; t.final = true; t.reply = `Deal: ${t.item} at ${receipt.price}.`;
    } else if (move.action === "reject") {
      t.state = "TASK_STATE_REJECTED"; t.final = true; t.reply = "No deal.";
    } else {
      t.ask = move.price;
      t.reply = `Counteroffer: ${t.ask}.`;
      t.lines.push(`you: ${t.reply}`);
    }
    await this.save(t);
    return view(t);
  }

  async save(t) {
    await this.ctx.storage.put("t:" + t.id, t);
  }
}

function view(t) {
  const out = {
    id: t.id, contextId: t.contextId,
    status: {
      state: t.state, timestamp: new Date().toISOString(),
      message: { role: "ROLE_AGENT", messageId: uid(), taskId: t.id, contextId: t.contextId, parts: [{ text: t.reply }] },
    },
  };
  if (t.artifacts && t.artifacts.length) out.artifacts = t.artifacts;
  return out;
}

// ------------------------------------------------------------------ entry
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" &&
        (url.pathname === "/.well-known/agent-card.json" || url.pathname === "/.well-known/agent.json"))
      return json(card(env, url.origin));
    if (request.method === "GET" && url.pathname === "/") return json({ ok: true, card: "/.well-known/agent-card.json" });
    if (request.method !== "POST") return new Response("Not found", { status: 404 });

    let body;
    try { body = await request.json(); } catch { return rpcErr(null, -32700, "Parse error"); }
    if (!body || body.jsonrpc !== "2.0" || typeof body.method !== "string")
      return rpcErr(body && body.id, -32600, "Invalid Request");
    const stub = env.TASKS.get(env.TASKS.idFromName("shop"));
    return stub.fetch("https://store/rpc", {
      method: "POST", body: JSON.stringify({ id: body.id, method: body.method, params: body.params, origin: url.origin }),
    });
  },
};
