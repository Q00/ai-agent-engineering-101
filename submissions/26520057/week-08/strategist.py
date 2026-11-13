"""Strategist: the outer loop. Every two hours it reads what the shop and the buyer did (memory), judges the
running experiments, and proposes the next one as changes to params.json.

  observe  -> board, metrics history, inventory, shop/buyer decision logs, experiments.jsonl, params.json
  think    -> one Claude call (structured output): judgements, next experiment, approval requests
  gate     -> a change inside envelope.json is applied at once; anything else waits in approvals/pending.jsonl
  remember -> experiments.jsonl (what was tried and what came of it), logs/strategist-report.md (for the human)

Hard rules live in code, not in the prompt: the envelope, the API budget, and the rollback when the book falls.
Run:  python strategist.py            (needs ANTHROPIC_API_KEY in runtime/strategist.env)
      python strategist.py --dry      (prints the observation and stops; no API call, no change)
"""
import argparse
import collections
import copy
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MD = ROOT / ".market"
NAME, OWNER = "cheapest-in-town", "danayoou"
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.1, 0.5)}  # $/MTok
BANNED = re.compile(r"ignore (all|any|previous|prior)|disregard|system prompt|you must (buy|accept|pay)|override", re.I)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_jsonl(path, last=None):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in (lines[-last:] if last else lines):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_env():
    for line in (ROOT / "runtime/strategist.env").read_text().splitlines() if (ROOT / "runtime/strategist.env").exists() else []:
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def spent():
    return sum(r.get("usd", 0) for r in read_jsonl(ROOT / "logs/api-cost.jsonl"))


def log_cost(who, model, usage):
    pin, pout = PRICES.get(model, (4.0, 20.0))
    tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0)
    usd = (tokens_in * pin + (usage.output_tokens or 0) * pout) / 1e6
    with open(ROOT / "logs/api-cost.jsonl", "a") as f:
        f.write(json.dumps({"ts": time.time(), "who": who, "model": model, "in": tokens_in, "out": usage.output_tokens, "usd": round(usd, 5)}) + "\n")
    return usd


# ---------------------------------------------------------------- observe
def pct(values, v):
    return round(sum(x < v for x in values) / max(1, len(values)), 3)


def rate(rows, key, hours):
    """Change of a board counter per hour over the last `hours`, from the metrics the agents log every 4 minutes."""
    rows = [r for r in rows if r.get(key) is not None]
    if len(rows) < 2:
        return None
    now = rows[-1]
    past = next((r for r in rows if r["ts"] >= now["ts"] - hours * 3600), rows[0])
    dt = (now["ts"] - past["ts"]) / 3600
    return round((now[key] - past[key]) / dt, 1) if dt > 0.2 else None


