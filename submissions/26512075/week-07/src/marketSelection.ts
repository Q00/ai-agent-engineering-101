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

        const score = reviewScore - mismatches * 2;

        return {
            shop, reviewScore, reviewCount: stars.length, ownTradeCount: ownTrades.length, mismatches, score, onCooldown,
        };
    });

    scored.sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
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
        `score=${selected.score.toFixed(2)}`;

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