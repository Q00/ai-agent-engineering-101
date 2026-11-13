"""Week 05 runner: one run = one condition, one repeat, all scenarios.

    OPENROUTER_API_KEY=<key> python runner.py --condition server_inject --repeat 1 [--port 8801]

For each run the runner
1. starts its own market server (server.py) on --port with a fresh random admin key
   that lives only in this process and the child's environment; the server's JSON
   log goes to server_logs/<run>.txt
2. per scenario: opens a negotiation (/admin/open), then gives the turn to the party
   whose turn it is, one host run per turn, with that party's token
3. a host run that ends without a move the server accepted -> /admin/pass (decided from
   the server's move count, not from the model's final JSON)
4. the episode ends when the server says deal/no_deal, at 8 moves, or at 16 host runs
   (a cap this runner adds; recorded as stop=host_run_cap)
5. reads /admin/summary and appends one row to results.csv

Everything the hosts did (every model reply, tool call, tool result, refusal) goes to
logs/<run>.txt. Rows already in results.csv for (run, condition, scenario) are skipped,
so an interrupted run continues. A crashed episode keeps its row with blank fields
and the error in `note`.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx2

from host import HOST_VERSION, run_turn, system_prompt
from llm import LLM, MODEL, PROVIDER, REASONING, sha
from market import CONDITIONS, MAX_MOVES

HERE = Path(__file__).resolve().parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct", "violation",
          "attempted_violations", "refused_calls", "turns", "tool_calls", "note"]
MAX_HOST_RUNS = 16
HOST = f"week01-loop-mcp-{HOST_VERSION}"


class Log:
    def __init__(self, path: Path):
        self.f = path.open("a", encoding="utf-8")

    def __call__(self, event: str, **fields):
        line = f"{time.strftime('%H:%M:%S')} [{event}] {json.dumps(fields, ensure_ascii=False)}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def done_rows(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["condition"], r["scenario"]) for r in csv.DictReader(f)}


def append_row(path: Path, row: dict):
    """Runs may go in parallel (one process per run): lock, then write header if empty."""
    with path.open("a", encoding="utf-8", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        w = csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n")
        if f.tell() == 0:
            w.writeheader()
        w.writerow(row)
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)


def start_server(port: int, admin_key: str, log_path: Path) -> subprocess.Popen:
    env = dict(os.environ, MARKET_ADMIN_KEY=admin_key, MARKET_URL=f"http://127.0.0.1:{port}")
    env.pop("OPENROUTER_API_KEY", None)          # the market never needs the model key
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], cwd=HERE, env=env,
                            stdout=log_path.open("a", encoding="utf-8"), stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            httpx2.get(f"http://127.0.0.1:{port}/admin/summary", timeout=1)
            return proc
        except httpx2.TransportError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("market server did not start")


async def episode(base: str, admin: dict, run: str, condition: str, sc: dict, llm: LLM, log: Log) -> dict:
    opened = httpx2.post(f"{base}/admin/open", headers=admin, json={
        "item": sc["item"], "reserve": sc["reserve"], "budget": sc["budget"], "condition": condition}).json()
    nid, tokens = opened["negotiation_id"], opened["tokens"]
    log("episode_start", run=run, condition=condition, scenario=sc["id"], item=sc["item"],
        reserve=sc["reserve"], budget=sc["budget"], negotiation_id=nid,
        prompt_sha={r: sha(system_prompt(r, sc)) for r in ("buyer", "seller")})

    def summary():
        return httpx2.get(f"{base}/admin/summary", params={"negotiation_id": nid}, headers=admin).json()

    tool_calls = host_runs = refusals = recovered = nudges = 0
    stop = None
    while True:
        s = summary()
        if s["outcome"] != "open":
            stop = s["outcome"]
            break
        if s["turns"] >= MAX_MOVES:
            stop = "turn_limit"
            break
        if host_runs >= MAX_HOST_RUNS:
            stop = "host_run_cap"
            break
        role = s["turn"]
        host_runs += 1
        hr = f"{run}/s{sc['id']}/h{host_runs:02d}"
        r = await run_turn(f"{base}/mcp", tokens[role], nid, role, sc, llm, log, hr)
        tool_calls += r["tool_calls"]
        refusals += r["limit_refusals"]
        recovered += r["refusals_followed_by_valid_move"]
        nudges += r["nudges"]
        if summary()["turns"] == s["turns"]:           # the server accepted no move this run
            p = httpx2.post(f"{base}/admin/pass", headers=admin, json={"negotiation_id": nid}).json()
            log("runner_pass", host_run=hr, role=role, **p)
    s = summary()
    log("episode_result", run=run, scenario=sc["id"], stop=stop, host_runs=host_runs,
        host_tool_calls=tool_calls, refusals_followed_by_valid_move=recovered, nudges=nudges, server_summary=s)
    return {"s": s, "tool_calls": tool_calls, "host_runs": host_runs, "stop": stop, "nudges": nudges,
            "refusals": refusals, "recovered": recovered}


async def main(a):
    if a.condition not in CONDITIONS:
        raise SystemExit(f"condition must be one of {list(CONDITIONS)}")
    run = f"{a.condition}-r{a.repeat}"
    scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    results, logs = HERE / a.results, HERE / a.log_dir
    logs.mkdir(parents=True, exist_ok=True)
    server_logs = HERE / a.server_log_dir
    server_logs.mkdir(parents=True, exist_ok=True)
    todo = [sc for sc in scenarios if (run, a.condition, str(sc["id"])) not in done_rows(results)]
    if not todo:
        print(f"{run}: all scenarios already in {a.results}")
        return
    log = Log(logs / f"{run}.txt")
    admin_key = secrets.token_hex(16)                # in memory only
    admin = {"X-Admin-Key": admin_key}
    base = f"http://127.0.0.1:{a.port}"
    proc = start_server(a.port, admin_key, server_logs / f"{run}.txt")
    if a.scripted:                                   # smoke test: no API call
        from scripted_llm import ScriptedLLM
        llm, model = ScriptedLLM(log, overshoot=a.overshoot, lazy=a.lazy), f"scripted(overshoot={a.overshoot},lazy={a.lazy})"
    else:
        llm, model = LLM(log), MODEL
    log("run_start", run=run, condition=a.condition, host=HOST, model=model, provider=PROVIDER, reasoning=REASONING,
        temperature="not sent (unsupported by the model)", max_moves=MAX_MOVES,
        max_host_runs=MAX_HOST_RUNS, scenarios=[sc["id"] for sc in todo])
    try:
        for sc in todo:
            calls_before = llm.calls
            try:
                e = await episode(base, admin, run, a.condition, sc, llm, log)
                s = e["s"]
                row = {"run": run, "condition": a.condition, "scenario": sc["id"],
                       "deal_possible": s["deal_possible"], "outcome": s["outcome"],
                       "price": "" if s["price"] is None else s["price"], "correct": s["correct"],
                       "violation": s["violation"], "attempted_violations": s["attempted_violations"],
                       "refused_calls": s["refused_calls"], "turns": s["turns"], "tool_calls": e["tool_calls"],
                       "note": (f"host={HOST}; model={model}; reasoning={REASONING}; passes={s['passes']}; "
                                f"host_runs={e['host_runs']}; nudges={e['nudges']}; stop={e['stop']}; "
                                f"refusals_followed_by_valid_move={e['recovered']}/{e['refusals']}; "
                                f"llm_calls={llm.calls - calls_before}")}
            except Exception as exc:                 # crashed episode: keep the row, blank fields
                log("episode_crash", run=run, scenario=sc["id"], error=f"{type(exc).__name__}: {exc}"[:500])
                row = {k: "" for k in HEADER}
                row.update(run=run, condition=a.condition, scenario=sc["id"],
                           deal_possible=int(sc["reserve"] <= sc["budget"]),
                           note=f"host={HOST}; model={model}; crashed: {type(exc).__name__}: {exc}"[:400])
            append_row(results, row)
    finally:
        proc.terminate()
        proc.wait(5)
        log("run_end", run=run, llm_calls=llm.calls)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True)
    ap.add_argument("--repeat", type=int, required=True)
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--results", default="results.csv")
    ap.add_argument("--log-dir", default="logs")
    ap.add_argument("--server-log-dir", default="server_logs")
    ap.add_argument("--scripted", action="store_true", help="smoke test with scripted_llm, no API call")
    ap.add_argument("--overshoot", type=int, default=0, help="scripted buyer accepts up to budget+N")
    ap.add_argument("--lazy", action="store_true", help="scripted model first answers with a summary, no move")
    asyncio.run(main(ap.parse_args()))
