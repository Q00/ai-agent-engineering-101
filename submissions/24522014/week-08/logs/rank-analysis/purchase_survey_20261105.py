"""Read-only, no network, no model: replay of saved buyer records for four purchase questions. Nothing here changes a rule. Shop text is searched, not printed.

A replay cannot show what a seller would do with an offer never made, and acceptance rates are borrowed from the rule path.
"""
import ctypes
from collections import Counter, defaultdict
from datetime import datetime
import glob
import json
import os
from pathlib import Path
import re
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from buyer_fast import parse_ask, _names_another_price, _token_is_price, _preceding_word, NOT_CURRENT, PAST_LABEL

ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)
SINCE = "20261104T100000"
UNTIL = "20261105T051100"  # Fixed so the 19.2-hour figures can be reproduced.
names = sorted(n for n in os.listdir(ROOT / "logs/market-buyer") if SINCE <= n < UNTIL)
hours = (datetime.strptime(names[-1][:15], "%Y%m%dT%H%M%S") - datetime.strptime(names[0][:15], "%Y%m%dT%H%M%S")).total_seconds() / 3600
rows = []
for name in names:
    data = json.loads((ROOT / "logs/market-buyer" / name).read_text(encoding="utf-8"))
    rows.append((name, data["record"], data.get("selection_evidence") or []))