def observe():
    obs = {"now": time.strftime("%Y-%m-%d %H:%M KST"), "deadline": "2026-11-17 23:59 KST"}
    board = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board", timeout=20))
    shops = [s for s in board["shops"] if s["deals"] > 0]
    buyers = [b for b in board["buyers"] if b["deals"] > 0]
    me_s = next((s for s in board["shops"] if s["name"] == NAME), {})
    me_b = next((b for b in board["buyers"] if b["name"] == NAME), {})
    players = sorted(board["players"], key=lambda p: -p["total"])
    rank = next((i + 1 for i, p in enumerate(players) if p["player"] == OWNER), None)
    q = lambda rows, k, f: sorted(r[k] for r in rows if r.get(k) is not None)[int(len(rows) * f) - 1]
    obs["rank"] = {"mine": rank, "of": len(players), "my_total": next((p["total"] for p in players if p["player"] == OWNER), None),
                   "total_at_rank_10": players[9]["total"] if len(players) > 9 else None,
                   "total_at_rank_20": players[19]["total"] if len(players) > 19 else None}
    obs["shop"] = {"all": me_s.get("all"), "weights": "book 0.5, revenue 0.3, stars 0.2 (each a percentile)",
                   **{k: {"mine": me_s.get(k), "percentile": pct([s[k] for s in shops], me_s.get(k, 0)),
                          "p50": q(shops, k, .5), "p75": q(shops, k, .75), "p90": q(shops, k, .9)} for k in ("book", "revenue", "stars_rank")},
                   "deals": me_s.get("deals"), "broken": me_s.get("broken"), "refunds": me_s.get("refunds")}
    obs["buyer"] = {"all": me_b.get("all"), "weights": "saved 0.5 (percentile), forum 0.3 (as itself), deals 0.1, posts 0.1 (percentiles)",
                    **{k: {"mine": me_b.get(k), "percentile": pct([b[k] for b in buyers], me_b.get(k, 0)),
                           "p50": q(buyers, k, .5), "p75": q(buyers, k, .75), "p90": q(buyers, k, .9)} for k in ("saved", "deals", "posts")},
                    "forum": me_b.get("forum"), "paid_rate": me_b.get("paid")}
    sm, bm = read_jsonl(ROOT / "logs/shop-metrics.jsonl", 400), read_jsonl(ROOT / "logs/buyer-metrics.jsonl", 400)
    obs["shop"]["per_hour"] = {f"{k}_last_{h}h": rate(sm, k, h) for k in ("revenue", "book", "deals") for h in (2, 6)}
    obs["buyer"]["per_hour"] = {f"deals_last_{h}h": rate(bm, "deals", h) for h in (2, 6)}
    for h in (2, 6):        # Saved on the deals made in the window: delta(saved*(deals+100)) / delta(deals)
        rows = [r for r in bm if r.get("saved") is not None and r.get("deals")]
        if len(rows) > 1:
            now, past = rows[-1], next((r for r in rows if r["ts"] >= rows[-1]["ts"] - h * 3600), rows[0])
            if now["deals"] > past["deals"]:
                obs["buyer"][f"saved_on_new_deals_last_{h}h"] = round(
                    (now["saved"] * (now["deals"] + 100) - past["saved"] * (past["deals"] + 100)) / (now["deals"] - past["deals"]), 3)
    inv = read_json(MD / "inventory.json", {})
    units, now = inv.get("units", []), time.time()
    age = lambda u: (now - u.get("supplied_at", now)) / 60
    obs["inventory"] = {"balance": inv.get("balance"), "units": len(units), "stock_cost": round(sum(u["cost"] for u in units)),
                        "by_age_min": {b: {"units": len(x), "cost": round(sum(u["cost"] for u in x))} for b, x in (
                            ("under_60", [u for u in units if age(u) < 60]), ("60_to_180", [u for u in units if 60 <= age(u) < 180]),
                            ("over_180", [u for u in units if age(u) >= 180]))},
                        "most_expensive": [{"name": u["name"][:30], "grade": u["grade"], "cost": u["cost"], "list": u["list_price"],
                                            "age_min": round(age(u))} for u in sorted(units, key=lambda u: -u["cost"])[:8]]}
    catalog = read_json(MD / "catalog.json", {})
    sig = read_json(MD / "shop-signals.json", {})
    top = sorted(sig.get("sku_sales", {}).items(), key=lambda kv: -kv[1] * catalog.get(kv[0], {}).get("list_price", 0))[:15]
    obs["market_demand_memory"] = {"note": "sales counts decay 5% per 4 min; revenue-weighted top SKUs seen on the public ledger",
        "top_skus": [{"name": catalog.get(k, {}).get("name", k)[:30], "category": catalog.get(k, {}).get("category", "").split(">")[-1],
                      "list": catalog.get(k, {}).get("list_price"), "sales": round(v, 1)} for k, v in top],
        "category_price_over_list": {k.split(">")[-1]: round(v, 3) for k, v in sorted(sig.get("category_ratio", {}).items(), key=lambda kv: -kv[1])[:12]}}
    sd = [r for r in read_jsonl(ROOT / "logs/shop-decisions.jsonl", 6000) if r["ts"] > now - 7200]
    agreed = [r for r in sd if r["event"] == "agreed"]
    obs["shop_last_2h"] = {"events": dict(collections.Counter(r["event"] for r in sd)), "sold": len(agreed),
                           "revenue": sum(r["price"] for r in agreed), "gross": sum(r.get("expected_gross", 0) for r in agreed),
                           "sold_over_100k": sum(r["price"] >= 100000 for r in agreed)}
    bd = [r for r in read_jsonl(ROOT / "logs/buyer-decisions.jsonl", 8000) if r["ts"] > now - 7200]
    paid = [r for r in bd if r["event"] == "ready_for_payment" and r.get("list_price")]
    obs["buyer_last_2h"] = {"events": dict(collections.Counter(r["event"] for r in bd)),
                            "mean_price_over_list": round(sum(r["price"] / r["list_price"] for r in paid) / len(paid), 3) if paid else None,
                            "why_no_deal": collections.Counter((r.get("reason") or "")[:50] for r in bd if r["event"] in ("candidate_rejected", "probe_failed")).most_common(6),
                            "shops_remembered": len(read_json(MD / "buyer-shop-memory.json", {}))}
    obs["services"] = {s: subprocess.run(["systemctl", "--user", "is-active", s], capture_output=True, text=True).stdout.strip()
                       for s in ("ax-round3-shop", "ax-round3-buyer", "ax-round3-ollama")}
    sent = read_jsonl(MD / "buyer-box/outbox.sent.jsonl")
    started = time.mktime(time.strptime("2026-11-13 14:20", "%Y-%m-%d %H:%M"))
    minutes = (time.time() - started) / 60
    obs["forum_posting"] = {"started": "2026-11-13 14:20 KST (review.py)", "posts_accepted": sum(r.get("status") == 200 for r in sent),
                            "posts_possible_at_cap": int(minutes // 5), "note": "the market allows one buyer post per 5 minutes"}
    obs["api_budget"] = {"spent_usd": round(spent(), 3), "limit_usd": read_json(ROOT / "envelope.json", {}).get("_api_budget_usd", 40)}
    return obs


# ---------------------------------------------------------------- think
SYSTEM = """You are the strategist of one student's shop and buyer agents in a two-week agent market (Round 3). \
You run every two hours. Your job is a research loop: judge the running experiments from the numbers, then choose the \
single most valuable next experiment and express it as changes to params.json.

How the score works (the student's rank is shop ALL + buyer ALL):
- Shop ALL = 0.5 * percentile(book) + 0.3 * percentile(revenue) + 0.2 * percentile(stars). Book = cash + cost of unsold stock.
- Buyer ALL = 0.5 * percentile(saved) + 0.3 * forum rating (0 to 1, as itself) + 0.1 * percentile(deals) + 0.1 * percentile(posts).
- Saved per deal = (list price - price paid) / (list price - the shop's unit cost), capped at 1; a deal that served nothing is -1.
- Unit cost as a share of list price: grade A 55-75%, B 40-60%, C 25-45%. Goods break up to 6 h after a sale; a warranty refunds the full price.
- Supply opens for 3 minutes every 20 minutes. Goods in stock cost 1% of their cost per day. Food past its date is lost at cost.
Counters on the board are cumulative since the round began, so percentiles move slowly: judge an experiment by the rate \
(per hour) inside its window, not by the cumulative number.

Rules you must follow:
- One experiment at a time per agent (shop, buyer) unless two changes cannot confound each other's metric. State a falsifiable hypothesis, the metric, the window in hours, and what result rejects it.
- Do not judge an experiment before its window has passed unless its reject_if condition is already met; say "running" and give the interim number.
- Prefer reverting a rejected change to stacking a new one on top of it.
- Every change is {key, value_json}: key is a dotted path in params.json such as "shop.max_list_price" or "buyer.anchor.A"; value_json is the JSON-encoded new value.
- Changes inside the envelope are applied automatically. Anything outside it, anything the student could see as against market ethics (paid dislikes, lowering others' stars, untruthful posts or claims, text meant to manipulate other agents' instructions), bankruptcy, spending on the API beyond the budget, or selling below cost must go in approval_requests instead and will wait for the student. approval_requests is only for those decisions.
- A suspected bug, a broken feature, or anything that needs a code change or an investigation goes in issues_for_claude, never in approval_requests. A code agent (code_agent.py) picks these up within 20 minutes, fixes them in a copy of the code, and ships the fix only if the tests pass; it rolls the fix back if the agents stop working. So write each issue as a precise bug report: the file or behaviour, the evidence (log lines, counts, times) and what should happen instead. The student is not asked to debug. Before filing one, check the observation: forum_posting shows how many posts the cap allowed since posting began.
- model.* chooses which model reads ambiguous messages for the shop and buyer ("local" is a free 4B model; "anthropic" costs money from the same budget). prompts.* are the instructions those models get, and shop.reply_note is one truthful sentence added to every quote. Treat these like any other experiment: change them only with a hypothesis about a measured failure (misread offers, lost haggles), and keep every statement true.
- Shop pricing changes reach the running shop on its own; no restart is needed for any params.json change.
- You run every hour and four days remain, so slow experiments are expensive. Give each experiment the shortest window that still yields a readable signal: about 30 new deals for the buyer, or three supply windows (one hour) plus selling time for the shop. Two hours is typical; six is the maximum. A big, clearly directional effect may be judged early.
- Facts about what already exists, so you do not ask for it again: the buyer already posts a factual purchase record on the forum after settled deals, at the market's own cap of one post per 5 minutes (review.py), and reviews every settled deal with stars. Post count cannot go faster than that cap. The shop does not post.
- Write every human-facing string (result, decision, hypothesis, metric, reject_if, approval what/why, issues, summary_ko) in Korean. The student is called 다나.
- How to write Korean for 다나: talk to her the way a helpful labmate texts, ending sentences in -요 (했어요, 늘었어요, 볼게요), never -습니다. One fact per sentence; sentences under about 40 characters. Use her everyday words: 가게, 바이어, 재고, 매출, Book, 깎은 비율(Saved), 실험, 판정. Give numbers with units and a before→after (매출 시간당 65만 → 1,000만 원). No translated phrasing, no hedging stacks, no English words where a Korean one exists.
- summary_ko is at most 4 lines: line 1 is the one-sentence conclusion; then what changed or what you changed; then when the next judgement comes. Do not mention notes when there are none.
- When 다나 left a note, answer it in summary_ko: say which experiment tests it, or why it has to wait.
- An approval request's what is one sentence saying what changes if 다나 says yes; why is one sentence."""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["judgements", "next_experiments", "approval_requests", "issues_for_claude", "summary_ko"],
    "properties": {
        "judgements": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["id", "status", "result", "decision"],
            "properties": {"id": {"type": "string"}, "status": {"type": "string", "enum": ["running", "supported", "rejected", "inconclusive"]},
                           "result": {"type": "string"}, "decision": {"type": "string"}}}},
        "next_experiments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["hypothesis", "changes", "metric", "window_h", "reject_if"],
            "properties": {"hypothesis": {"type": "string"},
                           "changes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "value_json"],
                                       "properties": {"key": {"type": "string"}, "value_json": {"type": "string"}}}},
                           "metric": {"type": "string"}, "window_h": {"type": "number"}, "reject_if": {"type": "string"}}}},
        "approval_requests": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["what", "why"],
                              "properties": {"what": {"type": "string"}, "why": {"type": "string"}}}},
        "issues_for_claude": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["what", "evidence"],
                              "properties": {"what": {"type": "string"}, "evidence": {"type": "string"}}}},
        "summary_ko": {"type": "string"},
    },
}


