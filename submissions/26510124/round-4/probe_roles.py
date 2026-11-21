#!/usr/bin/env python3
"""Real CLI role responses in an isolated fixture; no market/tool/wallet execution."""
from contextlib import redirect_stdout, redirect_stderr
import json
from pathlib import Path
import shutil

from claude_model import ClaudeModel
from local_support import ROOT, offline_context, run_id
from role_runtime import MultiRoleMind, SharedToolbox, kit


class NoTools:
    tools = {}

    def listing(self):
        return "No tools: isolated model response probe"

    def read(self, name):
        return "Tool reads disabled in probe"

    def forge(self, args):
        raise AssertionError("Probe model tried forging; no tool was executed")

    def call(self, name, args):
        raise AssertionError("Probe model tried a tool; no tool was executed")


def probe():
    name = run_id("role-model-probe")
    work = ROOT / ".local" / name
    work.mkdir(parents=True)
    evidence = ROOT / "logs" / name
    evidence.mkdir()
    with (evidence / "console.log").open("x") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            agent = json.loads((ROOT / "agent.json").read_text())
            ctx = offline_context()
            ctx["addresses"] = {"shop": "OFFLINE-NO-WALLET", "buyer": "OFFLINE-NO-WALLET"}
            mind = MultiRoleMind(work, agent, None, SharedToolbox(work, ctx), ctx, ["shop", "buyer"])
            for role, child in mind.minds.items():
                child.toolbox = NoTools()
                child.agent.update(max_steps=1, run_minutes=1)
                event = kit.Event("human", "isolated local probe",
                    "이것은 시장 없는 연결 검사입니다. 현재 역할과 기존 정책 하나를 한국어로 짧게 설명해주세요. "
                    "추가 작업은 없습니다. 반드시 지금 이 메시지 ID에 reply 행동 한 번만 반환하세요. "
                    "재고나 거래 결과는 모른다고 답하세요. 도구 생성·조회·실행은 금지합니다.")
                child.deliver(event)
                child.run_once(child.fresh())
                if not event.done.is_set() or not event.answer.get("reply"):
                    raise AssertionError(f"{role} did not answer the probe")
                print(json.dumps({"role": role, "requested_model": agent["role_models"][role]["model"],
                                  "answer": event.answer, "market": "not contacted"}, ensure_ascii=False), flush=True)
            print("PASS: shop Haiku, buyer Opus, operations Opus replied through the role runtime; no market/tools/wallets", flush=True)
        finally:
            if (work / "logs").exists():
                shutil.copytree(work / "logs", evidence / "runtime-logs")
    print("Probe evidence:", evidence.relative_to(ROOT))


if __name__ == "__main__":
    probe()
