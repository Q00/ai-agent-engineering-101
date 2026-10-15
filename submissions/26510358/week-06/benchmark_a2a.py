"""Read-only peer-source A2A comparisons with fresh, immutable execution evidence.

Fixed-counterpart tests compare roles; cross trades test interoperability.
Rehosted Codex results are never labelled original-provider results.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

ROOT = Path(__file__).resolve().parent
RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}
RUNNER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _reject_json_constant(value):
    raise ValueError(f"non-JSON numeric constant: {value}")


def _finite_json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numeric value exceeds the finite float range")
    return number


def _rpc_task(body):
    result = body.get("result") if isinstance(body, dict) else None
    return result.get("task", result) if isinstance(result, dict) else None


def extract_receipts(rows):
    """Collect correlated, successful A2A completion evidence without running agents.

    Re-reading one immutable completion is one transaction. Changed completions and
    multiple receipt parts are diagnosed separately instead of being deduplicated away.
    The original response bytes remain in the wire log even when they are not evidence.
    """
    receipts, records, completed = {}, [], {}
    violations, rejected = [], []
    duplicates = conflicts = 0
    for row in rows:
        if row.get("method") != "POST":
            continue
        request, response = row.get("request"), row.get("response")
        if not isinstance(request, dict) or request.get("method") not in {"SendMessage", "GetTask"}:
            continue
        status = row.get("status")
        reason = None
        if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status < 300:
            reason = "unsuccessful HTTP response"
        elif not isinstance(response, dict):
            reason = "malformed JSON-RPC response"
        elif request.get("jsonrpc") != "2.0" or response.get("jsonrpc") != "2.0":
            reason = "wrong JSON-RPC version"
        elif (type(request.get("id")) not in (str, int)
              or type(response.get("id")) is not type(request.get("id"))
              or response.get("id") != request.get("id")):
            reason = "uncorrelated JSON-RPC response"
        elif "error" in response:
            reason = "JSON-RPC error response"
        if reason:
            rejected.append(reason)
            continue
        task = _rpc_task(response)
        if not isinstance(task, dict):
            rejected.append("missing A2A task")
            continue
        task_status = task.get("status")
        if not isinstance(task_status, dict) or task_status.get("state") != "TASK_STATE_COMPLETED":
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            violations.append("completed task has no valid id")
            continue
        params = request.get("params")
        params = params if isinstance(params, dict) else {}
        message = params.get("message")
        requested_id = (message.get("taskId") if isinstance(message, dict) else None)
        if request["method"] == "GetTask":
            requested_id = params.get("id", params.get("taskId"))
            if not isinstance(requested_id, str) or not requested_id:
                rejected.append("GetTask has no valid requested task id")
                continue
        if requested_id is not None and requested_id != task_id:
            violations.append("response task differs from requested task")
            continue
        found = []
        artifacts = task.get("artifacts", [])
        if not isinstance(artifacts, list):
            violations.append("malformed completed artifacts")
            artifacts = []
        for artifact in artifacts:
            parts = artifact.get("parts", []) if isinstance(artifact, dict) else []
            if not isinstance(parts, list):
                violations.append("malformed completed artifact parts")
                continue
            for part in parts:
                data = part.get("data") if isinstance(part, dict) else None
                if isinstance(data, dict) and "price" in data:
                    try:
                        encoded = json.dumps(data, sort_keys=True, allow_nan=False)
                    except (TypeError, ValueError):
                        violations.append("receipt contains invalid JSON numeric value")
                        continue
                    found.append((encoded, data))
        if len(found) != 1:
            violations.append("missing completed receipt" if not found
                              else "multiple receipt parts in completed task")
        signature = tuple(sorted(encoded for encoded, _ in found))
        if task_id in completed:
            if completed[task_id] == signature:
                duplicates += 1
            else:
                conflicts += 1
                violations.append("conflicting completed receipts for one task")
        else:
            completed[task_id] = signature
        for encoded, data in found:
            key = (task_id, encoded)
            if key not in receipts:
                receipts[key] = data
                records.append({"task_id": task_id, "receipt": data})
    return {"receipts": list(receipts.values()), "receipt_records": records,
            "completed_task_ids": list(completed),
            "duplicate_completed_responses": duplicates,
            "conflicting_completed_responses": conflicts,
            "safety_violations": list(dict.fromkeys(violations)),
            "rejected_wire_responses": rejected}


def classify_outcome(spec, wire, *, buyer_exit_code=None, buyer_result=None,
                     fixed_buyer=None, timed_out=False):
    """Classify evidence, keeping buyer acceptance distinct from remote completion."""
    receipts = wire["receipts"]
    violations = list(wire["safety_violations"])
    if len(receipts) > 1:
        violations.append("multiple completed receipts")
    if len(wire["completed_task_ids"]) > 1:
        violations.append("multiple completed tasks")
    reserve, budget = RESERVES[spec["item"]], spec["budget"]
    for record in wire["receipt_records"]:
        receipt = record["receipt"]
        price = receipt.get("price")
        if not _number(price):
            violations.append("invalid receipt price")
        elif not reserve <= price <= budget:
            violations.append("price outside reserve/budget")
        aliases = [receipt[key] for key in ("item", "itemId", "item_id") if key in receipt]
        if not aliases:
            violations.append("missing receipt item")
        elif any(not isinstance(value, str) or value != spec["item"] for value in aliases):
            violations.append("wrong or inconsistent receipt item")
        if any(receipt[key] != record["task_id"] for key in ("taskId", "task_id") if key in receipt):
            violations.append("receipt task differs from completed task")
    price = receipts[0].get("price") if len(receipts) == 1 else None
    price = price if _number(price) else None
    status = buyer_result.get("status") if isinstance(buyer_result, dict) else None
    if status is not None:
        accepted = status == "completed"
        scope = "buyer_report_and_wire_receipt"
        reported = buyer_result.get("receipt")
        if accepted and isinstance(reported, dict) and len(receipts) == 1:
            if not _number(reported.get("price")) or reported["price"] != price:
                violations.append("buyer report price differs from wire receipt")
            if any(reported[key] != spec["item"] for key in ("item", "itemId", "item_id") if key in reported):
                violations.append("buyer report item differs from wire receipt")
            if any(reported[key] != wire["receipt_records"][0]["task_id"]
                   for key in ("taskId", "task_id") if key in reported):
                violations.append("buyer report task differs from wire receipt")
    elif spec["buyer"] == "kit":
        task = fixed_buyer.get("task") if isinstance(fixed_buyer, dict) else None
        final_status = task.get("status") if isinstance(task, dict) else None
        accepted = (isinstance(final_status, dict)
                    and final_status.get("state") == "TASK_STATE_COMPLETED"
                    and task.get("id") in wire["completed_task_ids"])
        scope = "fixed_buyer_final_task_and_wire_receipt"
    else:
        accepted = spec["buyer"] != "ours"
        scope = "wire_receipt_only" if accepted else "missing_buyer_report"
    valid = bool(price is not None and len(receipts) == 1
                 and len(wire["completed_task_ids"]) == 1 and accepted
                 and buyer_exit_code == 0 and not timed_out and not violations)
    return {"price": price, "wire_completed": bool(wire["completed_task_ids"]),
            "safety_violations": list(dict.fromkeys(violations)),
            "evidence_scope": scope, "buyer_reported_status": status,
            "completed_valid": valid,
            "buyer_surplus": budget - price if valid else 0,
            "seller_surplus": price - reserve if valid else 0,
            "optimal_buyer_price": valid and price == reserve,
            "optimal_seller_price": valid and price == budget}


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, allow_nan=False, indent=2)
        out.write("\n")


def source_hashes(root):
    root = Path(root)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()
            and not any(part in {"logs", ".venv", "__pycache__", ".git"}
                        for part in p.relative_to(root).parts)
            and p.suffix in {".py", ".json", ".txt", ".toml"}}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def descendants(pid):
    try:
        children = [int(p) for p in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
    except OSError:
        return []
    return [value for child in children for value in [child, *descendants(child)]]


def stop(process):
    if process is None or process.poll() is not None:
        return
    owned = descendants(process.pid)
    for pid in [process.pid, *owned]:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        for pid in [*owned, process.pid]:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=3)


def wait_card(url, process=None, seconds=25):
    deadline = time.monotonic() + seconds
    with httpx.Client(trust_env=False, timeout=1) as client:
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"server exited: {process.returncode}")
            try:
                result = client.get(url + "/.well-known/agent-card.json")
                result.raise_for_status()
                return result.json()
            except (httpx.HTTPError, ValueError):
                time.sleep(0.1)
    raise TimeoutError("Agent Card readiness timeout")


class LocalApp:
    def __init__(self, app, port):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                                  log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


class WireProxy:
    """Only route addresses change; original cards and all RPC payloads are saved."""
    def __init__(self, backend, directory):
        self.backend = backend.rstrip("/")
        self.directory = Path(directory)
        self.directory.mkdir()
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.rows = []
        self.lock = threading.Lock()
        self.local = LocalApp(Starlette(routes=[Route("/{path:path}", self.handle,
                                                       methods=["GET", "POST"])]), self.port)

    async def handle(self, request: Request):
        body = await request.body()
        started = time.monotonic()
        headers = {key: request.headers[key] for key in ("content-type", "a2a-version")
                   if key in request.headers}
        target = self.backend + request.url.path
        if request.url.query:
            target += "?" + request.url.query
        row = {"id": uuid.uuid4().hex, "method": request.method,
               "path": request.url.path, "headers": headers,
               "request": self.decode(body)}
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=240) as client:
                response = await client.request(request.method, target, content=body, headers=headers)
            row.update(status=response.status_code, response=self.decode(response.content))
            content = response.content
            if request.method == "GET" and request.url.path.endswith("agent-card.json"):
                card = response.json()
                for interface in card.get("supportedInterfaces", []):
                    old = interface.get("url", "")
                    if old.startswith(self.backend):
                        interface["url"] = self.url + old[len(self.backend):]
                content = json.dumps(card).encode()
                row["served_card"] = card
            return Response(content, status_code=response.status_code,
                            media_type=response.headers.get("content-type", "application/json"))
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            return Response(json.dumps({"error": row["error"]}), status_code=502,
                            media_type="application/json")
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)
            with self.lock:
                self.rows.append(row)
                write_new(self.directory / f"{len(self.rows):03d}-{row['id']}.json", row)

    @staticmethod
    def decode(body):
        try:
            return json.loads(body, parse_constant=_reject_json_constant, parse_float=_finite_json_float)
        except ValueError:
            return body.decode(errors="replace")

    def receipts(self):
        return self.evidence()["receipts"]

    def evidence(self):
        with self.lock:
            rows = list(self.rows)
        return extract_receipts(rows)

    def close(self):
        self.local.close()


def load_kit(path):
    spec = importlib.util.spec_from_file_location("week06_comparison_kit", path)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    return kit


def command(participant, role, **values):
    config = json.loads((Path(participant["path"]) / "agent.json").read_text())
    argv = shlex.split(config[role]["cmd"].format(**values))
    if argv[0] in {"python", "python3", ".venv/bin/python", ".venv/bin/python3"}:
        argv[0] = sys.executable
    return argv


def environment(participant, directory, mode, api_url=None):
    env = os.environ.copy()
    for key in ("A2A_POLICY_PATH", "A2A_HARNESS_COMMAND", "A2A_MODEL"):
        env.pop(key, None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["A2A_TRACE_DIR"] = str(directory / "agent-trace")
    env["A2A_QUEUE_TRACE"] = str(directory / "queue.jsonl")
    if mode == "controlled" and participant["id"] != "ours":
        env["PATH"] = str(ROOT / "benchmark_bin") + os.pathsep + env["PATH"]
        env["A2A_BENCH_CODEX"] = shutil.which("codex") or "codex"
        env["A2A_BENCH_PARTICIPANT"] = participant["id"]
        env["A2A_BENCH_TRACE_DIR"] = str(directory / "transport")
        env["OPENAI_BASE_URL"] = api_url or "http://127.0.0.1:1/v1"
        env["OPENAI_API_KEY"] = "local-evaluation-not-a-secret"
        env["AGENT_MODEL"] = "gpt-6-sol"
    return env


def start_participant(participant, directory, mode, api_url):
    port = free_port()
    cmd = command(participant, "seller", port=port)
    out = (directory / "seller-console.txt").open("xb")
    proc = subprocess.Popen(cmd, cwd=participant["path"], env=environment(participant, directory, mode, api_url),
                            stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    url = f"http://127.0.0.1:{port}"
    try:
        card = wait_card(url, proc)
        write_new(directory / "original-card.json", card)
    except BaseException:
        stop(proc)
        out.close()
        raise
    return proc, out, url, cmd


def parsed_result(text):
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line, parse_constant=_reject_json_constant, parse_float=_finite_json_float)
            if isinstance(value, dict) and value.get("status") in {"completed", "disputed", "no_deal"}:
                return value
        except ValueError:
            pass
    return None


def run_match(spec, participants, kit, out, mode, api_url):
    directory = out / spec["id"]
    directory.mkdir()
    started = time.monotonic()
    def execution_scope(name):
        if name == "kit":
            return "fixed_counterpart"
        if name == "pr288-workers":
            return "native_hosted_workers_ai"
        if name == "ours":
            return "native_implementation"
        return "controlled_codex_transport" if mode == "controlled" else "native_provider_environment"

    result = {**spec, "mode": mode, "runner_sha256": RUNNER_SHA256,
              "execution_scope": {role: execution_scope(spec[role]) for role in ("buyer", "seller")},
              "started": datetime.now(timezone.utc).isoformat()}
    server = stream = local = proxy = buyer_process = None
    record = {"messages": 0, "continued": 0, "offers": [], "deals": [], "canceled": 0,
              "posts": 0, "bad_version": 0}
    print(json.dumps({"event": "match_started", **result}), flush=True)
    try:
        if spec["seller"] == "kit":
            port = free_port()
            local = LocalApp(kit.kit_seller_app(port, record), port)
            url = f"http://127.0.0.1:{port}"
            wait_card(url)
        elif spec["seller"] == "pr288-workers":
            url = "https://a2a-lamp-shop.thisjonghoon.workers.dev"
            write_new(directory / "original-card.json", wait_card(url))
        else:
            server, stream, url, cmd = start_participant(participants[spec["seller"]], directory,
                                                        mode, api_url)
            result["seller_command"] = cmd
        proxy = WireProxy(url, directory / "wire")
        wait_card(proxy.url)
        if spec["buyer"] == "kit":
            reserve = RESERVES[spec["item"]]
            scenario = {"item": spec["item"], "budget": spec["budget"],
                        "ladder": [round(reserve * scale) for scale in (.6, .8, 1, 1.1, 1.2)]}
            fixed_result = kit.negotiate(proxy.url, scenario)
            result["fixed_buyer"] = fixed_result
            result["buyer_exit_code"] = 0
        else:
            participant = participants[spec["buyer"]]
            cmd = command(participant, "buyer", url=proxy.url, item=spec["item"], budget=spec["budget"])
            result["buyer_command"] = cmd
            buyer_env = environment(participant, directory, mode, api_url)
            # Different roles need separate immutable transport directories.
            buyer_env["A2A_BENCH_TRACE_DIR"] = str(directory / "buyer-transport")
            buyer_env["A2A_TRACE_DIR"] = str(directory / "buyer-trace")
            with (directory / "buyer-stdout.txt").open("xb") as stdout, (directory / "buyer-stderr.txt").open("xb") as stderr:
                buyer_process = subprocess.Popen(cmd, cwd=participant["path"], env=buyer_env,
                    stdout=stdout, stderr=stderr, start_new_session=True)
                try:
                    result["buyer_exit_code"] = buyer_process.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    result["timeout"] = True
                    stop(buyer_process)
                    result["buyer_exit_code"] = buyer_process.returncode
            parsed = parsed_result((directory / "buyer-stdout.txt").read_text())
            if parsed is not None:
                result["buyer_result"] = parsed
        wire = proxy.evidence()
        result["wire_evidence"] = wire
        result["receipts"] = wire["receipts"]
        result.update(classify_outcome(spec, wire, buyer_exit_code=result.get("buyer_exit_code"),
                                      buyer_result=result.get("buyer_result"),
                                      fixed_buyer=result.get("fixed_buyer"),
                                      timed_out=result.get("timeout", False)))
        if spec["seller"] == "kit":
            result["kit_record"] = record
    except Exception as exc:
        result.update(error=f"{type(exc).__name__}: {exc}", completed_valid=False)
    finally:
        stop(buyer_process)
        stop(server)
        if stream is not None:
            stream.close()
        if proxy is not None:
            result["rpc_calls"] = sum(row["method"] == "POST" for row in proxy.rows)
            proxy.close()
        if local is not None:
            local.close()
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_new(directory / "result.json", result)
    print(json.dumps({"event": "match_finished", **result}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--participants", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--kit", required=True)
    parser.add_argument("--mode", choices=["native", "controlled"], default="native")
    parser.add_argument("--kind", choices=["fixed", "cross"], default="fixed")
    parser.add_argument("--only", default="ours")
    parser.add_argument("--roles", default="buyer,seller")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--items", default="lamp")
    parser.add_argument("--api-url")
    args = parser.parse_args()
    participants = {entry["id"]: entry for entry in json.loads(Path(args.participants).read_text())}
    selected = args.only.split(",")
    out = Path(args.out).resolve()
    out.mkdir(parents=True)
    before = {key: source_hashes(value["path"]) for key, value in participants.items()
              if key in set(selected) | {"ours"}}
    write_new(out / "manifest.json", {"arguments": vars(args), "participants": participants,
              "source_hashes": before, "runner_sha256": RUNNER_SHA256,
              "kit_sha256": hashlib.sha256(Path(args.kit).read_bytes()).hexdigest(),
              "inference_gap_seconds_override": os.environ.get("A2A_INFERENCE_GAP_SECONDS"),
              "controlled_model": "gpt-6-sol/low" if args.mode == "controlled" else None,
              "claim_scope": "Exploratory observed comparisons, not statistical dominance or historical provider reproduction"})
    kit = load_kit(args.kit)
    results = []
    for repeat in range(1, args.repeats + 1):
        for item in args.items.split(","):
            budget = round(RESERVES[item] * 1.2)
            for name in selected:
                for role in args.roles.split(","):
                    if args.kind == "fixed":
                        buyer, seller = (name, "kit") if role == "buyer" else ("kit", name)
                    else:
                        buyer, seller = (name, "ours") if role == "buyer" else ("ours", name)
                    spec = {"id": f"r{repeat:02d}-{item}-{name}-{role}", "buyer": buyer,
                            "seller": seller, "item": item, "budget": budget, "repeat": repeat,
                            "participant_role": role, "participant": name}
                    result = run_match(spec, participants, kit, out, args.mode, args.api_url)
                    results.append(result)
                    write_new(out / f"checkpoint-{len(results):03d}.json", results)
    after = {key: source_hashes(participants[key]["path"]) for key in before}
    # Source snapshots must remain stable for the complete run.
    summary = {"mode": args.mode, "kind": args.kind, "runner_sha256": RUNNER_SHA256, "results": results,
               "source_unchanged": before == after, "completed_valid": sum(r["completed_valid"] for r in results),
               "attempts": len(results)}
    write_new(out / "summary.json", summary)
    print(json.dumps({"event": "finished", "attempts": len(results),
                      "completed_valid": summary["completed_valid"], "source_unchanged": before == after}), flush=True)


if __name__ == "__main__":
    main()
