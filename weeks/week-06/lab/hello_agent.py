import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


class Hello(AgentExecutor):
    async def execute(self, context, event_queue):
        task = new_task_from_user_message(context.message)  # every message starts a new task
        await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message)
        await up.complete(up.new_agent_message(parts=[new_text_part(f"Hello! You said: {text}")]))

    async def cancel(self, context, event_queue):
        pass


card = AgentCard(
    name="Hello Agent",
    description="Says hello back.",
    version="1.0.0",
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    capabilities=AgentCapabilities(),
    supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url="http://127.0.0.1:9000/", protocol_version="1.0")],
    skills=[AgentSkill(id="hello", name="Say hello", description="Replies to any message.", tags=["demo"])],
)
handler = DefaultRequestHandler(agent_executor=Hello(), task_store=InMemoryTaskStore(), agent_card=card)
app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000)
