import { readFileSync } from "node:fs";
import { writeFileSync } from "fs";



export type Review = {
    stars: number;
    review: string;
    round: number;
}

export type Shop = {
    name: string;
    url: string;
    reviews: Review[];
}

export type TradeRecord = {
    orderId?: string;
    shopUrl?: string;
    sku?: string;
    paymentStatus?: "paid" | "refused";
    paymentReason?: string;
    negotiationReason?: "out_of_stock";
    settledGrade?: string;
    ledger?: number;
    brokenAt?: number;
    warrantyHours?: number | null;
    warrantyEligible?: boolean | null;
    shopName: string;
    item: string;
    price: number | null;
    result: string;
    receiptMismatch: boolean | null;
    occurredAt?: number;
}

export const MEMORY_PATH = "buyer_memory.json";

export function loadShops(path: string): Shop[] {
    const data: unknown = JSON.parse(readFileSync(path, "utf-8"));

    if (!Array.isArray(data) || data.length === 0) {
        throw new Error("the shops files must contain at least one shop");
    }

    for (const shop of data) {
        if (
            typeof shop != "object" ||
            shop === null ||
            typeof shop.name !== "string" ||
            typeof shop.url !== "string" ||
            !Array.isArray(shop.reviews)
        ) {
            throw new Error("not valid shop entry in shop file")
        }

        for (const review of shop.reviews) {
            if (
                typeof review !== "object" ||
                review === null ||
                typeof review.stars !== "number" ||
                !Number.isFinite(review.stars) ||
                review.stars < 1 ||
                review.stars > 5 ||
                typeof review.review !== "string" ||
                typeof review.round !== "number" ||
                !Number.isFinite(review.round)
            ) {
                throw new Error(`not valid review entry in reviews, ${shop.name}: ${JSON.stringify(review)}`)
            }
        }
    }

    return data as Shop[]
}

export function loadMemory(path = MEMORY_PATH): TradeRecord[] {
    try {
        const data: unknown = JSON.parse(readFileSync(path, "utf-8"));


         if (!Array.isArray(data)) {
            throw new Error("Buyer memory must be a json")
        }

        return data as TradeRecord[];
    } catch(error) {
        const e = error as NodeJS.ErrnoException;

        if (e.code === "ENOENT") {
            return [];
        }

        throw error;
    }
}

// TODO : implement a fallback function for if it fails to save
export function saveMemory(records: TradeRecord[], path = MEMORY_PATH): void {
    writeFileSync(path, JSON.stringify(records, null, 2), "utf-8");
}

export function chooseShop(
    shops: Shop[],
    memory: TradeRecord[],
    sku?: string,
): { shop: Shop; reason: string } {
    if (shops.length === 0) {
        throw new Error("no shops available");
    }

    const scored = shops.map((shop) => {
        const stars = shop.reviews
            .map((review) => review?.stars)
            .filter(
                (value): value is number =>
                    typeof value == "number" &&
                    Number.isFinite(value) &&
                    value >= 1 &&
                    value <= 5,
            )

        const reviewScore = stars.length > 0
                                ? stars.reduce((sum, value) => sum + value, 0) / stars.length
                                : 3;

        
        const ownTrades = memory.filter(
            (trade) => trade.shopName === shop.name,
        );

        const mismatches = ownTrades.filter(
            (trade) => trade.receiptMismatch === true,
        ).length;

        const recentTrades = ownTrades.slice(-2);
        const lastTrade = recentTrades[recentTrades.length - 1];

        const cooldownMs = 5 * 60 * 1000;
        const now = Date.now();

        const onCooldown =
            recentTrades.length === 2 &&
            recentTrades.every((trade) => trade.result === "remote_error") &&
            typeof lastTrade?.occurredAt === "number" &&
            now >= lastTrade.occurredAt &&
            now - lastTrade.occurredAt < cooldownMs;

        const recentOutcomes = ownTrades.slice(-20);
        const brokenCount = recentOutcomes.filter(t => t.brokenAt !== undefined).length;
        // Only stock checkout refusals affect selection; local/API failures are not shop dishonesty.
        const stockRefusals = recentOutcomes.filter(t => t.paymentStatus === "refused" &&
            t.paymentReason?.includes("not in stock")).length;
        const score = reviewScore - mismatches * 2 - brokenCount - stockRefusals * 0.5;

        return {
            shop, reviewScore, reviewCount: stars.length, ownTradeCount: ownTrades.length, mismatches, brokenCount, stockRefusals, score, onCooldown,
        };
    });

    const evidence = sku ? comparablePrices(memory, shops, sku) : new Map<string, number>();
    const prices = evidence.size === shops.length ? evidence : new Map<string, number>();
    scored.sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        const ap = prices.get(a.shop.name), bp = prices.get(b.shop.name);
        if (ap !== undefined && bp !== undefined && ap !== bp) return ap - bp;
        if (b.reviewCount != a.reviewCount) return b.reviewCount - a.reviewCount;
        if (a.ownTradeCount != b.ownTradeCount) return a.ownTradeCount - b.ownTradeCount;

        return a.shop.name.localeCompare(b.shop.name);
    });

    const avail = scored.filter((entry) => !entry.onCooldown);

    for (const entry of scored) {
        if (entry.onCooldown) {
            console.error(
                `[cooldown] temporarily avoiding shop=${entry.shop.name}`,);
        }
    }

    if (avail.length === 0) {
        console.error("[cooldown] all shops excluded; using score fallback");
    }

    const selected = (avail.length > 0 ? avail : scored)[0]!;

    const reason =
        `shop=${selected.shop.name},` +
        `review_average=${selected.reviewScore.toFixed(2)}, ` +
        `reviews=${selected.reviewCount}, ` +
        `own_trades=${selected.ownTradeCount}, ` +
        `receipt_mismatches=${selected.mismatches}, ` +
        `broken=${selected.brokenCount}, stock_refusals=${selected.stockRefusals}, ` +
        `score=${selected.score.toFixed(2)}, ` +
        `historical_price_tiebreak=${prices.get(selected.shop.name) ?? "unavailable"}`;

    return {
        shop: selected.shop, reason,
    };
}

