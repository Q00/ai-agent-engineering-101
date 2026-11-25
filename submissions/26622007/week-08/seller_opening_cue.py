"""First-response wording only; isolated flags, immutable task choices and trace."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from seller_concession_trial import Trial

CUE = "Please send your offer as a number."

class OpeningCue(Trial):
    def __init__(self, root=None):
        base = Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent / ".market")
        super().__init__(base / "opening_cue")

    @staticmethod
    def _snapshot(deal):
        return dict(unit=deal.get("unit"), sku=deal.get("id"), kind=deal.get("kind"),
                    cost=deal.get("cost"), reserve=deal["reserve"], open=deal["open"],
                    ask=deal["ask"], warranty=deal.get("warranty"),
                    policy_sha=deal.get("reader_trial_policy_sha"))

    def _assign(self, task, deal, now):
        control = dict(arm="A", id=None, stratum=None, enrolled_at=now, **self._snapshot(deal))
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.root / "seller_concession_trial.sqlite3", timeout=5)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS assignments (task TEXT PRIMARY KEY, data TEXT NOT NULL)")
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT data FROM assignments WHERE task=?", (task,)).fetchone()
                if row:
                    return json.loads(row[0])
                a = control
                if not (self.root / "disabled").exists():
                    if (self.root / "adopted").is_file():
                        a = dict(control, arm="B", id="adopted-v1", stratum="adopted")
                    else:
                        cfg = self.config()
                        if cfg and not cfg.get("stop") and cfg["start"] <= now < cfg["end"]:
                            db.execute("CREATE TABLE IF NOT EXISTS strata (trial TEXT,stratum TEXT,n INTEGER,PRIMARY KEY(trial,stratum))")
                            n_all = db.execute("SELECT COUNT(*) FROM assignments WHERE json_extract(data,'$.id')=?", (cfg["id"],)).fetchone()[0]
                            n_b = db.execute("SELECT COUNT(*) FROM assignments WHERE json_extract(data,'$.id')=? AND json_extract(data,'$.arm')='B'", (cfg["id"],)).fetchone()[0]
                            if n_all < 200 and n_b < 100:
                                band = "high" if deal["open"] >= 300000 else "low"
                                stratum = f"{deal.get('kind')}|{band}|{int((now-cfg['start'])//300)}"
                                row = db.execute("SELECT n FROM strata WHERE trial=? AND stratum=?", (cfg["id"], stratum)).fetchone()
                                n = row[0] if row else 0
                                first = hashlib.sha256(f"{cfg['seed']}|{cfg['id']}|{stratum}|{n//2}".encode()).digest()[0] % 2
                                a = dict(control, arm="B" if (first+n%2)%2 else "A",
                                         id=cfg["id"], stratum=stratum)
                                db.execute("INSERT OR REPLACE INTO strata VALUES (?,?,?)", (cfg["id"], stratum, n+1))
                db.execute("INSERT INTO assignments VALUES (?,?)", (task, json.dumps(a, allow_nan=False)))
                return a
        except Exception:
            self.invalid("opening_cue_assignment")
            return control

    def arm(self, assignment):
        return "A" if assignment.get("contaminated") else assignment.get("arm", "A")

    def event(self, task, deal, event, price=None, now=None, **fields):
        clone = dict(deal, concession_trial=deal.get("opening_cue") or {})
        super().event(task, clone, event, price, now, **fields)

    def message(self, task, deal, text, now=None):
        """Decorate a NEW stock quote; the caller keeps its original metadata.ask."""
        if deal.get("rounds", 0):
            return text
        now = time.time() if now is None else now
        a = deal.get("opening_cue")
        if a is None:
            a = self._assign(task, deal, now)
        if a.get("id") and any(a.get(k) != v for k, v in self._snapshot(deal).items()):
            a = dict(a, contaminated=True)
            self.invalid("opening_cue_snapshot")
        deal["opening_cue"] = a
        added = bool(a.get("id")) and self.arm(a) == "B"
        shown = text + " " + CUE if added else text
        self.event(task, deal, "quote", deal["ask"], now, cue_added=added,
                   baseline_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                   shown_text_sha256=hashlib.sha256(shown.encode()).hexdigest())
        return shown
