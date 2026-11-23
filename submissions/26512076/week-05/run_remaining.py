"""Run week-05 negotiations against an already running market server.

Place beside agent_host.py and scenarios.json. The runner opens negotiations,
executes one buyer/seller host turn at a time, and saves actual results/logs.
It does not start the server or write REPORT.md.
"""

import argparse
import asyncio
import csv
import json
import os
import traceback
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx

from agent_host import ModelSettings, run_turn


FIELDS = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "attempted_violations", "refused_calls",
    "turns", "tool_calls", "note",
]
CONDITIONS = ("prompt", "server", "prompt_inject", "server_inject")
HERE = Path(__file__).resolve().parent


def read_completed(output, expected, settings):
    path = output / "results.csv"
    if not path.is_file() or not (output / "logs").is_dir():
        raise ValueError("--resume requires existing results.csv and logs")
    done = set()
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != FIELDS:
            raise ValueError("Existing CSV header does not match this runner")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("Incomplete CSV row; inspect the file before resuming")
            key = (row["condition"], int(row["run"]), int(row["scenario"]))
            if key not in expected or key in done:
                raise ValueError(f"Unexpected or duplicate episode: {key}")
            note = dict(part.split("=", 1) for part in row["note"].split("; ") if "=" in part)
            if (note.get("model") != settings.model or
                    float(note.get("temperature", "nan")) != settings.temperature):
                raise ValueError("Use the same model and temperature as the saved results")
            if row["outcome"] not in {"deal", "no_deal", "open"}:
                raise ValueError(f"Invalid saved outcome: {key}")
            if key[0].startswith("server") and int(row["violation"]) != 0:
                raise ValueError("Saved server violation; inspect its log before resuming")
            done.add(key)
    if path.stat().st_size and not path.read_bytes().endswith(b"\n"):
        raise ValueError("CSV must end with a newline before appending")
    return done


def preserve_interrupted(log_dir, stem):
    paths = [log_dir / f"{stem}.{suffix}" for suffix in ("txt", "json")]
    if any(path.exists() for path in paths):
        archive = log_dir / "interrupted"
        archive.mkdir(exist_ok=True)
        tag = uuid4().hex
        for path in paths:
            if path.exists():
                path.rename(archive / f"{stem}-{tag}{path.suffix}")
        print(f"[resume] preserved interrupted log: {stem}", flush=True)


async def request(client, method, path, **kwargs):
    response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def read_scenarios(path):
    scenarios = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenarios.json must contain a nonempty list")
    seen = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("Each scenario must be an object")
        if set(scenario) != {"id", "item", "reserve", "budget"}:
            raise ValueError("Each scenario needs id, item, reserve, budget only")
        if any(type(scenario[key]) is not int for key in ("id", "reserve", "budget")):
            raise ValueError("Scenario id/reserve/budget must be integers")
        if not isinstance(scenario["item"], str) or not scenario["item"].strip():
            raise ValueError("Scenario item must be a nonempty string")
        if scenario["id"] in seen:
            raise ValueError("Scenario ids must be unique")
        seen.add(scenario["id"])
    return scenarios


