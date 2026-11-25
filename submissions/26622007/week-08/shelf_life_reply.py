"""Exact non-monetary food question, answered only from a known current unit."""
import math
import re
import time

QUESTION=re.compile(r"How many hours of shelf life does this unit have left\? It must last at least ([0-9]+(?:\.[0-9]+)?) hours\.")

def factual_answer(text,deal,now=None):
    if not isinstance(text,str):return None
    m=QUESTION.fullmatch(text.strip())
    if not m or len(m[1])>24 or deal.get("kind")!="food" or not deal.get("unit"):
        return None
    expiry=deal.get("expires_at")
    if type(expiry) not in (int,float) or not math.isfinite(expiry):return None
    if type(deal.get("ask")) is not int or deal["ask"]<=0:return None
    now=time.time() if now is None else now
    if type(now) not in (int,float) or not math.isfinite(now):return None
    remaining=expiry-now
    required=float(m[1])
    if not math.isfinite(remaining) or remaining<60 or not math.isfinite(required):return None
    hours=math.floor(remaining/36)/100
    if hours<required:return None
    return dict(message=f"This unit has {hours:.2f} hours of shelf life remaining at this quote. My price: {deal['ask']}.",hours=hours,required=required,at=now)

import hashlib
from seller_opening_cue import OpeningCue

class ShelfLifeTrial(OpeningCue):
    def __init__(self,root=None):
        super().__init__(root)
        self.root=self.root.parent/"shelf_life"

    @staticmethod
    def _snapshot(deal):
        return dict(OpeningCue._snapshot(deal),expires_at=deal.get("expires_at"))

    def quote(self,task,deal,now=None):
        if "shelf_life" in deal or deal.get("rounds",0):return
        if any((deal.get(k) or {}).get("id") for k in ("reader_trial","concession_trial","opening_cue","late_speed","jev_transport")):
            deal["shelf_life"]={"id":None,"arm":"A"};return
        now=time.time() if now is None else now
        a=self._assign(task,deal,now)
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items()):
            a=dict(a,contaminated=True);self.invalid("shelf_snapshot")
        deal["shelf_life"]=a
        self.event(task,deal,"quote",deal["ask"],now)

    def reply(self,text,deal,now=None):
        deal["shelf_reply_used"]=False
        deal["shelf_reply_sha256"]=None
        deal["shelf_eligible"]=False
        deal["_shelf_answer"]={}
        a=deal.get("shelf_life") or {}
        # Check every enrolled arm and input before eligibility can early-return.
        # Negotiated ask may change; the unit, opening anchor and terms may not.
        snapshot=self._snapshot(deal)
        if a.get("id") and any(a.get(k)!=v for k,v in snapshot.items() if k!="ask"):
            deal["shelf_life"]=dict(a,contaminated=True)
            self.invalid("shelf_snapshot")
            return None
        answer=factual_answer(text,deal,now)
        deal["shelf_eligible"]=answer is not None
        deal["_shelf_answer"]=({k:answer[k] for k in ("hours","required","at")} if answer else {})
        if not answer or not a.get("id") or self.arm(a)!="B":return None
        deal["shelf_reply_used"]=True
        deal["shelf_reply_sha256"]=hashlib.sha256(answer["message"].encode()).hexdigest()
        return answer["message"]

    def event(self,task,deal,event,price=None,now=None,**fields):
        answer=deal.get("_shelf_answer") or {}
        super().event(task,dict(deal,opening_cue=deal.get("shelf_life") or {}),event,price,now,
            shelf_eligible=bool(deal.get("shelf_eligible")),shelf_reply_used=bool(deal.get("shelf_reply_used")),
            remaining_hours=answer.get("hours"),required_hours=answer.get("required"),
            reply_at=answer.get("at"),reply_sha256=deal.get("shelf_reply_sha256"),**fields)
