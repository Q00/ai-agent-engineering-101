"""Default-off finite wording trial. No pricing, offer, history or receipt writes."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import threading
import uuid

# Exact full-message allowlist, including the caller's unchanged final stance.
TEMPLATES = (
 ("Thanks for your offer of {offer}. I can come down a little.", "Thanks for your offer. I can come down a little."),
 ("I appreciate the offer of {offer}. Here is a better price.", "I appreciate the offer. Here is a better price."),
 ("Thank you for {offer}. I can meet you partway.", "Thank you for your offer. I can meet you partway."),
 ("Thanks for your offer of {offer}. This is the least I can take.", "Thanks for your offer. This is the least I can take."),
 ("I appreciate the offer of {offer}, but this price stays.", "I appreciate the offer, but this price stays."),
 ("Thanks for your offer of {offer}. This is already the lowest price we can sell it for.", "Thanks for your offer. This is already the lowest price we can sell it for."),
 ("I appreciate the offer of {offer}, but we are already at our floor price for this item.", "I appreciate the offer, but we are already at our floor price for this item."),
 ("Thank you for {offer}. Our price is already as low as it goes.", "Thank you for your offer. Our price is already as low as it goes."),
)
def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()

def trial_config(study, start, end, salt):
    if not isinstance(study,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",study):
        raise ValueError("invalid study")
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)):
        raise ValueError("finite timestamps required")
    if start <= 0 or end-start != 1800 or not isinstance(salt,str) or not 1 <= len(salt) <= 128:
        raise ValueError("exactly 1800 seconds and salt required")
    return dict(enabled=True,study=study,start=start,end=end,salt=salt)

def task_arm(task, cfg):
    return "B" if int(sha(cfg["salt"]+"|"+cfg["study"]+"|"+task),16)%2 else "A"

def rewrite(text,price):
    if type(price) is not int or price <= 0:
        return text,False
    for old,new in TEMPLATES:
        pattern=re.escape(old).replace(re.escape("{offer}"),r"(?P<offer>[1-9][0-9]*)")
        match=re.fullmatch(pattern+r"(?P<final> This is our final price\.)? My price: "+str(price)+r"\.",text)
        if match:
            return new+(match["final"] or "")+f" My price: {price}.",True
    return text,False

class SinglePrice:
    def __init__(self,root=None):
        base=Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent/".market")
        self.root=base/"single_price"
        self.pending=threading.local()
    def stamp(self,path):
        s=path.stat()
        return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    def config(self):
        path=self.root/"config.json"
        before=self.stamp(path)
        c=json.loads(path.read_text())
        after=self.stamp(path)
        if before!=after:raise ValueError("configuration changed during read")
        self.pending.config_stamp=after
        expected=trial_config(c["study"],c["start"],c["end"],c["salt"])
        if c.get("enabled") is not True or c!=expected:
            raise ValueError("off or invalid configuration")
        return c
    def record(self,row):
        # Leading newline separates any previous torn append. Neither a full
        # append nor close success alone certifies a returned response.
        b=("\n"+json.dumps(row,allow_nan=False,separators=(",",":"))+"\n").encode()
        fd=os.open(self.root/"trace.jsonl",os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        try:
            if os.write(fd,b)!=len(b):raise OSError("short render record")
        finally:os.close(fd)
    def render(self,task,text,price=None,now=None):
        """Called at outgoing render, not decision time. Trace is NOT delivery ACK."""
        try:
            # Same-thread next entry proves the preceding render call exited.
            # Never flush after the current final guard/selection.
            pending=getattr(self.pending,"row",None)
            if pending is not None:
                self.record(pending)
                self.pending.row=None
            live_clock=now is None
            now=time.time() if live_clock else now
            if type(now) not in (int,float) or not math.isfinite(now) or not isinstance(task,str) or not task:
                return text
            c=self.config()
            digest=sha(json.dumps(c,sort_keys=True,separators=(",",":")))
            active=c["start"] <= now < c["end"] and not (self.root/"disabled").exists()
            key=sha(task)
            # No legacy cap or alternate controller. Include ALL rendered tasks in
            # the cohort, not just template-eligible tasks; no recruitment cap.
            with closing(sqlite3.connect(self.root/"assignments.sqlite3",timeout=0.1)) as db,db:
                db.execute("CREATE TABLE IF NOT EXISTS assignments(study TEXT,task TEXT,data TEXT,PRIMARY KEY(study,task))")
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS studies(study TEXT PRIMARY KEY,config_sha TEXT)")
                pinned=db.execute("SELECT config_sha FROM studies WHERE study=?",(c["study"],)).fetchone()
                if pinned and pinned[0]!=digest:return text
                if active and not pinned:
                    db.execute("INSERT INTO studies VALUES(?,?)",(c["study"],digest))
                row=db.execute("SELECT data FROM assignments WHERE study=? AND task=?",(c["study"],key)).fetchone()
                a=json.loads(row[0]) if row else None
                if a is None and active:
                    a=dict(arm=task_arm(task,c),enrolled=now,config_sha=digest)
                    db.execute("INSERT INTO assignments VALUES(?,?,?)",(c["study"],key,json.dumps(a)))
            candidate,eligible=rewrite(text,price)
            event=uuid.uuid4().hex
            baseline_sha=sha(text);candidate_sha=sha(candidate)
            intent=dict(protocol=2,kind="intent",event=event,ts=now,
                        study=c["study"],task_sha=key,config_sha=digest,
                        itt_arm=a["arm"] if a else None,eligible=eligible,
                        effective_arm="unknown",window_active=None,
                        price=price if type(price) is int else None,
                        baseline_sha=baseline_sha,candidate_sha=candidate_sha,
                        render_sha=None,scope="intent-not-return-or-delivery")
            self.record(intent)
            # All DB/write/close/config-read work precedes final observations.
            same=self.config()==c
            unchanged=self.stamp(self.root/"config.json")==self.pending.config_stamp
            disabled=(self.root/"disabled").exists()
            selected_at=time.time() if live_clock else now
            # Linearization boundary: no filesystem/logging/cleanup after here.
            active=bool(same and unchanged and not disabled and
                        c["start"] <= selected_at < c["end"])
            valid=bool(a and a["config_sha"]==digest and
                       c["start"]<=a["enrolled"]<=selected_at and a["enrolled"]<c["end"])
            treatment=bool(active and valid and a["arm"]=="B" and eligible)
            shown=candidate if treatment else text
            self.pending.row=dict(protocol=2,kind="selection_observed",event=event,
                                  selected_at=selected_at,window_active=active,
                                  effective_arm="B" if treatment else ("A" if eligible else "not-exposed"),
                                  render_sha=candidate_sha if treatment else baseline_sha,
                                  scope="prior-render-selection-not-delivery")
            return shown
        except Exception:
            # No logging/controller/cleanup exception can alter the original response.
            return text

# No background/shutdown flush; last/unflushed intent remains unknown.
_controller=SinglePrice()
def render(task,text,price=None):
    return _controller.render(task,text,price)
