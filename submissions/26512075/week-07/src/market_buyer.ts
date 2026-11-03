/**
 * Week-07 market buyer. Speaks A2A; the harness decides the price.
 * 
 *
 *   npx tsx buyer.ts --url http://127.0.0.1:9999 --item lamp --budget 120
 *
 * Needs Node 20+ and @a2a-js/sdk. ClientFactory reads the agent card and
 * sends A2A-Version: 1.0. The first message has no taskId; later turns
 * attach the same taskId.
 */

import { randomUUID } from "node:crypto";
import type { Task } from "@a2a-js/sdk"

import { askPythonHarness } from "./harnessBridge.js"
import { chooseShop, loadMemory, loadShops, MEMORY_PATH, saveMemory } from "./marketSelection.js";

class RemoteShopError extends Error {}

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
    const meta = task.status?.message?.metadata as
    | Record<string, unknown>
    | undefined;
    
    if (Number.isSafeInteger(meta?.ask)) return meta!.ask as number;
    
    
    const txt= statusText(task);
    if (!txt) {
      console.error("undefined txt")
      return undefined;
    }

    const explicitPatterns = [
      /\b(?:counter\s*offer|counteroffer)\s*[:=]?\s*(\d+)\b/i,
      /\basking(?:\s+price)?\s*[:=]?\s*(\d+)\b/i,
      /\bmy\s+price\s*[:=]?\s*(\d+)\b/i,
      /\b(?:ask|price)\s*[:=]\s*(\d+)\b/i,
      /\bnow\s+asking\s+(\d+)\b/i,
    ];

  
    for (const pattern of explicitPatterns) {
      const match = txt.match(pattern);
      if (match) {
        return Number(match[1]);
      }}
    
    const keyword = txt.match(
      /\b(?:asking|counteroffer|counter\s*offer|price)\b[^\d]*(\d+)\b/i,
    );

    if (keyword) {
      return Number(keyword[1]);
    }

    const numbers = [...txt.matchAll(/\b(\d+)\b/g)].map((m) => Number(m[1]));
    if (numbers.length === 1 && numbers[0]! >= 50 && numbers[0]! <= 1000) {
      return numbers[0];
    }
    return undefined;
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

function extractReceipt(task: Task): Record<string, unknown> | null {
  for (const artifact of task.artifacts ?? []) {
    if (artifact.name !== "receipt") { continue; }

    for (const part of artifact.parts ?? []) {
      const proto = part as {
        content?: {
          $case?: string;
          value?: unknown;
        };
      };

      const data = proto.content?.$case === "data"
                    ? proto.content.value
                    : "data" in part
                      ? part.data
                      : undefined;
      
      if (data !== null && typeof data === "object" && !Array.isArray(data)) {
        return data as Record<string, unknown>;
      }
    }

  }

  return null;
}

function stateOf(task: Task): string {
  return String(task.status?.state ?? "");
}

function isState(state: string, name: string): boolean {
  const folded = state.toUpperCase().replace(/-/g, "_");
  return folded === name || folded === `TASK_STATE_${name}`;
}