export function recordTrade(params:{
    shopName: string;
    item: string;
    price: number | null;
    result: string;
    receiptMismatch: boolean | null;
}) {
    const memory = loadMemory();

    try {
        memory.push({
            shopName: params.shopName,
            item: params.item,
            price: params.price,
            result: params.result,
            receiptMismatch: params.receiptMismatch,
        });

        saveMemory(memory);
    } catch(error) {
        console.error(`cannot load and save memory ${error}`)
        throw error
    }
}

/** Explicit terminal stock refusals only; never infer this from arbitrary failures. */
export function isStockRefusal(text: string): boolean {
    if (/\b(?:not|never)\s+out of stock\b|재고(?:가|는)?\s*없지/.test(text.toLowerCase())) return false;
    return /\b(?:out of stock|not in stock|none of that in stock|no stock available)\b/i.test(text)
        || /재고(?:가|는)?\s*(?:없|부족)|품절/.test(text);
}

export function stockCoolingDown(memory: TradeRecord[], shop: string, sku: string, now = Date.now()): boolean {
    return memory.some(t => t.shopName === shop && t.sku === sku &&
        t.negotiationReason === "out_of_stock" && typeof t.occurredAt === "number" &&
        now >= t.occurredAt && now - t.occurredAt < 5 * 60_000);
}

/** Historical price tie-break only: same SKU, grade and warranty, >=3 paid ledgers per shop.
 * No estimate of actual cost/Saved; current stock conditions must still be checked during negotiation.
 */
export function comparablePrices(memory: TradeRecord[], shops: Shop[], sku: string, now = Date.now()): Map<string, number> {
    const groups = new Map<string, Map<string, Map<number, number>>>();
    for (const t of memory) {
        if (t.sku !== sku || t.paymentStatus !== "paid" || !Number.isSafeInteger(t.ledger) ||
            !Number.isSafeInteger(t.price) || t.price! <= 0 || t.receiptMismatch === true ||
            !["A", "B", "C"].includes(t.settledGrade ?? "") ||
            typeof t.warrantyHours !== "number" || !Number.isFinite(t.warrantyHours) || t.warrantyHours < 0 ||
            typeof t.occurredAt !== "number" || now < t.occurredAt || now - t.occurredAt > 24 * 3600_000) continue;
        const key = `${t.settledGrade}:${t.warrantyHours}`;
        const group = groups.get(key) ?? new Map<string, Map<number, number>>();
        const trades = group.get(t.shopName) ?? new Map<number, number>();
        trades.set(t.ledger!, t.price!); group.set(t.shopName, trades); groups.set(key, group);
    }
    // Require a single common condition cohort across ALL candidate shops.
    for (const [, group] of [...groups].sort(([a], [b]) => a.localeCompare(b))) {
        if (!shops.every(s => (group.get(s.name)?.size ?? 0) >= 3)) continue;
        return new Map(shops.map(s => {
            const values = [...group.get(s.name)!.values()].sort((a, b) => a - b);
            const middle = Math.floor(values.length / 2);
            return [s.name, values.length % 2 ? values[middle]! : (values[middle - 1]! + values[middle]!) / 2];
        }));
    }
    return new Map();
}
