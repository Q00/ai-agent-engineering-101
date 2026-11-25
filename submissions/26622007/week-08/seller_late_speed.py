"""Isolated new-task pilot: adopted late 10% versus 20% residual-gap concession."""
from pathlib import Path
import time
from seller_opening_cue import OpeningCue

class LateSpeed(OpeningCue):
    def __init__(self, root=None):
        super().__init__(root)
        self.root=self.root.parent/"late_speed"

    def quote(self, task, deal, now=None):
        if "late_speed" in deal or deal.get("rounds",0):
            return
        old=deal.get("late_concession") or {}
        if old.get("id")!="adopted-v1" or old.get("arm")!="B" or old.get("contaminated"):
            deal["late_speed"]={"id":None,"arm":"A"}
            return
        now=time.time() if now is None else now
        a=self._assign(task,deal,now)
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items()):
            a=dict(a,contaminated=True)
            self.invalid("late_speed_snapshot")
        deal["late_speed"]=a
        self.event(task,deal,"quote",deal["ask"],now)

    def floor(self, deal, current, original):
        deal["speed_current_floor"]=current
        deal["speed_original_floor"]=original
        a=deal.get("late_speed") or {}
        if a.get("id") and self.arm(a)=="B" and deal.get("rounds",0)>=3:
            return max(deal["reserve"],min(current,deal["ask"],round(deal["reserve"]+(original-deal["reserve"])*.8)))
        return current

    def event(self, task, deal, event, price=None, now=None, **fields):
        clone=dict(deal,opening_cue=deal.get("late_speed") or {})
        super().event(task,clone,event,price,now,current_policy_floor=deal.get("speed_current_floor"),
                      original_schedule_floor=deal.get("speed_original_floor"),**fields)
