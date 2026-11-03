"""Shadow memory runner: python -m memory.run --state .market [--loop 60] [--now T]. Run from the r3 folder (like
hooks/forum.py). No LLM, no network; never imported by the decision path.

Each run reads the events appended to <R3_STATE>/events.jsonl since the byte cursor (a half-written last line waits
for the next run; at most MAX_EVENTS per run), feeds the layers in LAYERS (memory.graph, memory.gotchas,
memory.prices: ingest(ev) -> bool, to_json(), from_json(d, catalog=...); a layer whose module is missing is
skipped with a note), sweeps the graph at `now`, runs the POST_INGEST steps, then writes under <R3_STATE>/memory/:
graph_ops.jsonl (op log, append), <layer>.json (snapshots, tmp + replace) and cursor.json
{"layers": {layer: byte offset}, "seq", "ts"} last. Each layer has its own offset, so a layer that appears later (or
whose snapshot is missing) is rebuilt from the start of the ledger. Prints one line "HH:MM:SS [memory] ..." per run;
the loop outlives a failed run.
"""

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

from ledger import state_dir

from memory import source

LAYERS = (
    ("graph", "memory.graph", "Graph"),
    ("gotchas", "memory.gotchas", "Gotchas"),
    ("prices", "memory.prices", "Prices"),
)
MAX_EVENTS = 200_000

# Extension point (next wave: hourly taste forecast and its gate). Steps run after ingest and the graph sweep,
# before anything is written: step(ctx) -> short note or None, ctx = {"now", "root", "md", "mem", "layers",
# "events", "catalog"}. A step that raises is noted on the run line and the run goes on.
POST_INGEST = []


def market_dir(arg=None):
    return Path(arg or os.environ.get("MARKET_DIR", ".market"))


def write_json(path, obj):
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(tmp, path)


def load_layers(mem, catalog, notes):
    """{name: layer} from the snapshots under `mem` (fresh when missing), and {name: True if from a snapshot}."""
    layers, loaded = {}, {}
    for name, mod, cls in LAYERS:
        try:
            C = getattr(importlib.import_module(mod), cls)
        except (ImportError, AttributeError) as e:
            notes.append(f"{name} skipped ({type(e).__name__}: {str(e)[:80]})")
            continue
        d = source.read_json(mem / f"{name}.json")
        layers[name] = C.from_json(d, catalog=catalog) if d else C(catalog=catalog)
        loaded[name] = bool(d)
    return layers, loaded


def run_once(md, now=None):
    now = time.time() if now is None else now
    root = state_dir()
    mem = root / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    notes = []
    catalog = source.load_catalog(md)
    layers, loaded = load_layers(mem, catalog, notes)
    cur = source.read_json(mem / "cursor.json", {}) or {}
    offs = {
        n: int((cur.get("layers") or {}).get(n, 0)) if loaded[n] else 0 for n in layers
    }
    rows, end = source.read_new(
        root / "events.jsonl", min(offs.values(), default=0), MAX_EVENTS
    )
    used, errs = dict.fromkeys(layers, 0), dict.fromkeys(layers, 0)
    for off, ev in rows:
        for n, L in layers.items():
            if off > offs[n]:
                try:
                    used[n] += bool(L.ingest(ev))
                except Exception:  # noqa: BLE001  one bad event must not stall the layer
                    errs[n] += 1
    offs = {n: max(o, end) for n, o in offs.items()}
    G = layers.get("graph")
    if G is not None:
        G.sweep(now)
    ctx = {
        "now": now,
        "root": root,
        "md": md,
        "mem": mem,
        "layers": layers,
        "events": [e for _, e in rows],
        "catalog": catalog,
    }
    for step in POST_INGEST:
        try:
            r = step(ctx)
        except Exception as e:  # noqa: BLE001
            r = f"{getattr(step, '__name__', 'step')} error {type(e).__name__}: {str(e)[:100]}"
        if r:
            notes.append(r)
    nops = G.flush_ops(mem / "graph_ops.jsonl") if G is not None else 0
    for n, L in layers.items():
        write_json(mem / f"{n}.json", L.to_json())
    seq = rows[-1][1].get("seq") if rows else cur.get("seq")
    write_json(mem / "cursor.json", {"layers": offs, "seq": seq, "ts": now})
    notes += [f"{n}: {k} bad events skipped" for n, k in errs.items() if k]
    took = " ".join(f"{n} {k}" for n, k in used.items())
    return f"+{len(rows)} events seq={seq} ({took}) graph ops {nops}" + "".join(
        f"; {x}" for x in notes
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--state", default=None, help=".market path (default MARKET_DIR or .market)"
    )
    ap.add_argument(
        "--loop", type=float, default=0, help="seconds between runs; 0 = run once"
    )
    ap.add_argument("--now", type=float, default=None, help="clock override (tests)")
    a = ap.parse_args()
    md = market_dir(a.state)
    while True:
        try:
            why = run_once(md, a.now)
        except Exception as e:  # noqa: BLE001  the loop must outlive one bad run
            why = f"error {type(e).__name__}: {str(e)[:150]}"
        print(f"{time.strftime('%H:%M:%S')} [memory] {why}", flush=True)
        if not a.loop:
            return
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