paid = {}
for line in (ROOT / ".market/payments.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        p = json.loads(line)
    except ValueError:
        continue
    if p.get("paid"):
        paid[p.get("order")] = p
catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
print(f"records {len(rows)}, {hours:.1f} h, paid {sum(r.get('order') in paid for _, r, _ in rows)}, orders seen twice {sum(n > 1 for n in Counter(r.get('order') for _, r, _ in rows if r.get('order')).values())}")


def agent_message(raw):
    """Text and metadata of the seller's last message in a saved JSON-RPC reply."""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw, {}
    found = []

    def walk(value):
        if isinstance(value, dict):
            if value.get("role") == "ROLE_AGENT" and isinstance(value.get("parts"), list):
                found.append(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(data)
    if not found:
        return "", {}
    last = found[-1] if "history" not in str(data)[:0] else found[-1]
    text = " ".join(p.get("text", "") for p in last.get("parts", []) if isinstance(p, dict))
    meta = dict(last.get("metadata") or {})
    for part in last.get("parts", []):
        if isinstance(part, dict) and isinstance(part.get("metadata"), dict):
            meta.update(part["metadata"])
    return text, meta


def last_reply(rec):
    folder = Path(str(rec.get("buyer_log_dir") or "").replace("\\", "/")).name
    files = sorted(glob.glob(str(ROOT / "logs/buyer" / folder / "*.response.txt")))
    return agent_message(Path(files[-1]).read_text(encoding="utf-8", errors="replace")) if files else ("", {})


NUMBER = r"(\d[\d,.]*)"
RULES = [
    ("my_price", re.compile(r"(?<![\w])my price\s*[:：=]?\s*" + NUMBER, re.I)),
    ("lowest_price", re.compile(r"(?<![\w])(?:our |my )?lowest price(?: for this unit)?\s*[:：=]?\s*" + NUMBER, re.I)),
    ("final_price", re.compile(r"(?<![\w])(?:our |my )?final price(?: for [^.:\d]{1,60})?(?:\s+is)?\s*[:：=]?\s*" + NUMBER, re.I)),
    ("meet_partway", re.compile(r"meet you (?:partway|halfway) at\s*" + NUMBER, re.I)),
    ("name_colon_krw", re.compile(r":\s*" + NUMBER + r"\s*(?:KRW|won|원)\b", re.I)),
]


def proposed(text):
    """(kind, price) when exactly one new label names exactly one clean price, with the current parser's cautions. Else (None, None)."""
    if PAST_LABEL.search(text):
        return None, None
    hits = defaultdict(set)
    for kind, pattern in RULES:
        for match in pattern.finditer(text):
            if _preceding_word(text, match.start()) in NOT_CURRENT or not _token_is_price(match[1], text[match.end():]):
                return None, None
            hits[kind].add(int(match[1].rstrip(".,").replace(",", "")))
    values = set().union(*hits.values()) if hits else set()
    if len(values) != 1:
        return None, None
    price = next(iter(values))
    if _names_another_price(text, price):
        return None, None
    return sorted(hits)[0], price


def number(value):
    return value if type(value) in (int, float) and value > 0 else None


def trail_of(rec):
    return [s for s in rec.get("decision_trail") or [] if isinstance(s, dict)]


rule_paid = Counter()
for _, rec, _ in rows:
    trail = trail_of(rec)
    code = trail[-1].get("reason_code") if trail else None
    if code in ("rule_offer", "rule_counter_offer"):
        rule_paid[code, "n"] += 1
        rule_paid[code, "paid"] += rec.get("order") in paid
RATE = rule_paid["rule_offer", "paid"] / max(1, rule_paid["rule_offer", "n"])
print("rule_offer paid rate", rule_paid["rule_offer", "paid"], "/", rule_paid["rule_offer", "n"], round(RATE, 3), " rule_counter", rule_paid["rule_counter_offer", "paid"], "/", rule_paid["rule_counter_offer", "n"])

print("\n== A. no_labeled_price stopped at a model limit")
kinds = Counter()
by_shop = Counter()
fit = Counter()
surplus = []
agree = Counter()
values_list = []
over_list = 0
for _, rec, _ in rows:
    trail = trail_of(rec)
    if not trail or trail[-1].get("parser_reason") != "no_labeled_price" or not str(trail[-1].get("reason_code")).startswith("model_"):
        continue
    if str(trail[-1].get("reason_code")) not in ("model_quota_hour", "model_quota_day"):
        continue
    text, meta = last_reply(rec)
    kind, price = proposed(text)
    structured = number(meta.get("ask"))
    if structured and price:
        agree["same" if structured == price else "different"] += 1
    chosen = price or structured
    label = kind or ("metadata_ask_only" if structured else ("has_number" if re.search(r"\d{3}", text) else "no_number"))
    kinds[label] += 1
    by_shop[rec.get("shop_name")] += 1
    if not chosen:
        fit["unreadable"] += 1
        continue
    cap, order_budget = number(rec.get("budget")), number(rec.get("order_budget"))
    value = (catalog.get(rec.get("item")) or {}).get("list_price")
    if value and chosen == value:
        fit["equals_list"] += 1
    if value and chosen > value:
        over_list += 1
    if cap and chosen <= cap:
        fit["within_cap"] += 1
        if value:
            surplus.append(value - chosen)
    elif order_budget and chosen <= order_budget:
        fit["over_cap_within_order_budget"] += 1
    else:
        fit["over_order_budget"] += 1
total = sum(kinds.values())
print(f"  stops {total} ({total/hours:.1f}/h); by shape {kinds.most_common()}")
print(f"  top 2 shops {sum(n for _, n in by_shop.most_common(2))}/{total}: {by_shop.most_common(6)}")
print(f"  readable fit {dict(fit)}; price over list {over_list}")
print(f"  surplus if within cap: n {len(surplus)} median {median(surplus) if surplus else None} sum {sum(surplus)}; at rule rate {RATE:.2f}: {len(surplus)*RATE/hours:.1f} deals/h, {sum(surplus)*RATE/hours:,.0f} surplus/h before breakage")
print(f"  new text rule against structured ask, same reply: {dict(agree)}")

print("  false reading test: new text rules on replies the current parser already reads")
checks = Counter()
tested = 0
for _, rec, _ in rows:
    if not rec.get("buyer_log_dir"):
        continue
    text, meta = last_reply(rec)
    if not text:
        continue
    current, code = parse_ask(text)
    kind, price = proposed(text)
    if price is None:
        continue
    tested += 1
    structured = number(meta.get("ask"))
    if current is not None:
        checks[kind, "same_as_current_parser" if current == price else "differs_from_current_parser"] += 1
    if structured:
        checks[kind, "same_as_structured" if structured == price else "differs_from_structured"] += 1
    value = (catalog.get(rec.get("item")) or {}).get("list_price")
    if value:
        checks[kind, "over_list" if price > value else "under_15pct_of_list" if price < value * .15 else "plausible"] += 1
print("  replies read by a new rule", tested)
for key, n in sorted(checks.items()):
    print("   ", key, n)

print("\n== B. asks over our cap, inside the order budget (mandate limit), stopped at a model limit")
settle_grade = {}
counts = defaultdict(Counter)
broken_units = {}
seen = set()
for path in ("logs/star-audit/20261105/rows.jsonl", "logs/star-audit/20261105/rows-tail.jsonl"):
    with open(ROOT / path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("seq") in seen:
                continue
            seen.add(row.get("seq"))
            data = row.get("data") or {}
            sku = (catalog.get(data.get("item")) or {})
            if row.get("kind") == "settle" and sku.get("kind") == "goods":
                top = str(sku.get("category")).split(">")[0]
                settle_grade[data.get("unit")] = (data.get("grade"), top, bool(data.get("warranty_hours")))
                counts[data.get("grade"), bool(data.get("warranty_hours"))]["settled"] += 1
                counts[top, bool(data.get("warranty_hours"))]["settled"] += 1
            elif row.get("kind") == "broken":
                broken_units[data.get("unit")] = data
matched = 0
for unit, data in broken_units.items():
    known = settle_grade.get(unit)
    if known:
        matched += 1
        counts[known[0], known[2]]["broken"] += 1
        counts[known[1], known[2]]["broken"] += 1
print(f"  public sample: goods settles {len(settle_grade)}, broken rows {len(broken_units)}, broken rows whose settle is also in the sample {matched}")
print("  The sample is pages, so a sale and its break are often apart; rates below count only joined pairs and are floors.")
join = matched / max(1, len(broken_units))
RATES = {}
for key, row in sorted(counts.items(), key=str):
    if row["settled"] >= 200:
        rate = row["broken"] / row["settled"]
        RATES[key] = rate
        print(f"   {key}: settled {row['settled']} broken joined {row['broken']} rate {rate:.4f}")
all_goods = sum(1 for _ in settle_grade)
no_warranty_broken = sum(not (d.get("warranty_hours")) for d in broken_units.values())
no_warranty_settled = sum(1 for g in settle_grade.values() if not g[2])
overall = no_warranty_broken / max(1, no_warranty_settled)
print(f"  unjoined upper view, no warranty goods: broken rows {no_warranty_broken} / settled rows {no_warranty_settled} = {overall:.4f}")
print("  ours as a shop on the board: 70 broken / 1063 deals = 0.0659; goods only, mostly no warranty, grade mix A70 B20 C10 by contract")
deals = [d for d in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / ".market/buyer-deals/*.json")))]
grade_deals = Counter(d.get("grade") for d in deals)
broke_lines = len((ROOT / ".market/broken.jsonl").read_text(encoding="utf-8").splitlines())
print(f"  ours as a buyer: {len(deals)} deals by grade {dict(grade_deals)}, broken.jsonl lines {broke_lines}, deals with a matched fault {sum(bool(d.get('fault')) for d in deals)}")
BREAK = {"A": .03, "B": .06, "C": .12}  # Assumption, stated in the report: above the joined floors, and about the board's 6.6% across the contract's grade mix.
print(f"  assumed break rate for the sums below {BREAK}, no warranty: a break turns surplus into minus the price")
records = []
for _, rec, _ in rows:
    trail = trail_of(rec)
    if not trail or trail[-1].get("parser_reason") != "over_budget" or trail[-1].get("reason_code") not in ("model_quota_hour", "model_quota_day"):
        continue
    check = trail[-1].get("counter_check") or {}
    ask, cap, value = number(check.get("ask")), number(check.get("cap")), number(check.get("list_price"))
    order_budget = number(rec.get("order_budget"))
    if not ask or not cap or not value or not order_budget:
        continue
    grade = (trail[-1].get("quote_check") or {}).get("grade")
    kind = (catalog.get(rec.get("item")) or {}).get("kind")
    records.append(dict(ask=ask, cap=cap, value=value, budget=order_budget, grade=grade, kind=kind, ratio=ask / cap, code=check.get("reason_code"), category=str(rec.get("category")).split(">")[-1]))
