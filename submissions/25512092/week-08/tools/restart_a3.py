"""Seller restart for the high-price A-grade warranty on low-break SKUs (person 11/13 ~13:10: "적용하고 seller 재시작"):
a3_warranty.json enabled true with applied_ts = now (the 3 h check's before/after point), then the seller restart
(depts/sales.warranty_hours -> depts/a3_warranty.hours is loaded at seller start). One Telegram when the seller is
back (or not back within 3 min); then, within 15 min, the first price line of a listed SKU with 'warranty 48 h'.
    python tools/restart_a3.py        (log in logs/restart_a3.log)
"""
import glob
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
LOG = os.path.join(HERE, "logs", "restart_a3.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def enable(now, path=None):
    from depts import a3_warranty
    path = path or a3_warranty.RULES
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    r.update(enabled=True, applied_ts=round(now))
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    return r


def listed_48(since, skus):
    """A seller '[price] <listed sku> ... warranty 48 h' line after since in the newest kit console log."""
    f = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)[-1]
    with open(f, "rb") as fh:
        fh.seek(max(0, os.path.getsize(f) - 3_000_000))
        tail = fh.read().decode("utf-8", errors="replace").splitlines()
    day0 = time.mktime(time.localtime(since)[:3] + (0, 0, 0, 0, 0, -1))
    for ln in tail:
        if "[price]" in ln and "warranty 48 h" in ln and any(f"[price] {s} " in ln for s in skus):
            try:
                h, m, s = map(int, ln[:8].split(":"))
            except ValueError:
                continue
            if day0 + h * 3600 + m * 60 + s >= since:
                return ln[:200]
    return None


def main():
    import alerts
    import decisions
    from seller_conc_rollout import restart_seller, seller_back
    t = time.time()
    r = enable(t)
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    log(f"enabled ({len(r.get('skus') or {})} SKUs), killed {killed}, back {back}")
    decisions.record("sales", "a3_warranty_on", "a3_warranty.json", {"applied_ts": round(t), "skus": len(r.get("skus") or {})},
                     "person 11/13 ~13:10: high-price A warranty 48 h on the 3% list; seller restarted "
                     + ("ok" if back else "(not back yet)"))
    if not back:
        alerts.send("urgent", "restart_a3_back", "고가 A등급 보증 적용 seller 재시작 후 3분 안에 복귀 줄이 안 보임. 확인 필요", log=log)
        return
    seen = None
    for _ in range(90):
        seen = listed_48(t, set(r.get("skus") or {}))
        if seen:
            break
        time.sleep(10)
    log(f"listed 48 h: {seen}")
    alerts.send("info", "restart_a3", f"{datetime.fromtimestamp(t):%H:%M} seller 재시작 정상, 고가 A등급 보증(3% 목록 "
                f"{len(r.get('skus') or {})}개, 48시간) 적용. " + (f"첫 적용 확인: {seen[9:120]}" if seen
                                                              else "15분 안에 목록 SKU 가격 줄은 아직 없음"), log=log)


if __name__ == "__main__":
    main()
