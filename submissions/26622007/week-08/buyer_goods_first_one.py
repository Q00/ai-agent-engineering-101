"""Bounded first1 goods pilot; never emits a zero-price offer."""
import collections
import fcntl
import hashlib
import json
import os
import time
import ask_read
import oneshot

ENABLED = True  # eligible goods first-code-offer only, after reviewed pilot activation
START = 1794037500  # 11/07 16:45 KST
END = 1794039300    # 11/07 17:15 KST
SHOPS = frozenset(("25620027","mjforge"))
JOURNAL = "goods_first_one_assignments.json"
STUDY = "goods-first-one-20261107-1645"

def first_decision(q,limit,steps):
    if steps != (.9,) or limit<1 or q.get("refused") or ask_read.no_warranty(q.get("text") or ""):
        return None
    if q.get("ask_src") not in ("metadata","text"):return None
    return oneshot.turn(q.get("ask") if q.get("ask_src")=="metadata" else None,
                        q.get("text"),[],limit,steps,q.get("ask"),True)

def turn(decision,history,limit,steps,goods,arm):
    if arm=="B" and goods and steps==(.9,) and decision and decision.get("action")=="offer" and not any(who=="you offered" for who,_ in history):
        if limit<1:return {**decision,"action":"walk_away","price":0}
        return {**decision,"price":1}
    return decision

def reserve(md,order,shop,sku,now=None,env=None):
    now=time.time() if now is None else now
    env=os.environ if env is None else env
    oid=order.get("order");started=order.get("_buyer_started_at",0)
    if not ENABLED or order.get("kind")!="goods" or shop not in SHOPS or not isinstance(oid,str) or not oid or env.get("BUYER_GOODS_FIRST_ONE","1")!="1":return None
    active=START<=now<END and isinstance(started,(int,float)) and START<=started<END
    path=os.path.join(md,JOURNAL)
    if not active and not os.path.exists(path):return None
    try:
        os.makedirs(md,exist_ok=True)
        with open(path+".lock","a") as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:
                with open(path) as f:j=json.load(f)
            except FileNotFoundError:j={"study":STUDY,"orders":{}}
            if not isinstance(j,dict) or j.get("study")!=STUDY or not isinstance(j.get("orders"),dict) or any(not isinstance(r,dict) or r.get("arm") not in ("A","B") for r in j["orders"].values()):return None
            prior=j["orders"]
            if oid in prior:return prior[oid]["arm"]
            if not active:return None
            same=[r for r in prior.values() if r.get("shop")==shop]
            matched=[r for r in same if r.get("sku")==sku]
            counts=collections.Counter(r["arm"] for r in matched)
            if counts["A"]==counts["B"]:counts=collections.Counter(r["arm"] for r in same)
            arm=("A" if counts["A"]<counts["B"] else "B") if counts["A"]!=counts["B"] else ("A" if hashlib.sha256((shop+oid).encode()).digest()[0]%2 else "B")
            prior[oid]={"arm":arm,"at":now,"shop":shop,"sku":sku,"category":order.get("category")}
            tmp=path+f".tmp.{os.getpid()}"
            with open(tmp,"w") as f:json.dump(j,f);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
            return arm
    except (OSError,ValueError,TypeError):return None