def think(obs, experiments, params, envelope, model, effort):
    import anthropic
    client = anthropic.Anthropic()
    user = ("Observation (JSON):\n" + json.dumps(obs, ensure_ascii=False, indent=1)
            + "\n\nExperiment log, oldest first (JSON lines):\n" + "\n".join(json.dumps(e, ensure_ascii=False) for e in experiments[-14:])
            + "\n\nNotes from the student (hypotheses to test and corrections of your earlier judgement; these outrank your own plan):\n"
            + ("\n".join(json.dumps(n, ensure_ascii=False) for n in read_jsonl(ROOT / "human-notes.jsonl", 12)) or "(none)")
            + "\n\nCurrent params.json:\n" + json.dumps(params, ensure_ascii=False, indent=1)
            + "\n\nEnvelope (what you may change without approval; [min, max] or a list of allowed values):\n"
            + json.dumps(envelope, ensure_ascii=False, indent=1))
    with client.beta.messages.stream(
        model=model, max_tokens=32000, system=SYSTEM,
        thinking={"type": "adaptive"}, output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        messages=[{"role": "user", "content": user}],
    ) as stream:
        response = stream.get_final_message()
    usd = log_cost("strategist", model, response.usage)
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"strategist stopped with {response.stop_reason}")
    return json.loads(next(b.text for b in response.content if b.type == "text")), usd


