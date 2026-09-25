import json
from datetime import datetime
from pathlib import Path

from prompts import make_system_prompt
from tools_shared import (
    Chat, Meter, MODEL, PROVIDER,
    TEMPERATURE, MAX_COMPLETION_TOKENS,
)


if __name__ == "__main__":
    folder = Path(__file__).resolve().parent
    scenarios = json.loads(
        (folder / "scenarios.json").read_text(encoding="utf-8")
    )
    scenario = scenarios[0]

    system = make_system_prompt(
        "buyer",
        scenario["item"],
        scenario["budget"],
        "structured",
    )
    meter = Meter()
    chat = Chat(system, meter, tools=False)
    chat.add_user("Begin the negotiation.")

    log_folder = folder / "development_logs"
    log_folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_folder / f"buyer_check_{stamp}.txt"

    with log_path.open("x", encoding="utf-8") as file:
        def log(message):
            print(message, flush=True)
            file.write(message + "\n")
            file.flush()

        log(
            f"provider={PROVIDER}, model={MODEL}, "
            f"temperature={TEMPERATURE}, "
            f"max_completion_tokens={MAX_COMPLETION_TOKENS}"
        )
        log("Development check: one structured buyer message")
        log(f"[system] {system}")
        log("[user] Begin the negotiation.")

        try:
            reply = chat.send()
            log(f"[buyer] {reply.text}")
        except Exception as error:
            log(f"ERROR: {type(error).__name__}")
            log(
                f"HTTP status: "
                f"{getattr(error, 'status_code', 'not available')}"
            )
        finally:
            log(f"Completed calls: {meter.iters}; tokens: {meter.tokens}")

    print(f"Saved log: {log_path}")