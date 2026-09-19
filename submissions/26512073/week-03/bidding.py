import json
from datetime import datetime
from pathlib import Path

from contractors import build_team
from prompts import make_system_prompt, make_announcement
from tools_shared import Chat, Meter, MODEL, PROVIDER


def request_bid(contractor, task, meter, log=print):
    system = make_system_prompt(contractor)
    announcement = make_announcement(task)

    log(f"[system] {system}")
    log(f"[announcement to {contractor['name']}]\n{announcement}")

    chat = Chat(system, meter, tools=False)
    chat.add_user(announcement)
    reply = chat.send()

    log(f"[raw bid from {contractor['name']}] {reply.text}")
    return reply.text


if __name__ == "__main__":
    folder = Path(__file__).resolve().parent
    tasks = json.loads((folder / "tasks.json").read_text(encoding="utf-8"))
    contractor = build_team("baseline")[0]
    meter = Meter()

    log_folder = folder / "development_logs"
    log_folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_folder / f"first_bid_{stamp}.txt"

    with log_path.open("x", encoding="utf-8") as file:
        def log(message):
            print(message, flush=True)
            file.write(message + "\n")
            file.flush()

        log(f"provider={PROVIDER}, model={MODEL}, temperature=0, max_tokens=300")
        log("Development check: baseline contractor A, task 1")
        try:
            request_bid(contractor, tasks[0], meter, log)
        except Exception as error:
            log(f"ERROR: {type(error).__name__}")
            log(f"Details: {error}")
            log(f"HTTP status: {getattr(error, 'status_code', 'not available')}")
        finally:
            log(f"Model calls completed: {meter.iters}; tokens: {meter.tokens}")

    print(f"Saved log: {log_path}")