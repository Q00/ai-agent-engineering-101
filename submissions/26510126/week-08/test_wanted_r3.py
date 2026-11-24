"""python test_wanted_r3.py: the shop answers a fresh wanted post once, with our A/B stock only, in its thread."""
import json, tempfile, time
from pathlib import Path
import wanted_r3

md = Path(tempfile.mkdtemp())
now = time.time()
(md / "forum.json").write_text(json.dumps([
    {"id": 1, "ts": now - 60, "author": "11im", "thread": None, "text": "[구합니다] 소파/의자/스툴: 최근 6시간 주문 14건 중 7건"},
    {"id": 2, "ts": now - 30, "author": "x", "thread": 1, "text": "[구합니다] 소파/의자/스툴: reply, not a top post"},
    {"id": 3, "ts": now - 5 * 3600, "author": "y", "thread": None, "text": "[구합니다] 소파/의자/스툴: too old"}]), encoding="utf-8")
cat = "가구/침구>가구>소파/의자/스툴"
(md / "inventory.json").write_text(json.dumps({"units": [
    {"sku": "GS", "name": "Sofa", "category": cat, "kind": "goods", "grade": "A", "cert": "x"},
    {"sku": "GS", "name": "Sofa", "category": cat, "kind": "goods", "grade": "C", "cert": "x"}]}), encoding="utf-8")
ok = []
line = wanted_r3.maybe_reply(now, md)
ok.append(("a fresh top-level wanted post gets one reply in its thread, as the shop",
           bool(line) and line["thread"] == 1 and line["as"] == "shop" and "A 1개" in line["post"] and "C" not in line["post"].split("GS")[1][:12]))
ok.append(("no second reply inside the 5-minute shop gap, nor to the same post", wanted_r3.maybe_reply(now + 10, md) is None
           and wanted_r3.maybe_reply(now + 400, md) is None))
for name, good in ok:
    print("PASS" if good else "FAIL", name)
print(f"{sum(g for _, g in ok)}/{len(ok)} passed")
raise SystemExit(0 if all(g for _, g in ok) else 1)
