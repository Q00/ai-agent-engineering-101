"""Scripted buyer for the seller: sends fixed messages on one A2A task and prints the replies and the receipt.

    python tests/talk.py http://127.0.0.1:9311/ "I want to buy <name> (<sku>). Is it fresh?" 1000 2000 3000

Each message after the first goes on the same task; the script stops when the task completes or is rejected.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from buyer import receipt, send, status_text  # noqa: E402


def main():
    url, *msgs = sys.argv[1:]
    task_id = None
    for text in msgs:
        task = send(url, text, task_id)
        task_id = task["id"]
        state = task["status"]["state"]
        print(f"<- {state}: {status_text(task)}")
        if state != "TASK_STATE_INPUT_REQUIRED":
            r = receipt(task)
            if r:
                print("receipt:", json.dumps(r, ensure_ascii=False))
            return


if __name__ == "__main__":
    main()
