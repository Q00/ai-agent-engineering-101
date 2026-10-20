function normalize(decision) {
    if (decision.price == null && decision.ask != null)
        decision.price = decision.ask;
    if (typeof decision.price === "string")
        decision.price = Number(decision.price);
    return decision;
}
export function checkDecision(request, decision) {
    if (!["offer", "accept", "reject", "walk_away"].includes(decision.action)) {
        throw new Error(`Unknown action: ${decision.action}`);
    }
    const d = normalize(decision);
    if (d.action == "offer" || d.action == "accept") {
        if (!Number.isSafeInteger(d.price) || (d.price ?? 0) <= 0) {
            console.log(`decision err: ${d.price}`);
            throw new Error("offer or acceptance needs a positive integer price");
        }
        if (request.side == "seller" && d.price < request.limit) {
            throw new Error("seller decision violates reserve");
        }
        if (request.side == "buyer" && d.price > request.limit) {
            throw new Error("Buyer decision exceeds budget");
        }
    }
    return d;
}
// module.exports = { checkDecision }
//# sourceMappingURL=decision.js.map