# ---------------------------------------------------------------- gate
def allowed(key, value, envelope):
    rule = envelope.get(key)
    if rule is None:
        return False, "not in the envelope"
    if isinstance(rule, dict) and "max_chars" in rule:
        if not isinstance(value, str) or len(value) > rule["max_chars"]:
            return False, f"text longer than {rule['max_chars']} characters"
        if BANNED.search(value):
            return False, "text looks like an instruction aimed at another agent"
        return True, ""
    if isinstance(rule, list) and len(rule) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in rule):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and rule[0] <= value <= rule[1]
        return ok, "" if ok else f"outside [{rule[0]}, {rule[1]}]"
    ok = value in rule
    return ok, "" if ok else f"not one of {rule}"


def set_path(obj, key, value):
    parts = key.split(".")
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    old = obj.get(parts[-1])
    obj[parts[-1]] = value
    return old


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    load_env()
    sys.path.insert(0, str(ROOT))
    import shop_strategy
    conf = shop_strategy.params("strategist")
    envelope = read_json(ROOT / "envelope.json", {})
    params = read_json(ROOT / "params.json", {})
    experiments = read_jsonl(ROOT / "experiments.jsonl")
    state = read_json(ROOT / "logs/strategist-state.json", {})
    obs = observe()
    if args.dry:
        print(json.dumps(obs, ensure_ascii=False, indent=1))
        return
    report = [f"# 전략가 보고 {obs['now']}", ""]
    pending = []
    plan_summary, rolled_back, applied_lines, issues = "", "", [], []
    # Rollback is code, not judgement: a dead service or a falling book restores the last good parameters first.
    book, last_book = obs["shop"]["book"]["mine"], state.get("book")
    dead = [k for k, v in obs["services"].items() if v != "active"]
    if dead or (last_book and book is not None and book < last_book - 2_000_000):
        good = read_json(ROOT / "params.last-good.json", None)
        if good is not None:
            write_json(ROOT / "params.json", good)
            params = good
        why = f"서비스 멈춤: {dead}" if dead else f"Book이 {last_book:,} → {book:,}로 200만 넘게 줄었음"
        report += [f"**되돌림**: {why}. params.json을 마지막 정상 값으로 되돌렸어요.", ""]
        rolled_back = why
        pending.append({"what": "rollback happened; look before continuing", "why": why})
    elif not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        report += ["API 키가 없어서 모델을 부르지 않았어요 (runtime/strategist.env). 값은 그대로예요.", ""]
    elif spent() >= envelope.get("_api_budget_usd", 40):
        report += ["API 예산을 다 써서 이번에는 모델을 부르지 않았어요. 값은 그대로예요.", ""]
        plan_summary = "API 예산($40)을 다 써서 모델을 부르지 않았어요. 값은 그대로예요."
    else:
        write_json(ROOT / "params.last-good.json", params)
        plan, usd = think(obs, experiments, params, envelope, conf["model"], conf["effort"])
        by_id = {e["id"]: e for e in experiments if "id" in e}
        for j in plan["judgements"]:
            if j["id"] in by_id:
                by_id[j["id"]].update(status="running" if j["status"] == "running" else "done", result=j["result"],
                                      decision=f"{j['status']}: {j['decision']}", judged=obs["now"])
        applied = []
        new_params = copy.deepcopy(params)
        number = max([int(e["id"][1:]) for e in experiments if re.fullmatch(r"E\d+", e.get("id", ""))] + [0])
        for exp in plan["next_experiments"]:
            changes = {}
            for c in exp["changes"]:
                try:
                    value = json.loads(c["value_json"])
                except ValueError:
                    pending.append({"what": f"{c['key']} = {c['value_json']}", "why": "value is not JSON"})
                    continue
                ok, why = allowed(c["key"], value, envelope)
                if ok:
                    changes[c["key"]] = [set_path(new_params, c["key"], value), value]
                else:
                    pending.append({"what": f"{c['key']} = {c['value_json']}", "why": f"{why}. {exp['hypothesis']}"})
            if changes:
                number += 1
                experiments.append({"id": f"E{number}", "started": obs["now"], "type": "strategist", "hypothesis": exp["hypothesis"],
                                    "change": changes, "metric": exp["metric"], "window_h": exp["window_h"], "reject_if": exp["reject_if"],
                                    "baseline": {"shop": obs["shop"]["per_hour"], "buyer": obs["buyer"]["per_hour"],
                                                 "saved_new_2h": obs["buyer"].get("saved_on_new_deals_last_2h")}, "status": "running"})
                applied.append((f"E{number}", exp["hypothesis"], changes))
        pending += plan["approval_requests"]
        issues = plan.get("issues_for_claude", [])
        for issue in issues:
            (ROOT / "approvals").mkdir(exist_ok=True)
            with open(ROOT / "approvals/issues.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": obs["now"], **issue}, ensure_ascii=False) + "\n")
        write_json(ROOT / "params.json", new_params)
        with open(ROOT / "experiments.jsonl.tmp", "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(e, ensure_ascii=False) for e in experiments) + "\n")
        (ROOT / "experiments.jsonl.tmp").replace(ROOT / "experiments.jsonl")
        plan_summary = plan["summary_ko"]
        applied_lines = [f"{i} " + ", ".join(f"{k} {a}→{b}" for k, (a, b) in ch.items()) for i, _, ch in applied]
        report += [plan["summary_ko"], "", "## 판정"] + [f"- {j['id']}: {j['status']}. {j['result']}" for j in plan["judgements"]]
        report += ["", "## 새 실험"] + ([f"- {i}: {h} / 바꾼 값: " + ", ".join(f"{k} {a}→{b}" for k, (a, b) in ch.items()) for i, h, ch in applied] or ["- 없음"])
        report += ["", f"이번 호출 비용 ${usd:.3f}, 누적 ${spent():.2f}"]
    if pending:
        (ROOT / "approvals").mkdir(exist_ok=True)
        with open(ROOT / "approvals/pending.jsonl", "a", encoding="utf-8") as f:
            for p in pending:
                f.write(json.dumps({"ts": obs["now"], **p}, ensure_ascii=False) + "\n")
        report += ["", "## 승인 필요 (적용 안 됨)"] + [f"- {p['what']} — {p['why']}" for p in pending]
    r, s, b = obs["rank"], obs["shop"], obs["buyer"]
    report += ["", f"순위 {r['mine']}/{r['of']} · 합계 {r['my_total']} (10위 {r['total_at_rank_10']}) · 가게 {s['all']} · 바이어 {b['all']}"]
    text = "\n".join(report) + "\n"
    (ROOT / "logs/strategist-report.md").write_text(text, encoding="utf-8")
    with open(ROOT / "logs/strategist-reports.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "report": text, "rank": r, "shop_all": s["all"], "buyer_all": b["all"]}, ensure_ascii=False) + "\n")
    write_json(ROOT / "logs/strategist-state.json", {"ts": time.time(), "book": book})
    print(text)
    slack(obs, plan_summary, pending, rolled_back, applied_lines, issues)


def clip(text, limit):
    """Cut at the last sentence end before the limit, so a phone never shows half a sentence."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = max(text.rfind(m, 0, limit) for m in (". ", "요.", "다.", "\n"))
    return text[:cut + 2].strip() if cut > limit // 3 else text[:limit].rstrip() + "…"


def slack(obs, summary, pending, rolled_back, applied, issues=()):
    """One short message per run; approvals and rollbacks are marked so they stand out on a phone."""
    import notify
    r, s, b = obs["rank"], obs["shop"], obs["buyer"]
    lines = []
    if rolled_back:
        lines.append(":rotating_light: *되돌림* " + rolled_back)
    open_items = read_jsonl(ROOT / "approvals/pending.jsonl")
    if pending and open_items:
        lines.append(f":raising_hand: *승인할 일 {len(open_items)}건* · 앱을 열어 Claude에게 번호로 답해 주세요")
        lines += [f"{i}. {clip(p['what'], 200)}" for i, p in enumerate(open_items, 1)][-5:]
    lines.append(f"*{obs['now'][11:16]}* 순위 {r['mine']}/{r['of']} · 합계 {r['my_total']} (가게 {s['all']} + 바이어 {b['all']})")
    if summary:
        lines.append(clip(summary, 700))
    if applied:
        lines.append("바꾼 값: " + clip(" / ".join(applied), 400))
    if issues:
        lines.append(f":wrench: 코드 에이전트에게 맡긴 일 {len(issues)}건 (다나가 할 일은 없어요): " + clip(issues[0]["what"], 150))
    notify.send("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            import notify
            notify.send(f":warning: 전략가가 오류로 멈췄어요: {type(exc).__name__}: {str(exc)[:300]}. 값은 그대로예요.")
        finally:
            raise
