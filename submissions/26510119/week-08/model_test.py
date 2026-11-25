import sys, time, json
sys.path.insert(0, sys.argv[1])
from openai import OpenAI
import os
from seller import OPEN_PROMPT_R3, OFFER_PROMPT
from harness import extract_json
c = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENAI_API_KEY"], timeout=60)
ctx = "List price 29900; buyers value it at the list price at most. You have no sales history in this category yet."
cases = [
 ("open A goods (list 29900, floor 15698)", OPEN_PROMPT_R3.format(name="양털토퍼 S", sku="G006cebbb", list_price=29900,
   quality="grade A (new), market certificate included, 6 h warranty", floor=15698,
   history="You have no sales history in this category yet."), lambda o: 15698 <= (o or {}).get("ask", 0) <= 32000),
 ("open C food 49h (list 56900, floor 8535)", OPEN_PROMPT_R3.format(name="고추장 3.5kg", sku="G00200492", list_price=56900,
   quality="grade C, 49 h shelf life left, market certificate included", floor=8535,
   history="You have no sales history in this category yet."), lambda o: 8535 <= (o or {}).get("ask", 0) <= 60000),
 ("offer 24000 > floor, rising (expect accept/counter≤25000)", OFFER_PROMPT.format(item="양털토퍼 S", reserve=15698, offer=24000, ask=25000,
   history="you asked 27000\nbuyer offered 18000\nyou asked 25000\nbuyer offered 24000", sold=ctx),
   lambda o: (o or {}).get("action") == "accept" or ((o or {}).get("action") == "counter" and 24000 < (o or {}).get("price", 0) <= 25000)),
 ("offer 9000 < floor (expect counter ≥15698)", OFFER_PROMPT.format(item="양털토퍼 S", reserve=15698, offer=9000, ask=27000,
   history="you asked 27000\nbuyer offered 9000", sold=ctx),
   lambda o: (o or {}).get("action") == "counter" and (o or {}).get("price", 0) >= 15698),
 ("repeat offer 20000 x3 (expect accept)", OFFER_PROMPT.format(item="양털토퍼 S", reserve=15698, offer=20000, ask=22000,
   history="you asked 27000\nbuyer offered 20000\nyou asked 24000\nbuyer offered 20000\nyou asked 22000\nbuyer offered 20000", sold=ctx),
   lambda o: (o or {}).get("action") == "accept"),
]
models = sys.argv[2:]
REP = 3
for m in models:
    ok = valid = 0; cost = 0.0; lat = []; toks = []
    notes = []
    for name, p, check in cases:
        for _ in range(REP):
            t0 = time.time()
            try:
                r = c.chat.completions.create(model=m, messages=[{"role": "user", "content": p}], temperature=0.7,
                    max_tokens=300, extra_body={"reasoning": {"enabled": False}, "usage": {"include": True}})
                txt = (r.choices[0].message.content or "").strip()
                u = r.usage; cost += float(getattr(u, "cost", 0) or (u.model_extra or {}).get("cost", 0) or 0)
                toks.append((u.prompt_tokens, u.completion_tokens))
            except Exception as e:
                txt = f"ERR {str(e)[:80]}"
            lat.append(time.time() - t0)
            o = extract_json(txt)
            valid += isinstance(o, dict)
            try: good = bool(check(o))
            except Exception: good = False
            ok += good
            if not good: notes.append(f"{name}: {txt[:70]!r}")
    n = len(cases) * REP
    print(f"\n== {m}: json {valid}/{n}, sensible {ok}/{n}, avg {sum(lat)/n:.1f}s, cost ${cost:.5f} "
          f"(${cost/n*1000:.3f} per 1000 calls), tokens in/out avg {sum(t[0] for t in toks)/max(1,len(toks)):.0f}/{sum(t[1] for t in toks)/max(1,len(toks)):.0f}")
    for x in notes[:6]: print("   miss", x)
