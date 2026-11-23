"""Shared bits of the R3 hooks: path setup, json reading, ingest + metrics wrapper."""
import json
import os
import sys
import time
from pathlib import Path

R3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R3))

import policy as P            # noqa: E402
from ledger import Ledger, market_fetcher   # noqa: E402
from metrics import Recorder  # noqa: E402


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_int=P.json_int)   # v1.16: huge ints
    except (OSError, ValueError):
        return default


def market_dir(arg=None):
    return Path(arg or os.environ.get("MARKET_DIR", ".market"))


def ingest(md, with_market=True, collect=None):
    """Ingest every source from its cursor. Market ledger only if R3_MARKET is set (GET only).
    `collect`: a list that receives the events this call appended."""
    t = time.perf_counter()
    base = os.environ.get("R3_MARKET")
    with Ledger() as L:
        counts = L.ingest_all(md, market_fetcher(base) if (base and with_market) else None,
                              os.environ.get("R3_KIT_LOG", "logs/market-kit.log"))
        seq = L.last_seq()
        if collect is not None:
            collect.extend(L.new_events)
    return seq, counts, round((time.perf_counter() - t) * 1000, 2)


def hook_event(type_, key, data, role):
    with Ledger() as L:
        L.append(type_, "hook", key, data, None, role, True)
        return L.last_seq()


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def volume_level(side, pol, now=None):
    """(relax r, settled count) for 'shop' or 'buyer' via volume.settled_counts + policy.volume_relax.
    R3_ROUND_START_TS / R3_ROUND_END_TS override the policy's round window (mock runs only; logged)."""
    import volume
    try:
        n = volume.settled_counts()[side]
    except (OSError, ValueError, KeyError):
        n = 0
    v = dict(pol.get("volume") or {})
    for k, e in (("round_start_ts", "R3_ROUND_START_TS"), ("round_end_ts", "R3_ROUND_END_TS")):
        if os.environ.get(e):
            v[k] = float(os.environ[e])
    return P.volume_relax(n, dict(pol, volume=v), now), n


_AG_REPORTED = []


def aggregates_module():
    """v1.4: `import aggregates`, failing LOUDLY. A deploy without aggregates.py used to
    turn the stats off silently (load_stats -> None, policy priors). Now an ImportError prints an ERROR line on
    stderr (the kit copies hook output to its log) and writes a metrics row {"hook": "r3", "decision": "error",
    "extra": {"what": "aggregates_import"}} (once per process); callers get (None, "IMPORT ERROR: ...") and still
    run on priors (a trade is never blocked by stats). Other errors stay per call (stats_error)."""
    try:
        import aggregates as AG
        return AG
    except ImportError as e:
        err = f"IMPORT ERROR: {type(e).__name__}: {str(e)[:140]}"
        if not _AG_REPORTED:
            _AG_REPORTED.append(err)
            print(f"[r3] ERROR aggregates import failed, stats disabled (deploy aggregates.py + cards.py, "
                  f"check MANIFEST.deploy.sha256): {e}", file=sys.stderr, flush=True)
            try:
                Recorder("r3").write("error", 0, extra={"what": "aggregates_import", "error": err, "r3_dir": str(R3)})
            except Exception:                   # noqa: BLE001
                pass
        raise ImportError(err) from e


def _err(e):
    s = str(e)
    return s if s.startswith("IMPORT ERROR") else f"{type(e).__name__}: {s[:150]}"


def load_stats(catalog=None):
    """Policy `stats` for a SHORT-LIVED hook (restock, market_buyer, review): `aggregates.StatsView`, read-only,
    category-sharded views written by the seller's Resident checkpoint (R3-01_summary.md 2). No snapshot read or
    write, no ledger replay here (the legacy open+update+save per decision was rejected). Returns
    (view or None, error or None) and never raises: no views yet / module missing / API change -> None and
    policy.py uses its priors. Views lag the ledger by at most 500 events / 30 s (view.core["seq"])."""
    try:
        AG = aggregates_module()
        V = AG.StatsView(catalog=catalog or {})
        for m in ("category", "sku", "shop"):
            if not callable(getattr(V, m, None)):
                return None, f"aggregates has no {m}()"
        return V, None
    except Exception as e:                      # noqa: BLE001
        return None, _err(e)


def open_resident(catalog=None):
    """`aggregates.Resident` for the LONG-LIVED seller: one snapshot load at start, `refresh()` (new ledger
    events only) per decision, `maybe_checkpoint()` after answering (500 events or 30 s), `close()` at exit.
    The only writer of aggregates.json + views/ in its state folder. Returns (resident or None, error)."""
    try:
        AG = aggregates_module()
        return AG.Resident(is_own=AG.owner_checker(), catalog=catalog or {}), None
    except Exception as e:                      # noqa: BLE001
        return None, _err(e)


def catch_up_stats(catalog=None):
    """Buyer folder (no resident aggregator there), R3-01_summary.md 4: `aggregates.catch_up` ONCE at process start,
    right after the ledger ingest. One snapshot read, only new events applied, views + snapshot written under
    `<state>/aggregates.lock` (non-blocking: if another process holds it nothing is written, info["wrote"] False,
    the returned stats are still fresh). Returns (stats or None, info dict or None, error or None); never raises."""
    try:
        AG = aggregates_module()
        state = os.environ.get("R3_STATE", ".r3")
        A, info = AG.catch_up(state, AG.owner_checker(state=state), catalog or {})
        return A, info, None
    except Exception as e:                      # noqa: BLE001
        return None, None, _err(e)
