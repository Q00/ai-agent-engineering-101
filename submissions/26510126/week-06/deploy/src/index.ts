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
 * Routes
 *   GET  /.well-known/agent-card.json   Agent Card, url = this deployment's origin
 *   POST /                              JSON-RPC: SendMessage, GetTask, CancelTask
 *
 * State: one Durable Object per task id (TaskObject). Workers are stateless, so the
 * Python server's in-memory task store becomes a Durable Object here.
 */
import { DurableObject } from "cloudflare:workers";
import { Hono } from "hono";

const MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8";
const RESERVE: Record<string, number> = { lamp: 100, chair: 180, bookshelf: 260 };
const NAME = "Llama Furniture Shop on Workers";

// ---------------------------------------------------------------- prompts (as in seller.py)

const openPrompt = (item: string, reserve: number) => `You are the seller in a furniture shop. A buyer has just asked for a ${item}.
Your reserve, the lowest price you may ever ask or sell for, is ${reserve}. The buyer does not know it.
Name your opening asking price. Leave room to come down, since the buyer will bargain.

Answer with one JSON object and nothing else:
{"price": <integer>, "note": "<one short sentence to the buyer, no digits>"}`;

const counterPrompt = (item: string, reserve: number, history: string, offer: number, ask: number) =>
  `You are the seller in a furniture shop, negotiating the price of a ${item} with a buyer.
Your reserve, the lowest price you may ever ask or sell for, is ${reserve}. The buyer does not know it.

So far:
${history}
The buyer now offers ${offer}. Your current asking price is ${ask}.

Decide:
- "accept": sell at the buyer's offer of ${offer}.
- "counter": name a new asking price, at or above ${reserve} and no higher than ${ask}.
- "reject": end the negotiation without a deal.
A deal at or above your reserve is better than no deal. Concede step by step while the buyer keeps raising.
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
type Deal = { item: string; ask: number; history: string[] };
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

type Env = { TASKS: DurableObjectNamespace<TaskObject>; AI: Ai };

// ---------------------------------------------------------------- one task, one Durable Object

export class TaskObject extends DurableObject<Env> {
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
      const answer = await ask(this.env.AI, openPrompt(item, reserve));
      let price = asInt(answer.price);
      if (price === null) {
        price = Math.ceil(reserve * 1.3);
        console.log(`GUARD: no usable opening price from the model, asking ${price}`);
      } else if (price < reserve) {
        console.log(`GUARD: opening ask ${price} below reserve ${reserve}, asking ${reserve}`);
        price = reserve;
      }
      const note = cleanNote(answer.note);
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
    deal.history.push(`The buyer offered ${offer}.`);

    if (offer >= current) {
      // the buyer met our own price
      console.log(`OUT: offer ${offer} meets ask ${current}, accepted by code`);
      return this.close(task, item, offer, "Agreed.");
    }

    const answer = await ask(this.env.AI, counterPrompt(item, reserve, deal.history.slice(0, -1).join("\n"), offer, current));
    let action = answer.action;
    let price = asInt(answer.price);
    const note = cleanNote(answer.note);

    if (action === "accept") {
      if (offer >= reserve) return this.close(task, item, offer, note);
      console.log(`GUARD: model accepted ${offer} below reserve ${reserve}, countering at ${current}`);
      action = "counter";
      price = current;
    }
    if (action === "reject") {
      console.log(`OUT: reject at offer ${offer}`);
      return this.respond(task, "TASK_STATE_REJECTED", `${note} No deal.`.trim());
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
    if (price <= offer) {
      // countering at or below the offer means the offer is acceptable
      console.log(`GUARD: counter ${price} at or below offer ${offer}, accepting ${offer}`);
      return this.close(task, item, offer, note);
    }
    deal.ask = price;
    deal.history.push(`You countered ${price}.`);
    console.log(`OUT: counter ${price} to offer ${offer}`);
    this.respond(task, "TASK_STATE_INPUT_REQUIRED", `${note} Counteroffer: ${price}.`.trim());
  }

  private close(task: Task, item: string, price: number, note: string) {
    task.artifacts = [{ artifactId: crypto.randomUUID(), name: "receipt", parts: [{ data: { seller: NAME, item, price } }] }];
    console.log(`OUT: deal ${item} at ${price}`);
    this.respond(task, "TASK_STATE_COMPLETED", `${note} Deal: ${item} at ${price}.`.trim());
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

app.get("/.well-known/agent-card.json", (c) => {
  const url = new URL(c.req.url).origin + "/";
  return c.json({
    name: NAME,
    description: `Sells lamps, chairs and bookshelves. A model (${MODEL} on Workers AI) negotiates the price with the buyer.`,
    supportedInterfaces: [{ url, protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
    version: "1.0.0",
    capabilities: {},
    defaultInputModes: ["text/plain"],
    defaultOutputModes: ["application/json", "text/plain"],
    skills: [
      {
        id: "negotiate",
        name: "Negotiate a purchase",
        description:
          "Name an item (lamp, chair, bookshelf), then send offers as numbers on the same task. " +
          "The asking price is the last number in each reply; a deal ends with a receipt artifact.",
        tags: ["shopping", "negotiation"],
        examples: ["I want to buy a lamp", "90"],
      },
    ],
  });
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
