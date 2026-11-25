"""Default-off exact explicit offers only, inside unchanged current price guards."""
import hashlib
from seller_opening_cue import OpeningCue
from seller_reader_trial import template_offer
from fast_classify import bare_offer

def exact_accept(text,deal,floor):
    if not isinstance(text,str) or deal.get("kind") not in ("food","goods") or not deal.get("unit"):return None
    if any(type(v) is not int or v<=0 for v in (floor,deal.get("ask"),deal.get("reserve"))):return None
    if bare_offer(text) is not None:return None
    value=template_offer(text)
    if value is None or not max(floor,deal["reserve"])<=value<=deal["ask"]:return None
    return dict(buyer_offer=value,action="accept",price=value,message="",why="exact explicit offer within unchanged floor and ask")

class ExactAcceptTrial(OpeningCue):
    def __init__(self,root=None):
        super().__init__(root)
        self.root=self.root.parent/"exact_accept"

    @staticmethod
    def _snapshot(deal):
        # warranty_terms legitimately adds terms during the baseline conversation.
        # This trial never edits terms, so keep current warranty in telemetry instead.
        return dict({k:v for k,v in OpeningCue._snapshot(deal).items() if k!="warranty"},
                    expires_at=deal.get("expires_at"))

    def quote(self,task,deal,now=None):
        if "exact_accept" in deal or deal.get("rounds",0):return
        late=(deal.get("late_concession") or {}).get("id")
        if (late and late!="adopted-v1") or any((deal.get(k) or {}).get("id") for k in ("reader_trial","concession_trial","opening_cue","late_speed","jev_transport","shelf_life")):
            deal["exact_accept"]={"id":None,"arm":"A"};return
        import time
        now=time.time() if now is None else now
        a=self._assign(task,deal,now)
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items()):
            a=dict(a,contaminated=True);self.invalid("exact_snapshot")
        deal["exact_accept"]=a
        self.event(task,deal,"quote",deal["ask"],now)

    def decide(self,text,deal,floor):
        deal["exact_offer_eligible"]=False
        deal["exact_offer_used"]=False
        deal["exact_offer_input_sha256"]=hashlib.sha256(text.encode()).hexdigest() if isinstance(text,str) else None
        deal["exact_offer_value"]=None
        a=deal.get("exact_accept") or {}
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items() if k!="ask"):
            deal["exact_accept"]=dict(a,contaminated=True)
            self.invalid("exact_snapshot")
            return None
        result=exact_accept(text,deal,floor)
        deal["exact_offer_eligible"]=result is not None
        deal["exact_offer_value"]=result["buyer_offer"] if result else None
        if not result or not a.get("id") or self.arm(a)!="B":return None
        deal["exact_offer_used"]=True
        return result

    def event(self,task,deal,event,price=None,now=None,**fields):
        super().event(task,dict(deal,opening_cue=deal.get("exact_accept") or {}),event,price,now,
            exact_offer_eligible=bool(deal.get("exact_offer_eligible")),
            exact_offer_used=bool(deal.get("exact_offer_used")),
            exact_offer_value=deal.get("exact_offer_value"),
            input_sha256=deal.get("exact_offer_input_sha256"),
            current_warranty=deal.get("warranty"),**fields)
