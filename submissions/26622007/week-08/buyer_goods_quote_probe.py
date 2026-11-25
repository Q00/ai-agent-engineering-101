"""Bounded goods-only extra-search experiment; no pricing/payment changes."""
import collections
import fcntl
import hashlib
import json
import os
import time

ENABLED = False  # no recovered paid orders in the completed pilot
START = 1794034200  # 11/07 15:50 KST
END = 1794036000    # 11/07 16:20 KST, 30 minutes
EARLY_SECONDS = 8
EXTRA_WAIT = 3
JOURNAL = "goods_extra_quote_assignments.json"
STUDY = "goods-extra-20261107-1550"

def reserve(md, order, now=None, env=None):
    now = time.time() if now is None else now
    env = os.environ if env is None else env
    oid=order.get("order")
    if not ENABLED or order.get("kind")!="goods" or not isinstance(oid,str) or not oid or env.get("BUYER_GOODS_EXTRA_QUOTE","1")!="1":
        return None
    path=os.path.join(md,JOURNAL)
    active=START<=now<END
    if not active and not os.path.exists(path):return None
    try:
        os.makedirs(md,exist_ok=True)
        with open(path+".lock","a") as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:
                with open(path) as f:j=json.load(f)
            except FileNotFoundError:j={"study":STUDY,"orders":{}}
            if not isinstance(j,dict) or j.get("study")!=STUDY or not isinstance(j.get("orders"),dict) or any(not isinstance(r,dict) or r.get("arm") not in ("A","B") for r in j["orders"].values()):
                return None
            prior=j["orders"]
            if oid in prior:return prior[oid]["arm"]
            if not active:return None
            category=order.get("category")
            matched=[r for r in prior.values() if r.get("category")==category]
            counts=collections.Counter(r["arm"] for r in matched)
            if counts["A"]==counts["B"]:counts=collections.Counter(r["arm"] for r in prior.values())
            arm=("A" if counts["A"]<counts["B"] else "B") if counts["A"]!=counts["B"] else ("A" if hashlib.sha256(oid.encode()).digest()[0]%2 else "B")
            prior[oid]={"arm":arm,"at":now,"category":category,"budget":order.get("budget")}
            tmp=path+f".tmp.{os.getpid()}"
            with open(tmp,"w") as f:json.dump(j,f);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
            return arm
    except (OSError,ValueError,TypeError):
        return None
