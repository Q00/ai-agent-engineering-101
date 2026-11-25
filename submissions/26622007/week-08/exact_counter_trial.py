"""Default-off exact mid-bid counter; adopted accept and price floors stay unchanged."""
import hashlib
import time
import negotiate
from fast_classify import bare_offer
from seller_reader_trial import template_offer
from exact_accept_trial import ExactAcceptTrial
from seller_opening_cue import OpeningCue

def counter_offer(text,deal,floor,max_rounds=10):
    if not isinstance(text,str) or deal.get("kind") not in ("food","goods") or not deal.get("unit"):
        return None
    if any(type(v) is not int or v<=0 for v in (floor,deal.get("ask"),deal.get("reserve"),deal.get("rounds"))):
        return None
    if deal["rounds"]>=max_rounds or not negotiate.engine_on() or bare_offer(text) is not None:
        return None
    value=template_offer(text)
    least=max(floor,deal["reserve"])
    if value is None or not value<least<deal["ask"] or value*2<deal["ask"]:
        return None
    return value

def exact_counter(text,deal,floor,max_rounds=10):
    value=counter_offer(text,deal,floor,max_rounds)
    if value is None:return None
    return dict(buyer_offer=value,action="counter",price=negotiate.counter_price(deal,floor,value),
                message=f"Thank you for your offer of {value}.",
                why="exact explicit mid-bid counter using unchanged configured formula")

class ExactCounterTrial(OpeningCue):
    _snapshot=staticmethod(ExactAcceptTrial._snapshot)
    def __init__(self,root=None):
        super().__init__(root)
        self.root=self.root.parent/"exact_counter"

    def quote(self,task,deal,now=None):
        if "exact_counter" in deal or deal.get("rounds",0):return
        allowed={"late_concession","exact_accept"}
        for key in ("reader_trial","concession_trial","opening_cue","late_speed","jev_transport","shelf_life","late_concession","exact_accept"):
            identity=(deal.get(key) or {}).get("id")
            if identity and (key not in allowed or identity!="adopted-v1"):
                deal["exact_counter"]={"id":None,"arm":"A"};return
        now=time.time() if now is None else now
        a=self._assign(task,deal,now)
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items()):
            a=dict(a,contaminated=True);self.invalid("exact_counter_snapshot")
        deal["exact_counter"]=a
        self.event(task,deal,"quote",deal["ask"],now)

    def decide(self,text,deal,floor,max_rounds=10):
        deal["exact_counter_eligible"]=False
        deal["exact_counter_used"]=False
        deal["exact_counter_value"]=None
        deal["exact_counter_input_sha256"]=hashlib.sha256(text.encode()).hexdigest() if isinstance(text,str) else None
        a=deal.get("exact_counter") or {}
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items() if k!="ask"):
            deal["exact_counter"]=dict(a,contaminated=True)
            self.invalid("exact_counter_snapshot")
            return None
        value=counter_offer(text,deal,floor,max_rounds)
        deal["exact_counter_eligible"]=value is not None
        deal["exact_counter_value"]=value
        if value is None or not a.get("id") or self.arm(a)!="B":return None
        result=exact_counter(text,deal,floor,max_rounds)
        deal["exact_counter_used"]=result is not None
        return result

    def event(self,task,deal,event,price=None,now=None,**fields):
        super().event(task,dict(deal,opening_cue=deal.get("exact_counter") or {}),event,price,now,
                      exact_counter_eligible=bool(deal.get("exact_counter_eligible")),
                      exact_counter_used=bool(deal.get("exact_counter_used")),
                      exact_counter_value=deal.get("exact_counter_value"),
                      input_sha256=deal.get("exact_counter_input_sha256"),
                      current_warranty=deal.get("warranty"),**fields)
