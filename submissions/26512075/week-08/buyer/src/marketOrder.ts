import { readFileSync } from "node:fs";



export type MarketOrder = {
    round: number;
    item: string; 
    budget: number;
    acceptable: string[];
    kind?: string;
    use_within_hours?: number
};


/** Parse the public order file the kit writes; never fall back on bad Round 3 input. */
export function parseOrder(value: unknown, expectedItem: string, expectedBudget: number): MarketOrder {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("order must be an object");
  const x = value as Record<string, unknown>;
  if (!Number.isSafeInteger(x.round) || (x.round as number) < 0) throw new Error("invalid order round");
  if (x.item !== expectedItem || x.budget !== expectedBudget) throw new Error("order item or budget does not match command");
  if ((x.round as number) < 3) return {round: x.round as number, item: expectedItem, budget: expectedBudget, acceptable: []};
  if (!Array.isArray(x.acceptable) || x.acceptable.length === 0 || x.acceptable.some(s => typeof s !== "string" || !s.trim())) {
    throw new Error("Round 3 order requires a nonempty acceptable SKU list");
  }
  if (x.kind !== "food" && x.kind !== "goods") throw new Error("invalid order kind");
  const h = x.use_within_hours;
  if (x.kind === "food" && (typeof h !== "number" || !Number.isFinite(h) || h < 0)) {
    throw new Error("food order requires valid use_within_hours");
  }

  return {
    round: x.round as number, item: expectedItem, budget: expectedBudget,
    acceptable: [...new Set(x.acceptable as string[])], kind: x.kind,
    ...(x.kind === "food" ? {use_within_hours: h as number} : {})
  };
}

export function readOrder(path: string, item: string, budget: number): MarketOrder {
  return parseOrder(JSON.parse(readFileSync(path, "utf8")), item, budget);
}

/** Kit advertises per-shop available SKUs. This is a snapshot, not a reservation. */
export function matchingSkus(shop: unknown, order: MarketOrder): string[] {
    if (!shop || typeof shop !== "object" || Array.isArray(shop)) return [];
  const offers = (shop as { offers?: unknown }).offers;
  if (!Array.isArray(offers)) return [];

  const offered = new Set<string>();

   for (const o of offers) {
    if (o && typeof o === "object" && typeof o.sku === "string" &&
        typeof o.qty === "number" && Number.isSafeInteger(o.qty) && o.qty > 0) offered.add(o.sku);
  }

  return order.acceptable.filter(s => offered.has(s));
}

export function receiptFitsSku(receipt:Record<string, unknown>, sku: string): boolean {
    return receipt.item === sku;
}

export function receiptFitsBudget(receipt: Record<string, unknown>, budget: number): boolean {
  return typeof receipt.price === "number" && Number.isSafeInteger(receipt.price) && receipt.price > 0 && receipt.price <= budget;
}

export function acceptance(order: MarketOrder): {min_hours_left: number} | undefined {
  return order.round >= 3 && order.kind === "food" && order.use_within_hours !== undefined
    ? {min_hours_left: order.use_within_hours} : undefined;
}