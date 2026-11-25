"""Step 1 of rule collection: the worker model reads each chunk and lists candidate rules.

    py -m redesign extract [--source agent.md] [--limit N]

Each candidate must quote its chunk word for word; a candidate whose quote is not in the chunk is kept
but marked quote_ok=false, so nothing invented passes silently. Results are cached per chunk id in
work/candidates/<id>.json, so a rerun only reads new or changed chunks.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from redesign import guard, llm, rules, sources

AREAS = ["goal", "seller.score", "seller.strategy", "buyer.score", "buyer.orders", "buyer.strategy",
         "market", "market.buy", "market.trust", "food", "goods", "forum", "review", "harness", "ops"]
KINDS = ["definition", "market_rule", "user_decision", "observation", "lesson", "process", "question"]

SYSTEM = """You collect the rules a trading-agent harness must carry. The harness runs one shop (seller) and one buyer
in an open agent market (Round 3, 11/3 - 11/17 23:59). The final score is the Players percentile (shop ALL + buyer ALL).

From the chunk, list every statement that the redesigned harness must know or obey:
- definition: how a score or a term is defined
- market_rule: how the market works (orders, stock, grades, food, goods, warranty, forum, kit checks)
- user_decision: what the user decided, wants or forbade (look for "user", "사용자", dates with a decision)
- observation: what our data showed, with its numbers and time
- lesson: a mistake or failure and what it taught
- process: how the harness or the operator must work (restarts, commits, safety, night mode)
- question: something stated as unknown or to be checked
Skip pure implementation detail (function names, file layout) unless it is a rule for behaviour.

Answer with JSON only: {"candidates": [{"area": one of AREAS, "kind": one of KINDS, "rule": one plain English
sentence or two, self-contained (no "this", give numbers and dates), "quote": the shortest exact span copied
from the chunk that supports it, in the chunk's own language, at most 200 characters, "harness": what the
harness must do about it, or ""}]}. No candidates -> {"candidates": []}."""


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().strip("`*|").lower()


def quote_ok(quote, text):
    q = _norm(quote)
    return bool(q) and q in _norm(text)


def _parse(answer):
    m = re.search(r"\{.*\}", answer, re.S)
    if not m:
        raise ValueError("no JSON object")
    return json.loads(m.group(0))["candidates"]


def extract(chunk, model="worker"):
    path = guard.WORK / "candidates" / f"{chunk['id']}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    prompt = (f"AREAS: {', '.join(AREAS)}\nKINDS: {', '.join(KINDS)}\n\n"
              f"Chunk from {chunk['loc']}:\n<<<\n{chunk['text']}\n>>>")
    err = None
    for _ in range(2):
        try:
            cands = _parse(llm.ask(prompt, system=SYSTEM, model=model, tag=f"extract {chunk['id']}"))
            break
        except (ValueError, KeyError, llm.LLMError) as e:
            err, cands = str(e), None
    if cands is None:
        return {"chunk": chunk["id"], "error": err, "candidates": []}
    for i, c in enumerate(cands):
        c["cid"] = f"{chunk['id']}-{i}"
        c["source"], c["loc"] = chunk["source"], chunk["loc"]
        c["quote_ok"] = quote_ok(c.get("quote", ""), chunk["text"])
    out = {"chunk": chunk["id"], "loc": chunk["loc"], "model": llm.config()["roles"].get(model, model),
           "candidates": cands}
    guard.write(path, json.dumps(out, ensure_ascii=False, indent=1))
    return out


def run(only_source=None, limit=None, workers=6):
    chunks = sources.all_chunks()
    if only_source:
        chunks = [c for c in chunks if c["source"] == only_source]
    chunks = chunks[:limit] if limit else chunks
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(extract, chunks))
    cands = [c for r in results for c in r["candidates"]]
    errors = [r for r in results if r.get("error")]
    guard.write("candidates.jsonl", "\n".join(json.dumps(c, ensure_ascii=False) for c in cands) + "\n")
    return chunks, cands, errors
