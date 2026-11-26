"""Week-04 HTML lab: buyer/seller, three formats, eight messages per episode."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from pilot.transport import OpenRouterClient, read_key, redact

MAX_TURNS = 8
ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
CONDITIONS = ("free", "tagged", "structured")
HEADER = "run,condition,scenario,deal_possible,outcome,price,correct,violation,turns,format_errors,reader_calls,note".split(",")
ROLE = {
    "buyer": "You are the buyer of {item}, negotiating the price with the seller. "
             "Your private limit: you can pay at most {limit}. Never agree to a price above {limit}.",
    "seller": "You are the seller of {item}. You can accept at least {limit}. "
              "Never agree to a price below {limit}.",
}
COMMON = (" Four acts are available: propose (offer a price), accept-proposal (agree to the other side's "
          "last price, which ends the negotiation with a deal), reject-proposal (decline the last price and "
          "keep negotiating), refuse (leave the negotiation for good, no deal).")
FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": " Start your message with exactly one performative tag in parentheses, one of (propose), "
              "(accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.",
    "structured": ' Reply with exactly one JSON object and nothing else: {"performative": "propose" | '
                  '"accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}.',
}
READER_SYSTEM = ("You are an observer reading a price negotiation between a buyer and a seller. "
                 "Label the LAST message only. Reply with exactly one JSON object and nothing else: "
                 '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}.')
TEXT_FORMAT = {"type": "text"}


def schema_format(nested=False):
    price = {"type": ["integer", "null"]}
    field = "content" if nested else "price"
    value = {"type": "object", "properties": {"price": price}, "required": ["price"], "additionalProperties": False} if nested else price
    return {"type": "json_schema", "json_schema": {"name": "negotiation_message" if nested else "negotiation_reader",
        "strict": True, "schema": {"type": "object", "additionalProperties": False,
            "properties": {"performative": {"type": "string", "enum": list(ACTS)}, field: value},
            "required": ["performative", field]}}}


def system_prompt(role, scenario, condition):
    limit = scenario["budget"] if role == "buyer" else scenario["reserve"]
    return ROLE[role].format(item=scenario["item"], limit=limit) + COMMON + FORMAT[condition]


def validate_object(obj, nested=False):
    field = "content" if nested else "price"
    if not isinstance(obj, dict) or set(obj) != {"performative", field} or obj["performative"] not in ACTS:
        raise ValueError("Invalid performative or object fields")
    content = obj[field]
    if nested:
        if not isinstance(content, dict) or set(content) != {"price"}:
            raise ValueError("Invalid content fields")
        content = content["price"]
    if content is not None and (type(content) is not int or content < 0):
        raise ValueError("Price must be a nonnegative integer or null")
    return obj["performative"], content


def read_message(condition, text, transcript, call, emit, result):
    if condition == "structured":
        # HTML reference: read JSON only; a following natural-language price is ignored.
        obj, end = json.JSONDecoder().raw_decode(text.lstrip())
        act, price = validate_object(obj, nested=True)
        trailing = text.lstrip()[end:]
        if trailing.strip():
            emit("ignored_trailing_text", text=trailing)
    else:
        if condition == "tagged":
            match = re.match(r"^\((propose|accept-proposal|reject-proposal|refuse)\)", text)
            if not match:
                raise ValueError("Missing leading performative tag")
            act = match.group(1)
            if act != "propose":
                return act, None
        result["reader_calls"] += 1
        raw = call("reader", [{"role": "system", "content": READER_SYSTEM},
                              {"role": "user", "content": json.dumps(transcript, ensure_ascii=False)}], schema_format())
        emit("reader_output", text=raw)
        reader_act, price = validate_object(json.loads(raw))
        if condition == "free":
            act = reader_act
        # In tagged, the regex fixes the act; only the reader's price is used.
    if act == "propose" and price is None:
        raise ValueError("Proposal has no integer price")
    return act, price


def negotiate(scenario, condition, call, emit, result):
    history = {"buyer": [], "seller": []}
    transcript, last_price = [], {"buyer": None, "seller": None}
    role, other = "buyer", "seller"
    result.update(outcome="open", price="", turns=0, format_errors=0, reader_calls=0)
    for turn in range(1, MAX_TURNS + 1):
        text = call(role, [{"role": "system", "content": system_prompt(role, scenario, condition)}] + history[role],
                    schema_format(nested=True) if condition == "structured" else TEXT_FORMAT)
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append({"speaker": role, "text": text})
        result["turns"] = turn
        emit("message", turn=turn, speaker=role, text=text)
        try:
            act, price = read_message(condition, text, transcript, call, emit, result)
            if act == "accept-proposal" and last_price[other] is None:
                raise ValueError("Acceptance without a recorded proposal from the other party")
        except (ValueError, TypeError) as exc:
            result["format_errors"] += 1
            emit("parse_result", turn=turn, ok=False, error=str(exc))
        else:
            emit("parse_result", turn=turn, ok=True, performative=act, price=price)
            if act == "propose":
                last_price[role] = price
            elif act == "accept-proposal":
                result.update(outcome="deal", price=last_price[other])
                break
            elif act == "refuse":
                result["outcome"] = "no_deal"
                break
        role, other = other, role
    result["violation"] = int(result["outcome"] == "deal" and not scenario["reserve"] <= result["price"] <= scenario["budget"])
    result["correct"] = int((result["outcome"] == "deal" and not result["violation"])
                            or (result["outcome"] == "no_deal" and not result["deal_possible"]))
    return result


def load_completed(path):
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != HEADER:
            raise ValueError("Existing CSV has a different header")
        rows = list(reader)
    keys = [(r["run"], r["scenario"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Existing CSV has duplicate episode identities")
    return set(keys)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.suite):
        parser.error("suite must be a simple identifier")
    key = read_key(args.env_file)
    config = json.loads((HERE / "config.json").read_text())
    scenarios = json.loads((ROOT / "scenarios.json").read_text())
    ids = [s["id"] for s in scenarios]
    if len(ids) < 4 or len(set(ids)) != len(ids):
        raise ValueError("Need four unique scenarios")
    for sc in scenarios:
        if any(type(sc[k]) is not int or sc[k] < 0 for k in ("reserve", "budget")):
            raise ValueError("Scenario limits must be nonnegative integers")
    if not any(s["reserve"] <= s["budget"] for s in scenarios) or not any(s["reserve"] > s["budget"] for s in scenarios):
        raise ValueError("Need both feasible and infeasible scenarios")
    sources = [HERE / "experiment.py", HERE / "config.json", ROOT / "scenarios.json", ROOT / "pilot/transport.py"]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    out = HERE / "runs" / args.suite
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "inputs": hashes, "config": config, "scenarios": scenarios, "max_turns": MAX_TURNS,
                "repeats": 3, "jobs": args.jobs, "format": FORMAT, "roles": ROLE, "common": COMMON,
                "reader_system": READER_SYSTEM, "suite": args.suite}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["inputs"] != hashes or manifest["jobs"] != args.jobs:
            raise ValueError("Resume must use identical source and settings")
    else:
        with manifest_path.open("x", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write("\n")
    csv_path = ROOT / "results.csv"
    completed = load_completed(csv_path)
    if not csv_path.exists():
        with csv_path.open("x", newline="", encoding="utf-8") as f:
            csv.writer(f, lineterminator="\n").writerow(HEADER)
    lock = threading.Lock()
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)

    def run_condition(condition, repeat):
        run = f"{args.suite}-{condition}-{repeat:02d}"
        todo = [s for s in scenarios if (run, str(s["id"])) not in completed]
        if not todo:
            return
        client = OpenRouterClient(key, config)
        with (logs / f"{run}.jsonl").open("a", encoding="utf-8") as events, \
             (logs / f"{run}.txt").open("a", encoding="utf-8") as console:
            active_scenario = None
            def emit(event, **fields):
                record = {"time": datetime.now(timezone.utc).isoformat(), "run": run,
                          "scenario": active_scenario, "event": event, **fields}
                line = redact(json.dumps(record, ensure_ascii=False), key)
                events.write(line + "\n"); events.flush()
                if event not in ("request", "response", "usage"):
                    console.write(line + "\n"); console.flush()
                if event in ("episode_result", "crash", "http_error"):
                    with lock:
                        print(line, flush=True)
            emit("setup", manifest=manifest, condition=condition, repeat=repeat)
            for scenario in todo:
                active_scenario = scenario["id"]
                row = dict.fromkeys(HEADER, "")
                row.update(run=run, condition=condition, scenario=scenario["id"],
                           deal_possible=int(scenario["reserve"] <= scenario["budget"]))
                emit("episode_start", systems={r: system_prompt(r, scenario, condition) for r in ("buyer", "seller")})
                def call(role, messages, response_format):
                    return client.complete(messages, emit, scenario["id"], role, response_format=response_format)
                try:
                    negotiate(scenario, condition, call, emit, row)
                except Exception as exc:
                    row.update(outcome="", price="", correct="", violation="",
                               note=f"crashed: {type(exc).__name__}: {redact(str(exc), key)}")
                    emit("crash", error=row["note"])
                emit("episode_result", result=row)
                with lock:
                    with csv_path.open("a", newline="", encoding="utf-8") as f:
                        csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n").writerow(row)
                    completed.add((run, str(scenario["id"])))
    tasks = [(c, r) for r in range(1, 4) for c in CONDITIONS]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_condition, c, r) for c, r in tasks]
        for future in futures:
            future.result()
    print(f"Completed suite {args.suite}: {len(completed)} total CSV episodes", flush=True)


if __name__ == "__main__":
    main()
