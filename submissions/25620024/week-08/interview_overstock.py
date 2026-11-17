import json, time, sqlite3, collections, statistics, sys
sys.path.insert(0, "/home/market/ai-agent-engineering-101/submissions/25620024/week-08")
import marketing_agent as m, market_model, promo
W = "/home/market/ai-agent-engineering-101/submissions/25620024/week-08"
c = market_model.db(); now = time.time()
inv = json.load(open(f"{W}/.market/inventory.json")); units = inv["units"]
by = collections.defaultdict(list)
for u in units: by[u["sku"]].append(u)
over = sorted(json.load(open(f"{W}/private/groupbuy.json"))["skus"], key=lambda s: -sum(u["cost"] for u in by.get(s, [])))[:8]
cat = market_model.catalog()
rows = []
for sku in over:
    xs = by.get(sku, []); lp = cat[sku]["list_price"]
    q = c.execute("select count(*), sum(outcome='deal') from quotes where sku=? and ts>?", (sku, now - 6 * 3600)).fetchone()
    s = c.execute("""select count(*) from settles s join settle_units x on x.seq=s.seq join supply_units su on su.unit=x.unit
                     where su.shop='dragonash' and s.sku=? and s.ts>?""", (sku, now - 6 * 3600)).fetchone()[0]
    mk = c.execute("select count(*), min(amount) from settles where sku=? and ts>?", (sku, now - 6 * 3600)).fetchone()
    shops = c.execute("select count(distinct shop) from settles where sku=? and ts>?", (sku, now - 6 * 3600)).fetchone()[0]
    g = collections.Counter(u["grade"] for u in xs)
    asks = {}
    for gr in g:
        u = min((x for x in xs if x["grade"] == gr), key=lambda x: x["cost"])
        asks[gr] = promo.quote(c, u, stock_n=len(xs))["price"]
    med = {gr: int(market_model.market_price(c, sku, grade=gr)[0] or 0) for gr in g}
    rows.append({"sku": sku, "name": cat[sku]["name"][:30], "category": cat[sku]["category"].split(">")[-1], "list": lp,
                 "held": len(xs), "held_by_grade": dict(g), "cost_total": int(sum(x["cost"] for x in xs)),
                 "avg_cost_by_grade": {gr: int(statistics.mean(x["cost"] for x in xs if x["grade"] == gr)) for gr in g},
                 "our_ask_from_by_grade": asks, "market_median_by_grade": med,
                 "last_6h": {"our_quotes": q[0], "our_agreed": q[1], "our_settled": s, "market_settles": mk[0],
                             "market_low": mk[1], "shops_selling": shops}})
brief = {"situation": "Our shop 'dragonash' (classroom agent market, Round 3 ends 2026-11-17 23:59 KST, ~7.4 days) over-bought these SKUs through a restock bug and holds ~270M won of them at cost. Goods pay storage 1%/day of cost. Score: shop ALL = 0.5 Book (cash + unsold stock at cost) + 0.3 revenue + 0.2 stars (one mean per reviewing account), percentiles among shops. Selling below ~92% of cost loses more Book than holding. Buyers are other students' LLM agents: they get orders (a category, acceptable SKUs, budget), ask shops for quotes over A2A, haggle, and pay through a kit that may refuse if the checkout fails their hidden policy (grade, warranty hours, shelf life, certificate). Many buyers settle at whichever shop they ask first or the cheapest they see; forum posts and public ledger prices may steer them. We already: price overstock about cost (floor 92% of cost, never under 85% of grade median), post a [재고 정리] shop ad and factual buyer price-data posts (same owner disclosed), give category orders the most-held SKU, stop restocking these. Constraints: no self-dealing (our buyer never buys from our shop), no prompt injection or instructions to other agents, no false claims, no attacks on other shops.",
         "cash_won": inv["balance"], "overstock": rows}
Q = ("You are the {team} of dragonash. HQ asks every team: find clever, honest ways to sell this overstock faster in the next 7 days "
     "(more buyers asking us, more of those paying, or other channels within the market rules). Use the numbers. Answer JSON: "
     "{{\"ideas\": [{{\"idea\": str, \"why_it_works\": str, \"evidence_from_brief\": str, \"how_to_implement\": str, \"risk\": str}}] (max 4)}}\n\n")
S = {"type": "object", "additionalProperties": False, "required": ["ideas"], "properties": {"ideas": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["idea", "why_it_works", "evidence_from_brief", "how_to_implement", "risk"],
    "properties": {k: {"type": "string"} for k in ("idea", "why_it_works", "evidence_from_brief", "how_to_implement", "risk")}}}}}
teams = {"sales": "sales team (pricing and the shop clerk)", "research": "market research team", "scm": "SCM team (stock and supply)",
         "finance": "finance team (Book, cash, storage)", "buyer": "buyer team (you know how buyer agents choose shops)"}
out = {"brief": brief}
for role, name in teams.items():
    try:
        out[role] = json.loads(m.ask(role, Q.format(team=name) + json.dumps(brief, ensure_ascii=False), S))
    except Exception as e:
        out[role] = {"error": str(e)[:300]}
    print(role, "ok" if "ideas" in out[role] else out[role], flush=True)
json.dump(out, open(f"{W}/private/interview_overstock.json", "w"), ensure_ascii=False, indent=1)
print("done")
