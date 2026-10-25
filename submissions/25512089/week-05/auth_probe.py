import asyncio
import os

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


MCP_URL = "http://127.0.0.1:8000/mcp"


async def main():
    token = os.environ["MARKET_TOKEN"]
    negotiation_id = os.environ["TARGET_NEGOTIATION"]
    tool_name = os.environ.get(
        "MARKET_TOOL",
        "get_negotiation",
    )

    arguments = {
        "negotiation_id": negotiation_id
    }

    price = os.environ.get("MARKET_PRICE")

    if price is not None and tool_name == "propose":
        arguments["price"] = int(price)

    headers = {
        "Authorization": f"Bearer {token}"
    }

    async with httpx2.AsyncClient(
        headers=headers
    ) as http_client:

        async with streamable_http_client(
            MCP_URL,
            http_client=http_client,
        ) as (read_stream, write_stream):

            async with ClientSession(
                read_stream,
                write_stream,
            ) as session:

                await session.initialize()

                result = await session.call_tool(
                    tool_name,
                    arguments,
                )

                print("tool:", tool_name)
                print("isError:", result.is_error)

                for item in result.content:
                    if hasattr(item, "text"):
                        print(item.text)


if __name__ == "__main__":
    asyncio.run(main())