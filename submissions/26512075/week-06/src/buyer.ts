/**
 * Week-06 buyer. Speaks A2A; the harness decides the price.
 * 
 *
 *   npx tsx buyer.ts --url http://127.0.0.1:9999 --item lamp --budget 120
 *
 * Needs Node 20+ and @a2a-js/sdk. ClientFactory reads the agent card and
 * sends A2A-Version: 1.0. The first message has no taskId; later turns
 * attach the same taskId.
 */

import { randomUUID } from "node:crypto";
import { Message, SendMessageRequest, TaskState, type Task } from "@a2a-js/sdk"
import { ClientFactory } from "@a2a-js/sdk/client"

import { askPythonHarness } from "./harnessBridge.js"

function arg(name: string): string {
    const i = process.argv.indexOf(name);
    if (i < 0 || !process.argv[i + 1]) throw new Error(`Missing ${name}`);
    return process.argv[i + 1]!;
}

/** Seller text. Handles both SDK part shapes. */
function statusText(task: Task): string {
  const parts = task.status?.message?.parts ?? [];
  return parts
    .map((part: any) => {
      if ("text" in part && typeof part.text === "string") return part.text;
      const proto = part as { content?: { $case?: string; value?: string } };
      if (proto.content?.$case === "text" && typeof proto.content.value === "string") {
        return proto.content.value;
      }
      return "";
    })
    .filter(Boolean)
    .join(" ");
}


function currentAsk(task: Task): number | undefined {
    const meta = task.status?.message?.metadata as Record<string, unknown> | undefined;
    if (Number.isSafeInteger(meta?.ask)) return meta!.ask as number;
    const numbers = [...statusText(task).matchAll(/\b\d+\b/g)];
    return numbers.length === 1 ? Number(numbers[0]![0]) : undefined;
}


function isTask(value: unknown): value is Task {
  return Boolean(value && typeof value === "object" && "id" in value && "status" in value);
}

function taskOf(result: unknown): Task {
    //console.log("result: ")
    //console.log(result)
    if (isTask(result)) return result;
    if (result && typeof result === "object") {
        const wrapped = result as { result?: unknown; task?: unknown };
        if (isTask(wrapped.result)) return wrapped.result;
        if (isTask(wrapped.task)) return wrapped.task;
    }
  throw new Error("Expected an A2A Task response");
}



function receiptPrice(task: Task, item: string): number {
    for (const artifact of task.artifacts ?? []) {
        for (const part of artifact.parts ?? []) {
            const proto = part as { content?: { $case?: string; value?: Record<string, unknown> } };
            const data = proto.content?.$case == "data"
                            ? proto.content.value
                            : "data" in part && part.data && typeof part.data === "object"
                            ? (part.data as Record<string, unknown>)
                            :undefined;
            if (!data || !Number.isSafeInteger(data.price)) continue;
            const name = String(data.item ?? "");
            const id = String(data.itemId ?? "");
            if (name === item || id === item || name.toLowerCase().includes(item.toLowerCase())) {
                return data.price as number;
            }
        }
    }
    throw new Error("Completed task did not include a numeric receipt price");
}

function stateOf(task: Task): string {
  return String(task.status?.state ?? "");
}

function isState(state: string, name: string): boolean {
  const folded = state.toUpperCase().replace(/-/g, "_");
  return folded === name || folded === `TASK_STATE_${name}`;
}

async function main(): Promise<void> {
  const url = arg("--url");
  const item = arg("--item");
  const budget = Number(arg("--budget"));
  if (!Number.isSafeInteger(budget) || budget <= 0) throw new Error("not valid budget");

  const endpoint = url.replace(/\/$/, "");
  const transcript: string[] = [];
  let taskId = "";
  let contextId = "";

  const send = async (text: string): Promise<Task> => {
    // a2a-sdk 1.2 (v1.0) expects proto JSON: ROLE_USER and parts as {"text": "..."}.
    // The JS SDK's role:"user" / {kind:"text"} is rejected as INVALID_PARAMS.
    const body = {
      jsonrpc: "2.0",
      id: randomUUID(),
      method: "SendMessage",
      params: {
        message: {
          messageId: randomUUID(),
          role: "ROLE_USER",
          parts: [{ text }],
          ...(taskId ? { taskId, contextId } : {}),
        },
      },
    };
    transcript.push(`buyer: ${text}`);
    const http = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "A2A-Version": "1.0" },
      body: JSON.stringify(body),
    });
    const json = (await http.json()) as { result?: unknown; error?: { message?: string } };
    if (json.error) throw new Error(json.error.message ?? "SendMessage failed");
    const task = taskOf(json.result);
    if (process.env.DEBUG_A2A) console.error(JSON.stringify({ request: text, task }));
    if (!task.id) throw new Error("no task id");
    taskId = task.id;
    contextId = task.contextId ?? contextId;
    transcript.push(`seller: ${statusText(task)}`);
    return task;
  };

  let task = await send(`I want to buy a ${item}`);
  for (let turn = 0; turn < 8; turn++) {
    const state = stateOf(task);

    if (isState(state, "COMPLETED")) {
      const price = receiptPrice(task, item);
      if (price > budget) throw new Error(`Receipt price ${price} exceeds budget ${budget}`);
      console.log(JSON.stringify({ item, price, taskId }));
      return;
    }

    if (isState(state, "REJECTED") || isState(state, "CANCELED") || isState(state, "FAILED")) {
      console.log(JSON.stringify({ item, outcome: "no-deal", taskId, state }));
      return;
    }

    if (isState(state, "INPUT_REQUIRED")) {
      const say = statusText(task);
      const ask = currentAsk(task);
      if (ask == null) {
        console.error("ask is undefined; seller text should contain the offer as a number");
        console.error(say);
        return;
      }
      const decision = await askPythonHarness({
        side: "buyer",
        item,
        limit: budget,
        currentAsk: ask,
        sellerTxt: say,
        transcript,
      });
      if (decision.action === "walk_away" || decision.action === "reject") {
        console.log(JSON.stringify({ item, outcome: "no-deal", taskId }));
        return;
      }
      if (decision.action === "accept" && decision.price !== ask) {
        throw new Error("Acceptance price must match current ask");
      }
      task = await send(String(decision.price));
      continue;
    }

    throw new Error(`Unexpected task state: ${state}`);
  }
  console.log(JSON.stringify({ item, outcome: "no-deal", taskId, reason: "turn limit" }));
}

main().catch((error) => { console.error(error); process.exitCode = 1; });

