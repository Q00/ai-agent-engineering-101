"""Default-off exact-template at-floor counter using the existing numeric rule."""
from contextlib import closing
import hashlib,json,sqlite3,time,uuid
import fast_classify,negotiate
from seller_reader_trial import template_offer
from seller_opening_cue import OpeningCue
from exact_accept_trial import ExactAcceptTrial

def at_floor_exact(text,deal,floor,max_rounds=10,unit_ok=False):
    if unit_ok is not True or not isinstance(text,str):return None
    if deal.get("kind") not in ("food","goods") or not isinstance(deal.get("unit"),str) or not deal["unit"]:return None
    if any(type(v) is not int or v<=0 for v in (floor,deal.get("ask"),deal.get("reserve"),deal.get("rounds"),max_rounds)):return None
    if deal["rounds"]>=max_rounds or not negotiate.engine_on():return None
    if max(floor,deal["reserve"])!=deal["ask"] or fast_classify.bare_offer(text) is not None:return None
    offer=template_offer(text)
    if offer is None or not 0<offer<deal["ask"]:return None
    result=fast_classify.seller_turn(str(offer),deal,floor,None)
    if not result or result.get("action")!="counter" or result.get("price")!=deal["ask"]:return None
    return result

class AtFloorExactTrial(OpeningCue):
    def __init__(self,root=None):
        super().__init__(root)
        self.root=self.root.parent/"at_floor_exact"

    @staticmethod
    def _snapshot(deal):
        return dict(ExactAcceptTrial._snapshot(deal),kind=deal.get("kind"))

    def _existing(self,task):
        p=self.root/"seller_concession_trial.sqlite3"
        if not p.exists():return None
        with closing(sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,timeout=5)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='assignments'").fetchone():
                trace=self.root/"seller_concession_trial.jsonl"
                if trace.exists() and trace.stat().st_size:raise ValueError("missing assignments")
                return None
            row=db.execute("SELECT data FROM assignments WHERE task=?",(task,)).fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def _other_active(c,now):
        if (c.root/"disabled").exists() or (c.root/"adopted").is_file():return False
        cfg=c.config()
        return bool(cfg and not cfg.get("stop") and cfg["start"]<=now<cfg["end"])

    def quote(self,task,deal,now=None,unit_ok=False,others=()):
        if unit_ok is not True or "at_floor_exact" in deal or deal.get("rounds",0):return
        if deal.get("kind") not in ("food","goods") or not isinstance(deal.get("unit"),str) or not deal["unit"]:return
        now=time.time() if now is None else now
        try:
            if not isinstance(task,str) or str(uuid.UUID(task))!=task:return
            old=self._existing(task);cfg=self.config();adopted=(self.root/"adopted").is_file()
            active=not (self.root/"disabled").exists() and (adopted or bool(cfg and not cfg.get("stop") and cfg["start"]<=now<cfg["end"]))
            if old is None and not active:return
            blocked=any((deal.get(k) or {}).get("id") not in (None,"adopted-v1") for k in ("reader_trial","concession_trial","late_concession","opening_cue","late_speed","jev_transport","shelf_life","exact_accept","exact_counter","grade_preference","food_opening"))
            if old is None and not adopted and (blocked or any(self._other_active(c,now) for c in others)):return
            a=self._assign(task,deal,now)
            if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items()):
                a=dict(a,contaminated=True);self.invalid("at_floor_snapshot")
            deal["at_floor_exact"]=a
            self.event(task,deal,"quote",deal["ask"],now)
        except Exception:self.invalid("at_floor_quote")

    def decide(self,text,deal,floor,max_rounds=10,unit_ok=False):
        deal["at_floor_exact_eligible"]=False;deal["at_floor_exact_used"]=False
        deal["at_floor_exact_input_sha"]=hashlib.sha256(text.encode()).hexdigest() if isinstance(text,str) else None
        a=deal.get("at_floor_exact") or {}
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items() if k!="ask"):
            deal["at_floor_exact"]=dict(a,contaminated=True);self.invalid("at_floor_snapshot");return None
        result=at_floor_exact(text,deal,floor,max_rounds,unit_ok)
        deal["at_floor_exact_eligible"]=result is not None
        if result is None or not a.get("id") or self.arm(a)!="B":return None
        deal["at_floor_exact_used"]=True
        return result

    def event(self,task,deal,event,price=None,now=None,**fields):
        from seller_concession_trial import Trial
        a=deal.get("at_floor_exact") or {}
        Trial.event(self,task,dict(deal,concession_trial=a),event,price,now,
            kind=deal.get("kind"),expires_at=deal.get("expires_at"),
            at_floor_exact_eligible=bool(deal.get("at_floor_exact_eligible")),
            at_floor_exact_used=bool(deal.get("at_floor_exact_used")),
            input_sha256=deal.get("at_floor_exact_input_sha"),**fields)
