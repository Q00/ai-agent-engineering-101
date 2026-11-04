import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { Message, SendMessageRequest, TaskState, } from "@a2a-js/sdk";
import { ClientFactory, DefaultAgentCardResolver } from "@a2a-js/sdk/client";
import { askPythonHarness, askRawHarness } from "./harnessBridge.js";
function arg(key) {
    const n = process.argv.indexOf(key);
    if (n < 0 || !process.argv[n + 1])
        throw new Error(`Missing ${key}`);
    return process.argv[n + 1];
}
function errorChain(error) {
    const messages = [];
    let current = error;
    for (let depth = 0; depth < 4 && current; depth++) {
        messages.push(String(current));
        current = current instanceof Error ? current.cause : undefined;
    }
    return messages.join(" <- ");
}
function taskOf(value) {
    if (!value || typeof value !== "object" || !("id" in value) || !("status" in value)) {
        throw new Error("Expected an A2A task");
    }
    return value;
}
function textOf(task) {
    return (task.status?.message?.parts ?? [])
        .filter((p) => p.content?.$case === "text")
        .map((p) => p.content.value).join(" ");
}
function askOf(task) {
    const meta = task.status?.message?.metadata;
    if (Number.isSafeInteger(meta?.ask))
        return meta.ask;
    const nums = [...textOf(task).matchAll(/\b\d+\b/g)];
    return nums.length === 1 ? Number(nums[0][0]) : undefined;
}
function receiptPrice(task, requested) {
    for (const artifact of task.artifacts) {
        for (const part of artifact.parts) {
            if (part.content?.$case !== "data")
                continue;
            const data = part.content.value;
            if (Number.isSafeInteger(data?.price) && data.price > 0) {
                const label = `${String(data.item ?? "")} ${String(data.itemId ?? "")}`.toLowerCase();
                if (!label.includes(requested.toLowerCase())) {
                    throw new Error(`Receipt item does not match ${requested}: ${label}`);
                }
                return data.price;
            }
        }
    }
    throw new Error("Completed task lacks numeric receipt price");
}
class Session {
    shop;
    item;
    taskId = "";
    task;
    summary = [];
    constructor(shop, item) {
        this.shop = shop;
        this.item = item;
    }
    async send(text) {
        const msg = Message.fromJSON({
            messageId: randomUUID(), role: "ROLE_USER", taskId: this.taskId,
            parts: [{ text, mediaType: "text/plain" }],
        });
        const response = await this.shop.client.sendMessage(SendMessageRequest.fromJSON({
            message: Message.toJSON(msg),
        }));
        const task = taskOf(response);
        this.taskId = task.id;
        this.task = task;
        this.summary.push(`buyer ${text}; seller ${textOf(task)}`);
        this.summary = this.summary.slice(-4);
        return task;
    }
    snapshot() {
        return {
            shop: this.shop.id, taskId: this.taskId,
            state: this.task?.status?.state, ask: this.task ? askOf(this.task) : undefined,
            recent: this.summary,
        };
    }
}
async function selectShops(item, shops, receipts, budget, sessions) {
    const candidate = shops.map((shop) => ({
        id: shop.id, name: shop.card.name,
        description: shop.card.description,
        skills: shop.card.skills.map((skill) => ({
            name: skill.name, description: skill.description, tags: skill.tags,
        })),
        quote: sessions?.find((s) => s.shop.id === shop.id)?.snapshot(),
    }));
    const decision = await askRawHarness({
        side: "select_shops", item, limit: budget,
        purchased: receipts.map(({ item, price }) => ({ item, price })),
        candidates: candidate,
    });
    if (decision.action !== "select_shops" || !Array.isArray(decision.shops)) {
        throw new Error("Harness did not select shops");
    }
    const valid = new Set(shops.map((x) => x.id));
    const selected = decision.shops.filter((id) => typeof id === "string" && valid.has(id));
    return [...new Set(selected)].slice(0, 4);
}
async function negotiate(session, cap) {
    for (let turn = 0; turn < 7; turn++) {
        const task = session.task;
        if (task.status?.state === TaskState.TASK_STATE_COMPLETED)
            return task;
        if (task.status?.state !== TaskState.TASK_STATE_INPUT_REQUIRED)
            return undefined;
        const answer = await askPythonHarness({
            side: "buyer", item: session.item, limit: cap,
            currentAsk: askOf(task), sellerTxt: textOf(task), transcript: session.summary,
        });
        if (answer.action === "walk_away" || answer.action === "reject")
            return undefined;
        if (answer.action === "accept" && askOf(task) !== undefined && answer.price !== askOf(task)) {
            throw new Error("Buyer acceptance differs from current ask");
        }
        console.error("SHOP_DECISION", JSON.stringify({ shop: session.shop.id, item: session.item,
            taskId: session.taskId, action: answer.action, price: answer.price, cap }));
        await session.send(String(answer.price));
    }
    return undefined;
}
async function main() {
    const directory = JSON.parse(await readFile(arg("--directory"), "utf8"));
    const list = JSON.parse(await readFile(arg("--list"), "utf8"));
    const budget = Number(arg("--budget"));
    if (!Array.isArray(directory) || !Array.isArray(list) ||
        !Number.isSafeInteger(budget) || budget <= 0)
        throw new Error("Invalid inputs");
    if (new Set(list.map((x) => x.item)).size !== list.length)
        throw new Error("Duplicate shopping item");
    const resolver = new DefaultAgentCardResolver({
        fetchImpl: (input, init) => fetch(input, {
            ...init, signal: AbortSignal.timeout(5000),
        }),
    });
    const factory = new ClientFactory();
    const shops = [];
    const failures = [];
    // A directory can have 20+ URLs; bound simultaneous connections and retain each failure.
    for (let offset = 0; offset < directory.length; offset += 4) {
        const batch = directory.slice(offset, offset + 4);
        const discovered = await Promise.allSettled(batch.map(async ({ id, url }) => {
            let card;
            let lastError;
            for (let attempt = 0; attempt < 3; attempt++) {
                try {
                    card = await resolver.resolve(url);
                    break;
                }
                catch (error) {
                    lastError = error;
                    if (attempt < 2)
                        await delay(200 * (attempt + 1));
                }
            }
            if (!card)
                throw new Error(`card fetch failed after 3 attempts: ${errorChain(lastError)}`);
            if (!card.supportedInterfaces?.some((i) => i.protocolBinding === "JSONRPC" && i.protocolVersion === "1.0")) {
                throw new Error(`${id} lacks JSON-RPC 1.0`);
            }
            return { id, url, card, client: await factory.createFromAgentCard(card) };
        }));
        discovered.forEach((result, index) => {
            if (result.status === "fulfilled") {
                shops.push(result.value);
            }
            else {
                const detail = `${batch[index].id}: ${errorChain(result.reason)}`;
                failures.push(detail);
                console.error("CARD_ERROR", detail);
            }
        });
    }
    if (!shops.length) {
        throw new Error(`No reachable Agent Cards (${failures.length} failed). ` +
            `First errors: ${failures.slice(0, 3).join(" | ")}`);
    }
    if (failures.length)
        console.error("CARD_SKIPPED", `${failures.length} of ${directory.length}`);
    console.error("DIRECTORY", JSON.stringify({ cards: shops.length,
        skills: shops.map((s) => ({ id: s.id, tags: s.card.skills.flatMap((x) => x.tags) })) }));
    const receipts = [];
    const purchased = new Set();
    for (const line of list) {
        if (purchased.has(line.item))
            continue;
        const remaining = budget - receipts.reduce((sum, r) => sum + r.price, 0);
        const picked = await selectShops(line.item, shops, receipts, remaining);
        if (!picked.length)
            throw new Error(`No shop for ${line.item}`);
        const sessions = await Promise.all(picked.map(async (id) => {
            const session = new Session(shops.find((s) => s.id === id), line.item);
            try {
                await session.send(`I want to buy a ${line.item}`);
                return session;
            }
            catch (error) {
                console.error("SHOP_OPEN_ERROR", id, String(error));
                return undefined;
            }
        }));
        const opened = sessions.filter((s) => !!s &&
            s.task?.status?.state === TaskState.TASK_STATE_INPUT_REQUIRED);
        console.error("SHOP_OPEN", JSON.stringify(opened.map((s) => s.snapshot())));
        if (!opened.length)
            throw new Error(`No open negotiation for ${line.item}`);
        const ranked = await selectShops(line.item, opened.map((s) => s.shop), receipts, remaining, opened);
        let bought = false;
        for (const id of ranked) {
            const session = opened.find((s) => s.shop.id === id);
            const result = await negotiate(session, remaining);
            if (!result || result.status?.state !== TaskState.TASK_STATE_COMPLETED)
                continue;
            const price = receiptPrice(result, line.item);
            if (price > remaining || purchased.has(line.item))
                throw new Error("Budget or duplicate violation");
            const receipt = { item: line.item, shop: id, price, taskId: session.taskId };
            receipts.push(receipt);
            purchased.add(line.item);
            console.error("PURCHASE", JSON.stringify({ ...receipt, remaining: remaining - price }));
            bought = true;
            break;
        }
        if (!bought)
            throw new Error(`No deal for ${line.item}; ${JSON.stringify(opened.map((s) => s.snapshot()))}`);
    }
    const spent = receipts.reduce((sum, r) => sum + r.price, 0);
    if (receipts.length !== list.length || spent > budget)
        throw new Error("Shopping constraints failed");
    console.log(JSON.stringify({ receipts, spent, budget, remaining: budget - spent }));
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
//# sourceMappingURL=shopping-list.js.map