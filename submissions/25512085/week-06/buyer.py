"""A2A v1.0 Buyer Agent driven by Codex CLI harness.
Negotiates to buy within an assigned budget; never pays more than budget.
"""
import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid
import httpx


def call_harness(prompt: str) -> str:
    """Call Codex CLI harness non-interactively in an empty directory."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        tmp_path = tmp.name
    empty_dir = tempfile.gettempdir()
    p = None
    try:
        cmd = f'codex exec --skip-git-repo-check --ephemeral --cd "{empty_dir}" -m gpt-5.6-sol -o "{tmp_path}" -'
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            text=True,
        )
        p.communicate(input=prompt, timeout=20)
        if os.path.exists(tmp_path):
            with open(tmp_path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception as e:
        print(f"[Buyer Harness Warning]: {e}", file=sys.stderr)
        if p:
            try:
                p.kill()
            except Exception:
                pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    return ""


def rpc_call(url: str, method: str, params: dict) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4())[:8],
        "method": method,
        "params": params,
    }
    headers = {"A2A-Version": "1.0", "Content-Type": "application/json"}
    resp = httpx.post(url, json=body, headers=headers, timeout=60)
    data = resp.json()
    if "error" in data:
        return {"error": data["error"]}
    return data.get("result", {})


def extract_last_number(task: dict) -> int | None:
    msg = (task or {}).get("status", {}).get("message", {})
    text = " ".join(p.get("text", "") for p in msg.get("parts", []))
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if nums:
        return math.ceil(float(nums[-1]))
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Seller base URL")
    parser.add_argument("--item", required=True, help="Item to buy")
    parser.add_argument("--budget", type=int, required=True, help="Maximum budget")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    item = args.item.lower()
    budget = args.budget

    # 1. Fetch Agent Card
    card_url = f"{base_url}/.well-known/agent-card.json"
    card_resp = httpx.get(card_url, timeout=15)
    card = card_resp.json()
    interfaces = card.get("supportedInterfaces") or []
    rpc_url = interfaces[0].get("url") if interfaces else f"{base_url}/"

    # 2. First message: open inquiry
    msg = {
        "role": "ROLE_USER",
        "messageId": str(uuid.uuid4()),
        "parts": [{"text": f"I want to buy a {item}"}],
    }
    res = rpc_call(rpc_url, "SendMessage", {"message": msg})
    if "error" in res:
        print(f"Error opening negotiation: {res['error']}", file=sys.stderr)
        return

    task = res.get("task", res)
    task_id = task.get("id")
    task_status = task.get("status", {})
    task_state = task_status.get("state")
    seller_ask = extract_last_number(task)

    last_offer = 0
    rounds = 0

    while task_state == "TASK_STATE_INPUT_REQUIRED" and rounds < 6:
        rounds += 1

        # Check if seller ask is already within our budget
        if seller_ask is not None and seller_ask <= budget:
            offer = seller_ask
        else:
            prompt = (
                f"You are a buyer negotiating to purchase a {item}.\n"
                f"Maximum budget: {budget} (You MUST NEVER exceed {budget}).\n"
                f"Seller's current ask: {seller_ask}.\n"
                f"Your last offer: {last_offer}.\n"
                f"Round: {rounds}/5.\n"
                f"If seller refuses to go below {budget} and you have already offered your maximum, reply: QUIT\n"
                f"Otherwise propose your next offer (must be <= {budget}). Reply format: OFFER <number>"
            )
            resp = call_harness(prompt).upper()
            if "QUIT" in resp and last_offer >= budget:
                print(f"Walking away: seller ask {seller_ask} exceeds budget {budget}")
                rpc_call(rpc_url, "CancelTask", {"id": task_id})
                break

            nums = re.findall(r"\d+", resp)
            if nums:
                suggested = int(nums[-1])
                offer = min(budget, suggested)
            else:
                step = max(10, (budget - last_offer) // 2)
                offer = min(budget, last_offer + step)

            if last_offer >= budget and seller_ask is not None and seller_ask > budget:
                print(f"Cannot agree: seller ask {seller_ask} > budget {budget}")
                rpc_call(rpc_url, "CancelTask", {"id": task_id})
                break

        # Strictly enforce budget guardrail in code
        offer = min(budget, offer)
        last_offer = offer

        follow_up = {
            "role": "ROLE_USER",
            "messageId": str(uuid.uuid4()),
            "taskId": task_id,
            "parts": [{"text": str(offer)}],
        }
        res = rpc_call(rpc_url, "SendMessage", {"message": follow_up})
        if "error" in res:
            print(f"Negotiation ended with response: {res['error']}")
            break

        task = res.get("task", res)
        task_state = task.get("status", {}).get("state")
        seller_ask = extract_last_number(task)

        if task_state == "TASK_STATE_COMPLETED":
            print(f"Deal closed at {offer} within budget {budget}!")
            break
        elif task_state in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED"):
            print(f"Negotiation terminated ({task_state})")
            break


if __name__ == "__main__":
    main()
