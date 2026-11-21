#!/usr/bin/env python3
"""Local mini app preview: real SDK/server, no market, wallets, tunnel or forging.

Default fixture mode is deterministic. --model uses the chosen Claude CLI login
for chat and the page's shopping_brief tool only, never a market action.
"""
import argparse
import json
from pathlib import Path
import threading
import time

from local_support import ROOT, load_kit, run_id


class PreviewMind:
    def __init__(self, root, model=None):
        self.root, self.model, self.app_tools = root, model, {}
        self.lock = threading.Lock()

    def record(self, action, thought):
        entry = {"mode": "local_chat_only" if self.model else "scripted_preview",
                 "ts": int(time.time()), "action": action, "thought": thought}
        with (self.root / "logs/react.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def deliver(self, event):
        threading.Thread(target=self.answer, args=(event,), daemon=True).start()
        return event

    def answer(self, event):
        if not self.lock.acquire(blocking=False):
            event.answer = {"reply": "이전 답변을 처리 중입니다. 잠시 뒤 다시 보내 주세요.", "state": "input_required"}
            event.done.set()
            return
        try:
            if self.model is None:
                info = self.app_tools.get("app_shopping_brief")
                result = info[1]({}) if info else {"status": "page_tool_not_connected"}
                self.record("app_shopping_brief", "고정 응답 미리보기에서 페이지 도구 연결 확인")
                reply = "[로컬 미리보기] 전달받은 구매 조건입니다. 실제 거래는 하지 않았습니다.\n" + json.dumps(result, ensure_ascii=False)
            else:
                config = json.loads((ROOT / "agent.json").read_text())
                messages = [
                    {"role": "system", "content": config["persona"] + "\n지금은 시장 미접속 로컬 대화 테스트다. "
                     "가능한 action은 reply(args.text에 한국어 답변), app_shopping_brief(args={})뿐이다. "
                     "도구·상품·재고·결제 결과를 만들어내지 말고, 구매 조건을 보려면 페이지 도구를 먼저 호출한다. "
                     "한 번에 thought/action/args JSON 하나만 답한다."},
                    {"role": "user", "content": event.text}]
                reply = None
                for _ in range(2):
                    raw = self.model.complete(messages, timeout=40)
                    decision = json.loads(raw)
                    self.record(decision["action"], decision["thought"])
                    if decision["action"] == "reply":
                        reply = decision["args"].get("text")
                        if not isinstance(reply, str) or not reply.strip():
                            raise ValueError("Empty model reply")
                        break
                    if decision["action"] != "app_shopping_brief":
                        raise ValueError("Market and forge actions are disabled in the local preview")
                    tool = self.app_tools.get("app_shopping_brief")
                    result = tool[1]({}) if tool else {"error": "Open the page before using its tool"}
                    messages.extend([{"role": "assistant", "content": raw},
                                     {"role": "user", "content": "Observation: " + json.dumps(result, ensure_ascii=False)}])
                if reply is None:
                    raise ValueError("Local chat step limit reached")
            event.answer = {"reply": reply, "state": "completed"}
        except Exception as error:
            self.record("preview_error", type(error).__name__)
            # CLI details remain in the model logs, not in the page or its tools.
            event.answer = {"reply": "로컬 대화 연결을 확인하지 못했습니다. Claude 로그인과 실행 로그를 확인해 주세요.", "state": "rejected"}
        finally:
            self.lock.release()
            event.done.set()


def create_app(root, *, use_model=False):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    (root / "app").symlink_to(ROOT / "app", target_is_directory=True)
    kit = load_kit()
    import miniapp
    miniapp.CALL_WAIT = 20  # Local chat stays within the SDK's 120 s request window.
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    model = None
    if use_model:
        from claude_model import ClaudeModel
        model = ClaudeModel(json.loads((ROOT / "agent.json").read_text()))
    mind = PreviewMind(root, model)
    toolbox = type("EmptyToolbox", (), {"tools": {}})()
    web = miniapp.app(root, mind, toolbox, kit.Event, name="덕용상점")

    async def preparation(_request):
        return JSONResponse({"mode": "claude" if use_model else "fixture", "market_connected": False,
                             "wallets_loaded": False, "forging_enabled": False})
    web.routes.insert(0, Route("/api/preparation", preparation))
    web.state.preview_mind = mind
    return web


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8814)
    parser.add_argument("--model", action="store_true", help="use Claude CLI for local chat only")
    args = parser.parse_args()
    root = ROOT / ".local" / run_id("preview")
    app = create_app(root, use_model=args.model)
    print(f"Local URL: http://127.0.0.1:{args.port}", flush=True)
    print("Mode:", "Claude chat only" if args.model else "scripted fixture (not LLM)", flush=True)
    print("No market registration, wallet, payment or tunnel.", flush=True)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
