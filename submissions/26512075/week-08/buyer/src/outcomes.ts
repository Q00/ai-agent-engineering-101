import {readFileSync} from "node:fs";
import {join} from "node:path";
import type {TradeRecord} from "./marketSelection.js";

function rows(path: string): any[] {
  try {
    return readFileSync(path, "utf8").split("\n").filter(Boolean).flatMap(line => {
      try { return [JSON.parse(line)]; } catch { return []; } // A concurrent append may be incomplete.
    });
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") console.error(`[outcomes] cannot read ${path}`);
    return [];
  }
}

export function reconcileOutcomes(memory: TradeRecord[], payments: any[], purchases: any[], broken: any[]) {
  const updated = memory.map(t => ({...t}));
  const byLedger = new Map(purchases.filter(p => Number.isSafeInteger(p.ledger)).map(p => [p.ledger, p]));
  for (const trade of updated) {
    if (!trade.orderId || !trade.shopUrl) continue;
    const matches = payments.filter(p => p.order === trade.orderId && p.shop === trade.shopUrl);
    const signatures = new Set(matches.map(p => JSON.stringify([p.paid, p.ledger, p.reason])));
    if (!matches.length || signatures.size !== 1) continue;
    const payment = matches[0];
    if (payment.paid === true && Number.isSafeInteger(payment.ledger)) {
      trade.paymentStatus = "paid";
      trade.ledger = payment.ledger;
      trade.paymentReason = "";
      const purchase = byLedger.get(payment.ledger);
      if (purchase && purchase.orderId === trade.orderId && purchase.shopUrl === trade.shopUrl &&
          purchase.sku === trade.sku && ["A", "B", "C"].includes(purchase.grade)) {
        trade.settledGrade = purchase.grade;
        if (typeof purchase.warrantyHours === "number" && Number.isFinite(purchase.warrantyHours) && purchase.warrantyHours >= 0)
          trade.warrantyHours = purchase.warrantyHours;
      }
    } else if (payment.paid === false) {
      trade.paymentStatus = "refused";
      trade.paymentReason = String(payment.reason ?? "unknown");
    }
  }
  let ambiguousBreaks = 0;
  const observedTimes = [...byLedger.values()].map(p => p.observedAt).filter(Number.isFinite);
  const trackedSince = observedTimes.length ? Math.min(...observedTimes) : Infinity;
  for (const event of broken) {
    if (typeof event.name !== "string" || !Number.isFinite(event.broke_at)) continue;
    // During the first six hours an older, untracked purchase could have broken.
    if (event.broke_at - trackedSince < 6 * 3600) { ambiguousBreaks++; continue; }
    const candidates = [...byLedger.values()].filter(p => p.name === event.name &&
      Number.isFinite(p.observedAt) && event.broke_at >= p.observedAt && event.broke_at - p.observedAt <= 6 * 3600);
    if (candidates.length !== 1) { ambiguousBreaks++; continue; }
    const purchase = candidates[0];
    const trade = updated.find(t => t.ledger === purchase.ledger && t.orderId === purchase.orderId && t.shopUrl === purchase.shopUrl);
    if (!trade) { ambiguousBreaks++; continue; }
    trade.brokenAt = event.broke_at;
    trade.warrantyHours = purchase.warrantyHours;
    // Eligibility is not proof that the market actually refunded the payment.
    trade.warrantyEligible = typeof purchase.warrantyHours === "number"
      ? purchase.warrantyHours > 0 && event.broke_at - purchase.observedAt <= purchase.warrantyHours * 3600 : null;
  }
  return {memory: updated, ambiguousBreaks};
}

export function loadOutcomes(memory: TradeRecord[], state = process.env.MARKET_DIR || ".market") {
  const result = reconcileOutcomes(memory, rows(join(state,"payments.jsonl")),
    rows(join(state,"buyer-purchases.jsonl")), rows(join(state,"broken.jsonl")));
  console.error(`[outcomes] paid=${result.memory.filter(t=>t.paymentStatus==="paid").length} refused=${result.memory.filter(t=>t.paymentStatus==="refused").length} unresolved_breaks=${result.ambiguousBreaks}`);
  return result.memory;
}