print(f"  records with ask, cap and list price {len(records)}; over order budget {sum(r['ask'] > r['budget'] for r in records)}; over list {sum(r['ask'] >= r['value'] for r in records)}; grade {Counter(r['grade'] for r in records).most_common()}; kind {Counter(r['kind'] for r in records).most_common()}; second-turn {sum(r['code'] == 'counter_already_used' for r in records)}")
inside = [r for r in records if r["ask"] <= r["budget"] and r["ask"] < r["value"]]
print(f"  inside order budget and under list: {len(inside)} ({len(inside)/hours:.1f}/h)")
print("  ratio | n | /h | surplus before breakage | expected after breakage | at rule paid rate, per hour | median surplus/list")
for limit in (1.1, 1.2, 1.25, 1.3, 1.4):
    hit = [r for r in inside if r["ratio"] <= limit]
    gross = sum(r["value"] - r["ask"] for r in hit)
    net = 0
    for r in hit:
        rate = BREAK.get(r["grade"], BREAK["C"]) if r["kind"] == "goods" else 0
        net += (r["value"] - r["ask"]) - rate * r["value"]
    share = [(r["value"] - r["ask"]) / r["value"] for r in hit]
    print(f"   {limit}: {len(hit)} | {len(hit)/hours:.1f} | {gross:,.0f} | {net:,.0f} | {net*RATE/hours:,.0f} | {median(share) if share else None}")
print("  per deal, negative after breakage:", sum((r["value"] - r["ask"]) - BREAK.get(r["grade"], .12) * r["value"] < 0 for r in inside if r["kind"] == "goods"), "of", sum(r["kind"] == "goods" for r in inside))
print("  by grade, within 1.4:", Counter(r["grade"] for r in inside if r["ratio"] <= 1.4).most_common())

