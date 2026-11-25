"""Default-off food-only opening price; preserve the baseline first-round floor."""
from contextlib import closing
import json
import sqlite3
import time
import uuid
from seller_opening_cue import OpeningCue

def candidate_price(deal, floor):
    baseline=deal.get("opening_default") or {}
    if deal.get("kind")!="food" or not deal.get("unit") or deal.get("rounds",0):
        return None
    if baseline.get("policy")!="opening-3pct-v1" or baseline.get("price")!=deal.get("ask"):
        return None
    history=deal.get("history")
    if not isinstance(history,list) or not history or not isinstance(history[0],(tuple,list)) or len(history[0])!=2:
        return None
    if history[0][0]!="you asked" or history[0][1]!=deal["ask"]:
        return None
    if any(type(v) is not int or v<=0 for v in
           (floor,deal.get("reserve"),deal.get("open"),deal.get("ask"))):
        return None
    if type(deal.get("cost")) is not int or deal["cost"]<0:
        return None
    if not deal["reserve"]<=floor<=deal["ask"]<=deal["open"]:
        return None
    price=max(floor,deal["reserve"],round(deal["open"]*.96))
    return price if price<deal["ask"] else None

class FoodOpeningTrial(OpeningCue):
    def __init__(self,root=None):
        super().__init__(root)
        self.root=self.root.parent/"food_opening"

    @staticmethod
    def _snapshot(deal):
        return dict(unit=deal.get("unit"),sku=deal.get("id"),kind=deal.get("kind"),
                    cost=deal.get("cost"),reserve=deal["reserve"],open=deal["open"],
                    expires_at=deal.get("expires_at"),policy_sha=deal.get("reader_trial_policy_sha"),
                    baseline_price=(deal.get("opening_default") or {}).get("price"),
                    baseline_floor=deal.get("food_opening_floor"),
                    candidate_price=deal.get("food_opening_candidate"))

    def _existing(self,task):
        path=self.root/"seller_concession_trial.sqlite3"
        if not path.exists():return None
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as db:
            table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assignments'").fetchone()
            if not table:
                trace=self.root/"seller_concession_trial.jsonl"
                if trace.exists() and trace.stat().st_size:
                    raise RuntimeError("assignment schema missing with existing trace")
                return None
            row=db.execute("SELECT data FROM assignments WHERE task=?",(task,)).fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def _other_active(controller,now):
        if (controller.root/"disabled").exists() or (controller.root/"adopted").is_file():
            return False
        cfg=controller.config()
        return bool(cfg and not cfg.get("stop") and cfg["start"]<=now<cfg["end"])

    def quote(self,task,deal,first_floor,now=None,blocked=False,others=()):
        if "food_opening" in deal or deal.get("rounds",0) or deal.get("kind")!="food":
            return
        now=time.time() if now is None else now
        try:
            if not isinstance(task,str) or str(uuid.UUID(task))!=task:return
            old=self._existing(task)
            cfg=self.config()
            adopted=(self.root/"adopted").is_file()
            active=not (self.root/"disabled").exists() and (
                adopted or
                bool(cfg and not cfg.get("stop") and cfg["start"]<=now<cfg["end"]))
            if old is None and not active:return
            if old is None and not adopted and (blocked or any(self._other_active(c,now) for c in others)):
                return
            floor=first_floor(dict(deal,rounds=1))
            price=candidate_price(deal,floor)
            if price is None:return
            deal["food_opening_floor"]=floor
            deal["food_opening_candidate"]=price
            a=self._assign(task,deal,now)
            if a.get("id") and (deal.get("rounds",0) or
                    any(a.get(k)!=v for k,v in self._snapshot(deal).items())):
                a=dict(a,contaminated=True)
                self.invalid("food_opening_snapshot")
            deal["food_opening"]=a
            used=bool(a.get("id")) and self.arm(a)=="B"
            deal["food_opening_used"]=used
            if used:
                deal["ask"]=a["candidate_price"]
                deal["history"][0]=("you asked",deal["ask"])
            self.event(task,deal,"quote",deal["ask"],now)
        except Exception:
            self.invalid("food_opening_quote")

    def event(self,task,deal,event,price=None,now=None,**fields):
        a=deal.get("food_opening") or {}
        from seller_concession_trial import Trial
        Trial.event(self,task,dict(deal,concession_trial=a),event,price,now,
                    kind=deal.get("kind"),expires_at=deal.get("expires_at"),
                    baseline_price=a.get("baseline_price"),
                    baseline_floor=a.get("baseline_floor"),
                    candidate_price=a.get("candidate_price"),
                    food_opening_used=bool(deal.get("food_opening_used")),
                    input_sha256=deal.get("reader_trial_input_sha"),**fields)
