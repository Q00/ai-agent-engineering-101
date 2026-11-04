"""Internal A2A server: one A2A v1.0 agent per department, mounted at /<department> on 127.0.0.1 only (never behind
the tunnel). Each request is a text part holding a signed envelope (envelope.py); the answer is an artifact whose
text part holds {"result": ..., "receipt": ...}. Rejected envelopes fail closed with TASK_STATE_REJECTED.

    python3 -m r3lib.org.server --port 0        # 0 = any free port; the port is written to .market/org/server.json
"""
import argparse
import asyncio
import json
import os
import socket
import time

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

from a2a.helpers import get_message_text, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from .. import config
from ..memory import Memory
from . import client, departments, envelope


class Department(AgentExecutor):
    def __init__(self, name, mem):
        self.name, self.mem, self.gate = name, mem, envelope.Gate(name)

    async def execute(self, context, event_queue):
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        try:
            env = json.loads(get_message_text(context.message) or "")
            body = self.gate.open(env)
        except (ValueError, envelope.Rejected) as e:
            client.log_call({"by": self.name, "rejected": str(e)[:200]})
            await up.reject(say(f"rejected: {e}"))
            return
        ask = lambda to, skill, args: client.call(self.name, to, skill, args, mem=self.mem)
        try:
            result = await asyncio.to_thread(departments.run, self.name, body["skill"], body["args"], self.mem, ask)
        except Exception as e:                           # a skill error is an answer, not a crash
            result = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        result = json.loads(json.dumps(result, ensure_ascii=False, default=str))   # what the caller will hash
        rec = envelope.receipt(self.name, env, result)
        await up.add_artifact(parts=[new_text_part(json.dumps({"result": result, "receipt": rec},
                                                               ensure_ascii=False))], name="result")
        await up.complete(say("done"))

    async def cancel(self, context, event_queue):
        task = context.current_task
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def card(name, port):
    skills = [AgentSkill(id=k, name=k, description=desc, tags=[name]) for k, (_, desc) in departments.SKILLS[name].items()]
    return AgentCard(
        name=f"myeonggi-{name}", version="1.0.0",
        description=f"Internal {name} department of the myeonggi market agent. Signed envelopes only; "
                    f"answers carry a signed receipt.",
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/{name}/",
                                             protocol_version="1.0")],
        skills=skills)


def build(port, mem=None):
    mem = mem or Memory()
    mounts = []
    for name in departments.SKILLS:
        c = card(name, port)
        handler = DefaultRequestHandler(agent_executor=Department(name, mem), task_store=InMemoryTaskStore(), agent_card=c)
        mounts.append(Mount(f"/{name}", app=Starlette(routes=create_agent_card_routes(c) +
                                                       create_jsonrpc_routes(handler, "/"))))
    return Starlette(routes=mounts)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    port = ap.parse_args().port or free_port()
    envelope.registry()                                  # keys and registry exist before the first call
    info = envelope.org_dir() / "server.json"
    info.write_text(json.dumps({"port": port, "pid": os.getpid(), "started": round(time.time())}))
    print(json.dumps({"event": "org_server_started", "port": port, "departments": list(departments.SKILLS)}), flush=True)
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
