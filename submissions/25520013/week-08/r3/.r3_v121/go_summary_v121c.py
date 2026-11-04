#!/usr/bin/env python3
# v121c go summary helper (READ-ONLY, stdlib only, prints SUM lines; never prints text fields or secrets).
#   --w <week-09 dir> --store <rlpo state> --state <seller R3_STATE> --runner-dir <.r3_runner_v121> --since <epoch>
# Prints: RLPO store open_watch + gates (labpc); caps breakers; LLM file paths (<W>/state/...) with row counts and the
# last real_calls rows (ts / role / model / status / latency / bytes only); llm_live metric rows (flag changes);
# runner memory stage rows and the first memory LLM decision row (stage / status / error / counts only).
import argparse
import json
import time
from pathlib import Path


def kst(t):
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(t)))
    except (TypeError, ValueError):
        return "?"


def rows(p, tail=2000):
    try:
        ls = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]
    except OSError:
        return None
    out = []
    for ln in ls:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--w", required=True)
    a.add_argument("--store")
    a.add_argument("--state", default=".r3")
    a.add_argument("--runner-dir")
    a.add_argument("--since", type=float, default=0.0)
    o = a.parse_args()
    W = Path(o.w)
    st = Path(o.state) if Path(o.state).is_absolute() else W / o.state
    if o.store:
        try:
            b = json.loads(Path(o.store).read_text(encoding="utf-8")).get("body") or {}
            print("SUM rlpo_store open_watch=%s enabled=%s gates=%s verified=%s events=%d" % (
                json.dumps(b.get("open_watch")), b.get("enabled"), json.dumps(b.get("gates"), sort_keys=True),
                json.dumps(b.get("verified")), len(b.get("events") or [])))
            src = [e for e in b.get("events") or [] if e.get("ev") in ("gate_source", "open_watch_cleared")]
            for e in src[-3:]:
                print("SUM rlpo_store_ev %s %s %s" % (kst(e.get("t")), e.get("ev"), e.get("source") or e.get("ref")))
        except (OSError, ValueError) as e:
            print("SUM rlpo_store unreadable/missing (%s)" % type(e).__name__)
    sd = W / "state"
    for n in sorted(p.name for p in sd.glob("caps_breaker*.json")) if sd.is_dir() else []:
        try:
            d = json.loads((sd / n).read_text(encoding="utf-8"))
            op = time.time() < float(d.get("off_until") or 0)
            print("SUM breaker %s streak=%s trips=%d open=%s off_until=%s" % (n, d.get("streak"), len(d.get("trips") or []),
                                                                         op, kst(d.get("off_until"))))
        except (OSError, ValueError):
            print("SUM breaker %s unreadable (= open, fail-closed)" % n)
    if not (sd.is_dir() and list(sd.glob("caps_breaker*.json"))):
        print("SUM breaker none (no caps_breaker*.json under %s = closed; files appear on the first LLM call result)" % sd)
    for n in ("real_calls.jsonl", "caps_log.jsonl", "live_call_counter.json", "real_call_counter.json"):
        p = sd / n
        if not p.exists():
            print("SUM llm_file %s missing" % p)
            continue
        if n.endswith(".jsonl"):
            r = rows(p, 100000) or []
            new = [x for x in r if (x.get("ts") or 0) >= o.since]
            print("SUM llm_file %s rows=%d since_restart=%d" % (p, len(r), len(new)))
            if n == "real_calls.jsonl":
                for x in r[-3:]:
                    print("SUM real_call %s role=%s model=%s status=%s latency=%s bytes=%s" % (
                        kst(x.get("ts")), x.get("role"), x.get("model"), x.get("status"), x.get("latency_s"), x.get("prompt_bytes")))
        else:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                print("SUM llm_file %s %s" % (p, json.dumps({k: v for k, v in d.items() if isinstance(v, int)}, sort_keys=True)))
            except (OSError, ValueError):
                print("SUM llm_file %s unreadable" % p)
    m = rows(st / "metrics.jsonl") or []
    lv = [x for x in m if x.get("decision") == "llm_live" or x.get("event") == "llm_live" or "llm_live" in json.dumps(x)[:200]]
    print("SUM llm_live_metric rows=%d (seller metric on a live-flag change, LIVE.refresh)" % len(lv))
    for x in lv[-3:]:
        print("SUM llm_live %s %s" % (kst(x.get("ts")), json.dumps({k: v for k, v in x.items() if k not in ("ts",)}, sort_keys=True)[:200]))
    if o.runner_dir:
        rr = rows(Path(o.runner_dir) / "runner.jsonl") or []
        mem = [x for x in rr if x.get("stage") == "memory" and (x.get("t") or x.get("ts") or 0) >= o.since]
        print("SUM runner_rows=%d memory_rows=%d" % (len(rr), len(mem)))
        for x in mem[:1]:
            print("SUM memory_first " + json.dumps(x, sort_keys=True)[:300])
    dl = rows(st / "memory" / "llm_decisions.jsonl") or []
    dl = [x for x in dl if (x.get("t") or 0) >= o.since]
    if dl:
        x = dl[0]
        print("SUM memory_llm_first %s status=%s error=%s keys=%s" % (kst(x.get("t")), x.get("status"), x.get("error"),
                                                                     sorted(x)[:12]))
    else:
        print("SUM memory_llm_first none yet (%s)" % (st / "memory" / "llm_decisions.jsonl"))
    return 0


if __name__ == "__main__":
    main()
