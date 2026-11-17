"""Exercise the official ReAct/forge loop with scripted fixtures, not an LLM.

This is infrastructure verification, not evidence of an agent learning market tools.
The only generated tool counts values in a supplied list. No market logic is supplied.
"""
import json
from pathlib import Path
import shutil

from local_support import ROOT, load_kit, offline_context, run_id


def tool_fixture(*, broken=False, version=1):
    logic = "def run(args, kit):\n    return {'count': len(args['values'])}\n"
    if broken:
        logic = "def run(args, kit):\n    return {'count': 999}\n"
    elif version == 2:
        logic = "def run(args, kit):\n    return {'count': len(args.get('values', []))}\n"
    test = ("def test(run, kit):\n"
            "    assert kit.testing\n"
            "    assert run({'values': []}, kit) == {'count': 0}\n"
            "    assert run({'values': ['a', 'b']}, kit) == {'count': 2}\n")
    if version == 2 and not broken:
        test += "    assert run({}, kit) == {'count': 0}\n"
    return {"name": "count_fixture", "description": "TEST FIXTURE: count args.values; no market behavior",
            "permission": {"network": [], "write": [], "wallet": [], "identity": []},
            "logic": logic, "test": test}


class ScriptedModel:
    def __init__(self, moves):
        self.moves = iter(moves)

    def complete(self, messages):
        return json.dumps(next(self.moves), ensure_ascii=False)


def move(action, args):
    return {"thought": "SCRIPTED FIXTURE: infrastructure verification, no LLM decision",
            "action": action, "args": args}


def demo():
    kit = load_kit()
    ident = run_id("scripted-demo")
    root = ROOT / ".local" / ident
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    evidence = ROOT / "logs" / ident
    metadata = {"mode": "scripted_offline_fixture", "llm_called": False,
                "market_registered": False, "wallet_created": False,
                "intentional_failure": "The first tool returns 999 and must fail its test.",
                "scope": "Official kit wiring, not autonomous tool design or Round 4 settlement."}
    (root / "RUN.json").write_text(json.dumps(metadata, indent=2) + "\n")
    old_log = kit.mk.LOG_FILE
    kit.mk.LOG_FILE = root / "logs" / "agent-kit.log"
    try:
        ctx = offline_context()
        toolbox = kit.Toolbox(root, ctx)
        script = [move("forge_tool", tool_fixture(broken=True)),
                  move("forge_tool", tool_fixture()),
                  move("count_fixture", {"values": ["a", "b", "c"]}),
                  move("forge_tool", tool_fixture(version=2)),
                  move("write_note", {"text": "SCRIPTED FIXTURE: count_fixture v2 tested; this is not market evidence."}),
                  move("sleep", {"seconds": 30, "why": "fixture finished; no real sleep loop"})]
        agent = {"goal": "Verify local fixture wiring only", "persona": "Scripted test",
                 "max_steps": 10, "run_minutes": 2}
        kit.Mind(root, agent, ScriptedModel(script), toolbox, ctx, []).run_once([])
        # Construct a fresh toolbox/mind: demonstrate persistence without forging again.
        reloaded = kit.Toolbox(root, ctx)
        reuse = [move("count_fixture", {}),
                 move("sleep", {"seconds": 30, "why": "reuse verified"})]
        kit.Mind(root, agent, ScriptedModel(reuse), reloaded, ctx, []).run_once([])
        forged = [json.loads(x) for x in (root / "logs/forge.jsonl").read_text().splitlines()]
        assert [x["passed"] for x in forged] == [False, True, True], forged
        assert reloaded.tools["count_fixture"]["version"] == 2
        assert (root / "tools/.deprecated/count_fixture@1/forge.json").is_file()
        steps = [json.loads(x) for x in (root / "logs/react.jsonl").read_text().splitlines()]
        calls = [json.loads(x["observation"]) for x in steps if x["action"] == "count_fixture"]
        assert [x["result"]["count"] for x in calls] == [3, 0], calls
        assert not list(root.rglob("*.key"))
        print("PASS: expected failure, forge, call, version replacement, reload and reuse")
        print("SCRIPTED FIXTURE ONLY: no model, market, wallet or payment was used")
        return evidence
    finally:
        kit.mk.LOG_FILE = old_log
        evidence.parent.mkdir(exist_ok=True)
        # Preserve an untouched snapshot, including a failed run if assertions fail.
        shutil.copytree(root, evidence)
        print("Evidence:", evidence.relative_to(ROOT))


if __name__ == "__main__":
    demo()
