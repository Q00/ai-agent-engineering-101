"""Scripted demo model. This file provides scheduler fixtures, not LLM evidence."""
import asyncio
import json


def step(task_id, deps=(), writes=()):
    return {"id": task_id, "goal": f"Fixture: {task_id}",
            "acceptance": "Return fixture analysis facts with evidence.",
            "depends_on": list(deps), "reads": [], "writes": list(writes)}


def proposal(steps=None, confidence=80):
    return {"bid": True, "confidence": confidence, "reason": "Scripted scheduler fixture",
            "plan": {"mode": "delegate" if steps else "execute",
                     "actions": ["Calculate from supplied inputs", "Check and return evidence"],
                     "steps": steps or []}}


def artifact(facts=None):
    return {"summary": "Scripted fixture output, not an LLM result.", "facts": facts or {},
            "evidence": ["Python fixture calculation; no real operations performed."]}


class DemoModel:
    def __init__(self, delay=0.01):
        self.delay = delay

    async def __call__(self, worker, phase, payload, task_id):
        # Longer fixture execution makes actual artifact-generation overlap observable.
        await asyncio.sleep(self.delay * (4 if phase in ("execute", "synthesize") else 1))
        local = payload["task"]["id"]
        if phase == "propose":
            children = None
            if payload["depth"] == 0:
                children = [step("economics"), step("technical"),
                            step("recommendation", ("economics", "technical"))]
            elif local == "economics":
                children = [step("p"), step("q")]
            return json.dumps(proposal(children))
        if phase == "review":
            chosen = {"economics": "B", "technical": "C", "p": "A", "q": "C"}.get(local, "A")
            return json.dumps({"scores": {w: {d: 2 if w == chosen else 1
                                              for d in ("coverage", "feasibility", "verification")}
                                          for w in payload["candidates"]}})
        facts = {}
        if local in ("p", "q"):
            price, customers, variable = (15000, 400, 5000) if local == "p" else (22000, 260, 6000)
            margin = price - variable
            facts = {f"{local}_margin": margin, f"{local}_profit": margin * customers - 2000000,
                     f"{local}_break_even": (2000000 + margin - 1) // margin,
                     f"{local}_low_profit": margin * customers * 0.75 - 2000000,
                     f"{local}_high_profit": margin * customers * 1.25 - 2000000}
        elif local == "technical":
            facts = {"migration_before_app": True, "can_release": False}
        else:
            sources = payload.get("children", payload["inputs"].get("predecessors", {}))
            for result in sources.values():
                facts.update(result["facts"])
        return json.dumps(artifact(facts))
