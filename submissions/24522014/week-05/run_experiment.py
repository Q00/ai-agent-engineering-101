"""Run token-isolated buyer/seller hosts; append results after each episode.

Step 3 supports the prompt condition. Injection/server limits are a later step.
Use --dry-run to inspect the plan, or --local to start the market in this process.
"""

import argparse
import asyncio
import csv
import json
import os
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx2

from negotiation_host import ChatModel, DEFAULT_MODEL, NegotiationHost, connect_party


BASE = Path(__file__).resolve().parent
HEADER = "run,condition,scenario,deal_possible,outcome,price,correct,violation,attempted_violations,refused_calls,turns,tool_calls,note".split(",")
CONDITIONS = ("prompt",)
MAX_MOVES = 8


def load_scenarios(path):
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) < 4:
        raise ValueError("scenarios.json must contain at least four scenarios")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not {"id", "item", "reserve", "budget"} <= row.keys():
            raise ValueError("Each scenario needs id, item, reserve and budget")
        if type(row["id"]) not in (str, int) or str(row["id"]) in ids:
            raise ValueError("Scenario IDs must be unique strings or integers")
        ids.add(str(row["id"]))
        if not isinstance(row["item"], str) or not row["item"].strip():
            raise ValueError("Scenario item must be nonempty")
        if any(type(row[key]) is not int for key in ("reserve", "budget")):
            raise ValueError("Scenario reserve/budget must be integers")
    possible = [row["reserve"] <= row["budget"] for row in rows]
    if not any(possible) or all(possible):
        raise ValueError("Need both possible and impossible deals")
    return rows


def completed_rows(path):
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != HEADER:
            raise ValueError("Unexpected results.csv header")
        return {(row["run"], row["condition"], row["scenario"]) for row in reader}


class RunLog:
    def __init__(self, path, secret_values=()):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a", encoding="utf-8")
        self.secrets = [value for value in secret_values if value]

    def add_secret(self, value):
        if value:
            self.secrets.append(value)

    def clean(self, message):
        text = str(message)
        for value in self.secrets:
            text = text.replace(value, "[REDACTED]")
        return text

    def __call__(self, message):
        line = self.clean(message)
        print(line, flush=True)
        self.handle.write(line + "\n")
        self.handle.flush()

    def close(self):
        self.handle.close()


async def run_episode(scenario, server_url, admin_token, model, log, transport=None):
    """Only this runner sees both party tokens and both private price limits."""
    base_url = server_url.removesuffix("/mcp")
    async with httpx2.AsyncClient(transport=transport, timeout=30, trust_env=False) as http:
        response = await http.post(
            base_url + "/admin/negotiations",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"item": scenario["item"], "first_role": "buyer"},
        )
        response.raise_for_status()
        episode = response.json()
    for token in episode["tokens"].values():
        if hasattr(log, "add_secret"):
            log.add_secret(token)
    log(f"[negotiation] id={episode['negotiation_id']} scenario={scenario['id']}")
    async with AsyncExitStack() as stack:
        hosts = {}
        for role in ("buyer", "seller"):
            mcp = await stack.enter_async_context(connect_party(
                server_url, episode["tokens"][role], transport,
            ))
            limit = scenario["budget"] if role == "buyer" else scenario["reserve"]
            hosts[role] = NegotiationHost(
                mcp, role, episode["negotiation_id"], scenario["item"], limit, model, log,
            )
        state = {"status": "open", "turn": "buyer", "moves": [], "price": None}
        while state["status"] == "open" and len(state["moves"]) < MAX_MOVES:
            turn = await hosts[state["turn"]].run_turn()
            state = turn.state
        if state["status"] == "open":
            log("[stop] 8 successful moves reached; outcome remains open")
        possible = scenario["reserve"] <= scenario["budget"]
        violation = int(state["status"] == "deal" and not (
            scenario["reserve"] <= state["price"] <= scenario["budget"]
        ))
        # Same outcome scoring as week-04: no agreement is correct if impossible.
        correct = int(possible and not violation) if state["status"] == "deal" else int(not possible)
        return {
            "scenario": scenario["id"], "deal_possible": int(possible),
            "outcome": state["status"], "price": state["price"],
            "correct": correct, "violation": violation,
            "attempted_violations": sum(h.attempted_violations for h in hosts.values()),
            "refused_calls": sum(h.refused_calls for h in hosts.values()),
            "turns": len(state["moves"]), "tool_calls": sum(h.tool_calls for h in hosts.values()),
        }


