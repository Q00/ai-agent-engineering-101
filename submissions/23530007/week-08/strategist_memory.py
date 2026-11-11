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
        # 11-11 12:25 (user: with the cost gate, check more often): every 30 min (:00, :30); run_gate decides
        # whether the model runs (min gap 50 min, max gap 3 h)
        return slot if slot % 2 == 0 else None
    # 11-11 12:35 (user: revenue 2nd must not be lost; the shop moved from margin-first to volume at or above cost):
    # every 2 h at :30 (01:30, 03:30, ... UTC; ~+$5.6/day) while the revenue defense settles; back to 6 h after
    # 11-11 12:25 (user: with the cost gate, check more often): every 30 min (:15, :45); run_gate decides whether
    # the model runs (min gap 60 min, max gap 6 h)
    return slot if slot % 2 == 1 else None


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
    try:                                # 11-11: t may be a string (notes written by hand / ops/tell.py)
        nt = float(n.get("t") or 0)
    except (TypeError, ValueError):
        nt = 0.0
    try:
        answered = float(_j.loads(Path(answer_file).read_text(encoding="utf-8")).get("t") or 0) > nt
    except (OSError, ValueError, TypeError):
        answered = False
    if answered and time.time() - nt > max_age_h * 3600:
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


# ---------------------------------------------------------------------------------------------------------------
# 11-11 (user: build something that cuts the API credit): a code-only gate in front of the strategists' model call.
# A run costs $0.4-0.8 (prompt ~35k tokens written to cache and never re-read at hourly cadence, plus 8-16k output
# and thinking tokens). The learn/revert step runs in code before the gate, so skipping only skips the model.
# Run the model when something moved; otherwise skip, but never longer than MAX_GAP_H. Effort high when there is a
# reason to think hard (wake, new operator note, rank change, big move), medium on routine runs.
GATE_MAX_GAP_H = {"buyer": 3.0, "shop": 6.0}
GATE_MIN_GAP_H = {"buyer": 50 / 60, "shop": 1.0}   # 11-11: checks every 30 min; a flapping rank must not buy a run each
CREDIT_END = 1795219200       # 2026-11-21 00:00 UTC: the promotional credit expires (no refill)
# 11-11 (user: 'use nearly all of what is left; raise the frequency'): the gate paces itself to the credit. daily
# budget = credit left / days to expiry; spend in the last 24 h under 85 % of it -> every check may run (min gap
# 30 min, max gap buyer 1 h / shop 2 h, effort high); 85-115 % -> min gap buyer 40 / shop 60 min, max 2 h / 4 h,
# effort high; over 115 % -> the frugal setting above (effort medium unless something hard happened)
PACE = {"under": ({"buyer": 0.5, "shop": 0.5}, {"buyer": 1.0, "shop": 2.0}, True),
        "on": ({"buyer": 40 / 60, "shop": 1.0}, {"buyer": 2.0, "shop": 4.0}, True),
        "over": (GATE_MIN_GAP_H, GATE_MAX_GAP_H, False)}


def pace(now=None):
    """('under'|'on'|'over', spend 24 h, daily budget)."""
    import time as _t
    now = now or _t.time()
    try:
        import model_env
        sp = model_env.spend_summary(now)
        budget = (sp.get("credit_left") or 0) / max(0.5, (CREDIT_END - now) / 86400)
        r = (sp.get("today") or 0) / budget if budget > 0 else 9
        return ("under" if r < 0.85 else "on" if r <= 1.15 else "over"), sp.get("today"), round(budget, 2)
    except Exception:
        return "over", None, None


