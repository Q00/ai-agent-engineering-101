import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Reference scenario only: category averages are NOT a unit's acquisition cost. */
export function savedScenario(item: {list_price?: unknown; category?: unknown; kind?: unknown}, ask: number) {
  const list = item.list_price;
  if (typeof list !== "number" || !Number.isFinite(list) || list <= 0 || !Number.isFinite(ask) || ask <= 0) return null;
  const ratio = item.kind === "food" ? 0.52 :
    typeof item.category === "string" && item.category.split(">")[0] === "가구/침구" ? 0.61 : null;
  return {
    formula: "min(1, (list_price - paid_price) / (list_price - actual_cost))",
    actualCostKnown: false,
    listPrice: list,
    categoryAverageCostRatio: ratio,
    source: "operator-provided Round 3 Saved patch announcement",
    estimateOnly: true,
    savedAtAskScenario: ratio === null ? null : Math.min(1, (list - ask) / (list * (1 - ratio))),
    targetPriceFormula: "list_price - target_saved * (list_price - estimated_cost)",
    outcomeRules: {useless: -1, refunded: 0, usefulMaximum: 1},
  };
}

export function readSavedContext(sku: string, ask: number, state = process.env.MARKET_DIR || ".market") {
  try {
    const catalog = JSON.parse(readFileSync(join(state, "catalog.json"), "utf8"));
    return catalog[sku] ? savedScenario(catalog[sku], ask) : null;
  } catch { return null; }
}
