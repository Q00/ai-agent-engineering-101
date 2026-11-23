"""Replay lecture checkpoints with the official CLI; preserve each raw run.

This is a lab client, not the assignment's harness-driven buyer or trade test.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import traceback

import httpx


SUBMISSION = Path(__file__).resolve().parent.parent
REFERENCE = SUBMISSION / "lab" / "reference"
CLI = SUBMISSION / ".runtime" / "a2a" / ("a2a.exe" if os.name == "nt" else "a2a")
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def find_task(value):
    if isinstance(value, dict):
        if isinstance(value.get("status"), dict) and "state" in value["status"]:
            return value
        for key in ("task", "result", "data"):
            if key in value:
                found = find_task(value[key])
                if found is not None:
                    return found
    return None


def part_texts(value):
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            yield value["text"]
        for child in value.values():
            yield from part_texts(child)
    elif isinstance(value, list):
        for child in value:
            yield from part_texts(child)


class Lab:
    def __init__(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.logs = SUBMISSION / "logs" / "lab" / stamp
        self.logs.mkdir(parents=True, exist_ok=False)
        self.sequence = 0
        self.checks = []
        self.env = os.environ.copy()
        self.env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", NO_PROXY="127.0.0.1,localhost")
        self.http = httpx.Client(trust_env=False, timeout=3)

    def require(self, name, condition, detail):
        self.checks.append({"check": name, "passed": bool(condition), "detail": detail})
        print(f"{'PASS' if condition else 'FAIL'} {name}: {detail}", flush=True)
        if not condition:
            raise AssertionError(f"{name}: {detail}")

    def command(self, name, args, *, expected=0, timeout=45):
        self.sequence += 1
        path = self.logs / f"{self.sequence:02d}-{name}.log"
        # A new file per invocation; never replace an earlier raw log.
        with path.open("xb") as log:
            log.write(("$ " + subprocess.list2cmdline([str(a) for a in args]) + "\n").encode())
            log.flush()
            try:
                result = subprocess.run(
                    [str(a) for a in args], cwd=SUBMISSION, env=self.env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                    creationflags=HIDDEN,
                )
            except subprocess.TimeoutExpired as exc:
                log.write(exc.stdout or b"")
                log.write(b"\nSTDERR:\n" + (exc.stderr or b""))
                log.write(b"\nTIMEOUT\n")
                raise
            log.write(result.stdout)
            log.write(b"\nSTDERR:\n" + result.stderr)
            log.write(f"\nexit code {result.returncode}\n".encode())
        if result.returncode != expected:
            raise RuntimeError(f"{name}: exit {result.returncode}, expected {expected}; see {path}")
        return result.stdout.decode("utf-8-sig"), result.stderr.decode("utf-8-sig")

    def cli_json(self, name, *args, expected=0, timeout=45):
        out, err = self.command(name, [CLI, *args, "-o", "json"], expected=expected, timeout=timeout)
        # Successful commands use stdout; structured CLI errors may use stderr.
        return json.loads(out.strip() or err.strip())

    def send(self, name, url, text, task_id=None):
        args = ["send", "-a", url]
        if task_id:
            args += ["--task-id", task_id]
        payload = self.cli_json(name, *args, text)
        task = find_task(payload)
        if task is None:
            raise ValueError(f"No task in {name}: {payload}")
        if task_id:
            self.require(name + "_same_task", task["id"] == task_id, task["id"])
        return task

    @contextmanager
    def server(self, name, args, port, *, cwd=SUBMISSION):
        url = f"http://127.0.0.1:{port}"
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is already occupied; no existing process was stopped.")
        with (self.logs / f"server-{name}.log").open("xb") as log:
            log.write(("$ " + subprocess.list2cmdline([str(a) for a in args]) + "\n").encode())
            log.flush()
            process = subprocess.Popen(
                [str(a) for a in args], cwd=cwd, env=self.env,
                stdout=log, stderr=subprocess.STDOUT, creationflags=HIDDEN,
            )
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"{name} exited {process.returncode}; inspect its server log")
                    try:
                        response = self.http.get(url + "/.well-known/agent-card.json")
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                else:
                    raise TimeoutError(f"{name} did not become ready")
                yield url
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def check_card(self, name, url, expected_name=None, expected_binding="JSONRPC", require_skills=True):
        card = self.cli_json(name + "_card", "card", "get", url)
        interface = card.get("supportedInterfaces", [{}])[0]
        self.require(
            name + "_card",
            bool(card.get("name")) and (not require_skills or bool(card.get("skills")))
            and interface.get("protocolBinding") == expected_binding
            and interface.get("protocolVersion") == "1.0"
            and (expected_name is None or card["name"] == expected_name),
            f"name={card.get('name')}; interface={interface}",
        )

    def run_protocol(self):
        out, _ = self.command("cli-version", [CLI, "version"])
        self.require("cli_version", "0.3.0" in out, out.strip())
        with self.server("echo", [CLI, "server", "--echo", "--port", "8080"], 8080) as url:
            self.check_card("echo", url, expected_binding="HTTP+JSON", require_skills=False)
            task = self.send("echo-send", url, "hello A2A")
            self.require("echo_completed_artifact", task["status"]["state"] == "TASK_STATE_COMPLETED"
                         and "hello A2A" in list(part_texts(task.get("artifacts", []))),
                         f"state={task['status']['state']}; artifacts={task.get('artifacts')}")

        with self.server("hello", [sys.executable, REFERENCE / "hello_agent.py"], 9000) as url:
            self.check_card("hello", url, "Hello Agent")
            task = self.send("hello-send", url, "hi there")
            self.require("hello_reply", task["status"]["state"] == "TASK_STATE_COMPLETED"
                         and "Hello! You said: hi there" in list(part_texts(task["status"])),
                         str(task["status"]))

        with self.server("seller", [sys.executable, REFERENCE / "seller_agent.py"], 9999) as url:
            self.check_card("seller", url, "Lamp Shop")
            request = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {
                "message": {"role": "ROLE_USER", "messageId": "missing-version", "parts": [{"text": "I want to buy a lamp"}]}}}
            response = self.http.post(url + "/", json=request)
            raw = "POST without A2A-Version\n" + json.dumps(request) + "\nHTTP " + str(response.status_code) + "\n" + response.text
            with (self.logs / "missing-version.log").open("x", encoding="utf-8") as log:
                log.write(raw)
            self.require("missing_version_rejected", response.json().get("error", {}).get("code") == -32009,
                         response.text)

            task = self.send("lamp-open", url, "I want to buy a lamp")
            task_id = task["id"]
            self.require("lamp_open_130", task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
                         and "asking 130" in " ".join(part_texts(task["status"])), str(task["status"]))
            task = self.send("lamp-offer-80", url, "80", task_id)
            self.require("lamp_counter_105", task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
                         and "Counteroffer: 105" in " ".join(part_texts(task["status"])), str(task["status"]))
            task = self.send("lamp-buy-105", url, "105", task_id)
            receipts = [part.get("data", {}) for artifact in task.get("artifacts", []) for part in artifact.get("parts", [])]
            self.require("lamp_receipt_105", task["status"]["state"] == "TASK_STATE_COMPLETED"
                         and any(r.get("item") == "lamp" and r.get("price") == 105 for r in receipts),
                         f"state={task['status']['state']}; receipts={receipts}")
            task = find_task(self.cli_json("lamp-get", "task", "get", "-a", url, task_id))
            self.require("lamp_history", task is not None and len(task.get("history", [])) == 5,
                         f"history length={len(task.get('history', [])) if task else None}")
            error = self.cli_json("lamp-finished-error", "send", "-a", url, "--task-id", task_id, "110", expected=1)
            self.require("finished_task_error", error.get("error", {}).get("a2aCode") == "UNSUPPORTED_OPERATION", str(error))

            task = self.send("sofa-rejected", url, "I want to buy a sofa")
            self.require("unknown_item_rejected", task["status"]["state"] == "TASK_STATE_REJECTED", str(task["status"]))
            task = self.send("chair-open", url, "I want to buy a chair")
            for attempt in range(1, 4):
                task = self.send(f"chair-low-{attempt}", url, "100", task["id"])
                expected = "TASK_STATE_REJECTED" if attempt == 3 else "TASK_STATE_INPUT_REQUIRED"
                self.require(f"chair_round_{attempt}", task["status"]["state"] == expected, str(task["status"]))
            task = self.send("bookshelf-open", url, "I want to buy a bookshelf")
            canceled = find_task(self.cli_json("bookshelf-cancel", "task", "cancel", "-a", url, task["id"]))
            self.require("bookshelf_canceled", canceled is not None and canceled["id"] == task["id"]
                         and canceled["status"]["state"] == "TASK_STATE_CANCELED", str(canceled))

    def run_harness(self, executable, shell_directory, kind):
        """Lecture STEP 6: one-shot harness output as an A2A artifact."""
        executable = Path(executable or sys.executable).resolve(strict=True)
        if shell_directory:
            shell_directory = Path(shell_directory).resolve(strict=True)
            self.env["PATH"] = str(shell_directory) + os.pathsep + self.env.get("PATH", "")
        empty = SUBMISSION / ".runtime" / "harness-empty"
        empty.mkdir(parents=True, exist_ok=True)
        if list(empty.iterdir()):
            raise RuntimeError("Harness work directory must be empty")
        if kind == "claude":
            harness_args = [
                executable.as_posix(), "-p", "--model", "haiku", "--tools", "",
                "--setting-sources", "", "--no-session-persistence",
            ]
        elif kind == "codex":
            # Keep the user's configured model. Only isolate project documents
            # and session storage for this one-shot, read-only protocol lab.
            harness_args = [
                executable.as_posix(), "exec", "--skip-git-repo-check",
                "--sandbox", "read-only", "--ephemeral", "--color", "never",
                "-c", "project_doc_max_bytes=0", "-",
            ]
        else:
            harness_args = [
                executable.as_posix(), (SUBMISSION / "lab" / "openai_smoke.py").as_posix(),
                "--metadata-log", (self.logs / "api-call.json").as_posix(),
            ]
        command = shlex.join(harness_args)
        if kind == "openai":
            out, _ = self.command("openai-config", [executable, SUBMISSION / "lab" / "openai_smoke.py", "--describe"])
            config = json.loads(out)
            self.require("api_key_configured", config["key_configured"], "Key is configured; its value is not logged.")
            self.require("api_model", bool(config["model"]), config["model"])
        else:
            out, _ = self.command(kind + "-version", [executable, "--version"])
            self.require("harness_version", bool(out.strip()), out.strip())
        card_name = kind.capitalize() + " Lab"
        args = [CLI, "server", "--exec", command, "--name", card_name, "--port", "8090"]
        with self.server("harness", args, 8090, cwd=empty) as url:
            self.check_card("harness", url, card_name, expected_binding="HTTP+JSON", require_skills=False)
            payload = self.cli_json(
                "harness-reply", "send", "-a", url,
                "Do not use tools. Reply with exactly these three words and nothing else: A2A lab ready",
                "--timeout", "120s", timeout=135,
            )
            task = find_task(payload)
            texts = list(part_texts(task.get("artifacts", []))) if task else []
            self.require("harness_completed_artifact", task is not None
                         and task["status"]["state"] == "TASK_STATE_COMPLETED"
                         and any(text.strip() == "A2A lab ready" for text in texts),
                         json.dumps(payload, ensure_ascii=False))

    def finish(self, error=None):
        self.http.close()
        summary = {
            "kind": "lecture-lab-not-assignment-trade-test",
            "python": sys.version,
            "packages": {p: version(p) for p in ("a2a-sdk", "uvicorn", "httpx")},
            "passed": error is None and all(c["passed"] for c in self.checks),
            "checks": self.checks,
            "error": error,
        }
        with (self.logs / "lab-summary.json").open("x", encoding="utf-8") as output:
            json.dump(summary, output, ensure_ascii=False, indent=2)
        print(f"Logs: {self.logs}", flush=True)
        return 0 if summary["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness-only", action="store_true", help="Run lecture STEP 6 using an existing harness login")
    parser.add_argument("--harness", choices=["claude", "codex", "openai"], default="claude")
    parser.add_argument("--harness-executable", "--claude-executable", dest="harness_executable", help="Path to the native harness executable")
    parser.add_argument("--shell-directory", help="Directory containing sh.exe; --exec in CLI v0.3.0 uses sh -c")
    args = parser.parse_args()
    if args.harness_only and args.harness != "openai" and not args.harness_executable:
        parser.error("--harness-only requires --harness-executable")
    lab = Lab()
    error = None
    try:
        if args.harness_only:
            lab.run_harness(args.harness_executable, args.shell_directory, args.harness)
        else:
            lab.run_protocol()
    except Exception:
        error = traceback.format_exc()
        with (lab.logs / "failure.log").open("x", encoding="utf-8") as log:
            log.write(error)
        print(error, file=sys.stderr)
    return lab.finish(error)


if __name__ == "__main__":
    raise SystemExit(main())
