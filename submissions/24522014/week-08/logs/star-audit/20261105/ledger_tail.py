"""Read-only: public ledger rows after what the star audit already holds, up to now, at one call a second. Kept rows go to a new file."""
import json, ssl, sys, time, httpx
keep = ("settle", "result", "broken", "refund")
after, out = int(sys.argv[1]), sys.argv[2]
c = httpx.Client(verify=ssl.create_default_context(), timeout=30, trust_env=False)
calls = 0
with open(out, "w", encoding="utf-8") as s:
    while calls < 40:
        rows = c.get("https://market.ourolabs.ai/api/ledger", params={"after": after}).json(); calls += 1
        if not rows: break
        for r in rows:
            if r.get("kind") in keep: s.write(json.dumps(r, ensure_ascii=False) + "\n")
        after = max(r["seq"] for r in rows)
        if len(rows) < 500: break
        time.sleep(1)
print("calls", calls, "last", after)
