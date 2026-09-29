"""Run the preregistered Week 05 MCP negotiation experiment."""

import argparse
import asyncio
import csv
import json
import os
import secrets
import subprocess
import sys
import traceback
from pathlib import Path

import httpx2
from dotenv import load_dotenv

from auth_checks import check
from market_host import MODEL, TEMPERATURE, take_turn

HERE = Path(__file__).resolve().parent
FIELDNAMES = ["run", "condition", "scenario", "deal_possible", "outcome",
              "price", "correct", "violation", "attempted_violations",
              "refused_calls", "turns", "tool_calls", "note"]
ORDER = [(1, "prompt_inject"), (2, "server_inject"),
         (3, "server_inject"), (4, "prompt_inject"),
         (5, "prompt_inject"), (6, "server_inject")]


def completed() -> set[tuple[str, str]]:
    path = HERE / "results.csv"
    if not path.exists():
        return set()
    with path.open(newline="") as stream:
        return {(row["run"], row["scenario"]) for row in csv.DictReader(stream)
                if row.get("outcome") in {"deal", "no_deal", "open"}}


async def wait_server(base: str) -> None:
    async with httpx2.AsyncClient(timeout=2) as http:
        for _ in range(60):
            try:
                response = await http.get(base + "/health")
                if response.status_code == 200:
                    return
            except httpx2.HTTPError:
                pass
            await asyncio.sleep(0.25)
    raise RuntimeError("market server did not become ready")


async def episode(http: httpx2.AsyncClient, base: str,
                  scenario: dict, condition: str, emit) -> dict:
    open_response = await http.post(base + "/admin/open", json={**scenario, "condition": condition})
    open_response.raise_for_status()
    opened = open_response.json()
    negotiation_id = opened["negotiation_id"]
    tokens = opened["tokens"]
    tool_calls = recovered_turns = 0
    for slot in range(1, 9):
        state_response = await http.get(base + f"/admin/state/{negotiation_id}")
        state_response.raise_for_status()
        state = state_response.json()
        if state["status"] != "open":
            break
        role = state["turn"]
        limit = scenario["budget"] if role == "buyer" else scenario["reserve"]
        emit(f"[slot] {slot} role={role}")
        moved, calls, refusals = await take_turn(
            base + "/mcp", negotiation_id, tokens[role], role,
            scenario["item"], limit, emit)
        tool_calls += calls
        if moved and refusals:
            recovered_turns += 1
            emit(f"[recovered] role={role} refused={refusals} then valid move in same turn")
        if not moved:
            emit(f"[no-move] role={role} after {calls} calls")
            skip = await http.post(base + f"/admin/skip/{negotiation_id}")
            skip.raise_for_status()
    state_response = await http.get(base + f"/admin/state/{negotiation_id}")
    state_response.raise_for_status()
    state = state_response.json()
    possible = scenario["reserve"] <= scenario["budget"]
    correct = (state["status"] == "deal" and possible and
               scenario["reserve"] <= state["price"] <= scenario["budget"] or
               state["status"] == "no_deal" and not possible)
    result = {"deal_possible": int(possible), "outcome": state["status"],
              "price": state["price"] if state["price"] is not None else "",
              "correct": int(correct), "violation": int(state["violation"]),
              "attempted_violations": state["attempted_violations"],
              "refused_calls": state["refused_calls"], "turns": state["turns"],
              "tool_calls": tool_calls,
              "note": f"mcp_host/{MODEL};temperature={TEMPERATURE};recovered_turns={recovered_turns}"}
    emit(f"[episode-result] scenario={scenario['id']} {json.dumps(result, ensure_ascii=False)}")
    return result


async def experiment(args) -> None:
    admin_key = secrets.token_urlsafe(32)
    port = int(os.getenv("MARKET_PORT", "18051"))
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "MARKET_PORT": str(port), "MARKET_ADMIN_KEY": admin_key}
    server_log = (HERE / "logs" / "server-runtime.txt").open("a")
    process = subprocess.Popen([sys.executable, str(HERE / "market_server.py")],
                               env=env, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        await wait_server(base)
        lines = await check(admin_key)
        (HERE / "auth_checks.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines), flush=True)
        scenarios = json.loads((HERE / "scenarios.json").read_text())
        done = completed()
        rows_path = HERE / ("pilot-results.csv" if args.pilot else "results.csv")
        new_file = not rows_path.exists()
        async with httpx2.AsyncClient(headers={"X-Admin-Key": admin_key}, timeout=30) as http:
            with rows_path.open("a", newline="") as stream:
                writer = csv.DictWriter(stream, FIELDNAMES, lineterminator="\n")
                if new_file:
                    writer.writeheader()
                runs = ORDER[:1] if args.pilot else ORDER
                for run, condition in runs:
                    log_name = f"pilot-{run:02d}-{condition}.txt" if args.pilot else f"run-{run:02d}-{condition}.txt"
                    with (HERE / "logs" / log_name).open("a") as log:
                        def emit(line: str) -> None:
                            print(line, file=log, flush=True)
                        emit(f"[run] {run} condition={condition} model={MODEL} temperature={TEMPERATURE}")
                        for scenario in scenarios[:1] if args.pilot else scenarios:
                            if not args.pilot and (str(run), scenario["id"]) in done:
                                continue
                            emit(f"[episode] scenario={scenario['id']} item={scenario['item']} "
                                 f"reserve={scenario['reserve']} budget={scenario['budget']}")
                            try:
                                result = await episode(http, base, scenario, condition, emit)
                            except Exception as exc:
                                emit("[crash] " + traceback.format_exc())
                                result = {name: "" for name in FIELDNAMES}
                                result["note"] = f"{type(exc).__name__}: {exc}"
                            row = {"run": run, "condition": condition,
                                   "scenario": scenario["id"], **result}
                            writer.writerow(row)
                            stream.flush()
                            print(f"run={run} condition={condition} scenario={scenario['id']} "
                                  f"outcome={row['outcome']} correct={row['correct']}", flush=True)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        server_log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is required")
    asyncio.run(experiment(args))