print("\n== C. selection_attempt_cooldown and seller_rejected")
stamps = [datetime.strptime(n[:15], "%Y%m%dT%H%M%S").timestamp() for n, _, _ in rows]
gaps = [b - a for a, b in zip(stamps, stamps[1:])]
print(f"  gap between orders: median {median(gaps)} s, under 20 s {sum(g < 20 for g in gaps)}/{len(gaps)}")
last_attempt = None
cooled = Counter()
for (name, rec, _), at in zip(rows, stamps):
    if rec.get("selection_reason") == "attempt_cooldown":
        cooled["skipped"] += 1
        cooled["paid_later"] += rec.get("order") in paid
print(f"  skipped {cooled['skipped']} ({cooled['skipped']/hours:.1f}/h); same order paid later {cooled['paid_later']}; orders are not retried (orders seen twice above)")
attempted = [r for _, r, _ in rows if r.get("selection_reason") != "attempt_cooldown"]
per_order = sum(r.get("order") in paid for r in attempted) / max(1, len(attempted))
print(f"  paid per order that was not skipped {per_order:.3f}; if skipped orders did as well: {cooled['skipped']*per_order/hours:.1f} deals/h more")
per_deal = []
for _, rec, _ in rows:
    p = paid.get(rec.get("order"))
    value = (catalog.get(rec.get("item")) or {}).get("list_price")
    if p and value and number(p.get("price")):
        per_deal.append(value - p["price"])
print(f"  surplus per paid deal, before breakage: mean {sum(per_deal)/max(1,len(per_deal)):,.0f} median {median(per_deal) if per_deal else None}")
rejected = [r for r in rows if r[1].get("failure_reason") == "seller_rejected"]
print(f"  seller_rejected {len(rejected)} ({len(rejected)/hours:.1f}/h), same order paid later {sum(r[1].get('order') in paid for r in rejected)}")
types = Counter()
bands = Counter()
alts = Counter()
shops = Counter()
for _, rec, evidence in rejected:
    text, _ = last_reply(rec)
    low = text.lower()
    types["no_stock" if re.search(r"none of that|no matching stock|out of stock|not in stock|sold out|no stock|품절|재고", low) else
          "cannot_verify_or_policy" if re.search(r"cannot be verified|unavailable|cannot|can't|unable", low) else
          "empty" if not text else "other"] += 1
    shops[rec.get("shop_name")] += 1
    chosen = next((e for e in evidence if e.get("shop") == rec.get("shop_name") and e.get("sku") == rec.get("item")), None)
    bands[(chosen or {}).get("listed_qty") if ((chosen or {}).get("listed_qty") or 0) < 4 else "4+"] += 1
    others = {e["shop"] for e in evidence if e.get("support_eligible") and e.get("shop") != rec.get("shop_name")}
    alts["another_eligible_shop" if others else "no_other_eligible_shop"] += 1
print(f"  reply type {types.most_common()}; listed qty of the chosen offer {bands.most_common()}; {alts.most_common()}; shops {len(shops)}, top {shops.most_common(5)}")
print(f"  orders get one attempt, so each is lost; with another eligible shop a second attempt might pay at about {per_order/ (1 - len(rejected)/max(1,len(attempted))):.3f}")

print("\n== D. model limit stops by category, against all attempted orders")
attempt_cat = Counter()
stop_cat = Counter()
reason_cat = defaultdict(Counter)
ratio_cat = defaultdict(list)
shop_cat = defaultdict(Counter)
source_cat = defaultdict(Counter)
order_budget_cat = defaultdict(list)
for _, rec, evidence in rows:
    cat = rec.get("category")
    if not cat:
        continue
    leaf = str(cat).split(">")[-1]
    attempt_cat[leaf] += 1
    order_budget_cat[leaf].append(number(rec.get("order_budget")) or 0)
    trail = trail_of(rec)
    if trail and trail[-1].get("reason_code") in ("model_quota_hour", "model_quota_day"):
        stop_cat[leaf] += 1
        reason_cat[leaf][trail[-1].get("parser_reason")] += 1
        shop_cat[leaf][rec.get("shop_name")] += 1
        check = trail[-1].get("counter_check") or {}
        if number(check.get("ask_per_cap")):
            ratio_cat[leaf].append(check["ask_per_cap"])
        chosen = next((e for e in evidence if e.get("shop") == rec.get("shop_name") and e.get("sku") == rec.get("item")), {})
        source_cat[leaf][chosen.get("reference_source")] += 1
for leaf, n in stop_cat.most_common(6):
    print(f"  {leaf}: attempted {attempt_cat[leaf]} stops {n} ({n/attempt_cat[leaf]:.2f}); parser {reason_cat[leaf].most_common(3)}; ask/cap median {median(ratio_cat[leaf]) if ratio_cat[leaf] else None}; cap source {source_cat[leaf].most_common(3)}; median order budget {median(order_budget_cat[leaf])}; shops {shop_cat[leaf].most_common(3)}")
print("  all attempted by category", attempt_cat.most_common(8))
