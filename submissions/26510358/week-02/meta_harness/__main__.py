import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from dotenv import load_dotenv

from .clients import APIClient, Budget, BudgetExceeded, specs
from .demo import DemoClient
from .evaluation import suite
from .search import optimize

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="GPT/Gemini/Solar harness policy improvement")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="local configuration check; no API calls")
    mode.add_argument("--demo", action="store_true", help="scripted offline demonstration; not real model results")
    mode.add_argument("--run", action="store_true", help="use all three providers to search policies")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-calls", type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5 or not 1 <= args.repeats <= 10 or not 1 <= args.max_calls <= 1000:
        parser.error("rounds=1..5, repeats=1..10, max-calls=1..1000")
    load_dotenv(ROOT / ".env", override=False)
    models = specs()
    assignments = {"proposer": "gpt", "reviewer": "gemini", "refiner": "solar", "executor": "gpt"}
    missing = [s.key_env for s in models.values() if not os.getenv(s.key_env, "").strip()]
    if args.check:
        for role, name in assignments.items():
            spec = models[name]
            print(f"{role}: {spec.provider}/{spec.model}; {spec.key_env} "
                  f"{'missing' if spec.key_env in missing else 'set'}")
        print("Local configuration only; model availability/authentication not tested.")
        return 1 if missing else 0
    if missing and not args.demo:
        print("Missing local configuration: " + ", ".join(missing))
        print("No API calls made. Configure the missing providers in the local .env.")
        return 1
    training, heldout = suite()
    mode_name = "demo" if args.demo else "live"
    ident = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = ROOT / "logs" / f"meta-harness-{mode_name}" / ident
    output.mkdir(parents=True, exist_ok=False)
    budget = Budget(args.max_calls)
    manifest = {"mode": mode_name, "arguments": vars(args), "roles": assignments,
                "models": {k: asdict(v) for k, v in models.items()},
                "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(Path(__file__).parent.glob("*.py"))},
                "fixtures": {split: {c.name: hashlib.sha256(c.text.encode()).hexdigest() for c in cases}
                             for split, cases in (("training", training), ("heldout", heldout))},
                "measurement": "synthetic_demo_counters" if args.demo else "provider_reported_usage"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    ledger = {"search": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "unmetered_calls": 0},
              "executor": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "unmetered_calls": 0}}
    clients = {}
    with (output / "events.jsonl").open("x", encoding="utf-8") as stream:
        def emit(kind, **data):
            stream.write(json.dumps({"event": kind, **data}, ensure_ascii=False) + "\n")
            stream.flush()
            if kind in ("response", "demo_response"):
                bucket = ledger["executor" if data.get("role") == "executor" else "search"]
                bucket["calls"] += 1
                prefix = "synthetic_" if args.demo else ""
                values = [data.get(prefix + name) for name in ("input_tokens", "output_tokens")]
                if None in values:
                    bucket["unmetered_calls"] += 1
                for name, value in zip(("input_tokens", "output_tokens"), values):
                    bucket[name] += value or 0
            if kind in ("training_decision", "heldout_start", "heldout_decision"):
                print(f"[{mode_name}] {kind}: {data.get('reason', 'evaluating unseen cases')}", flush=True)
        try:
            factory = DemoClient if args.demo else APIClient
            clients = {role: factory(models[name], budget, emit) for role, name in assignments.items()}
            print(f"Mode={mode_name}; artifacts={output}", flush=True)
            result = optimize(clients, training, heldout, args.rounds, args.repeats, emit)
            result.update(mode=mode_name, total_model_call_attempts=budget.calls,
                          ledger=ledger, measurement=manifest["measurement"])
            (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(f"[{mode_name}] {result['status']}; call attempts={budget.calls}")
            if args.demo:
                print("OFFLINE DEMO ONLY: scripted outputs and synthetic counters; no quality claim.")
            return 1 if result["status"] == "search_failed" else 0
        except Exception as exc:
            result = {"mode": mode_name, "status": "aborted", "error_type": type(exc).__name__,
                      "total_model_call_attempts": budget.calls, "ledger": ledger}
            emit("aborted", **result)
            (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(f"[{mode_name}] aborted: {type(exc).__name__}; partial evidence preserved")
            return 1
        finally:
            for client in clients.values():
                client.close()


if __name__ == "__main__":
    sys.exit(main())