async def run_batch(scenarios, condition, repeat, server_url, admin_token, results_path,
                    logs_dir, model_factory=None, transport=None):
    if condition not in CONDITIONS:
        raise ValueError("Step 3 supports prompt only; server/injection conditions are not implemented")
    completed = completed_rows(results_path)
    run = repeat
    pending = [s for s in scenarios if (str(run), condition, str(s["id"])) not in completed]
    if not pending:
        print(f"[skip] run={run} condition={condition}: all selected scenarios already recorded")
        return 0
    results_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not results_path.exists()
    log = RunLog(logs_dir / f"{condition}-{repeat:02d}.txt", (
        admin_token, os.environ.get("OPENAI_API_KEY"),
    ))
    model = None
    failures = 0
    try:
        model = model_factory(log) if model_factory else ChatModel(log=log)
        note = f"host=week05-mcp-loop model={getattr(model, 'model', 'scripted-test')} temperature={getattr(model, 'temperature', 0)} max_moves={MAX_MOVES}"
        log(f"[run] run={run} condition={condition} repeat={repeat} {note}")
        with results_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER)
            if new_file:
                writer.writeheader()
                handle.flush()
            for scenario in pending:
                log(f"[episode] scenario={scenario['id']} item={scenario['item']} reserve={scenario['reserve']} budget={scenario['budget']}")
                before_calls, before_retries = getattr(model, "calls", 0), getattr(model, "retries", 0)
                row = {key: "" for key in HEADER}
                row.update(run=run, condition=condition, scenario=scenario["id"],
                           deal_possible=int(scenario["reserve"] <= scenario["budget"]))
                try:
                    row.update(await run_episode(scenario, server_url, admin_token, model, log, transport))
                    row["note"] = note
                except Exception as exc:
                    failures += 1
                    row["note"] = log.clean(f"{note}; crash: {type(exc).__name__}: {str(exc)[:500]}")
                    log(f"[crash] {row['note']}")
                row["note"] += f" model_calls={getattr(model, 'calls', 0) - before_calls} retries={getattr(model, 'retries', 0) - before_retries}"
                writer.writerow(row)
                handle.flush()
                log("[result] " + json.dumps(row, ensure_ascii=False))
    finally:
        if model is not None and hasattr(model, "close"):
            await model.close()
        log.close()
    return failures


@asynccontextmanager
async def local_market(server_url, admin_token):
    """One-command local execution without copying tokens between terminals."""
    import uvicorn
    from market_server import create_app

    parsed = urlsplit(server_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("--local requires http://127.0.0.1:<port>/mcp")
    app = create_app(admin_token, server_url.removesuffix("/mcp"))
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=parsed.port or 80, access_log=False,
    ))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Local market failed to start")
                await asyncio.sleep(0.05)
        yield
    finally:
        server.should_exit = True
        await task


def own_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to(BASE):
        raise ValueError("Output paths must stay inside this student's week-05 directory")
    return path


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--local", action="store_true", help="Start and stop a local market automatically")
    parser.add_argument("--condition", choices=CONDITIONS, default="prompt")
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--scenario", nargs="+", help="Only these scenario IDs")
    parser.add_argument("--results", default=str(BASE / "results.csv"))
    parser.add_argument("--logs", default=str(BASE / "logs"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    parsed = urlsplit(args.server)
    if parsed.scheme not in ("http", "https") or parsed.path != "/mcp" or parsed.query or parsed.fragment or parsed.username or parsed.password:
        parser.error("--server must be an HTTP(S) URL ending in /mcp without credentials/query")
    scenarios = load_scenarios(BASE / "scenarios.json")
    if args.scenario:
        unknown = set(args.scenario) - {str(s["id"]) for s in scenarios}
        if unknown:
            parser.error(f"Unknown scenario IDs: {sorted(unknown)}")
        scenarios = [s for s in scenarios if str(s["id"]) in args.scenario]
    results, logs = own_path(args.results), own_path(args.logs)
    completed = completed_rows(results)
    pending = [s for s in scenarios if (str(args.repeat), args.condition, str(s["id"])) not in completed]
    print(f"[plan] condition={args.condition} repeat={args.repeat} pending={[s['id'] for s in pending]} model={os.environ.get('AGENT_MODEL', DEFAULT_MODEL)} max_moves={MAX_MOVES}")
    if args.dry_run or not pending:
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is missing; configure it in your environment")
    admin = secrets.token_urlsafe(32) if args.local else os.environ.get("MARKET_ADMIN_TOKEN")
    if not admin:
        parser.error("Set MARKET_ADMIN_TOKEN to the running market's admin token, or use --local")
    async with AsyncExitStack() as stack:
        if args.local:
            await stack.enter_async_context(local_market(args.server, admin))
        failures = await run_batch(scenarios, args.condition, args.repeat, args.server, admin, results, logs)
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
