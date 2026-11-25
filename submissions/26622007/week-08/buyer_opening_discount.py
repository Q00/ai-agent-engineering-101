"""Bounded food opener A/B; first price only, existing cap/counter path unchanged."""
import collections
import fcntl
import hashlib
import json
import os
import time
import oneshot

START = 1794028200  # 11/07 14:10 KST, actual new window after review fix
END = 1794030000    # 11/07 14:40 KST, 30 minute bounded pilot
JOURNAL = "opening_discount_assignments.json"
STUDY = "food-opening-085-20261107-1410"
EXCLUDED = frozenset(("11im", "choo-chu"))


def reserve(md, shop, order_id, sku, use_within, now=None, env=None):
    now = time.time() if now is None else now
    env = os.environ if env is None else env
    if env.get("BUYER_OPENING_DISCOUNT", "1") != "1" or shop in EXCLUDED or not shop or not isinstance(order_id,str) or not order_id:
        return None
    path = os.path.join(md,JOURNAL)
    active = START <= now < END
    if not active and not os.path.exists(path):
        return None
    try:
        os.makedirs(md,exist_ok=True)
        with open(path+".lock","a") as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:
                with open(path) as f:j=json.load(f)
            except FileNotFoundError:
                j={"study":STUDY,"orders":{}}
            if not isinstance(j,dict) or j.get("study")!=STUDY or not isinstance(j.get("orders"),dict) or any(not isinstance(r,dict) or r.get("arm") not in ("A","B") for r in j["orders"].values()):
                return None
            prior=j["orders"]
            if order_id in prior:return prior[order_id]["arm"]
            if not active:return None
            same=[r for r in prior.values() if r.get("shop")==shop]
            matched=[r for r in same if r.get("sku")==sku and r.get("use_within_hours")==use_within]
            counts=collections.Counter(r["arm"] for r in matched)
            if counts["A"]==counts["B"]:counts=collections.Counter(r["arm"] for r in same)
            arm=("A" if counts["A"]<counts["B"] else "B") if counts["A"]!=counts["B"] else ("A" if hashlib.sha256((shop+order_id).encode()).digest()[0]%2 else "B")
            prior[order_id]={"arm":arm,"shop":shop,"sku":sku,"use_within_hours":use_within,"at":now}
            tmp=path+f".tmp.{os.getpid()}"
            with open(tmp,"w") as f:
                json.dump(j,f);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
            return arm
    except (OSError,ValueError,TypeError):
        return None


def turn(meta,text,history,limit,steps,quoted,goods,arm):
    d=oneshot.turn(meta,text,history,limit,steps,quoted,goods)
    if arm=="B" and not goods and tuple(steps)==(.9,) and not any(who=="you offered" for who,_ in history) and d and d["action"]=="offer":
        if limit < 1:
            return {**d,"action":"walk_away","price":0}
        return {**d,"price":max(1,min(d["price"],int(.85*d["shop_price"]),limit))}
    return d
