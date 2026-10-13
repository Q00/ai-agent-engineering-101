/**
 * A rule-based A2A v1.0 seller (JSON-RPC binding) on Cloudflare Workers with Hono.
 *
 * Same rules as the trade test's kit seller: open at 1.3 x reserve, counter at
 * max(reserve, (offer + ask) / 2), close at any offer >= reserve, reject after
 * five low offers. No model inside, so the deployment costs nothing to call.
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

const RESERVE: Record<string, number> = { lamp: 100, chair: 180, bookshelf: 260 };
const MAX_LOW_OFFERS = 5;
const NAME = "Lamp Shop on Workers";

type Part = { text?: string; data?: Record<string, unknown> };
type Message = { messageId: string; contextId?: string; taskId?: string; role: string; parts: Part[] };
type State =
  | "TASK_STATE_INPUT_REQUIRED"
  | "TASK_STATE_COMPLETED"
  | "TASK_STATE_REJECTED"
  | "TASK_STATE_CANCELED";
type Task = {
  id: string;
  contextId: string;
  status: { state: State; message?: Message; timestamp: string };
  artifacts?: { artifactId: string; name: string; parts: Part[] }[];
  history: Message[];
};
type Deal = { item: string; ask: number; lowOffers: number };
type Outcome = { task: Task } | { error: RpcError };
type RpcError = { code: number; message: string; data: unknown[] };

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

export class TaskObject extends DurableObject {
  /** First message of a new task: find the item and name the opening price. */
  async open(id: string, contextId: string, msg: Message): Promise<Outcome> {
    const task: Task = { id, contextId, status: { state: "TASK_STATE_INPUT_REQUIRED", timestamp: "" }, history: [] };
    task.history.push({ ...msg, taskId: id, contextId });
    const item = Object.keys(RESERVE).find((k) => textOf(msg).toLowerCase().includes(k));
    if (!item) {
      this.respond(task, "TASK_STATE_REJECTED", "We only sell: lamp, chair, bookshelf.");
    } else {
      const ask = Math.ceil(RESERVE[item] * 1.3);
      await this.ctx.storage.put("deal", { item, ask, lowOffers: 0 } satisfies Deal);
      this.respond(task, "TASK_STATE_INPUT_REQUIRED", `${item}: asking ${ask}. Send your offer as a number.`);
    }
    await this.ctx.storage.put("task", task);
    return { task };
  }

  /** A follow-up on the same task: the buyer's offer. */
  async reply(msg: Message): Promise<Outcome> {
    const task = await this.ctx.storage.get<Task>("task");
    if (!task) return { error: rpcError(-32001, "TASK_NOT_FOUND", "Task not found") };
    if (TERMINAL.includes(task.status.state)) {
      return {
        error: rpcError(-32004, "UNSUPPORTED_OPERATION", `Task ${task.id} is in terminal state: ${task.status.state}`),
      };
    }
    this.archiveStatus(task);
    task.history.push({ ...msg, taskId: task.id, contextId: task.contextId });
    const deal = (await this.ctx.storage.get<Deal>("deal"))!;
    const m = textOf(msg).match(/\d+(?:\.\d+)?/);
    if (!m) {
      this.respond(task, "TASK_STATE_INPUT_REQUIRED", "Send your offer as a number.");
    } else {
      const offer = Math.round(Number(m[0]));
      const reserve = RESERVE[deal.item];
      if (offer >= reserve) {
        task.artifacts = [
          { artifactId: crypto.randomUUID(), name: "receipt", parts: [{ data: { seller: NAME, item: deal.item, price: offer } }] },
        ];
        this.respond(task, "TASK_STATE_COMPLETED", `Deal: ${deal.item} at ${offer}.`);
      } else if (++deal.lowOffers >= MAX_LOW_OFFERS) {
        this.respond(task, "TASK_STATE_REJECTED", `No deal at ${offer}.`);
      } else {
        deal.ask = Math.max(reserve, Math.floor((offer + deal.ask) / 2));
        this.respond(task, "TASK_STATE_INPUT_REQUIRED", `${offer} is too low. Counteroffer: ${deal.ask}.`);
      }
      await this.ctx.storage.put("deal", deal);
    }
    await this.ctx.storage.put("task", task);
    return { task };
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
    const message: Message = {
      messageId: crypto.randomUUID(),
      contextId: task.contextId,
      taskId: task.id,
      role: "ROLE_AGENT",
      parts: [{ text }],
    };
    task.status = { state, message, timestamp: new Date().toISOString() };
  }

  /** The previous status message moves into history before the next turn, as in a2a-sdk. */
  private archiveStatus(task: Task) {
    if (task.status.message) task.history.push(task.status.message);
  }
}

type Env = { TASKS: DurableObjectNamespace<TaskObject> };
const app = new Hono<{ Bindings: Env }>();

app.get("/.well-known/agent-card.json", (c) => {
  const url = new URL(c.req.url).origin + "/";
  return c.json({
    name: NAME,
    description: "Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer. Rule-based, on Cloudflare Workers.",
    supportedInterfaces: [{ url, protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
    version: "1.0.0",
    capabilities: {},
    defaultInputModes: ["text/plain"],
    defaultOutputModes: ["application/json", "text/plain"],
    skills: [
      {
        id: "negotiate",
        name: "Negotiate a purchase",
        description: "Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
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
  const reply = (o: Outcome | { result: unknown }) => c.json({ jsonrpc: "2.0", id: body.id ?? null, ...o });

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
