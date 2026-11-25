"""Default-off fallback for three complete optional-grade preference sentences."""
import re
import shop_stock

PREFERENCES=(
 "Grade B or C is fine if it is cheaper; please quote your lowest-priced unit.",
 "Prefer discounted grade C/refurbished stock when available.",
 "I prefer grade C (refurbished) or B (damaged box) with the warranty, priced for its grade; grade A only if you have nothing else.",
)

def preference_needs(text):
    if not isinstance(text,str):return None
    # Only plain natural-language requests: never interpret structured/quoted data.
    if any(ch in text for ch in ('{','}','[',']','"')):return None
    found=[p for p in PREFERENCES if p in text]
    if len(found)!=1 or text.count(found[0])!=1:return None
    phrase=found[0];start=text.index(phrase);end=start+len(phrase)
    if any(m.start()<end and start<m.end() for m in shop_stock.POLICY.finditer(text)):
        return None
    if shop_stock.policy_needs(text)["grades"]:return None
    residual=text[:start]+text[end:]
    # Unknown grade qualifiers outside the complete optional sentence stay strict.
    if re.search(r"\bgrades?\b",residual,re.I) or re.search(r"\b[ABC]\b",residual):
        return None
    if re.search(r"\b(?:only|not|no|except|avoid)\s+(?:grades?\s*)?[ABC]\b",residual,re.I):
        return None
    original=shop_stock.needs(text);remaining=shop_stock.needs(residual)
    if remaining["grades"] or any(original[k]!=remaining[k] for k in ("skus","hours","cert")):
        return None
    return dict(original,grades={"A","B","C"})


from contextlib import closing
import hashlib,json,os,sqlite3,time,uuid
from pathlib import Path
from seller_concession_trial import Trial

class GradePreferenceTrial(Trial):
    """Pre-selection ITT: every qualifying first request retains a denominator."""
    def __init__(self,root=None):
        base=Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent/".market")
        super().__init__(base/"grade_preference")

    def enroll(self,task,text,now=None):
        now=time.time() if now is None else now
        sha=hashlib.sha256(text.encode()).hexdigest() if isinstance(text,str) else None
        control=dict(id=None,arm="A",stratum=None,enrolled_at=now,input_sha256=sha)
        try:
            if not isinstance(task,str) or str(uuid.UUID(task))!=task:return control
            eligible=preference_needs(text)
            dbfile=self.root/"seller_concession_trial.sqlite3"
            cfg=self.config()
            adopted=(self.root/"adopted").is_file()
            identity="adopted-v1" if adopted else cfg["id"] if cfg else None
            seed=cfg["seed"] if cfg else 0
            start=cfg["start"] if cfg else now
            active=not (self.root/"disabled").exists() and bool(adopted or
                       (cfg and not cfg.get("stop") and start<=now<cfg["end"]))
            if not active and not dbfile.exists():return control
            self.root.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(dbfile,timeout=5)) as db,db:
                db.execute("CREATE TABLE IF NOT EXISTS assignments(task TEXT PRIMARY KEY,data TEXT NOT NULL)")
                db.execute("BEGIN IMMEDIATE")
                row=db.execute("SELECT data FROM assignments WHERE task=?",(task,)).fetchone()
                if row:
                    old=json.loads(row[0])
                    return dict(old,contaminated=True) if old.get("input_sha256")!=sha else old
                if not active or eligible is None:return control
                n_all=db.execute("SELECT COUNT(*) FROM assignments WHERE json_extract(data,'$.id')=?",(identity,)).fetchone()[0]
                n_b=db.execute("SELECT COUNT(*) FROM assignments WHERE json_extract(data,'$.id')=? AND json_extract(data,'$.arm')='B'",(identity,)).fetchone()[0]
                if not adopted and (n_all>=200 or n_b>=100):return control
                original=shop_stock.needs(text)
                req=dict(skus=sorted(original["skus"]),grades=sorted(original["grades"] or []),
                         hours=original["hours"],cert=original["cert"])
                request_band=hashlib.sha256("|".join(req["skus"]).encode()).hexdigest()[:12]
                stratum="adopted" if adopted else request_band+"|"+str(int((now-start)//300))
                db.execute("CREATE TABLE IF NOT EXISTS strata(trial TEXT,stratum TEXT,n INTEGER,PRIMARY KEY(trial,stratum))")
                row=db.execute("SELECT n FROM strata WHERE trial=? AND stratum=?",(identity,stratum)).fetchone()
                n=row[0] if row else 0
                first=hashlib.sha256(f"{seed}|{identity}|{stratum}|{n//2}".encode()).digest()[0]%2
                a=dict(control,id=identity,arm="B" if adopted or (first+n%2)%2 else "A",
                       stratum=stratum,request_need=req,preference=PREFERENCES.index(next(p for p in PREFERENCES if p in text)))
                db.execute("INSERT INTO assignments VALUES (?,?)",(task,json.dumps(a,allow_nan=False)))
                db.execute("INSERT OR REPLACE INTO strata VALUES (?,?,?)",(identity,stratum,n+1))
            self.emit(task,a,"enrolled",now=now)
            return a
        except Exception:
            self.invalid("grade_preference_assignment")
            return control

    def arm(self,assignment):
        return "A" if assignment.get("contaminated") else assignment.get("arm","A")

    def emit(self,task,a,event,now=None,**fields):
        if not a.get("id"):return
        try:
            rec=dict(ts=time.time() if now is None else now,task=task,id=a["id"],
                     arm=a["arm"],effective_arm=self.arm(a),event=event,
                     contaminated=bool(a.get("contaminated")),input_sha256=a.get("input_sha256"))
            rec.update(fields)
            data=(json.dumps(rec,allow_nan=False)+"\n").encode()
            fd=os.open(self.root/"seller_concession_trial.jsonl",os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
            try:
                if os.write(fd,data)!=len(data):raise OSError("short write")
            finally:os.close(fd)
        except Exception:self.invalid("grade_preference_trace")

    def selected(self,task,a,unit,miss,selection):
        reason=("grade" if str(miss).startswith("grade ") else
                "stock" if miss=="not in stock" else "other_fit" if miss else None)
        self.emit(task,a,"selected",unit=unit.get("unit") if unit else None,
                  sku=unit.get("sku") if unit else None,cost=unit.get("cost") if unit else None,
                  kind=unit.get("kind") if unit else None,grade=unit.get("grade") if unit else None,
                  cert=bool(unit.get("cert")) if unit else None,expires_at=unit.get("expires_at") if unit else None,
                  preference_used=bool(selection.get("preference_used")),miss=reason)

    def quote(self,task,deal,assignment,selection):
        deal["grade_preference"]=dict(assignment)
        deal["grade_preference_used"]=bool(selection.get("preference_used"))
        self.event(task,deal,"quote",deal["ask"])

    def event(self,task,deal,event,price=None,now=None,**fields):
        a=deal.get("grade_preference") or {}
        self.emit(task,a,event,now,price=price,unit=deal.get("unit"),sku=deal.get("id"),
                  kind=deal.get("kind"),cost=deal.get("cost"),reserve=deal.get("reserve"),
                  original_open=deal.get("open"),current_open=deal.get("open"),
                  current_reserve=deal.get("reserve"),assignment_unit=deal.get("unit"),
                  expires_at=deal.get("expires_at"),preference_used=bool(deal.get("grade_preference_used")),
                  **fields)
