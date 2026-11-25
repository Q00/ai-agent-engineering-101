"""Default-off isolated HTTP connection reuse trial. No reader/price policy change."""
import atexit
import math
import re
import time
import httpx
import fast_classify
from jev_connection_pool import JevConnectionPool
from seller_opening_cue import OpeningCue

class JevTransport(OpeningCue):
    def __init__(self,root=None):
        super().__init__(root)
        self.root=self.root.parent/"jev_transport"
        self.pool=JevConnectionPool()
        atexit.register(self.pool.close)

    def quote(self,task,deal,now=None):
        if "jev_transport" in deal or deal.get("rounds",0):return
        if any((deal.get(k) or {}).get("id") for k in ("reader_trial","concession_trial")):
            deal["jev_transport"]={"id":None,"arm":"A"};return
        now=time.time() if now is None else now
        a=self._assign(task,deal,now)
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items()):
            a=dict(a,contaminated=True);self.invalid("jev_transport_snapshot")
        deal["jev_transport"]=a
        self.event(task,deal,"quote",deal["ask"],now)

    @staticmethod
    def trial_config(trial_id, start, end, seed):
        """Build a finite trial config; never write or extend an existing epoch."""
        if not isinstance(trial_id,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",trial_id):
            raise ValueError("invalid trial id")
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)):
            raise ValueError("finite timestamps required")
        if not 0 < end-start <= 1800 or type(seed) is not int:
            raise ValueError("invalid trial window or seed")
        return dict(enabled=True,id=trial_id,start=start,end=end,seed=seed,stop=False)

    def _dispatch_choice(self,deal):
        # ITT enrollment stays immutable; only actual transport exposure expires.
        now=time.time()
        reason="invalid"
        try:
            a=deal.get("jev_transport") or {}
            if not a.get("id"): reason="not-enrolled"
            elif self.arm(a)!="B": reason="control-or-contaminated"
            elif (self.root/"disabled").exists(): reason="disabled"
            else:
                c=self.config()
                if not c: reason="config-unavailable"
                elif c.get("stop"): reason="stop"
                elif c.get("id")!=a["id"]: reason="id-changed"
                elif not math.isfinite(now) or not c["start"] <= now < c["end"]: reason="outside-window"
                else: reason="active"
        except Exception:
            pass
        return dict(arm="B" if reason=="active" else "A",
                    ts=now if math.isfinite(now) else None,reason=reason)

    def reader(self,deal):
        # One fresh state per turn, including lazy/no-Jev turns. Old closures own
        # their old state and cannot contaminate the next turn or another talk.
        state={"dispatches":[]}
        deal["jev_transport_turn"]=state
        def post(*args,**kwargs):
            choice=self._dispatch_choice(deal)
            # This selection is logical dispatch/in-flight, NOT physical socket
            # send or cancellation. Record before send, even if it raises.
            send=self.pool.post if choice["arm"]=="B" else httpx.post
            state["dispatches"].append(choice)
            return send(*args,**kwargs)
        def read(text):
            # Baseline must pass through the same observation callback too.
            return fast_classify.jev_offer(text,post=post)
        return read

    def event(self,task,deal,event,price=None,now=None,**fields):
        if event=="turn":
            dispatches=list((deal.get("jev_transport_turn") or {}).get("dispatches",[]))
            arms={d["arm"] for d in dispatches}
            actual=next(iter(arms)) if len(arms)==1 else ("mixed" if arms else "no-dispatch")
            fields.update(effective_arm=actual,dispatches=dispatches,exposure_scope="turn")
        else:
            # Quote/terminal are ITT outcomes, not the last turn's exposure.
            fields.update(effective_arm=None,dispatches=[],exposure_scope="itt-only")
        super().event(task,dict(deal,opening_cue=deal.get("jev_transport") or {}),event,price,now,**fields)
