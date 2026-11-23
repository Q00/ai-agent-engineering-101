"""Serve 20 distinct A2A shops. Negotiation is driven by Groq in each shop."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import time

import uvicorn
from starlette.applications import Starlette
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
import seller_catalog as base


async def serve(config_path):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    gate = asyncio.Semaphore(1)
    class Shop(base.CatalogSeller):
        async def call_model(self, kwargs, task_id, deadline):
            # Multiple negotiations stay open, while seller model requests are
            # serialized to reduce bursts against the same Groq account.
            async with gate:
                return await super().call_model(kwargs, task_id, time.monotonic() + 115)
    servers, agents = [], []
    for shop in config:
        catalog = base.Catalog(shop['catalog_path'])
        agent = Shop(catalog); agents.append(agent)
        url = shop['url'] + '/'
        card = AgentCard(name=shop['name'], description=shop['skill_description'], version='1.0.0',
            default_input_modes=['text/plain'], default_output_modes=['text/plain', 'application/json'],
            capabilities=AgentCapabilities(streaming=False),
            supported_interfaces=[AgentInterface(protocol_binding='JSONRPC', protocol_version='1.0', url=url)],
            skills=[AgentSkill(id='office-shopping', name='Negotiate office furniture',
                description=shop['skill_description'], tags=['shopping', 'negotiation'])])
        handler = DefaultRequestHandler(agent_executor=agent, task_store=InMemoryTaskStore(), agent_card=card)
        app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, '/'))
        port = int(shop['url'].rsplit(':', 1)[-1])
        servers.append(uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')))
    base.log('shops_starting', count=len(servers), model=base.MODEL)
    try:
        await asyncio.gather(*(server.serve() for server in servers))
    finally:
        for agent in agents:
            await agent.llm.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--config', required=True); args = p.parse_args()
    if not os.getenv('GROQ_API_KEY'): p.error('Set GROQ_API_KEY first.')
    asyncio.run(serve(args.config))
