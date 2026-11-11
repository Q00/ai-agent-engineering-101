"""The strategist's memory (strategist_memory.json), in three layers with their own upkeep.

  working    the last evidence pack, findings of the investigations it asked for, and its watch list
  episodic   every move: what, why, which lessons it relied on, its prediction and, once due, the outcome.
             The last KEEP_EPISODES stay word for word; older ones are folded into per-kind statistics.
  semantic   the playbook: lessons with an id, the evidence for and against each (moves that cited it and hit or
             missed), when it was added and last used. A lesson with CONTRADICT_MARGIN more misses than hits
             is retired; past MAX_LESSONS the least supported one goes. Each change of the lesson set is a new
             playbook version; a version that predicts worse than the one before it (VERSION_MIN scored moves,
             ROLLBACK_GAP lower hit rate) is rolled back.
Calibration: per metric, how far outcomes fell from predictions (median got/predicted), told to the model.
"""
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATH = HERE / "strategist_memory.json"
KEEP_EPISODES = 200
MAX_LESSONS = 20
CONTRADICT_MARGIN = 3
VERSION_MIN = 10
ROLLBACK_GAP = 0.15


class Memory:
    def __init__(self, path=PATH):
        self.path = Path(path)
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        self.d = d
        d.setdefault("t", 0)
        d.setdefault("working", {"findings": [], "watch": [], "queries": []})
        d.setdefault("episodes", [])
        d.setdefault("folded", {})                 # kind -> {"n", "hit"} of episodes past KEEP_EPISODES
        d.setdefault("lessons", [])
        d.setdefault("versions", [])               # [{"v", "t", "ids", "n", "hit"}]
        d.setdefault("steps", {})
        d.setdefault("suspended", None)
        d.setdefault("health", [])
        if "open" in d:                            # first layout (11-05): open moves and a plain playbook
            for p in d.pop("open"):
                d["episodes"].append(dict(p, uses=[], outcome=None))
            for i, text in enumerate(d.pop("playbook", [])):
                d["lessons"].append(self._lesson(text, f"L{i + 1}"))
            for k in ("score", "misses"):
                d.pop(k, None)
            self._new_version("migrated")

    # ---- semantic -------------------------------------------------------------------------------------------
    @staticmethod
    def _lesson(text, lid):
        return {"id": lid, "text": str(text)[:220], "added": time.time(), "used": 0, "for": 0, "against": 0}

    def lessons(self):
        return self.d["lessons"]

    def _next_id(self):
        n = max([int(x["id"][1:]) for x in self.d["lessons"] if x["id"][1:].isdigit()] + [0])
        return f"L{n + 1}"

    def edit_lessons(self, edits, log):
        """edits: [{"op": "add"|"rewrite"|"retire", "id": ..., "text": ...}] from the model."""
        changed = False
        for e in edits or []:
            op, lid = e.get("op"), e.get("id")
            cur = next((x for x in self.d["lessons"] if x["id"] == lid), None)
            if op == "add" and e.get("text"):
                new = self._lesson(e["text"], self._next_id())
                self.d["lessons"].append(new)
                log(f"memory: lesson {new['id']} added: {new['text']}")
                changed = True
            elif op == "rewrite" and cur and e.get("text"):
                log(f"memory: lesson {lid} rewritten: {cur['text']} -> {e['text'][:220]}")
                cur.update(text=str(e["text"])[:220], **{"for": 0, "against": 0})   # a new claim starts afresh
                changed = True
            elif op == "retire" and cur:
                self.d["lessons"].remove(cur)
                log(f"memory: lesson {lid} retired ({e.get('why', 'model')}): {cur['text']}")
                changed = True
        changed |= self._upkeep(log)
        if changed:
            self._new_version("edit")

    def _upkeep(self, log):
        changed = False
        for x in list(self.d["lessons"]):
            if x["against"] - x["for"] >= CONTRADICT_MARGIN:
                self.d["lessons"].remove(x)
                log(f"memory: lesson {x['id']} retired by evidence ({x['for']} hits, {x['against']} misses): {x['text']}")
                changed = True
        while len(self.d["lessons"]) > MAX_LESSONS:
            worst = min(self.d["lessons"], key=lambda x: (x["for"] - x["against"], x["used"], x["added"]))
            self.d["lessons"].remove(worst)
            log(f"memory: lesson {worst['id']} evicted (capacity {MAX_LESSONS}): {worst['text']}")
            changed = True
        return changed

    def _new_version(self, why):
        v = (self.d["versions"][-1]["v"] + 1) if self.d["versions"] else 1
        self.d["versions"].append({"v": v, "t": time.time(), "why": why, "n": 0, "hit": 0,
                                   "lessons": [dict(x) for x in self.d["lessons"]]})
        self.d["versions"] = self.d["versions"][-12:]

    def version(self):
        return self.d["versions"][-1]["v"] if self.d["versions"] else 0

    def maybe_rollback(self, log):
        """A playbook version that predicts clearly worse than the one before it is replaced by that one."""
        vs = self.d["versions"]
        if len(vs) < 2 or vs[-1]["n"] < VERSION_MIN or vs[-2]["n"] < VERSION_MIN:
            return
        now_rate, before = vs[-1]["hit"] / vs[-1]["n"], vs[-2]["hit"] / vs[-2]["n"]
        if now_rate < before - ROLLBACK_GAP:
            self.d["lessons"] = [dict(x) for x in vs[-2]["lessons"]]
            log(f"memory: playbook v{vs[-1]['v']} hit {now_rate:.0%} vs v{vs[-2]['v']} {before:.0%}: rolled back")
            self._new_version(f"rollback to v{vs[-2]['v']}")

    # ---- episodic -------------------------------------------------------------------------------------------
    def add_episode(self, ep):
        ep.update(version=self.version(), outcome=None)
        for x in self.d["lessons"]:
            if x["id"] in ep.get("uses", []):
                x["used"] += 1
        self.d["episodes"].append(ep)

    def due(self, now):
        return [e for e in self.d["episodes"] if e["outcome"] is None and now >= e["due"]]

    def score(self, ep, got, ok):
        ep["outcome"] = {"got": got, "hit": bool(ok), "t": time.time()}
        for x in self.d["lessons"]:
            if x["id"] in ep.get("uses", []):
                x["for" if ok else "against"] += 1
        for v in self.d["versions"]:
            if v["v"] == ep.get("version"):
                v["n"] += 1
                v["hit"] += int(ok)

    def fold(self):
        """Keep the last KEEP_EPISODES scored moves word for word, fold older ones into per-kind counts."""
        scored = [e for e in self.d["episodes"] if e["outcome"] is not None]
        if len(scored) <= KEEP_EPISODES:
            return
        old = set(id(e) for e in scored[:-KEEP_EPISODES])
        for e in scored[:-KEEP_EPISODES]:
            f = self.d["folded"].setdefault(e["knob"], {"n": 0, "hit": 0})
            f["n"] += 1
            f["hit"] += int(e["outcome"]["hit"])
        self.d["episodes"] = [e for e in self.d["episodes"] if id(e) not in old]

    def record(self):
        """Hit rates per kind of move (recent and folded), calibration per metric, and the latest misses."""
        kinds = {}
        for e in self.d["episodes"]:
            if e["outcome"] is None:
                continue
            k = kinds.setdefault(e["knob"], {"n": 0, "hit": 0, "last": []})
            k["n"] += 1
            k["hit"] += int(e["outcome"]["hit"])
            k["last"] = (k["last"] + [int(e["outcome"]["hit"])])[-12:]
        for knob, f in self.d["folded"].items():
            k = kinds.setdefault(knob, {"n": 0, "hit": 0, "last": []})
            k["n"] += f["n"]
            k["hit"] += f["hit"]
        cal = {}
        for e in self.d["episodes"]:
            p = e.get("prediction") or {}
            if e["outcome"] is not None and p.get("value"):
                cal.setdefault(p["metric"], []).append(e["outcome"]["got"] / p["value"])
        calib = {m: {"n": len(v), "median_got_over_predicted": round(sorted(v)[len(v) // 2], 2)} for m, v in cal.items()}
        misses = [{"knob": e["knob"], "sku": e.get("sku"), "value": e["value"], "why": e["why"][:140],
                   "uses": e.get("uses"), "predicted": e["prediction"], "got": e["outcome"]["got"]}
                  for e in self.d["episodes"] if e["outcome"] is not None and not e["outcome"]["hit"]][-10:]
        return {"by_kind": kinds, "calibration": calib, "recent_misses": misses,
                "playbook_version": self.version(),
                "versions": [{k: v[k] for k in ("v", "why", "n", "hit")} for v in self.d["versions"][-4:]]}

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


EXCHANGE = HERE / ".market" / "strategy_exchange.db"   # shared by the shop and buyer strategists (SQLite)
SLOT = 900                                                # they alternate 15-minute slots (user, 11-06)


def my_turn(role, now=None):
    """The buyer analyses in even 15-minute slots (:00, :30), the shop in odd ones (:15, :45): each every 30 min,
    each reading what the other wrote 15 minutes earlier. Returns the slot number when it is this role's turn."""
    # 11-10 (user: the API key had $195 left for 172 h after $304 in 67 h): the buyer every hour at :00, the shop
    # every 3 h at :30 (01:30, 04:30, ... UTC; book lead 340M, safe), down from every 30 min each. Same day, later
    # (user: Round 4 opens right after Round 3 and the credit refills only on the 20th): the shop every 6 h
    # (01:30, 07:30, 13:30, 19:30 UTC) so ~$60 is left for Round 4's first days
    slot = int((now or time.time()) // SLOT)
    if role == "buyer":
        # 11-11 restructure (user: 'as recommended'): the buyer every hour at :00 plus code-guard wakes
        # (.market/buyer_strategist_wake); the 30-min cadence of 01:50 did not address the cause (step size, bugs)
        return slot if slot % 4 == 0 else None
    # 11-11 12:35 (user: revenue 2nd must not be lost; the shop moved from margin-first to volume at or above cost):
    # every 2 h at :30 (01:30, 03:30, ... UTC; ~+$5.6/day) while the revenue defense settles; back to 6 h after
    return slot if slot % 8 == 6 else None


def _db():
    import sqlite3
    db = sqlite3.connect(EXCHANGE, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS entries (t REAL, role TEXT, read TEXT, findings TEXT, notes TEXT)")
    cols = [r[1] for r in db.execute("PRAGMA table_info(entries)")]
    if "requests" not in cols:
        db.execute("ALTER TABLE entries ADD COLUMN requests TEXT")      # the curator adds "curated" the same way
    return db


# What a strategist may ask the curator for (user 11-06: the strategists tell the memory agent what they need
# next; the curator answers in its next digest, under answers_to_your_requests). kind -> required keys.
REQUEST_KINDS = {"sku_prices": ("sku",), "category": ("category",), "competitor": ("shop",), "unserved": (),
                 "rules": ("term",), "buyer": ("name",), "shop": ("name",), "question": ("text",),
                 "knobs": (), "trades": ()}      # knobs: sku|knob|category; trades: outcome|shop|category|kind
MAX_REQUESTS = 5


def valid_requests(items):
    out = []
    for q in items or []:
        if isinstance(q, dict) and q.get("kind") in REQUEST_KINDS \
                and all(isinstance(q.get(k), str) and q[k].strip() for k in REQUEST_KINDS[q["kind"]]):
            out.append({k: (v if isinstance(v, (str, int, float)) else str(v)) for k, v in q.items()})
    return out[:MAX_REQUESTS]


def market_picture():
    """The curator's code-measured market (market_picture.py), for a strategist's evidence; never raises."""
    try:
        from market_picture import picture
        return picture()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def operator_note(note_file, answer_file, max_age_h=6):
    """11-10 (user: API budget, trim the prompts): the operator's note goes into a strategist's evidence while it
    is younger than max_age_h or has no answer written after it; older answered notes stay out (the full history of
    notes is in operator_notes_archive.jsonl)."""
    import json as _j
    try:
        n = _j.loads(Path(note_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not n.get("note"):
        return None
    try:
        answered = _j.loads(Path(answer_file).read_text(encoding="utf-8")).get("t", 0) > n.get("t", 0)
    except (OSError, ValueError):
        answered = False
    if answered and time.time() - n.get("t", 0) > max_age_h * 3600:
        return None
    return n["note"]


def buyer_picture(mp):
    """11-10 (API budget): the buyer's copy of the market picture without the shop-side parts (what sells, top
    shops, what buyers asked at our shop); the buyer has its own shop values (market_value_6h, by_shop_24h)."""
    if not isinstance(mp, dict):
        return mp
    out = {k: v for k, v in mp.items() if k not in ("what_sells", "top_shops")}
    if isinstance(out.get("what_buyers_buy"), dict):
        out["what_buyers_buy"] = {k: v for k, v in out["what_buyers_buy"].items() if k != "asked_at_our_shop_24h"}
    return out


def post_board(role, read, findings, notes=None, requests=None):
    """Store this strategist's read, findings, notes for the other one and requests to the curator."""
    with _db() as db:
        db.execute("INSERT INTO entries (t, role, read, findings, notes, requests) VALUES (?, ?, ?, ?, ?, ?)",
                   (time.time(), role, str(read)[:2000], json.dumps(findings, ensure_ascii=False),
                    json.dumps(notes or [], ensure_ascii=False), json.dumps(requests or [], ensure_ascii=False)))
    # the curator merges it into the digest and archives old entries (memory_curator.py), detached
    import subprocess
    import sys
    subprocess.Popen([sys.executable, str(HERE / "memory_curator.py")], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd=str(HERE))


def read_digest(role):
    """The curator's digest of both strategists' memory, with the notes meant for `role`."""
    try:
        from memory_curator import read_digest as rd
        return rd(role)
    except Exception:
        return "{}"


def read_board(role, n=1):
    """The other strategist's last n raw entries, newest first (older ones live on in the curator's digest)."""
    other = "buyer" if role == "shop" else "shop"
    try:
        with _db() as db:
            rows = db.execute("SELECT t, read, findings, notes FROM entries WHERE role = ? ORDER BY t DESC LIMIT ?",
                              (other, n)).fetchall()
    except Exception:
        return []
    return [{"minutes_ago": round((time.time() - t) / 60), "read": r, "findings": json.loads(f),
             "notes_to_you": json.loads(nt)} for t, r, f, nt in rows]
