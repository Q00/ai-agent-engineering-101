"""Shared deterministic control plane for the Round-3 buyer and seller.

The module has no market credentials and performs no network mutations.  It
stores bounded structured evidence that survives role restarts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
import uuid


RULE_VERSION = "round3-patch3.5"
SECRET_FIELDS = ("secret", "token", "password", "private", "signature", "owner_env", "member_key")

MUTATION_EDGES = {
    "PREPARED": {"SUBMITTED"},
    "SUBMITTED": {"CONFIRMED", "REJECTED", "UNCERTAIN"},
    "UNCERTAIN": {"RECONCILING"},
    "RECONCILING": {"CONFIRMED", "SAFE_TO_RETRY", "PRESERVED"},
    "SAFE_TO_RETRY": {"PREPARED"},
    "PRESERVED": {"RECONCILING"},
}


def _safe(value, key=""):
    """Return log-safe JSON data; raw external instructions never enter here."""
    if any(part in key.casefold() for part in SECRET_FIELDS):
        return "[redacted]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.replace("\r", " ").replace("\n", " ")
        return text[:512]
    if isinstance(value, dict):
        return {str(k)[:80]: _safe(v, str(k)) for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [_safe(v, key) for v in list(value)[:80]]
    return str(type(value).__name__)


def stable_arm(experiment_id: str, unit_id: str) -> tuple[str, str]:
    """Restart-stable assignment_v1: canonical UTF-8 JSON and SHA-256."""
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("experiment_id is required")
    if not isinstance(unit_id, str) or not unit_id.strip():
        raise ValueError("unit_id is required")
    body = {
        "experiment_id": unicodedata.normalize("NFC", experiment_id),
        "unit_id": unicodedata.normalize("NFC", unit_id),
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    bucket = int.from_bytes(bytes.fromhex(digest)[:8], "big") % 100
    return ("treatment" if bucket < 80 else "control"), digest


class Control:
    def __init__(self, state: str | Path):
        self.root = Path(state)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "adaptive.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=15)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS runtime_events(
          id INTEGER PRIMARY KEY, event_id TEXT UNIQUE, ts REAL, role TEXT,
          kind TEXT, correlation TEXT, rule_version TEXT, commit_id TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS state_transitions(
          id INTEGER PRIMARY KEY, transition_id TEXT UNIQUE, ts REAL,
          machine TEXT, entity_id TEXT, old_state TEXT, new_state TEXT,
          correlation TEXT, rule_version TEXT, commit_id TEXT, reason TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS mutations(
          mutation_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, role TEXT,
          kind TEXT, state TEXT, created REAL, updated REAL, checks INTEGER DEFAULT 0,
          last_check REAL, body TEXT);
        CREATE TABLE IF NOT EXISTS experiment_units(
          experiment_id TEXT, unit_id TEXT, arm TEXT, digest TEXT, created REAL,
          PRIMARY KEY(experiment_id,unit_id));
        CREATE TABLE IF NOT EXISTS input_snapshots(
          source TEXT PRIMARY KEY, fetched REAL, content_hash TEXT, sequence INTEGER,
          schema_ok INTEGER, body TEXT);
        CREATE TABLE IF NOT EXISTS recovery_checkpoints(
          id INTEGER PRIMARY KEY, checkpoint_id TEXT UNIQUE, ts REAL, role TEXT,
          commit_id TEXT, parameter_hash TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS forum_publications(
          id INTEGER PRIMARY KEY, publication_id TEXT UNIQUE, role TEXT, ts REAL,
          normalized_hash TEXT, tokens TEXT, refs TEXT, status TEXT, reason TEXT);
        CREATE TRIGGER IF NOT EXISTS runtime_events_no_update
          BEFORE UPDATE ON runtime_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
        CREATE TRIGGER IF NOT EXISTS runtime_events_no_delete
          BEFORE DELETE ON runtime_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
        CREATE TRIGGER IF NOT EXISTS transitions_no_update
          BEFORE UPDATE ON state_transitions BEGIN SELECT RAISE(ABORT,'append-only'); END;
        CREATE TRIGGER IF NOT EXISTS transitions_no_delete
          BEFORE DELETE ON state_transitions BEGIN SELECT RAISE(ABORT,'append-only'); END;
        """)

    @staticmethod
    def commit_id():
        return os.environ.get("MARKET_COMMIT", "working-tree")[:64]

    def event(self, role, kind, body=None, correlation=None, rule_version=RULE_VERSION):
        correlation = str(correlation or uuid.uuid4())
        event_id = str(uuid.uuid4())
        payload = json.dumps(_safe(body or {}), ensure_ascii=False, sort_keys=True)
        with self.db:
            self.db.execute("""INSERT INTO runtime_events
              (event_id,ts,role,kind,correlation,rule_version,commit_id,body)
              VALUES(?,?,?,?,?,?,?,?)""",
              (event_id,time.time(),role,kind,correlation,rule_version,self.commit_id(),payload))
        return event_id

    def transition(self, machine, entity_id, old, new, reason, body=None, correlation=None):
        transition_id = str(uuid.uuid4())
        with self.db:
            self.db.execute("""INSERT INTO state_transitions
              (transition_id,ts,machine,entity_id,old_state,new_state,correlation,
               rule_version,commit_id,reason,body) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (transition_id,time.time(),machine,entity_id,old,new,str(correlation or entity_id),
               RULE_VERSION,self.commit_id(),reason[:160],json.dumps(_safe(body or {}),ensure_ascii=False)))
        return transition_id

    def assign(self, experiment_id, unit_id):
        row = self.db.execute("SELECT arm,digest FROM experiment_units WHERE experiment_id=? AND unit_id=?",
                              (experiment_id,unit_id)).fetchone()
        if row:
            return row["arm"], row["digest"]
        arm, digest = stable_arm(experiment_id, unit_id)
        with self.db:
            self.db.execute("INSERT INTO experiment_units VALUES(?,?,?,?,?)",
                            (experiment_id,unit_id,arm,digest,time.time()))
        return arm, digest

    def prepare_mutation(self, role, kind, idempotency_key, body=None):
        mutation_id = str(uuid.uuid4())
        now = time.time()
        try:
            with self.db:
                self.db.execute("INSERT INTO mutations VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (mutation_id,idempotency_key,role,kind,"PREPARED",now,now,0,None,
                                 json.dumps(_safe(body or {}),ensure_ascii=False)))
            self.transition("mutation",mutation_id,None,"PREPARED","created",correlation=idempotency_key)
            return mutation_id, True
        except sqlite3.IntegrityError:
            row = self.db.execute("SELECT mutation_id FROM mutations WHERE idempotency_key=?",
                                  (idempotency_key,)).fetchone()
            return row[0], False

    def mutation_state(self, mutation_id):
        row = self.db.execute("SELECT state FROM mutations WHERE mutation_id=?",(mutation_id,)).fetchone()
        return row[0] if row else None

    def move_mutation(self, mutation_id, new, reason, body=None):
        old = self.mutation_state(mutation_id)
        if old is None or new not in MUTATION_EDGES.get(old,set()):
            raise ValueError(f"illegal mutation transition {old}->{new}")
        now=time.time()
        with self.db:
            self.db.execute("UPDATE mutations SET state=?,updated=?,body=? WHERE mutation_id=?",
                            (new,now,json.dumps(_safe(body or {}),ensure_ascii=False),mutation_id))
        self.transition("mutation",mutation_id,old,new,reason,body)

    def note_reconciliation(self, mutation_id):
        with self.db:
            self.db.execute("UPDATE mutations SET checks=checks+1,last_check=?,updated=? WHERE mutation_id=?",
                            (time.time(),time.time(),mutation_id))
        return self.db.execute("SELECT checks FROM mutations WHERE mutation_id=?",(mutation_id,)).fetchone()[0]

    def unresolved(self, role=None):
        q="SELECT * FROM mutations WHERE state IN ('SUBMITTED','UNCERTAIN','RECONCILING','PRESERVED')"
        args=()
        if role:
            q+=" AND role=?";args=(role,)
        return [dict(r) for r in self.db.execute(q,args)]

    def save_input(self, source, body, sequence=None, schema_ok=True, fetched=None):
        fetched=time.time() if fetched is None else fetched
        safe=_safe(body)
        raw=json.dumps(safe,ensure_ascii=False,sort_keys=True,separators=(",", ":"))
        digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO input_snapshots VALUES(?,?,?,?,?,?)",
                            (source,fetched,digest,sequence,int(schema_ok),raw))
        return digest

    def input_fresh(self, source, max_age, now=None):
        row=self.db.execute("SELECT fetched,schema_ok FROM input_snapshots WHERE source=?",(source,)).fetchone()
        now=time.time() if now is None else now
        return bool(row and row["schema_ok"] and now-row["fetched"]<=max_age)

    def checkpoint(self, role, parameters=None, body=None):
        params=json.dumps(_safe(parameters or {}),sort_keys=True,separators=(",", ":"))
        checkpoint_id=str(uuid.uuid4())
        with self.db:
            self.db.execute("INSERT INTO recovery_checkpoints VALUES(?,?,?,?,?,?,?)",
                            (None,checkpoint_id,time.time(),role,self.commit_id(),
                             hashlib.sha256(params.encode()).hexdigest(),json.dumps(_safe(body or {}))))
        self.event(role,"checkpoint",{"checkpoint_id":checkpoint_id},checkpoint_id)
        return checkpoint_id

    @staticmethod
    def normalize_publication(text):
        text=unicodedata.normalize('NFKC',str(text)).casefold()
        text=re.sub(r'(?<=\d),(?=\d)','',text)
        text=' '.join(part for part in text.split() if not part.startswith(('http://','https://')))
        normalized=' '.join(''.join(ch if ch.isalnum() else ' ' for ch in text).split())
        return normalized,set(normalized.split())

    def allow_publication(self,role,text,refs=(),now=None):
        now=time.time() if now is None else now
        refs=list(refs)
        normalized,tokens=self.normalize_publication(text)
        token_hashes={hashlib.sha256(token.encode()).hexdigest() for token in tokens}
        digest=hashlib.sha256(normalized.encode()).hexdigest()
        limits={'buyer':24,'shop':12};limit=limits.get(role,0)
        rows=self.db.execute('SELECT * FROM forum_publications WHERE role=? AND ts>? AND status=?',
                             (role,now-86400,'ACTIVE')).fetchall()
        reason=None
        if not normalized:reason='empty'
        elif not limit:reason='role'
        elif len(rows)>=limit:reason='daily_limit'
        elif rows and now-max(r['ts'] for r in rows)<300:reason='interval'
        else:
            for row in rows:
                old=set(json.loads(row['tokens']));union=token_hashes|old
                similarity=len(token_hashes&old)/len(union) if union else 1
                if row['normalized_hash']==digest or similarity>=.80:
                    reason='duplicate';break
        status='ACTIVE' if reason is None else 'STOPPED';publication_id=str(uuid.uuid4())
        with self.db:
            self.db.execute('INSERT INTO forum_publications VALUES(?,?,?,?,?,?,?,?,?)',(
                None,publication_id,role,now,digest,json.dumps(sorted(token_hashes)),
                json.dumps(_safe(refs)),status,reason))
        self.event(role,'forum_publication_'+status.lower(),{
            'publication_id':publication_id,'refs_count':len(refs),'reason':reason},publication_id)
        return reason is None,publication_id,reason

    def close(self):
        self.db.close()


def classify_forum_claim(official_match, independent_authors):
    if official_match:return 'VERIFIED'
    if independent_authors>=3:return 'CORROBORATED'
    return 'UNVERIFIED'