async function main(): Promise<void> {
  const shopsPath = arg("--shops");
  const item = arg("--item");
  const budget = Number(arg("--budget"));
  const shops = loadShops(shopsPath);
  const memory = loadMemory();
  if (!Number.isSafeInteger(budget) || budget <= 0) throw new Error("not valid budget");

  const { shop, reason } = chooseShop(shops, memory);
  console.debug(`choose ${reason}`)

  
  const shopReviews = shop.reviews?.slice(0, 5).map(
    ({ stars, review, round }) => ({
      stars,
      review: review.slice(0, 500),
      round,
    }),
  );

  const shopUrl = shop.url;

  let receipt: Record<string, unknown> | null = null;
  let resultState = "no_deal";
  let stars = 3;
  let review = "no deal was completed yet"

  const transcript: string[] = [];
  let taskId = "";
  let contextId = "";
  

  let lastOffer: number | null = null;
  let agreedPrice: number | null = null;
  let receiptMismatch: boolean | null = null;

  try {
    const cardUrl = shopUrl.endsWith("/.well-known/agent-card.json")
      ? shopUrl
      : `${shopUrl.replace(/\/$/, "")}/.well-known/agent-card.json`;

    const cardResp = await fetch(cardUrl, {
      signal: AbortSignal.timeout(10_000)
    })

    if (!cardResp.ok) {
      throw new Error(`Agent card request failed: HTTP ${cardResp.status}`,);
    }

    const card = (await cardResp.json()) as {
      supportedInterfaces?: Array<{
        protocolBinding?: string;
        protocolVersion?: string;
        url?: string;
      }>;
    };

    const jsonRpcInterface = card.supportedInterfaces?.find(
      (entry) => 
        entry.protocolBinding?.toUpperCase() === "JSONRPC" &&
        String(entry.protocolVersion) === "1.0" &&
        typeof entry.url === "string",
    );

    if (!jsonRpcInterface?.url) {
      throw new Error("Agent card has no A2A 1.0 JSON-RPC endpoint")
    }
  
    const endpoint = new URL(jsonRpcInterface.url, cardUrl).toString();

    console.log((`[card] ${shop.name}: JSON-RPC endpoint resolved`));

    const send = async (txt: string): Promise<Task> => {
      
      const body = {
          jsonrpc: "2.0",
          id: randomUUID(),
          method: "SendMessage",
          params: {
            message: {
              messageId: randomUUID(),
              role: "ROLE_USER",
              parts: [{ text:txt }],
              ...(taskId ? { taskId, contextId } : {}),
            },
        },
    };

    transcript.push(`buyer: ${txt}`);

    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "A2A-Version": "1.0" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(200_000),
    });

    if (!resp.ok) {
      throw new Error(`A2A request failed: HTTP ${resp.status}`)
    }

    const json = (await resp.json()) as {
      result?: unknown;
      error?: { message?: string };
    };


    if (json.error) {
      throw new RemoteShopError(json.error.message ?? "SendMessage failed",);
    }

    const task = taskOf(json.result);

    if (!task.id) {
      throw new Error("A2A response has no task ID");
    }

    taskId = task.id;
    contextId = task.contextId ?? contextId;
    
    const sellerText = statusText(task);
    transcript.push(`seller: ${sellerText}`);

    console.log(
      `[trade] state=${stateOf(task)} seller=${sellerText}`
    );

    return task;
  
  };

  let task = await send(`I want to buy a ${item}`);

    for (let turn = 0; turn <= 8; turn++) {
      const state = stateOf(task);

      if (isState(state, "COMPLETED")) {
        resultState = "completed";
        receipt = extractReceipt(task);

        if (!receipt) {
          stars = 2;
          review = "The task completed, but no receipt was found.";
          break;
        }

        const price = receipt.price;

        // Only explicit completion text establishes an agreement price.
        // A last offer alone does not prove the terms the seller accepted.
        const completionText = statusText(task);
        const agreedMatch = completionText.match(
          /\b(?:deal|agreed)\b[^\n]*?\bat\s+(\d+(?:\.\d+)?)(?!\d|\.\d)/i,
        );
        if (agreedMatch) {
          const parsed = Number(agreedMatch[1]);
          if (Number.isFinite(parsed)) agreedPrice = parsed;
        }
        if (agreedPrice !== null && typeof price === "number" && Number.isFinite(price)) {
          receiptMismatch = agreedPrice !== price;
          console.log(`[receipt] agreed=${agreedPrice} receipt=${price} mismatch=${receiptMismatch}`);
        } else {
          console.log("[receipt] agreed price not explicitly confirmed; mismatch unknown");
        }

        if (
          typeof price !== "number" ||
          !Number.isFinite(price)
        ) {
          stars = 2;
          review =
            "The task completed, but the receipt has no valid price.";
        } else if (price > budget) {
          stars = 1;
          review =
            `The receipt price ${price} exceeded my budget ${budget}.`;
        } else if (receiptMismatch === true) {
          stars = 1;
          review = `Agreed ${agreedPrice}, but the receipt states ${price}.`;
        } else {
          stars = 4;
          review = `The transaction completed at ${price}.`;
        }

        break;
      }

      if (
        isState(state, "REJECTED") ||
        isState(state, "CANCELED") ||
        isState(state, "FAILED")
      ) {
        resultState = isState(state, "FAILED") ? "remote_error" : state.toLowerCase();
        review = `The negotiation ended with state ${state}.`;
        break;
      }

      if (!isState(state, "INPUT_REQUIRED")) {
        resultState = "error";
        review = `Unexpected task state: ${state}.`;
        break;
      }

      if (turn === 8) {
        resultState = "turn_limit";
        review = "No agreement was reached within the turn limit.";
        break;
      }

      const sellerText = statusText(task);
      const ask = currentAsk(task);

      if (ask == null) {
        resultState = "unknown_ask";
        review = "I could not determine the seller's asking price.";
        break;
      }

      console.error(
        `[review-debug] selected_shop=${shop.name} reviews_to_harness=${shopReviews.length}`,
      );

      const decision = await askPythonHarness({
        side: "buyer",
        item,
        limit: budget,
        currentAsk: ask,
        sellerTxt: sellerText,
        shopReviews,
        transcript,
      });

      if (
        decision.action === "walk_away" ||
        decision.action === "reject"
      ) {
        resultState = "walked_away";
        review = "I walked away without completing a deal.";
        break;
      }

      if (
        decision.action !== "offer" &&
        decision.action !== "accept"
      ) {
        throw new Error(`Unexpected action: ${decision.action}`);
      }

      const offer = decision.price;

     if (
        typeof offer !== "number" ||
        !Number.isSafeInteger(offer) ||
        offer <= 0 ||
        offer > budget
      ) {
        throw new Error("Invalid offer or offer exceeds budget");
      }

      if (decision.action === "accept" && offer !== ask) {
        throw new Error("Acceptance price must match current ask");
      }

      lastOffer = offer ?? null;
      console.log(`[buyer] turn=${turn}, ask=${ask}, offer=${offer}, budget=${budget}, lastOffer=${lastOffer}, sellerText=${sellerText}`);
      console.log(`[offer] ${offer}, budget=${budget}`);

      task = await send(String(offer));
    }

    // A deliberate walk or turn limit can leave the remote task open.
    if (isState(stateOf(task), "INPUT_REQUIRED") && taskId) {
      try {
        const cancelResponse = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", "A2A-Version": "1.0" },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: randomUUID(),
            method: "CancelTask",
            params: { id: taskId },
          }),
          signal: AbortSignal.timeout(10_000),
        });
        if (!cancelResponse.ok) throw new Error(`HTTP ${cancelResponse.status}`);
        const cancelResult = (await cancelResponse.json()) as { error?: { message?: string } };
        if (cancelResult.error) throw new Error(cancelResult.error.message ?? "CancelTask failed");
        console.log(`[cancel] task=${taskId}`);
      } catch (error) {
        console.error("[cancel error]", error instanceof Error ? error.message : String(error));
      }
    }
  } catch (error) {
    resultState = error instanceof RemoteShopError 
        ? "remote_error" : "error";

    const message =
      error instanceof Error ? error.message : String(error);

    console.error(`[buyer error] ${message}`);

    review = `The transaction result could not be confirmed: ${message}`;
  }

  const price =
    receipt && typeof receipt.price === "number"
      ? receipt.price
      : null;

  memory.push({
    shopName: shop.name,
    item,
    price,
    result: resultState,
    receiptMismatch,
    occurredAt: Date.now(),
  });

  try {
    saveMemory(memory, MEMORY_PATH);
    console.error(`[memory] saved ${memory.length} records`);
  } catch (error) {
    console.error("[memory error]", error);
  }

  const result = {
    shop: shopUrl,
    receipt,
    stars,
    review,
  };

  console.log(JSON.stringify(result));

  }

main().catch((error) => { console.error(error); process.exitCode = 1; });