def _gate_state(role):
    import json as _j
    md = Path(__file__).resolve().parent / ".market"
    fp = {}
    try:
        rows = (md / "class_history.jsonl").read_text().splitlines()
        last = _j.loads(rows[-1])["p"]
        me = last.get("yms979") or [None, None, None]
        i = 1 if role == "shop" else 2
        fp["score"] = me[i]
        fp["rank"] = 1 + sum(1 for v in last.values() if v[i] is not None and me[i] is not None and v[i] > me[i])
        fp["total_rank"] = 1 + sum(1 for v in last.values() if v[0] is not None and me[0] is not None and v[0] > me[0])
    except (OSError, ValueError, IndexError, KeyError):
        pass
    try:
        g = _j.loads((md / "guard_state.json").read_text())
        fp["fires"] = sorted(g.get("fires") or [])
        if role == "shop":
            fp["book_eta_h"] = g.get("book_eta_h")
    except (OSError, ValueError):
        pass
    if role == "buyer":
        try:
            s = _j.loads((md / "buyer_score.json").read_text())
            fp["v6"] = (s.get("v6") or {}).get("all")
            fp["deals_h"] = (s.get("v6") or {}).get("deals_h")
        except (OSError, ValueError):
            pass
    note = Path(__file__).resolve().parent / ("operator_to_buyer.json" if role == "buyer" else "operator_to_shop.json")
    try:
        fp["note_t"] = float(_j.loads(note.read_text()).get("t") or 0)
    except (OSError, ValueError):
        fp["note_t"] = 0
    return fp


def _last_model_t(role):
    import json as _j
    name = "buyer strategist" if role == "buyer" else "strategist"
    t = 0.0
    try:
        for l in (Path(__file__).resolve().parent / ".market" / "spend.jsonl").read_text().splitlines()[-400:]:
            try:
                r = _j.loads(l)
            except ValueError:
                continue
            if r.get("role") == name:
                t = max(t, r.get("t") or 0)
    except OSError:
        pass
    return t


def run_gate(role, mem_d, woken=False, now=None):
    """(run?, effort, why). mem_d is the strategist's memory dict; it keeps gate_fp and model_t."""
    import time as _t
    now = now or _t.time()
    fp = _gate_state(role)
    old = mem_d.get("gate_fp") or {}
    if not old:                       # first gated run: compare with now, from the last real model call (spend ledger)
        old = dict(fp, note_t=0 if role == "buyer" else fp.get("note_t", 0))
        mem_d["model_t"] = mem_d.get("model_t") or _last_model_t(role)
    age_h = (now - (mem_d.get("model_t") or 0)) / 3600
    why = []
    if woken:
        why.append("woken")
    if fp.get("note_t", 0) > old.get("note_t", 0):
        why.append("new operator note")
    if old.get("rank") is not None and fp.get("rank") != old.get("rank"):
        why.append(f"rank {old.get('rank')} -> {fp.get('rank')}")
    if old.get("total_rank") is not None and fp.get("total_rank") != old.get("total_rank"):
        why.append(f"total rank {old.get('total_rank')} -> {fp.get('total_rank')}")
    if set(fp.get("fires") or []) - set(old.get("fires") or []):
        why.append(f"guard fires {sorted(set(fp['fires']) - set(old.get('fires') or []))}")
    hard = list(why)
    if fp.get("score") is not None and old.get("score") is not None and abs(fp["score"] - old["score"]) >= 0.01:
        why.append(f"score {old['score']} -> {fp['score']}")
    if role == "buyer":
        if fp.get("v6") is not None and old.get("v6") is not None and abs(fp["v6"] - old["v6"]) >= 0.02:
            why.append(f"v6 {old['v6']} -> {fp['v6']}")
        if fp.get("deals_h") and old.get("deals_h") and abs(fp["deals_h"] - old["deals_h"]) / old["deals_h"] >= 0.25:
            why.append(f"deals/h {old['deals_h']} -> {fp['deals_h']}")
    if role == "shop" and fp.get("book_eta_h") and old.get("book_eta_h"):
        if fp["book_eta_h"] < 150 <= old["book_eta_h"]:
            hard.append("book ETA under 150 h")
            why.append("book ETA under 150 h")
    mode, spent, budget = pace(now)
    min_gap, max_gap, always_high = PACE[mode]
    if age_h >= max_gap.get(role, 6):
        why.append(f"{age_h:.1f} h since the last model run")
    run = bool(why)
    urgent = woken or "new operator note" in why
    if run and not urgent and old and age_h < min_gap.get(role, 1.0):
        return False, "medium", f"waiting: {'; '.join(why)} (last model run {age_h * 60:.0f} min ago, min gap {min_gap[role] * 60:.0f} min, pace {mode})"
    effort = "high" if hard or always_high else "medium"
    if run:
        why.append(f"pace {mode}: ${spent}/24 h vs budget ${budget}/day")
    if run:
        mem_d["gate_fp"] = fp
        mem_d["model_t"] = now
    return run, effort, "; ".join(why) or "nothing moved since the last model run"