async def episode(client, scenario, condition, run, args, settings, log_dir):
    stem = f"{condition}-scenario{scenario['id']}-run{run}"
    log_path = log_dir / f"{stem}.txt"
    if args.resume:
        preserve_interrupted(log_dir, stem)
    opened = await request(client, "POST", "/admin/open", json={
        "scenario": scenario, "condition": condition,
    })
    nid = opened["negotiation_id"]
    tool_calls = model_calls = skipped = 0
    with log_path.open("x", encoding="utf-8") as log:
        def emit(line):
            line = str(line)
            log.write(line + "\n")
            log.flush()
            print(line, flush=True)

        emit(f"[episode] {stem} negotiation_id={nid}")
        emit("[scenario] " + json.dumps(scenario))
        emit(f"[settings] host=agent_host.py model={settings.model} "
             f"temperature={settings.temperature} max_tokens={settings.max_tokens} "
             f"max_steps={args.max_steps}")
        state = await request(client, "GET", f"/admin/state/{nid}")
        while not state["finished"] and state["status"] == "open":
            role = state["turn"]
            emit(f"[host turn] {state['host_turns'] + 1} role={role}")
            # The host receives only its own role, limit and token, not condition.
            try:
                stats = await run_turn(
                    role=role, item=scenario["item"],
                    limit=scenario["budget" if role == "buyer" else "reserve"],
                    negotiation_id=nid, party_token=opened[f"{role}_token"],
                    settings=settings, market_url=args.market_url,
                    emit=emit, max_steps=args.max_steps,
                )
            except Exception:
                emit("[host exception]\n" + traceback.format_exc())
                raise
            tool_calls += stats["tool_calls"]
            model_calls += stats["model_calls"]
            emit("[host result] " + json.dumps(stats))
            state = await request(client, "GET", f"/admin/state/{nid}")
            if not stats["moved"] and not state["finished"]:
                await request(client, "POST", f"/admin/pass/{nid}")
                skipped += 1
                emit(f"[runner pass] role={role} reason={stats['reason']}")
                state = await request(client, "GET", f"/admin/state/{nid}")

        # Do not relabel an unfinished deal as no_deal: preserve open outcomes.
        row = {
            "run": run, "condition": condition, "scenario": scenario["id"],
            "deal_possible": state["deal_possible"], "outcome": state["status"],
            "price": state["price"] if state["price"] is not None else "",
            "correct": state["correct"], "violation": state["violation"],
            "attempted_violations": state["attempted_violations"],
            "refused_calls": state["refused_calls"], "turns": state["turns"],
            "tool_calls": tool_calls,
            "note": (f"host=agent_host.py; model={settings.model}; "
                     f"temperature={settings.temperature}; skipped={skipped}; "
                     f"model_calls={model_calls}"),
        }
        emit("[result] " + json.dumps(row))
        # Tokens are deliberately excluded from saved state and text logs.
        (log_dir / f"{stem}.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        if condition.startswith("server") and state["violation"]:
            raise RuntimeError("Server enforcement failed; inspect this episode log")
    return row


async def main_async(args):
    admin_token = os.environ.get("MARKET_ADMIN_TOKEN", "")
    if not admin_token:
        raise ValueError("Set MARKET_ADMIN_TOKEN to the server's admin token")
    settings = replace(
        ModelSettings.from_env(), temperature=args.temperature,
        max_tokens=args.max_tokens,
    )
    scenarios = read_scenarios(args.scenarios)
    if args.scenario_ids:
        selected = set(args.scenario_ids)
        known = {s["id"] for s in scenarios}
        if not selected <= known:
            raise ValueError(f"Unknown scenario ids: {sorted(selected - known)}")
        scenarios = [s for s in scenarios if s["id"] in selected]
    output = args.output_dir
    # Avoid overwriting earlier experiments. Use --output-dir for a new batch.
    if not args.resume and ((output / "results.csv").exists() or (output / "logs").exists()):
        raise ValueError("Results/logs already exist; use a new --output-dir")
    expected = {(condition, run, scenario["id"])
                for condition in args.conditions for run in range(1, args.repeats + 1)
                for scenario in scenarios}
    done = read_completed(output, expected, settings) if args.resume else set()
    base = args.market_url.rstrip("/").removesuffix("/mcp")
    async with httpx.AsyncClient(
        base_url=base, timeout=20,
        headers={"Authorization": f"Bearer {admin_token}"},
    ) as client:
        await request(client, "GET", "/health")
        output.mkdir(parents=True, exist_ok=True)
        log_dir = output / "logs"
        log_dir.mkdir(exist_ok=args.resume)
        total = len(args.conditions) * len(scenarios) * args.repeats
        print(f"[runner] {total} episodes; model={settings.model}", flush=True)
        completed = len(done)
        print(f"[runner] saved={completed}; remaining={total - completed}", flush=True)
        with (output / "results.csv").open("a" if args.resume else "x", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            if not args.resume:
                writer.writeheader()
            file.flush()
            for condition in args.conditions:
                for run in range(1, args.repeats + 1):
                    for scenario in scenarios:
                        if (condition, run, scenario["id"]) in done:
                            continue
                        print(f"[progress] {completed + 1}/{total}", flush=True)
                        # Infrastructure errors stop the batch, not become model outcomes.
                        row = await episode(
                            client, scenario, condition, run, args, settings, log_dir,
                        )
                        writer.writerow(row)
                        file.flush()
                        completed += 1
        print(f"[done] {completed} episodes saved to {output.resolve()}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--scenarios", type=Path, default=HERE / "scenarios.json")
    parser.add_argument("--conditions", nargs="+", choices=CONDITIONS,
                        default=["prompt_inject", "server_inject"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenario-ids", nargs="+", type=int)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=500)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, default=HERE)
    parser.add_argument("--resume", action="store_true",
                        help="Skip saved CSV episodes and restart incomplete episodes")
    args = parser.parse_args()
    if min(args.repeats, args.max_tokens, args.max_steps) < 1:
        parser.error("repeats, max-tokens and max-steps must be positive")
    if len(set(args.conditions)) != len(args.conditions):
        parser.error("Do not repeat a condition")
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[stopped] Completed rows and logs have been preserved.", flush=True)
    except Exception as error:
        traceback.print_exc()
        raise SystemExit(f"[runner error] {type(error).__name__}: {error}") from error


if __name__ == "__main__":
    main()
