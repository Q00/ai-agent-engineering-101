import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createPublicKey, verify } from "node:crypto";
import { matchingSkus, type MarketOrder } from "./marketOrder.js";
import { chooseShop, type Shop, type TradeRecord, stockCoolingDown } from "./marketSelection.js";

export function readAnalysis(state = process.env.MARKET_DIR || ".market", now = Date.now() / 1000): any | null {
  try {
    const r = JSON.parse(readFileSync(join(state, "forum-candidates.json"), "utf8"));
    if (r.market !== (process.env.MARKET_URL || "https://market.ourolabs.ai").replace(/\/$/, "")) return null;
    if (![r.generated_at, r.forum_snapshot_mtime].every(t => Number.isFinite(t) && now - t >= -5 && now - t <= 300)) return null;
    return r;
  } catch { return null; }
}

export function selectPair(shops: Shop[], order: MarketOrder, memory: TradeRecord[], report: any) {
  const eligible = availableShops(shops, order, memory);
  if (!eligible.length) return null;
  const fallback = () => {
    const base = chooseShop(eligible, memory);
    const sku = matchingSkus(base.shop, order)[0]!;
    const offering = eligible.filter(s => matchingSkus(s, order).includes(sku));
    return {...chooseShop(offering, memory, sku), sku};
  };
  if (report?.coverage_ready !== true || !Array.isArray(report.medium_candidates)) return fallback();
  const band = report.volume_band;
  if (!band || !Number.isInteger(band.min) || !Number.isInteger(band.max) || band.min < 1 || band.max < band.min) return fallback();
  const rows = report.medium_candidates.filter((r: any) => typeof r?.sku === "string" &&
    Number.isInteger(r.sample_settlements) && r.sample_settlements >= band.min && r.sample_settlements <= band.max);
  rows.sort((a: any, b: any) => Math.abs(a.sample_settlements - (band.min + band.max) / 2) -
    Math.abs(b.sample_settlements - (band.min + band.max) / 2) ||
    (a.forum_posts_mentioning_sku || 0) - (b.forum_posts_mentioning_sku || 0) || a.sku.localeCompare(b.sku));
  for (const row of rows) {
    const offering = eligible.filter(s => matchingSkus(s, order).includes(row.sku));
    if (offering.length) return {...chooseShop(offering, memory, row.sku), sku: row.sku as string};
  }
  return fallback();
}

function canonical(value: any): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  return JSON.stringify(value);
}

/** Only market-signed unit grades are price evidence; seller prose is not. */
export function verifiedComps(meta: any, sku: string, order: MarketOrder, report: any, now = Date.now() / 1000) {
  try {
    if (!report || !Number.isFinite(report.generated_at) || now - report.generated_at > 300 || now - report.generated_at < -5 ||
        !order.market_pubkey || !meta?.cert?.cert || typeof meta.cert.sig !== "string") return null;
    const cert = meta.cert.cert;
    const key = createPublicKey({key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(order.market_pubkey, "base64")]), format: "der", type: "spki"});
    if (!verify(null, Buffer.from(canonical(cert)), key, Buffer.from(meta.cert.sig, "base64")) || cert.sku !== sku || cert.unit !== meta.unit) return null;
    const warranty = meta.offered_warranty_hours;
    if (typeof warranty !== "number" || !Number.isFinite(warranty) || warranty < 0 || warranty > 72) return null;
    let grade = cert.grade;
    if (order.kind === "food") {
      const catalog = JSON.parse(readFileSync(join(process.env.MARKET_DIR || ".market", "catalog.json"), "utf8"));
      const shelf = catalog[sku]?.shelf_hours;
      if (!Number.isFinite(shelf) || shelf <= 0 || !Number.isFinite(cert.expires_at) || cert.expires_at <= now) return null;
      const fraction = (cert.expires_at - now) / (3600 * shelf);
      grade = fraction >= .8 ? "A" : fraction >= .45 ? "B" : "C";
    }
    if (!["A", "B", "C"].includes(grade)) return null;
    const row = report.condition_candidates?.find((r: any) => r.sku === sku && r.grade === grade &&
      r.warranty_status === "known" && r.warranty_hours === warranty && r.sample_settlements >= 3 &&
      Number.isFinite(r.sample_median_settled_price) && r.sample_median_settled_price > 0);
    return row ? {sku, grade, warranty_hours: warranty, median: row.sample_median_settled_price,
      sample_count: row.sample_settlements, source: "official_ledger"} : null;
  } catch { return null; }
}

export function listPrice(sku: string): number | undefined {
  try {
    const p = JSON.parse(readFileSync(join(process.env.MARKET_DIR || ".market", "catalog.json"), "utf8"))[sku]?.list_price;
    return Number.isFinite(p) && p > 0 ? p : undefined;
  } catch { return undefined; }
}

export function availableShops(shops: Shop[], order: MarketOrder, memory: TradeRecord[]): Shop[] {
  return shops.map(s => {
    const allowed = matchingSkus(s, order).filter(sku => !stockCoolingDown(memory, s.name, sku));
    return {...s, offers: allowed.map(sku => ({sku, qty: 1}))};
  }).filter(s => s.offers.length > 0);
}
